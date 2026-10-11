"""Admin area: users, plan configuration, policies, and claim review (verify + calculate + approve/reject)."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import AdminUser, DbSession
from app.core.database import utcnow
from app.core.security import hash_password
from app.models import Claim, ClaimStatus, CoverageRule, Payment, Plan, Policy, User
from app.schemas.auth import AdminUserCreate, UserOut
from app.schemas.analysis import ClaimAnalysis
from app.schemas.claim import ApproveIn, CalculationOut, ClaimOut, RejectIn
from app.schemas.plan import PlanIn, PlanOut, PolicyCreate, PolicyOut
from app.services.analysis import store_analysis
from app.services.claims import calculate_claim

router = APIRouter(prefix="/admin", tags=["admin"])


# ---- Users ----


@router.get("/users", response_model=list[UserOut])
def list_users(admin: AdminUser, db: DbSession) -> list[User]:
    return list(db.scalars(select(User).order_by(User.id)))


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(data: AdminUserCreate, admin: AdminUser, db: DbSession) -> User:
    if db.scalar(select(User).where(User.email == data.email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists.")
    user = User(
        email=data.email, full_name=data.full_name, hashed_password=hash_password(data.password), role=data.role
    )
    db.add(user)
    db.commit()
    return user


# ---- Plans (the configurable settlement rules) ----


def _apply_plan(plan: Plan, data: PlanIn) -> None:
    for field, value in data.model_dump(exclude={"coverage_rules"}).items():
        setattr(plan, field, value)
    plan.coverage_rules = [CoverageRule(**rule.model_dump()) for rule in data.coverage_rules]


def _get_plan(db: DbSession, plan_id: int) -> Plan:
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found.")
    return plan


@router.get("/plans", response_model=list[PlanOut])
def list_plans(admin: AdminUser, db: DbSession) -> list[Plan]:
    return list(db.scalars(select(Plan).order_by(Plan.id)))


@router.get("/plans/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: int, admin: AdminUser, db: DbSession) -> Plan:
    return _get_plan(db, plan_id)


@router.post("/plans", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
def create_plan(data: PlanIn, admin: AdminUser, db: DbSession) -> Plan:
    plan = Plan()
    _apply_plan(plan, data)
    db.add(plan)
    db.commit()
    return plan


@router.put("/plans/{plan_id}", response_model=PlanOut)
def update_plan(plan_id: int, data: PlanIn, admin: AdminUser, db: DbSession) -> Plan:
    """Replace a plan's settings and rules. Affects claims not yet approved; approved ones keep their saved calculation."""
    plan = _get_plan(db, plan_id)
    plan.coverage_rules.clear()
    db.flush()  # delete old rules first so (plan_id, category) stays unique
    _apply_plan(plan, data)
    db.commit()
    return plan


# ---- Policies ----


@router.get("/policies", response_model=list[PolicyOut])
def list_policies(admin: AdminUser, db: DbSession) -> list[Policy]:
    return list(db.scalars(select(Policy).order_by(Policy.id)))


@router.post("/policies", response_model=PolicyOut, status_code=status.HTTP_201_CREATED)
def create_policy(data: PolicyCreate, admin: AdminUser, db: DbSession) -> Policy:
    if db.get(User, data.user_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    _get_plan(db, data.plan_id)
    if db.scalar(select(Policy).where(Policy.policy_number == data.policy_number)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Policy number already exists.")
    policy = Policy(**data.model_dump())
    db.add(policy)
    db.commit()
    return policy


# ---- Claim review ----


def _get_claim(db: DbSession, claim_id: int) -> Claim:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found.")
    return claim


def _require_submitted(claim: Claim) -> None:
    if claim.status != ClaimStatus.SUBMITTED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Claim is {claim.status}; only submitted claims can be reviewed.")


@router.get("/claims", response_model=list[ClaimOut])
def list_claims(
    admin: AdminUser,
    db: DbSession,
    status_filter: Annotated[ClaimStatus | None, Query(alias="status")] = None,
) -> list[Claim]:
    query = select(Claim).order_by(Claim.id.desc())
    if status_filter:
        query = query.where(Claim.status == status_filter)
    return list(db.scalars(query))


@router.get("/claims/{claim_id}", response_model=ClaimOut)
def get_claim(claim_id: int, admin: AdminUser, db: DbSession) -> Claim:
    return _get_claim(db, claim_id)


@router.get("/claims/{claim_id}/calculation", response_model=CalculationOut)
def preview_calculation(claim_id: int, admin: AdminUser, db: DbSession) -> CalculationOut:
    """Settlement breakdown + warning flags, without saving anything."""
    return calculate_claim(db, _get_claim(db, claim_id))


@router.post("/claims/{claim_id}/analyze", response_model=ClaimAnalysis)
def analyze_claim(claim_id: int, admin: AdminUser, db: DbSession) -> ClaimAnalysis:
    """Re-run the AI extraction and analysis for a claim and save the result."""
    analysis = store_analysis(db, _get_claim(db, claim_id))
    db.commit()
    return analysis


@router.get("/claims/{claim_id}/analysis", response_model=ClaimAnalysis)
def get_analysis(claim_id: int, admin: AdminUser, db: DbSession) -> dict:
    claim = _get_claim(db, claim_id)
    if claim.analysis is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No analysis yet. Submit the claim or POST .../analyze.")
    return claim.analysis


@router.post("/claims/{claim_id}/approve", response_model=ClaimOut)
def approve_claim(claim_id: int, data: ApproveIn, admin: AdminUser, db: DbSession) -> Claim:
    claim = _get_claim(db, claim_id)
    _require_submitted(claim)
    calculation = calculate_claim(db, claim)
    amount = calculation.payable
    if data.override_amount is not None:
        if not data.note:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Explain the override in `note`.")
        if data.override_amount > calculation.remaining_limit:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Override is above the remaining annual limit.")
        amount = data.override_amount

    claim.status = ClaimStatus.APPROVED
    claim.approved_amount = amount
    override = float(data.override_amount) if data.override_amount is not None else None
    claim.calculation = {**calculation.model_dump(mode="json"), "override_amount": override}
    claim.admin_note = data.note
    claim.reviewed_by_id = admin.id
    claim.reviewed_at = utcnow()
    if amount > 0:
        claim.payment = Payment(amount=amount, currency=calculation.currency)
    db.commit()
    return claim


@router.post("/claims/{claim_id}/reject", response_model=ClaimOut)
def reject_claim(claim_id: int, data: RejectIn, admin: AdminUser, db: DbSession) -> Claim:
    claim = _get_claim(db, claim_id)
    _require_submitted(claim)
    claim.status = ClaimStatus.REJECTED
    claim.admin_note = data.note
    claim.reviewed_by_id = admin.id
    claim.reviewed_at = utcnow()
    db.commit()
    return claim
