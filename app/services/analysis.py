"""Claim reconciliation and analysis. Plain code, no AI.

Input: what the claimant typed on the form + the fields the AI extracted from each document.
Output: a ClaimAnalysis listing every fact with where it came from, what is missing, where sources disagree,
which checks failed, and a recommended triage category with next steps (always labelled system_inferred).
Conflicts are reported, never resolved: the system does not pick one source over another.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import utcnow
from app.models import Claim
from app.schemas.analysis import (
    AnalyzeRequest,
    ClaimAnalysis,
    ClaimForm,
    Conflict,
    ConflictValue,
    ExtractionResult,
    Fact,
    FactValue,
    FieldSummary,
    LineItem,
    MissingField,
    NextStep,
    PolicyInfo,
    Provenance,
    Recommendations,
    SourceType,
    TriageRecommendation,
    ValidationFlag,
)
from app.services import ai_extraction, parsing
from app.services.claims import check_claim

Extractor = Callable[[str, SourceType, str], ExtractionResult]

FORM = "claim_form"
SYSTEM = "system"
DEFAULT_REQUIRED_DOCUMENTS = ("hospital_bill", "discharge_summary")
COMPARED_FIELDS = ("patient_name", "hospital_name", "invoice_number", "admission_date", "discharge_date", "total_amount")
REQUIRED_FIELDS = (*COMPARED_FIELDS, "line_items")

LABELS = {
    "patient_name": "patient name",
    "hospital_name": "hospital name",
    "invoice_number": "invoice number",
    "admission_date": "admission date",
    "discharge_date": "discharge date",
    "total_amount": "total amount",
    "line_items": "itemised charges",
}
WHY_NEEDED = {
    "patient_name": "Needed to match the patient to the policy holder.",
    "hospital_name": "Needed to confirm where the treatment took place.",
    "invoice_number": "Needed to detect the same bill being claimed twice.",
    "admission_date": "Needed to check the stay is inside the policy period and to apply per-day room limits.",
    "discharge_date": "Needed to check the stay is inside the policy period and to apply per-day room limits.",
    "total_amount": "Needed to know how much is being claimed.",
    "line_items": "Needed to apply the plan's coverage rules per expense category.",
}
SEVERITY = {
    "patient_name": "high",
    "admission_date": "high",
    "discharge_date": "high",
    "total_amount": "high",
    "invoice_number": "medium",
    "hospital_name": "low",
}


@dataclass
class _Value:
    value: str | float
    source: str
    provenance: Provenance
    evidence: str | None


def _same(field: str, a: str | float, b: str | float) -> bool:
    if field == "patient_name":
        return parsing.same_person(str(a), str(b))
    if field == "hospital_name":
        return parsing.same_org(str(a), str(b))
    if field == "invoice_number":
        return parsing.same_invoice(str(a), str(b))
    if field == "total_amount":
        return parsing.same_amount(a, b)  # ±1 INR
    return a == b


def _cluster(field: str, values: Iterable[_Value]) -> list[list[_Value]]:
    """Group values that mean the same thing ('Md. Rahim Uddin' and 'RAHIM UDDIN')."""
    clusters: list[list[_Value]] = []
    for value in values:
        group = next((c for c in clusters if _same(field, c[0].value, value.value)), None)
        if group is None:
            clusters.append([value])
        else:
            group.append(value)
    return clusters


def _representative(group: list[_Value]) -> str | float:
    """The fullest way the agreeing sources wrote the value (they all mean the same thing)."""
    if isinstance(group[0].value, str):
        return max((v.value for v in group), key=lambda text: len(str(text)))
    return group[0].value


def _describe(source: str) -> str:
    if source == FORM:
        return "the claim form"
    if source == SYSTEM:
        return "the system's sum of the bills"
    return f'"{source}"'


def _fmt(value: FactValue) -> str:
    if isinstance(value, float):
        return parsing.format_inr(value)
    if isinstance(value, list):
        return ", ".join(_fmt(v) for v in value)  # type: ignore[arg-type]
    return str(value)


def effective_type(extraction: ExtractionResult) -> str:
    """What the document is: the uploader's label, unless the model clearly reads it as a different specific type.

    Without this, a bill uploaded twice (once labelled "discharge_summary") would satisfy the required-documents check.
    Emails keep their label: they are the claimant's own statement, whatever they contain.
    """
    detected, declared = extraction.detected_type, extraction.source_type
    if detected and detected != "other" and declared != "claimant_email":
        return detected
    return declared


class _Builder:
    def __init__(self) -> None:
        self.facts: list[Fact] = []
        self.values: dict[str, list[_Value]] = {field: [] for field in COMPARED_FIELDS}
        self.fields: dict[str, FieldSummary] = {}
        self.conflicts: list[Conflict] = []
        self.flags: list[tuple[ValidationFlag, str | None]] = []  # (flag, next step for the reviewer)

    def fact(self, field: str, value: FactValue, provenance: Provenance, source: str, evidence: str | None = None) -> None:
        self.facts.append(Fact(field=field, value=value, provenance=provenance, source=source, evidence=evidence))
        if field in self.values and isinstance(value, str | float):
            self.values[field].append(_Value(value, source, provenance, evidence))

    def flag(self, code: str, severity: str, message: str, action: str | None = None) -> None:
        if all(existing.message != message for existing, _ in self.flags):
            self.flags.append((ValidationFlag(code=code, severity=severity, message=message), action))

    def summary(self, field: str, status: str, values: list[_Value], value: FactValue = None) -> None:
        self.fields[field] = FieldSummary(
            field=field,
            status=status,
            value=value,
            provenance=sorted({v.provenance for v in values}),
            sources=list(dict.fromkeys(v.source for v in values)),
        )

    def conflict(self, field: str, values: list[_Value]) -> None:
        self.conflicts.append(
            Conflict(
                field=field,
                values=[ConflictValue(value=v.value, source=v.source, provenance=v.provenance, evidence=v.evidence) for v in values],
                severity=SEVERITY[field],
            )
        )
        self.summary(field, "conflicting", values)

    def compare(self, field: str, values: list[_Value] | None = None) -> None:
        values = self.values[field] if values is None else values
        clusters = _cluster(field, values)
        if not clusters:
            self.summary(field, "missing", [])
        elif len(clusters) == 1:
            self.summary(field, "provided", values, _representative(clusters[0]))
        else:
            self.conflict(field, values)


# ---- Building the analysis ----


def _add_facts(b: _Builder, form: ClaimForm, extractions: list[ExtractionResult]) -> None:
    for field in ("patient_name", "policy_number", "hospital_name", "invoice_number", "description"):
        value = getattr(form, field)
        if value and value.strip():
            b.fact(field, value.strip(), "claimant_form", FORM)
    for field in ("admission_date", "discharge_date"):
        if getattr(form, field):
            b.fact(field, getattr(form, field).isoformat(), "claimant_form", FORM)
    if form.total_amount is not None:
        b.fact("total_amount", float(form.total_amount), "claimant_form", FORM)
    if form.line_items:
        items = [
            LineItem(description=i.description, amount=float(i.amount), category=i.category or "unspecified", category_provenance="claimant_form")
            for i in form.line_items
        ]
        b.fact("line_items", items, "claimant_form", FORM)

    for ext in extractions:
        if ext.detected_type:
            b.fact("document_type", ext.detected_type, "system_inferred", ext.source)
        for field, extracted in ext.fields.items():
            if extracted.value is not None:
                b.fact(field, extracted.value, "claimant_document", ext.source, extracted.evidence)
        if ext.line_items:
            b.fact("line_items", ext.line_items, "claimant_document", ext.source)


def _compare_bills(b: _Builder, bills: list[ExtractionResult]) -> None:
    """Invoice number and total: separate bills legitimately have different values, so they are not conflicts."""
    bill_names = {ext.source for ext in bills}
    invoice_clusters = _cluster("invoice_number", (v for v in b.values["invoice_number"] if v.source in bill_names))
    if len(invoice_clusters) < 2:
        b.compare("invoice_number")
        b.compare("total_amount")
        return

    invoices = [str(_representative(group)) for group in invoice_clusters]
    others = [v for v in b.values["invoice_number"] if v.source not in bill_names]
    if any(not any(parsing.same_invoice(str(v.value), inv) for inv in invoices) for v in others):
        b.conflict("invoice_number", b.values["invoice_number"])
    else:
        b.summary("invoice_number", "multiple", b.values["invoice_number"], invoices)

    bill_totals: list[float] = []
    total_conflict = False
    for group in invoice_clusters:
        sources = {v.source for v in group}
        totals = _cluster("total_amount", (v for v in b.values["total_amount"] if v.source in sources))
        total_conflict |= len(totals) > 1
        if len(totals) == 1:
            bill_totals.append(float(totals[0][0].value))
    combined = round(sum(bill_totals), 2)
    b.fact("total_amount_all_bills", combined, "system_inferred", SYSTEM)
    other_totals = [v for v in b.values["total_amount"] if v.source not in bill_names]
    if total_conflict or any(not parsing.same_amount(v.value, combined) for v in other_totals):
        b.conflict("total_amount", [*b.values["total_amount"], _Value(combined, SYSTEM, "system_inferred", None)])
    else:
        b.summary("total_amount", "multiple", b.values["total_amount"], bill_totals)
    b.flag(
        "multiple_bills",
        "warning",
        f"{len(invoices)} separate bills were submitted (invoices {', '.join(invoices)}); "
        f"their totals {_fmt(bill_totals)} add up to {_fmt(combined)}.",
        "Confirm with the claimant that every bill belongs to this hospital stay.",
    )


def _line_items(b: _Builder, form: ClaimForm, bills: list[ExtractionResult]) -> None:
    from_bills = [ext for ext in bills if ext.line_items]
    if from_bills:
        values = [_Value("", ext.source, "claimant_document", None) for ext in from_bills]
        b.summary("line_items", "provided", values, [item for ext in from_bills for item in ext.line_items])
    elif form.line_items:
        items = next(f.value for f in b.facts if f.field == "line_items" and f.source == FORM)
        b.summary("line_items", "provided", [_Value("", FORM, "claimant_form", None)], items)
    else:
        b.summary("line_items", "missing", [])


def _checks(b: _Builder, form: ClaimForm, extractions: list[ExtractionResult], policy: PolicyInfo | None) -> None:
    """The validation checks from docs/claim_rules.md that can run without the database."""
    for ext in extractions:
        if ext.error:
            b.flag("ai_extraction_failed", "warning", f'"{ext.source}" could not be read: {ext.error}', f'Review "{ext.source}" by hand.')
        declared, detected = ext.source_type, ext.detected_type
        if detected and detected != "other" and declared not in ("other", "claimant_email") and detected != declared:
            b.flag(
                "document_type_mismatch",
                "warning",
                f'"{ext.source}" was uploaded as {declared} but reads like a {detected}; it is counted as a {detected}.',
                f'Check what "{ext.source}" really is.',
            )
        for event in ext.guard_events:
            b.flag(
                "ai_value_rejected",
                "info",
                f'AI value for {event.field} on "{ext.source}" was discarded ({event.reason}): {event.value!r}.',
            )
        total = ext.fields.get("total_amount")
        if ext.line_items and total and total.value is not None:
            items_sum = round(sum(item.amount for item in ext.line_items), 2)
            if not parsing.same_amount(items_sum, total.value):
                b.flag(
                    "items_total_mismatch",
                    "warning",
                    f'Line items on "{ext.source}" add up to {_fmt(items_sum)} but the stated total is {_fmt(total.value)}.',
                    f'Check the charges on "{ext.source}" against its stated total.',
                )

    # Dates: per source, and against the policy period.
    per_source: dict[str, dict[str, str]] = {}
    for field in ("admission_date", "discharge_date"):
        for v in b.values[field]:
            per_source.setdefault(v.source, {})[field] = str(v.value)
    for source, dates in per_source.items():
        if len(dates) == 2 and dates["admission_date"] > dates["discharge_date"]:
            b.flag(
                "admission_after_discharge",
                "warning",
                f"{_describe(source).capitalize()} gives an admission date ({dates['admission_date']}) after the discharge date ({dates['discharge_date']}).",
                "Ask the claimant to confirm the admission and discharge dates.",
            )
    if policy and policy.start_date and policy.end_date:
        for field in ("admission_date", "discharge_date"):
            for v in b.values[field]:
                day = date.fromisoformat(str(v.value))
                if not policy.start_date <= day <= policy.end_date:
                    b.flag(
                        "outside_policy_period",
                        "warning",
                        f"{LABELS[field].capitalize()} {day} ({_describe(v.source)}) is outside the policy period {policy.start_date} to {policy.end_date}.",
                        "Check whether the policy covered the treatment dates.",
                    )

    if policy and policy.holder_name:
        for v in b.values["patient_name"]:
            if not parsing.same_person(str(v.value), policy.holder_name):
                b.flag(
                    "patient_not_policy_holder",
                    "warning",
                    f"Patient name {v.value!r} ({_describe(v.source)}) does not match the policy holder {policy.holder_name!r}.",
                    "Check whether the patient is covered by this policy.",
                )
    if policy and policy.policy_number and form.policy_number:
        if not parsing.same_invoice(form.policy_number, policy.policy_number):
            b.flag(
                "policy_number_mismatch",
                "warning",
                f"The claim form gives policy number {form.policy_number!r}, but the claim is filed under {policy.policy_number!r}.",
                "Confirm the policy number with the claimant.",
            )


def _missing(b: _Builder, extractions: list[ExtractionResult], required_documents: Iterable[str]) -> list[MissingField]:
    missing = [MissingField(field=f, reason=WHY_NEEDED[f]) for f in REQUIRED_FIELDS if b.fields[f].status == "missing"]
    uploaded = {effective_type(ext) for ext in extractions}
    missing += [
        MissingField(field=f"document:{doc}", reason="Required by the plan for every claim.")
        for doc in required_documents
        if doc not in uploaded
    ]
    return missing


def _recommend(b: _Builder, missing: list[MissingField], extractions: list[ExtractionResult]) -> Recommendations:
    warnings = [flag for flag, _ in b.flags if flag.severity == "warning"]
    if b.conflicts:
        triage = TriageRecommendation(
            value="conflicting_information",
            reason="Sources disagree on: " + ", ".join(LABELS[c.field] for c in b.conflicts) + ".",
        )
    elif missing:
        names = [LABELS.get(f.field) or f.field.removeprefix("document:").replace("_", " ") for f in missing]
        reason = "Not provided by the claimant: " + ", ".join(names) + "."
        unread = [ext.source for ext in extractions if ext.error]
        if unread:
            reason += f" Note: {len(unread)} document(s) could not be read by the AI, so some of these may be in them."
        triage = TriageRecommendation(value="needs_more_information", reason=reason)
    elif warnings:
        triage = TriageRecommendation(value="possible_issue", reason=" ".join(flag.message for flag in warnings))
    else:
        triage = TriageRecommendation(
            value="ready_for_review",
            reason="Every required field was provided by the claimant, the sources agree and no check failed.",
        )

    steps: list[NextStep] = []
    for c in b.conflicts:
        said = "; ".join(f"{_describe(v.source)} says {_fmt(v.value)}" for v in c.values)
        steps.append(
            NextStep(action=f"Ask the claimant to confirm the {LABELS[c.field]}: {said}.", reason="The sources disagree and the system does not choose between them.")
        )
    for m in missing:
        if m.field.startswith("document:"):
            doc = m.field.removeprefix("document:").replace("_", " ")
            steps.append(NextStep(action=f"Ask the claimant to upload the {doc}.", reason=m.reason))
        else:
            steps.append(NextStep(action=f"Ask the claimant to provide the {LABELS[m.field]}.", reason=m.reason))
    for flag, action in b.flags:
        if action and flag.severity == "warning":
            steps.append(NextStep(action=action, reason=flag.message))
    rejected = {
        (event.field.split("[")[0], ext.source)
        for ext in extractions
        for event in ext.guard_events
        if event.field.startswith("line_items") or b.fields[event.field].status == "missing"
    }
    for field, source in sorted(rejected):
        steps.append(
            NextStep(
                action=f'Check the {LABELS[field]} on "{source}" by hand.',
                reason="The AI returned a value the document text does not support, so it was discarded.",
            )
        )
    if not steps:
        steps.append(NextStep(action="Proceed to the settlement calculation and approval review.", reason=triage.reason))
    return Recommendations(triage_category=triage, next_steps=steps)


def analyze_extractions(
    form: ClaimForm,
    extractions: list[ExtractionResult],
    policy: PolicyInfo | None = None,
    required_documents: Iterable[str] = DEFAULT_REQUIRED_DOCUMENTS,
    extra_flags: Iterable[ValidationFlag] = (),
) -> ClaimAnalysis:
    b = _Builder()
    _add_facts(b, form, extractions)
    bills = [ext for ext in extractions if effective_type(ext) == "hospital_bill"]
    for field in ("patient_name", "hospital_name", "admission_date", "discharge_date"):
        b.compare(field)
    _compare_bills(b, bills)
    _line_items(b, form, bills)
    _checks(b, form, extractions, policy)
    for flag in extra_flags:
        b.flag(flag.code, flag.severity, flag.message, f"Review: {flag.message}" if flag.severity == "warning" else None)
    missing = _missing(b, extractions, required_documents)
    return ClaimAnalysis(
        facts=b.facts,
        fields={field: b.fields[field] for field in REQUIRED_FIELDS},
        missing_fields=missing,
        conflicts=b.conflicts,
        validation_flags=[flag for flag, _ in b.flags],
        recommendations=_recommend(b, missing, extractions),
        extractions=extractions,
        model=settings.gemini_model,
        prompt_version=extractions[0].prompt_version if extractions else settings.prompt_version,
        analyzed_at=utcnow(),
    )


def _unique_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result = []
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        result.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return result


def run_analysis(request: AnalyzeRequest, extractor: Extractor | None = None) -> ClaimAnalysis:
    """Extract every source with the AI, then analyse. Same code path for POST /analyze and the evaluation."""
    extract = extractor or ai_extraction.extract_document
    names = _unique_names([s.name for s in request.sources])
    extractions = [extract(name, s.type, s.text) for name, s in zip(names, request.sources)]
    return analyze_extractions(request.form, extractions, request.policy)


# ---- Stored claims ----


def form_from_claim(claim: Claim) -> ClaimForm:
    return ClaimForm(
        invoice_number=claim.invoice_number,
        hospital_name=claim.hospital_name,
        admission_date=claim.treatment_start,
        discharge_date=claim.treatment_end,
        total_amount=claim.claimed_amount if claim.claimed_amount else None,
        line_items=[{"description": i.description, "amount": i.amount, "category": i.category} for i in claim.items],
        description=claim.description,
    )


def analyze_claim(db: Session, claim: Claim, extractor: Extractor | None = None) -> ClaimAnalysis:
    extract = extractor or ai_extraction.extract_document
    extractions = []
    names = _unique_names([f"{doc.doc_type.replace('_', ' ').capitalize()} ({doc.original_filename})" for doc in claim.documents])
    for doc, name in zip(claim.documents, names):
        result = extract(name, doc.doc_type, doc.extracted_text or "")
        if doc.extraction_error and result.error:
            result.error = f"{result.error} Text extraction: {doc.extraction_error}"
        extractions.append(result)
    policy = claim.policy
    return analyze_extractions(
        form_from_claim(claim),
        extractions,
        PolicyInfo(
            policy_number=policy.policy_number,
            holder_name=policy.holder_name,
            start_date=policy.start_date,
            end_date=policy.end_date,
        ),
        required_documents=policy.plan.required_documents,
        extra_flags=[ValidationFlag(code="claim_check", severity="warning", message=m) for m in check_claim(db, claim, during_analysis=True)],
    )


def store_analysis(db: Session, claim: Claim) -> ClaimAnalysis:
    analysis = analyze_claim(db, claim)
    claim.analysis = analysis.model_dump(mode="json")
    return analysis

