"""Claims intake analysis without the database: paste the form and the document texts, get a ClaimAnalysis back."""

from fastapi import APIRouter

from app.api.deps import AdminUser
from app.schemas.analysis import AnalyzeRequest, ClaimAnalysis
from app.services.analysis import run_analysis

router = APIRouter(tags=["analysis"])


@router.post("/analyze", response_model=ClaimAnalysis)
def analyze(data: AnalyzeRequest, admin: AdminUser) -> ClaimAnalysis:
    """One Gemini call per source, then code checks. Used by the demo page and evals/run_eval.py."""
    return run_analysis(data)
