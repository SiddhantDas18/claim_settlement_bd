"""Runs every synthetic claim through the same code path as POST /api/v1/analyze and scores the result.

    python evals/run_eval.py                          # prompt version from PROMPT_VERSION / settings
    python evals/run_eval.py --prompt-version extraction_v2

Gemini responses are cached in evals/.cache (keyed by model + full prompt), so re-running an unchanged
prompt costs nothing. Results go to evals/results/<prompt_version>.json and .md.
"""

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
os.environ.setdefault("SECRET_KEY", "evaluation-only-no-logins-happen-here-0000")  # settings require one

from app.core.config import settings  # noqa: E402
from app.schemas.analysis import AnalyzeRequest, ClaimAnalysis, FieldSummary  # noqa: E402
from app.services import ai_extraction, parsing  # noqa: E402
from app.services.analysis import run_analysis  # noqa: E402
from app.services.text_extraction import extract_text  # noqa: E402

CONTENT_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
FATAL = ("GEMINI_API_KEY is not set", "quota exhausted")


# ---- Loading ----


def load_request(case: dict) -> tuple[AnalyzeRequest, list[dict]]:
    """Turn a case into the /analyze request body. File sources are read with the real OCR / PDF code first."""
    sources, ocr = [], []
    for source in case["sources"]:
        if "file" in source:
            path = ROOT / source["file"]
            extracted = extract_text(path, CONTENT_TYPES[path.suffix.lower()])
            ocr.append({"file": source["file"], "method": extracted.method, "ocr_confidence": extracted.ocr_confidence, "error": extracted.error})
            sources.append({"name": source["name"], "type": source["type"], "text": extracted.text})
        else:
            sources.append(source)
    return AnalyzeRequest(form=case["form"], sources=sources, policy=case.get("policy")), ocr


# ---- Scoring ----


def _same(field: str, expected, predicted) -> bool:
    if field in ("admission_date", "discharge_date"):
        return expected == predicted
    if field == "total_amount":
        return parsing.same_amount(expected, predicted)
    if field == "patient_name":
        return parsing.same_person(expected, predicted)
    if field == "hospital_name":
        return parsing.same_org(expected, predicted)
    if field == "invoice_number":
        return parsing.same_invoice(expected, predicted)
    raise ValueError(field)


def _same_amounts(expected: list[float], predicted: list[float]) -> bool:
    remaining = list(predicted)
    for amount in expected:
        match = next((p for p in remaining if parsing.same_amount(amount, p)), None)
        if match is None:
            return False
        remaining.remove(match)
    return not remaining


def check_field(field: str, expected, summary: FieldSummary) -> str | None:
    """None if correct, else a description of what went wrong."""
    got = summary.value
    if expected is None:
        return None if summary.status == "missing" else f"expected missing, got {summary.status} {got!r}"
    if summary.status in ("missing", "conflicting"):
        return f"expected {expected!r}, got {summary.status}"
    if field == "line_items":
        amounts = [item.amount for item in got]  # type: ignore[union-attr]
        return None if _same_amounts(expected, amounts) else f"expected amounts {expected}, got {amounts}"
    if isinstance(expected, list):
        values = got if isinstance(got, list) else [got]
        ok = len(values) == len(expected) and all(any(_same(field, e, v) for v in values) for e in expected)
        return None if ok else f"expected {expected!r}, got {got!r}"
    return None if _same(field, expected, got) else f"expected {expected!r}, got {got!r}"


