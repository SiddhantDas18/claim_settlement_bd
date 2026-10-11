"""AI field extraction: one Gemini call per document, then a code guard that drops anything the text doesn't back up.

The model returns each value together with the exact quote it came from. The guard (plain code) then checks:
1. the quote really appears in the document text            -> otherwise "unsupported_by_source"
2. the value really appears in the quote (dates and amounts parsed by code) -> otherwise "value_not_in_quote"
3. dates and amounts can be read by code                    -> otherwise "unparseable_value" / "incomplete_date"
A value that fails any check becomes null, so it shows up as missing instead of as a made-up fact.
"""

import hashlib
import logging
import re
import time
from decimal import Decimal
from functools import cache
from pathlib import Path

from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.schemas.analysis import ExtractedField, ExtractionResult, GuardEvent, GuardReason, LineItem, SourceType
from app.services import parsing

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
CATEGORIES = ("room", "medicine", "diagnostics", "consultation", "surgery", "other")
DOCUMENT_TYPES = ("hospital_bill", "discharge_summary", "prescription", "lab_report", "other")
TEXT_FIELDS = ("patient_name", "hospital_name", "invoice_number")
DATE_FIELDS = ("admission_date", "discharge_date")
AMOUNT_FIELDS = ("total_amount",)
FIELDS = TEXT_FIELDS + DATE_FIELDS + AMOUNT_FIELDS
_CENT = Decimal("0.01")

# Set by evals/run_eval.py so repeated evaluation runs don't call the API again.
cache_dir: Path | None = None
stats = {"api_calls": 0, "cache_hits": 0}


class ExtractionError(RuntimeError):
    pass


# ---- What Gemini must return (used as response_schema; no default values, the API rejects them) ----


class _Value(BaseModel):
    value: str | None
    evidence: str | None


class _LineItem(BaseModel):
    description: str | None
    category: str | None
    amount: str | None
    evidence: str | None


class ModelOutput(BaseModel):
    document_type: str | None
    patient_name: _Value
    hospital_name: _Value
    invoice_number: _Value
    admission_date: _Value
    discharge_date: _Value
    total_amount: _Value
    line_items: list[_LineItem]


# ---- Prompt ----


def load_prompt(version: str) -> str:
    if not re.fullmatch(r"[a-z0-9_]+", version):
        raise ValueError(f"Bad prompt version name: {version!r}")
    path = PROMPTS_DIR / f"{version}.txt"
    if not path.exists():
        raise ValueError(f"Prompt file not found: {path.name}")
    return path.read_text(encoding="utf-8")


def build_prompt(template: str, name: str, doc_type: str, text: str) -> str:
    return (
        template.replace("{{document_name}}", name).replace("{{document_type}}", doc_type).replace("{{text}}", text)
    )


# ---- Gemini call (with cache and retry) ----


GEMINI_TIMEOUT_MS = 90_000  # one document; a hung connection must not block a submission forever


@cache
def _client():
    from google import genai
    from google.genai import types

    return genai.Client(api_key=settings.gemini_api_key, http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS))


def _retry_delay(message: str, attempt: int) -> float:
    match = re.search(r"retry(?:Delay)?\D{0,5}(\d+(?:\.\d+)?)s", message, re.IGNORECASE)
    return min(float(match.group(1)) + 1, 90) if match else min(5 * 2**attempt, 60)


def call_gemini(prompt: str, max_attempts: int = 6) -> str:
    """Send one prompt and return the raw JSON text. Retries on rate limits, server errors and dropped connections."""
    import httpx
    from google.genai import errors, types

    key = hashlib.sha256(f"{settings.gemini_model}\n{prompt}".encode()).hexdigest()
    cached = cache_dir / f"{key}.json" if cache_dir else None
    if cached and cached.exists():
        stats["cache_hits"] += 1
        return cached.read_text(encoding="utf-8")
    if not settings.gemini_api_key:
        raise ExtractionError("GEMINI_API_KEY is not set, so documents can't be read by the AI.")

    config = types.GenerateContentConfig(
        temperature=0, response_mime_type="application/json", response_schema=ModelOutput
    )
    for attempt in range(max_attempts):
        try:
            stats["api_calls"] += 1
            response = _client().models.generate_content(model=settings.gemini_model, contents=prompt, config=config)
            text = response.text or ""
            break
        except errors.APIError as exc:
            message = str(exc)
            if exc.code == 429 and "PerDay" in message:
                raise ExtractionError(f"Gemini quota exhausted for today: {message[:300]}") from exc
            if exc.code in (429, 500, 502, 503, 504) and attempt < max_attempts - 1:
                delay = _retry_delay(message, attempt)
                log.warning("Gemini %s, retrying in %.0fs (attempt %d)", exc.code, delay, attempt + 1)
                time.sleep(delay)
                continue
            raise ExtractionError(f"Gemini error {exc.code}: {message[:300]}") from exc
        except httpx.TransportError as exc:  # dropped connection, timeout, DNS: the request never got an answer
            if attempt < max_attempts - 1:
                delay = min(5 * 2**attempt, 60)
                log.warning("Gemini connection problem (%s), retrying in %.0fs (attempt %d)", type(exc).__name__, delay, attempt + 1)
                time.sleep(delay)
                continue
            raise ExtractionError(f"Could not reach Gemini: {type(exc).__name__}: {exc}") from exc

    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(text, encoding="utf-8")
    return text


