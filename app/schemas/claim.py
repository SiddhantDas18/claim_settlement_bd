from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Money
from app.schemas.payment import PaymentOut


class ClaimItemIn(BaseModel):
    category: str = Field(min_length=1, max_length=30)
    description: str = Field(min_length=1, max_length=255)
    amount: Money = Field(gt=0, max_digits=12, decimal_places=2)
    quantity: int = Field(default=1, ge=1, le=365)


class ClaimItemOut(ClaimItemIn):
    model_config = ConfigDict(from_attributes=True)

    id: int


class ClaimCreate(BaseModel):
    policy_id: int
    invoice_number: str | None = Field(default=None, max_length=100)
    treatment_start: date | None = None
    treatment_end: date | None = None
    description: str | None = None
    items: list[ClaimItemIn] = []


class ClaimUpdate(BaseModel):
    """Only the fields sent are changed. Sending `items` replaces the whole list."""

    invoice_number: str | None = Field(default=None, max_length=100)
    treatment_start: date | None = None
    treatment_end: date | None = None
    description: str | None = None
    items: list[ClaimItemIn] | None = None


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doc_type: str
    original_filename: str
    content_type: str
    size_bytes: int
    uploaded_at: datetime
    extraction_method: str | None
    ocr_confidence: float | None
    extraction_error: str | None
    extracted_text: str | None


class ClaimOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    policy_id: int
    status: str
    invoice_number: str | None
    treatment_start: date | None
    treatment_end: date | None
    description: str | None
    claimed_amount: Money
    triage_category: str | None  # from the stored analysis; system_inferred
    approved_amount: Money | None
    admin_note: str | None
    submitted_at: datetime | None
    reviewed_at: datetime | None
    created_at: datetime
    items: list[ClaimItemOut]
    documents: list[DocumentOut]
    payment: PaymentOut | None


class CalculationLine(BaseModel):
    category: str
    description: str
    quantity: int
    claimed: Money
    covered: Money
    note: str


class CalculationOut(BaseModel):
    currency: str
    lines: list[CalculationLine]
    total_claimed: Money
    total_covered: Money
    deductible: Money
    after_deductible: Money
    annual_limit: Money
    already_paid: Money
    remaining_limit: Money
    payable: Money
    flags: list[str]  # problems the admin should look at before approving


class ApproveIn(BaseModel):
    note: str | None = None
    # Pay a different amount than the calculated one (the reason must go in `note`).
    override_amount: Money | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)


class RejectIn(BaseModel):
    note: str = Field(min_length=1)
