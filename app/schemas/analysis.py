"""Input and output schema of the claims intake analysis (POST /api/v1/analyze and the stored Claim.analysis)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import Money

# ---- Input ----

SourceType = Literal["hospital_bill", "discharge_summary", "prescription", "lab_report", "claimant_email", "other"]


class FormLineItem(BaseModel):
    description: str = Field(min_length=1, max_length=255)
    amount: Money = Field(ge=0, max_digits=12, decimal_places=2)
    category: str | None = None


class ClaimForm(BaseModel):
    """What the claimant typed into the claim form. Every field is optional: blank means "not provided"."""

    patient_name: str | None = Field(default=None, max_length=255)
    policy_number: str | None = Field(default=None, max_length=50)
    hospital_name: str | None = Field(default=None, max_length=255)
    invoice_number: str | None = Field(default=None, max_length=100)
    admission_date: date | None = None
    discharge_date: date | None = None
    total_amount: Money | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    line_items: list[FormLineItem] = []
    description: str | None = Field(default=None, max_length=5000)


class PolicyInfo(BaseModel):
    """The policy the claim is made under (from the insurer's records, not from the claimant)."""

    policy_number: str | None = None
    holder_name: str | None = None
    start_date: date | None = None
    end_date: date | None = None


class SourceDocument(BaseModel):
    """One document submitted with the claim, already turned into text (typed, PDF text or OCR)."""

    name: str = Field(min_length=1, max_length=255)
    type: SourceType = "other"
    text: str = Field(max_length=100_000)


class AnalyzeRequest(BaseModel):
    form: ClaimForm = ClaimForm()
    sources: list[SourceDocument] = Field(default=[], max_length=10)
    policy: PolicyInfo | None = None


# ---- AI extraction result (one per document, after the code guard) ----

GuardReason = Literal["unsupported_by_source", "value_not_in_quote", "unparseable_value", "incomplete_date"]


class ExtractedField(BaseModel):
    value: str | float | None = None  # normalised: dates as YYYY-MM-DD, amounts as numbers
    raw: str | None = None  # exactly what the model returned
    evidence: str | None = None  # quote from the document text


class LineItem(BaseModel):
    description: str
    amount: float
    category: str
    # Bills don't state a category: the model assigns it. Categories typed on the claim form are the claimant's.
    category_provenance: Literal["system_inferred", "claimant_form"] = "system_inferred"
    evidence: str | None = None


class GuardEvent(BaseModel):
    """A value the model returned that the code guard threw away."""

    field: str
    value: str | None
    evidence: str | None
    reason: GuardReason


class ExtractionResult(BaseModel):
    source: str
    source_type: SourceType
    detected_type: str | None = None  # the model's classification (system_inferred)
    model: str
    prompt_version: str
    fields: dict[str, ExtractedField] = {}
    line_items: list[LineItem] = []
    guard_events: list[GuardEvent] = []
    error: str | None = None


# ---- Analysis output ----

Provenance = Literal["claimant_form", "claimant_document", "system_inferred"]
FactValue = str | float | list[LineItem] | list[str] | list[float] | None


class Fact(BaseModel):
    field: str
    value: FactValue
    provenance: Provenance  # claimant_form and claimant_document both count as claimant-provided
    source: str  # "claim_form", a document name, or "system"
    evidence: str | None = None


class FieldSummary(BaseModel):
    """The state of one claim field across all sources. `value` is only set when the sources agree."""

    field: str
    status: Literal["provided", "missing", "conflicting", "multiple"]  # multiple = separate bills, see flags
    value: FactValue = None
    provenance: list[Provenance] = []
    sources: list[str] = []


class MissingField(BaseModel):
    field: str  # a claim field, or "document:<type>" for a document that was not uploaded
    reason: str  # why it is needed


class ConflictValue(BaseModel):
    value: FactValue
    source: str
    provenance: Provenance
    evidence: str | None = None


class Conflict(BaseModel):
    field: str
    values: list[ConflictValue]  # every value from every source; nothing is resolved
    severity: Literal["high", "medium", "low"]


class ValidationFlag(BaseModel):
    code: str
    severity: Literal["warning", "info"]
    message: str
    provenance: Literal["system_inferred"] = "system_inferred"


TriageCategory = Literal["ready_for_review", "needs_more_information", "conflicting_information", "possible_issue"]


class TriageRecommendation(BaseModel):
    value: TriageCategory
    reason: str
    provenance: Literal["system_inferred"] = "system_inferred"


class NextStep(BaseModel):
    action: str
    reason: str
    provenance: Literal["system_inferred"] = "system_inferred"


class Recommendations(BaseModel):
    triage_category: TriageRecommendation
    next_steps: list[NextStep]


class ClaimAnalysis(BaseModel):
    facts: list[Fact]
    fields: dict[str, FieldSummary]
    missing_fields: list[MissingField]
    conflicts: list[Conflict]
    validation_flags: list[ValidationFlag]
    recommendations: Recommendations
    extractions: list[ExtractionResult]  # per-document AI output, guard events and errors
    model: str
    prompt_version: str
    analyzed_at: datetime


def money_float(value: Decimal) -> float:
    return float(round(value, 2))
