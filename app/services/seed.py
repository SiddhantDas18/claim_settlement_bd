"""Startup data: the first admin account and a default plan (values from docs/claim_rules.md)."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.models import CoverageRule, Plan, User, UserRole

DEFAULT_PLAN = {
    "name": "Standard Health",
    "currency": "INR",
    "deductible": Decimal("1000"),
    "annual_limit": Decimal("200000"),
    "required_documents": ["hospital_bill", "discharge_summary"],
    "rules": [
        # category, coverage %, max per unit, unit label
        ("room", Decimal("100"), Decimal("2500"), "day"),
        ("medicine", Decimal("100"), None, None),
        ("diagnostics", Decimal("100"), None, None),
        ("consultation", Decimal("100"), Decimal("1000"), "visit"),
        ("surgery", Decimal("80"), None, None),
    ],
}


def seed_defaults(db: Session) -> None:
    email = settings.admin_email.strip().lower()
    if email and settings.admin_password and not db.scalar(select(User).where(User.email == email)):
        db.add(
            User(
                email=email,
                full_name="Administrator",
                hashed_password=hash_password(settings.admin_password),
                role=UserRole.ADMIN,
            )
        )

    if db.scalar(select(Plan.id).limit(1)) is None:
        plan = Plan(
            name=DEFAULT_PLAN["name"],
            currency=DEFAULT_PLAN["currency"],
            deductible=DEFAULT_PLAN["deductible"],
            annual_limit=DEFAULT_PLAN["annual_limit"],
            required_documents=DEFAULT_PLAN["required_documents"],
        )
        plan.coverage_rules = [
            CoverageRule(category=c, coverage_percent=p, max_per_unit=cap, unit_label=unit)
            for c, p, cap, unit in DEFAULT_PLAN["rules"]
        ]
        db.add(plan)
    db.commit()
