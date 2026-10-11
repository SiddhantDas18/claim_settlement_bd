from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, utcnow

if TYPE_CHECKING:
    from app.models.claim import Claim


class PaymentStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"


class Payment(Base):
    """Money owed to the claimant for an approved claim; marked paid by an admin."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20), default=PaymentStatus.PENDING, index=True)
    method: Mapped[str | None] = mapped_column(String(30))  # e.g. bank, upi, cash
    reference: Mapped[str | None] = mapped_column(String(100))  # transaction id
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    claim: Mapped["Claim"] = relationship(back_populates="payment")
