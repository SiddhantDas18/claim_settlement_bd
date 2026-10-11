"""Claim checks and settlement, connecting the database models to the pure settlement math."""

import re
from datetime import date
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


_QUANTITY = re.compile(r"\b(\d{1,3})\s*(?:days?|nights?|visits?)\b", re.IGNORECASE)


def _stay_days(claim: Claim) -> int | None:
    """Length of stay from the analysis, only when every source agrees on both dates."""
    fields = (claim.analysis or {}).get("fields") or {}
    admitted, discharged = fields.get("admission_date") or {}, fields.get("discharge_date") or {}
    if admitted.get("status") != "provided" or discharged.get("status") != "provided":
        return None
    return max((date.fromisoformat(discharged["value"]) - date.fromisoformat(admitted["value"])).days, 1)


def bill_items(claim: Claim) -> list[settlement.Item]:
    """Expense lines the AI read from the bill(s), taken from the stored analysis.

    The quantity (days / visits) for per-unit caps is read by code from the line's own quote ("3 days", "2 visits");
    a room line without one uses the length of stay, anything else counts as 1.
    """
    summary = ((claim.analysis or {}).get("fields") or {}).get("line_items") or {}
    if summary.get("status") != "provided" or "claimant_document" not in summary.get("provenance", []):
        return []
    stay = _stay_days(claim)
    items = []
    for line in summary.get("value") or []:
        match = _QUANTITY.search(f"{line.get('description', '')} {line.get('evidence') or ''}")
        quantity = int(match.group(1)) if match else (stay or 1 if line["category"] == "room" else 1)
        items.append(settlement.Item(line["category"], line["description"], Decimal(str(line["amount"])), quantity))
    return items


def settlement_items(claim: Claim) -> tuple[list[settlement.Item], str]:
    """The claimant's typed expense items if there are any, otherwise the bill's line items."""
    if claim.items:
        return [settlement.Item(i.category, i.description, i.amount, i.quantity) for i in claim.items], "claimant"
    return bill_items(claim), "bill"


def missing_documents(claim: Claim) -> list[str]:
    uploaded = {doc.doc_type for doc in claim.documents}
    return [doc_type for doc_type in claim.policy.plan.required_documents if doc_type not in uploaded]


def check_claim(db: Session, claim: Claim, during_analysis: bool = False) -> list[str]:
    """Problems an admin should look at before approving. Empty list = nothing suspicious found.

    `during_analysis`: called while the analysis is being built, so the bill's line items aren't stored yet
    (the analysis reports missing line items itself).
    """
    flags: list[str] = []
    policy = claim.policy

    if not during_analysis and not claim.items and not bill_items(claim):
        flags.append("No expense items: none were typed and none could be read from the bill.")
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
    items, source = settlement_items(claim)
    result = settlement.calculate(
        items=items,
        rules=plan_rules(plan),
        deductible=plan.deductible,
        annual_limit=plan.annual_limit,
        already_paid=already_paid(db, claim),
    )
    return CalculationOut(
        currency=plan.currency,
        items_source=source,
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
