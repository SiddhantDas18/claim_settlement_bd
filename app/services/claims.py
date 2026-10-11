"""Claim checks and settlement, connecting the database models to the pure settlement math."""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Claim, ClaimStatus, Plan
from app.schemas.claim import CalculationLine, CalculationOut
from app.services import settlement

_COUNTED_STATUSES = (ClaimStatus.APPROVED, ClaimStatus.PAID)


def plan_rules(plan: Plan) -> dict[str, settlement.Rule]:
    return {
        rule.category: settlement.Rule(
            coverage_percent=rule.coverage_percent,
            max_per_unit=rule.max_per_unit,
            unit_label=rule.unit_label,
            max_total=rule.max_total,
        )
        for rule in plan.coverage_rules
    }


def already_paid(db: Session, claim: Claim) -> Decimal:
    """Total approved on other claims of the same policy (counts against the annual limit)."""
    total = db.scalar(
        select(func.coalesce(func.sum(Claim.approved_amount), 0)).where(
            Claim.policy_id == claim.policy_id,
            Claim.id != claim.id,
            Claim.status.in_(_COUNTED_STATUSES),
        )
    )
    return Decimal(str(total))


def missing_documents(claim: Claim) -> list[str]:
    uploaded = {doc.doc_type for doc in claim.documents}
    return [doc_type for doc_type in claim.policy.plan.required_documents if doc_type not in uploaded]


def check_claim(db: Session, claim: Claim) -> list[str]:
    """Problems an admin should look at before approving. Empty list = nothing suspicious found."""
    flags: list[str] = []
    policy = claim.policy

    if not claim.items:
        flags.append("No expense items entered.")
    for doc_type in missing_documents(claim):
        flags.append(f"Missing required document: {doc_type}.")

    if claim.treatment_start and claim.treatment_end and claim.treatment_start > claim.treatment_end:
        flags.append("Treatment start date is after the end date.")
    for label, day in (("start", claim.treatment_start), ("end", claim.treatment_end)):
        if day and not (policy.start_date <= day <= policy.end_date):
            flags.append(f"Treatment {label} date {day} is outside the policy period.")
    if not claim.treatment_start or not claim.treatment_end:
        flags.append("Treatment dates not entered.")

    if claim.invoice_number:
        duplicate = db.scalar(
            select(Claim.id).where(
                Claim.invoice_number == claim.invoice_number,
                Claim.id != claim.id,
                Claim.status != ClaimStatus.REJECTED,
            )
        )
        if duplicate:
            flags.append(f"Invoice number {claim.invoice_number} is also used on claim #{duplicate}.")

    rules = plan_rules(policy.plan)
    for item in claim.items:
        if item.category not in rules:
            flags.append(f"Category '{item.category}' is not covered by the plan ({item.description}).")
    return flags


def calculate_claim(db: Session, claim: Claim) -> CalculationOut:
    plan = claim.policy.plan
    result = settlement.calculate(
        items=[settlement.Item(i.category, i.description, i.amount, i.quantity) for i in claim.items],
        rules=plan_rules(plan),
        deductible=plan.deductible,
        annual_limit=plan.annual_limit,
        already_paid=already_paid(db, claim),
    )
    return CalculationOut(
        currency=plan.currency,
        lines=[CalculationLine(**line.__dict__) for line in result.lines],
        total_claimed=result.total_claimed,
        total_covered=result.total_covered,
        deductible=result.deductible,
        after_deductible=result.after_deductible,
        annual_limit=result.annual_limit,
        already_paid=result.already_paid,
        remaining_limit=result.remaining_limit,
        payable=result.payable,
        flags=check_claim(db, claim),
    )
