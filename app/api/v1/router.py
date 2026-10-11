from fastapi import APIRouter

from app.api.v1 import admin, analyze, auth, claims, payments

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(claims.router)
api_router.include_router(admin.router)
api_router.include_router(payments.router)
api_router.include_router(analyze.router)
