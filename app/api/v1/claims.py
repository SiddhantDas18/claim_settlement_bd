"""Claimant side: create a claim, add expense items, upload documents, submit."""

import logging
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.database import utcnow
from app.models import DOCUMENT_TYPES, Claim, ClaimDocument, ClaimItem, ClaimStatus, Policy, User, UserRole
from app.schemas.claim import ClaimCreate, ClaimItemIn, ClaimOut, ClaimUpdate, DocumentOut
from app.services import storage, text_extraction
from app.services.analysis import store_analysis
from app.services.claims import missing_documents

router = APIRouter(prefix="/claims", tags=["claims"])
log = logging.getLogger(__name__)


def get_claim_for_user(db: DbSession, claim_id: int, user: User) -> Claim:
    """Owners see their own claims, admins see all. Others get 404 (not 403) to hide that it exists."""
    claim = db.get(Claim, claim_id)
    if claim is None or (claim.user_id != user.id and user.role != UserRole.ADMIN):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found.")
    return claim


def _require_draft(claim: Claim) -> None:
    if claim.status != ClaimStatus.DRAFT:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Claim is {claim.status}; only drafts can be changed.")


def _set_items(claim: Claim, items: list[ClaimItemIn], claimed_amount: Decimal | None) -> None:
    """Typed expense items set the claimed total; without items the claimant types the total directly."""
    claim.items = [ClaimItem(**item.model_dump()) for item in items]
    if items:
        claim.claimed_amount = sum((item.amount for item in items), Decimal("0"))
    elif claimed_amount is not None:
        claim.claimed_amount = claimed_amount


@router.post("", response_model=ClaimOut, status_code=status.HTTP_201_CREATED)
def create_claim(data: ClaimCreate, user: CurrentUser, db: DbSession) -> Claim:
    policy = db.get(Policy, data.policy_id)
    if policy is None or policy.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Policy not found.")
    claim = Claim(user_id=user.id, **data.model_dump(exclude={"items", "claimed_amount"}))
    _set_items(claim, data.items, data.claimed_amount)
    db.add(claim)
    db.commit()
    return claim


@router.get("", response_model=list[ClaimOut])
def list_my_claims(user: CurrentUser, db: DbSession) -> list[Claim]:
    return list(db.scalars(select(Claim).where(Claim.user_id == user.id).order_by(Claim.id.desc())))


@router.get("/{claim_id}", response_model=ClaimOut)
def get_claim(claim_id: int, user: CurrentUser, db: DbSession) -> Claim:
    return get_claim_for_user(db, claim_id, user)


@router.patch("/{claim_id}", response_model=ClaimOut)
def update_claim(claim_id: int, data: ClaimUpdate, user: CurrentUser, db: DbSession) -> Claim:
    claim = get_claim_for_user(db, claim_id, user)
    _require_draft(claim)
    changes = data.model_dump(exclude_unset=True, exclude={"items", "claimed_amount"})
    for field, value in changes.items():
        setattr(claim, field, value)
    if data.items is not None:
        _set_items(claim, data.items, data.claimed_amount)
    elif "claimed_amount" in data.model_fields_set and not claim.items and data.claimed_amount is not None:
        claim.claimed_amount = data.claimed_amount
    db.commit()
    return claim


@router.post("/{claim_id}/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
def upload_document(
    claim_id: int,
    doc_type: Annotated[str, Form(description=f"One of: {', '.join(DOCUMENT_TYPES)}")],
    file: Annotated[UploadFile, File(description="JPEG, PNG, WEBP or PDF")],
    user: CurrentUser,
    db: DbSession,
) -> ClaimDocument:
    claim = get_claim_for_user(db, claim_id, user)
    _require_draft(claim)
    if doc_type not in DOCUMENT_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"doc_type must be one of {DOCUMENT_TYPES}.")
    try:
        stored_path, size = storage.save_upload(claim.id, file.file, file.content_type)
    except storage.UploadError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    extracted = text_extraction.extract_text(Path(stored_path), file.content_type)
    document = ClaimDocument(
        claim_id=claim.id,
        doc_type=doc_type,
        original_filename=(file.filename or "upload")[:255],
        stored_path=stored_path,
        content_type=file.content_type,
        size_bytes=size,
        extracted_text=extracted.text,
        extraction_method=extracted.method,
        ocr_confidence=extracted.ocr_confidence,
        extraction_error=extracted.error,
    )
    db.add(document)
    db.commit()
    return document


def _get_document(claim: Claim, document_id: int) -> ClaimDocument:
    document = next((d for d in claim.documents if d.id == document_id), None)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    return document


@router.get("/{claim_id}/documents/{document_id}/file")
def download_document(claim_id: int, document_id: int, user: CurrentUser, db: DbSession) -> FileResponse:
    document = _get_document(get_claim_for_user(db, claim_id, user), document_id)
    return FileResponse(document.stored_path, media_type=document.content_type, filename=document.original_filename)


@router.delete("/{claim_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(claim_id: int, document_id: int, user: CurrentUser, db: DbSession) -> None:
    claim = get_claim_for_user(db, claim_id, user)
    _require_draft(claim)
    document = _get_document(claim, document_id)
    claim.documents.remove(document)
    db.commit()
    storage.delete_file(document.stored_path)


@router.post("/{claim_id}/submit", response_model=ClaimOut)
def submit_claim(claim_id: int, user: CurrentUser, db: DbSession) -> Claim:
    claim = get_claim_for_user(db, claim_id, user)
    _require_draft(claim)
    problems = []
    if not claim.items and not claim.claimed_amount:
        problems.append("Enter the total amount you are claiming.")
    problems += [f"Upload a {doc_type} document." for doc_type in missing_documents(claim)]
    if problems:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, problems)
    claim.status = ClaimStatus.SUBMITTED
    claim.submitted_at = utcnow()
    try:
        store_analysis(db, claim)
    except Exception:  # the analysis is a helper for the reviewer; it must never block a submission
        log.exception("Analysis failed for claim %s", claim.id)
    db.commit()
    return claim
