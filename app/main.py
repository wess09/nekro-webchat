from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles


from app.database import init_db, list_conversations
from app.sse_client import client, ensure_subscribed
from app.routes import router as http_router
from app.ws import router as ws_router
from app.auth import router as auth_router
from app.utils import UPLOAD_DIR

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
FRONTEND_STATIC = BASE_DIR / "frontend" / "static"

app = FastAPI(title="Nekro WebChat")

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount(
    "/static",
    StaticFiles(directory=FRONTEND_STATIC if FRONTEND_STATIC.exists() else STATIC_DIR),
    name="static",
)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")

app.include_router(auth_router)
app.include_router(http_router)
app.include_router(ws_router)


@app.on_event("startup")
async def startup() -> None:
    await init_db()
    await client.start()
    for conversation in await list_conversations():
        await ensure_subscribed(conversation.channel_id)


@app.on_event("shutdown")
async def shutdown() -> None:
    await client.stop()
