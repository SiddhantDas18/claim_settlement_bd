from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Money


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    claim_id: int
    amount: Money
    currency: str
    status: str
    method: str | None
    reference: str | None
    created_at: datetime
    paid_at: datetime | None


class MarkPaidIn(BaseModel):
    method: str = Field(min_length=1, max_length=30)  # e.g. bank, upi, cash
    reference: str | None = Field(default=None, max_length=100)


class PaymentSummary(BaseModel):
    pending_count: int
    pending_amount: Money
    paid_count: int
    paid_amount: Money
