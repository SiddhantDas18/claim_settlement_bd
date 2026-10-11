from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, utcnow

if TYPE_CHECKING:
    from app.models.payment import Payment
    from app.models.plan import Policy
    from app.models.user import User


class ClaimStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID = "paid"


DOCUMENT_TYPES = ("hospital_bill", "discharge_summary", "prescription", "lab_report", "other")


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("policies.id"))
    status: Mapped[str] = mapped_column(String(20), default=ClaimStatus.DRAFT, index=True)
    invoice_number: Mapped[str | None] = mapped_column(String(100), index=True)
    treatment_start: Mapped[date | None] = mapped_column(Date)
    treatment_end: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)
    claimed_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    analysis: Mapped[dict | None] = mapped_column(JSON)  # ClaimAnalysis, made on submit or by an admin

    # Filled in by the admin review
    approved_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    calculation: Mapped[dict | None] = mapped_column(JSON)  # settlement breakdown saved at approval
    admin_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    @property
    def triage_category(self) -> str | None:
        """The stored analysis' recommended triage (system_inferred), for list views."""
        try:
            return self.analysis["recommendations"]["triage_category"]["value"]  # type: ignore[index]
        except (TypeError, KeyError):
            return None

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    policy: Mapped["Policy"] = relationship()
    items: Mapped[list["ClaimItem"]] = relationship(back_populates="claim", cascade="all, delete-orphan")
    documents: Mapped[list["ClaimDocument"]] = relationship(back_populates="claim", cascade="all, delete-orphan")
    payment: Mapped[Optional["Payment"]] = relationship(back_populates="claim")


class ClaimItem(Base):
    """One expense line, e.g. 'Room charges, 3 days, 9000'. Entered by hand now, filled by AI later."""

    __tablename__ = "claim_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"))
    category: Mapped[str] = mapped_column(String(30))
    description: Mapped[str] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    quantity: Mapped[int] = mapped_column(Integer, default=1)  # days / visits, used by per-unit caps

    claim: Mapped["Claim"] = relationship(back_populates="items")


class ClaimDocument(Base):
    __tablename__ = "claim_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"))
    doc_type: Mapped[str] = mapped_column(String(30))
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    # Filled on upload by app/services/text_extraction.py
    extracted_text: Mapped[str | None] = mapped_column(Text)
    extraction_method: Mapped[str | None] = mapped_column(String(20))  # pdf_text / pdf_ocr / image_ocr / failed
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    extraction_error: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    claim: Mapped["Claim"] = relationship(back_populates="documents")
