"""Settlement math. Pure Python (no database, no AI) so every rule can be unit-tested.

payable = min(sum(covered per line) - deductible, annual_limit - already_paid), never below 0
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0.00")
_CENT = Decimal("0.01")


def money(value: Decimal | int | str) -> Decimal:
    return Decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def _fmt(value: Decimal) -> str:
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


@dataclass(frozen=True)
class Rule:
    coverage_percent: Decimal
    max_per_unit: Decimal | None = None
    unit_label: str | None = None
    max_total: Decimal | None = None


@dataclass(frozen=True)
class Item:
    category: str
    description: str
    amount: Decimal
    quantity: int = 1


@dataclass(frozen=True)
class Line:
    category: str
    description: str
    quantity: int
    claimed: Decimal
    covered: Decimal
    note: str


@dataclass(frozen=True)
class Settlement:
    lines: list[Line]
    total_claimed: Decimal
    total_covered: Decimal
    deductible: Decimal
    after_deductible: Decimal
    annual_limit: Decimal
    already_paid: Decimal
    remaining_limit: Decimal
    payable: Decimal


def cover_item(item: Item, rule: Rule | None) -> Line:
    claimed = money(item.amount)
    if rule is None:
        return Line(item.category, item.description, item.quantity, claimed, ZERO, "category not covered")

    covered = claimed * rule.coverage_percent / 100
    notes = []
    if rule.coverage_percent < 100:
        notes.append(f"{_fmt(rule.coverage_percent)}% covered")
    if rule.max_per_unit is not None:
        cap = rule.max_per_unit * item.quantity
        if covered > cap:
            covered = cap
            unit = rule.unit_label or "unit"
            notes.append(f"capped at {_fmt(rule.max_per_unit)} per {unit} x {item.quantity}")
    if rule.max_total is not None and covered > rule.max_total:
        covered = rule.max_total
        notes.append(f"capped at {_fmt(rule.max_total)} total")
    return Line(item.category, item.description, item.quantity, claimed, money(covered), "; ".join(notes))


def calculate(
    items: list[Item],
    rules: dict[str, Rule],
    deductible: Decimal,
    annual_limit: Decimal,
    already_paid: Decimal = ZERO,
) -> Settlement:
    lines = [cover_item(item, rules.get(item.category)) for item in items]
    total_claimed = sum((line.claimed for line in lines), ZERO)
    total_covered = sum((line.covered for line in lines), ZERO)
    after_deductible = max(total_covered - money(deductible), ZERO)
    remaining_limit = max(money(annual_limit) - money(already_paid), ZERO)
    return Settlement(
        lines=lines,
        total_claimed=total_claimed,
        total_covered=total_covered,
        deductible=money(deductible),
        after_deductible=after_deductible,
        annual_limit=money(annual_limit),
        already_paid=money(already_paid),
        remaining_limit=remaining_limit,
        payable=min(after_deductible, remaining_limit),
    )
