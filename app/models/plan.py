from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, utcnow

if TYPE_CHECKING:
    from app.models.user import User


class Plan(Base):
    """An insurance plan: the configurable settlement rules (limits, deductible, coverage per category)."""

    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    deductible: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    annual_limit: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    required_documents: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    coverage_rules: Mapped[list["CoverageRule"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class CoverageRule(Base):
    """How much of one expense category a plan pays. Categories without a rule are not covered."""

    __tablename__ = "coverage_rules"
    __table_args__ = (UniqueConstraint("plan_id", "category"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    category: Mapped[str] = mapped_column(String(30))
    coverage_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("100"))
    max_per_unit: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # e.g. 2500 per day
    unit_label: Mapped[str | None] = mapped_column(String(20))  # e.g. "day", "visit"
    max_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    plan: Mapped["Plan"] = relationship(back_populates="coverage_rules")


class Policy(Base):
    """A user's membership in a plan for a period."""

    __tablename__ = "policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    policy_number: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    holder_name: Mapped[str] = mapped_column(String(255))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="policies")
    plan: Mapped["Plan"] = relationship()
