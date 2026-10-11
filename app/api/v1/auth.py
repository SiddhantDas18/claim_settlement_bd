from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.security import create_access_token, hash_password, verify_password
from app.models import Policy, User
from app.schemas.auth import LoginIn, RegisterIn, TokenOut, UserOut
from app.schemas.plan import PolicyOut

router = APIRouter(tags=["auth"])


@router.post("/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(data: RegisterIn, db: DbSession) -> User:
    if db.scalar(select(User).where(User.email == data.email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists.")
    user = User(email=data.email, full_name=data.full_name, hashed_password=hash_password(data.password))
    db.add(user)
    db.commit()
    return user


@router.post("/auth/login", response_model=TokenOut)
def login(data: LoginIn, db: DbSession) -> TokenOut:
    user = db.scalar(select(User).where(User.email == data.email))
    if user is None or not user.is_active or not verify_password(data.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password.")
    return TokenOut(access_token=create_access_token(user.id))


@router.get("/auth/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user


@router.get("/policies", response_model=list[PolicyOut])
def my_policies(user: CurrentUser, db: DbSession) -> list[Policy]:
    return list(db.scalars(select(Policy).where(Policy.user_id == user.id).order_by(Policy.id)))