# ---- Guard ----


def _check_value(field: str, value: str, evidence: str | None, source_text: str) -> tuple[str | float | None, GuardReason | None]:
    """Return (normalised value, None) if the value is backed by the source, else (None, reason)."""
    if not evidence or not parsing.contains(source_text, evidence):
        return None, "unsupported_by_source"
    if field in DATE_FIELDS:
        day = parsing.parse_date(value)
        if day is None:
            partial = parsing.has_partial_date(value) or parsing.has_partial_date(evidence)
            return None, "incomplete_date" if partial else "unparseable_value"
        if day not in parsing.find_dates(evidence):
            return None, "value_not_in_quote"  # e.g. the year was filled in by the model
        return day.isoformat(), None
    if field in AMOUNT_FIELDS:
        amount = parsing.parse_amount(value)
        if amount is None:
            return None, "unparseable_value"
        if not any(parsing.same_amount(amount, found, tolerance=_CENT) for found in parsing.find_amounts(evidence)):
            return None, "value_not_in_quote"  # e.g. the model added up the line items itself
        return float(amount), None
    if not parsing.contains(evidence, value):
        return None, "value_not_in_quote"
    return value.strip(), None


def apply_guard(output: ModelOutput, source_text: str, result: ExtractionResult) -> ExtractionResult:
    events: list[GuardEvent] = []

    def reject(field: str, value: str | None, evidence: str | None, reason: GuardReason) -> None:
        events.append(GuardEvent(field=field, value=value, evidence=evidence, reason=reason))
        log.warning("AI guard rejected %s=%r from %r: %s (evidence %r)", field, value, result.source, reason, evidence)

    if output.document_type in DOCUMENT_TYPES:
        result.detected_type = output.document_type

    for field in FIELDS:
        item: _Value = getattr(output, field)
        if item.value is None or not item.value.strip():
            result.fields[field] = ExtractedField()
            continue
        value, reason = _check_value(field, item.value, item.evidence, source_text)
        if reason:
            reject(field, item.value, item.evidence, reason)
            result.fields[field] = ExtractedField()
        else:
            result.fields[field] = ExtractedField(value=value, raw=item.value, evidence=item.evidence)

    for index, line in enumerate(output.line_items):
        label = f"line_items[{index}]"
        if line.amount is None:
            continue
        if not line.evidence or not parsing.contains(source_text, line.evidence):
            reject(label, line.amount, line.evidence, "unsupported_by_source")
            continue
        amount = parsing.parse_amount(line.amount)
        if amount is None:
            reject(label, line.amount, line.evidence, "unparseable_value")
            continue
        in_quote = any(parsing.same_amount(amount, found, _CENT) for found in parsing.find_amounts(line.evidence))
        if not in_quote or (line.description and not parsing.contains(line.evidence, line.description)):
            reject(label, line.amount, line.evidence, "value_not_in_quote")
            continue
        category = (line.category or "").strip().lower()
        result.line_items.append(
            LineItem(
                description=(line.description or line.evidence).strip(),
                amount=float(amount),
                category=category if category in CATEGORIES else "other",
                evidence=line.evidence,
            )
        )

    result.guard_events = events
    return result


# ---- Entry point ----


def extract_document(name: str, doc_type: SourceType, text: str, prompt_version: str | None = None) -> ExtractionResult:
    """Extract the claim fields from one document's text. Never raises: problems are returned in `error`."""
    version = prompt_version or settings.prompt_version
    result = ExtractionResult(source=name, source_type=doc_type, model=settings.gemini_model, prompt_version=version)
    if not text.strip():
        result.error = "No text could be read from this document."
        return result
    try:
        raw = call_gemini(build_prompt(load_prompt(version), name, doc_type, text))
        output = ModelOutput.model_validate_json(raw)
    except (ExtractionError, ValidationError, ValueError) as exc:
        log.error("AI extraction failed for %r: %s", name, exc)
        result.error = str(exc)[:500]
        return result
    except Exception as exc:  # anything unexpected from the SDK: report it on this document, never break the claim
        log.exception("Unexpected AI extraction failure for %r", name)
        result.error = f"Unexpected error while reading the document: {type(exc).__name__}"
        return result
    return apply_guard(output, text, result)
