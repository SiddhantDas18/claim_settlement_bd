# Import every model so SQLAlchemy knows all tables before create_all / relationship setup.
from app.models.claim import DOCUMENT_TYPES, Claim, ClaimDocument, ClaimItem, ClaimStatus
from app.models.payment import Payment, PaymentStatus
from app.models.plan import CoverageRule, Plan, Policy
from app.models.user import User, UserRole

__all__ = [
    "DOCUMENT_TYPES",
    "Claim",
    "ClaimDocument",
    "ClaimItem",
    "ClaimStatus",
    "CoverageRule",
    "Payment",
    "PaymentStatus",
    "Plan",
    "Policy",
    "User",
    "UserRole",
]
