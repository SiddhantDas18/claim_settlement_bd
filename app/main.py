from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import models  # noqa: F401  (registers all tables)
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.services.seed import seed_defaults


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)  # creates missing tables; no migrations yet
    with SessionLocal() as db:
        seed_defaults(db)
    yield


app = FastAPI(title="Claims Processing API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
