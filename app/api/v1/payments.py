"""Money area (admin): payouts owed for approved claims, and marking them paid."""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import AdminUser, DbSession
from app.core.database import utcnow
from app.models import ClaimStatus, Payment, PaymentStatus
from app.schemas.payment import MarkPaidIn, PaymentOut, PaymentSummary

router = APIRouter(prefix="/admin/payments", tags=["payments"])


@router.get("", response_model=list[PaymentOut])
def list_payments(
    admin: AdminUser,
    db: DbSession,
    status_filter: Annotated[PaymentStatus | None, Query(alias="status")] = None,
) -> list[Payment]:
    query = select(Payment).order_by(Payment.id.desc())
    if status_filter:
        query = query.where(Payment.status == status_filter)
    return list(db.scalars(query))


@router.get("/summary", response_model=PaymentSummary)
def payment_summary(admin: AdminUser, db: DbSession) -> PaymentSummary:
    rows = db.execute(
        select(Payment.status, func.count(), func.coalesce(func.sum(Payment.amount), 0)).group_by(Payment.status)
    ).all()
    totals = {row[0]: (row[1], Decimal(str(row[2]))) for row in rows}
    pending_count, pending_amount = totals.get(PaymentStatus.PENDING, (0, Decimal("0")))
    paid_count, paid_amount = totals.get(PaymentStatus.PAID, (0, Decimal("0")))
    return PaymentSummary(
        pending_count=pending_count, pending_amount=pending_amount, paid_count=paid_count, paid_amount=paid_amount
    )


@router.post("/{payment_id}/mark-paid", response_model=PaymentOut)
def mark_paid(payment_id: int, data: MarkPaidIn, admin: AdminUser, db: DbSession) -> Payment:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found.")
    if payment.status != PaymentStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Payment is already paid.")
    payment.status = PaymentStatus.PAID
    payment.method = data.method
    payment.reference = data.reference
    payment.paid_at = utcnow()
    payment.paid_by_id = admin.id
    payment.claim.status = ClaimStatus.PAID
    db.commit()
    return payment
