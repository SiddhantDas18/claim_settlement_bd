"""Claims intake analysis without the database: paste the form and the document texts, get a ClaimAnalysis back."""

import json
from functools import cache
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import AdminUser
from app.schemas.analysis import AnalyzeRequest, ClaimAnalysis
from app.services.analysis import run_analysis
from app.services.text_extraction import extract_text

router = APIRouter(tags=["analysis"])

CASES_DIR = Path(__file__).resolve().parents[3] / "evals" / "cases"
_CONTENT_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class ExampleCase(BaseModel):
    id: str
    category: str
    description: str
    request: AnalyzeRequest
    expected: dict


@router.post("/analyze", response_model=ClaimAnalysis)
def analyze(data: AnalyzeRequest, admin: AdminUser) -> ClaimAnalysis:
    """One Gemini call per source, then code checks. Used by the demo page and evals/run_eval.py."""
    return run_analysis(data)


@cache
def _load_examples() -> list[ExampleCase]:
    examples = []
    for path in sorted(CASES_DIR.glob("case_*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        sources = []
        for source in case["sources"]:
            if "file" in source:  # the same text extraction an upload goes through
                file = CASES_DIR.parent / source["file"]
                text = extract_text(file, _CONTENT_TYPES[file.suffix.lower()]).text
                source = {"name": source["name"], "type": source["type"], "text": text}
            sources.append(source)
        request = AnalyzeRequest(form=case["form"], sources=sources, policy=case.get("policy"))
        examples.append(ExampleCase(id=case["id"], category=case["category"], description=case["description"], request=request, expected=case["expected"]))
    return examples


@router.get("/analyze/examples", response_model=list[ExampleCase])
def examples(admin: AdminUser) -> list[ExampleCase]:
    """The synthetic test claims from evals/cases, ready to send to POST /analyze (for the demo page)."""
    return _load_examples() if CASES_DIR.exists() else []