def score_case(case: dict, analysis: ClaimAnalysis) -> dict:
    expected = case["expected"]
    problems = []
    field_results = {}
    hallucinations = 0
    for field, value in expected["fields"].items():
        problem = check_field(field, value, analysis.fields[field])
        field_results[field] = problem is None
        if problem:
            problems.append(f"{field}: {problem}")
        if value is None and analysis.fields[field].status != "missing":
            hallucinations += 1

    exp_missing, got_missing = set(expected["missing_fields"]), {m.field for m in analysis.missing_fields}
    exp_conflicts, got_conflicts = set(expected["conflicts"]), {c.field for c in analysis.conflicts}
    if exp_missing - got_missing:
        problems.append(f"missing fields not flagged: {sorted(exp_missing - got_missing)}")
    if got_missing - exp_missing:
        problems.append(f"wrongly flagged as missing: {sorted(got_missing - exp_missing)}")
    if exp_conflicts - got_conflicts:
        problems.append(f"conflicts not detected: {sorted(exp_conflicts - got_conflicts)}")
    if got_conflicts - exp_conflicts:
        problems.append(f"false conflicts: {sorted(got_conflicts - exp_conflicts)}")
    got_flags = {flag.code for flag in analysis.validation_flags}
    for flag in expected.get("flags", []):
        if flag not in got_flags:
            problems.append(f"expected flag not raised: {flag}")
    triage = analysis.recommendations.triage_category.value
    if triage != expected["triage_category"]:
        problems.append(f"triage: expected {expected['triage_category']}, got {triage}")

    guard = [
        {"source": ext.source, **event.model_dump()} for ext in analysis.extractions for event in ext.guard_events
    ]
    errors = [{"source": ext.source, "error": ext.error} for ext in analysis.extractions if ext.error]
    return {
        "id": case["id"],
        "category": case["category"],
        "description": case["description"],
        "passed": not problems,
        "problems": problems,
        "fields": field_results,
        "hallucinations": hallucinations,
        "missing": {"expected": sorted(exp_missing), "got": sorted(got_missing)},
        "conflicts": {"expected": sorted(exp_conflicts), "got": sorted(got_conflicts)},
        "triage": {"expected": expected["triage_category"], "got": triage},
        "guard_events": guard,
        "extraction_errors": errors,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def metrics(results: list[dict]) -> dict:
    fields = [ok for r in results for ok in r["fields"].values()]
    exp_missing = sum(len(r["missing"]["expected"]) for r in results)
    hit_missing = sum(len(set(r["missing"]["expected"]) & set(r["missing"]["got"])) for r in results)
    got_conflicts = sum(len(r["conflicts"]["got"]) for r in results)
    exp_conflicts = sum(len(r["conflicts"]["expected"]) for r in results)
    hit_conflicts = sum(len(set(r["conflicts"]["expected"]) & set(r["conflicts"]["got"])) for r in results)
    reasons = Counter(event["reason"] for r in results for event in r["guard_events"])
    return {
        "cases": len(results),
        "cases_passed": sum(r["passed"] for r in results),
        "field_accuracy": _ratio(sum(fields), len(fields)),
        "missing_field_recall": _ratio(hit_missing, exp_missing),
        "false_missing": sum(len(set(r["missing"]["got"]) - set(r["missing"]["expected"])) for r in results),
        "hallucinations": sum(r["hallucinations"] for r in results),
        "conflict_recall": _ratio(hit_conflicts, exp_conflicts),
        "conflict_precision": _ratio(hit_conflicts, got_conflicts),
        "triage_accuracy": _ratio(sum(r["triage"]["expected"] == r["triage"]["got"] for r in results), len(results)),
        "guard_rejections": dict(sorted(reasons.items())),
        "extraction_errors": sum(len(r["extraction_errors"]) for r in results),
    }


# ---- Report ----

METRIC_ROWS = [
    ("Cases passed (all checks)", "cases_passed"),
    ("Field accuracy", "field_accuracy"),
    ("Missing-field recall", "missing_field_recall"),
    ("Fields wrongly flagged missing", "false_missing"),
    ("Hallucinations (value where none exists)", "hallucinations"),
    ("Conflict recall", "conflict_recall"),
    ("Conflict precision", "conflict_precision"),
    ("Triage accuracy", "triage_accuracy"),
    ("AI values discarded by the guard", "guard_rejections"),
    ("Extraction errors", "extraction_errors"),
]


def fmt_metric(key: str, m: dict) -> str:
    value = m[key]
    if key == "cases_passed":
        return f"{value}/{m['cases']}"
    if key == "guard_rejections":
        return ", ".join(f"{reason}: {n}" for reason, n in value.items()) or "0"
    if isinstance(value, float):
        return f"{value:.1%}"
    return "n/a" if value is None else str(value)


def markdown(report: dict) -> str:
    m = report["metrics"]
    lines = [
        f"# Evaluation: {report['prompt_version']}",
        "",
        f"Model `{report['model']}`, run {report['run_at']}, {m['cases']} cases. "
        f"Gemini calls this run: {report['api_calls']} (cached: {report['cache_hits']}).",
        "",
        "| Metric | Value |",
        "|---|---|",
        *[f"| {label} | {fmt_metric(key, m)} |" for label, key in METRIC_ROWS],
        "",
        "## Per case",
        "",
        "| Case | Category | Triage (expected / got) | Result |",
        "|---|---|---|---|",
    ]
    for r in report["cases"]:
        result = "pass" if r["passed"] else "FAIL"
        lines.append(f"| {r['id']} | {r['category']} | {r['triage']['expected']} / {r['triage']['got']} | {result} |")
    failing = [r for r in report["cases"] if not r["passed"]]
    lines += ["", "## Failing cases", ""]
    if not failing:
        lines.append("None.")
    for r in failing:
        lines.append(f"### {r['id']} ({r['category']}): {r['description']}")
        lines += [f"- {problem}" for problem in r["problems"]]
        for event in r["guard_events"]:
            lines.append(f"- guard discarded `{event['field']}` = {event['value']!r} from \"{event['source']}\" ({event['reason']})")
        lines += [f"- extraction error on \"{e['source']}\": {e['error']}" for e in r["extraction_errors"]]
        lines.append("")
    ocr = [o for r in report["cases"] for o in r.get("ocr", [])]
    if ocr:
        lines += ["## File-based sources (PDF / OCR)", "", "| File | Method | OCR confidence |", "|---|---|---|"]
        lines += [f"| {o['file']} | {o['method']} | {o['ocr_confidence'] if o['ocr_confidence'] is not None else '-'} |" for o in ocr]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prompt-version", default=settings.prompt_version)
    parser.add_argument("--cases", default="case_*.json", help="glob inside evals/cases")
    parser.add_argument("--min-interval", type=float, default=4.0, help="seconds between real API calls (free-tier rate limit)")
    args = parser.parse_args()

    settings.prompt_version = args.prompt_version
    ai_extraction.load_prompt(args.prompt_version)  # fail early on a typo
    ai_extraction.cache_dir = ROOT / ".cache"

    def extractor(name, doc_type, text):
        before = ai_extraction.stats["api_calls"]
        result = ai_extraction.extract_document(name, doc_type, text, args.prompt_version)
        if result.error and any(marker in result.error for marker in FATAL):
            sys.exit(f"Stopping: {result.error}")
        if ai_extraction.stats["api_calls"] > before:
            time.sleep(args.min_interval)
        return result

    results = []
    for path in sorted((ROOT / "cases").glob(args.cases)):
        case = json.loads(path.read_text(encoding="utf-8"))
        request, ocr = load_request(case)
        analysis = run_analysis(request, extractor=extractor)
        result = score_case(case, analysis)
        result["ocr"] = ocr
        result["analysis"] = analysis.model_dump(mode="json")
        results.append(result)
        print(f"{case['id']}: {'pass' if result['passed'] else 'FAIL ' + '; '.join(result['problems'])}")

    report = {
        "prompt_version": args.prompt_version,
        "model": settings.gemini_model,
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "api_calls": ai_extraction.stats["api_calls"],
        "cache_hits": ai_extraction.stats["cache_hits"],
        "metrics": metrics(results),
        "cases": results,
    }
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    (out / f"{args.prompt_version}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / f"{args.prompt_version}.md").write_text(markdown(report), encoding="utf-8")
    print()
    for label, key in METRIC_ROWS:
        print(f"{label:<44}{fmt_metric(key, report['metrics'])}")
    print(f"\nSaved evals/results/{args.prompt_version}.json and .md")


if __name__ == "__main__":
    main()
