from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File as FastAPIFile, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.database import (
    Conversation,
    ChatMessage,
    SessionLocal,
    conversation_to_dict,
    create_conversation,
    get_conversation,
    list_recent_messages,
    update_conversation_profile,
)
from app.sse_client import client, ensure_subscribed
from app.utils import get_upload_path, message_payload

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = BASE_DIR / "static"

router = APIRouter()


# ---------------------------------------------------------------------------
# 会话列表辅助函数
# ---------------------------------------------------------------------------

async def pack_single_conversation(session, conv: Conversation) -> dict[str, Any]:
    from sqlalchemy import select

    msg_res = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conv.id)
        .order_by(ChatMessage.id.desc())
        .limit(1)
    )
    last_msg = msg_res.scalar_one_or_none()
    d = conversation_to_dict(conv)
    if last_msg:
        if last_msg.file_url:
            if (last_msg.mime_type or "").startswith("image/"):
                d["last_message"] = f"[图片] {last_msg.file_name}"
            else:
                d["last_message"] = f"[文件] {last_msg.file_name}"
        else:
            d["last_message"] = last_msg.content
    else:
        d["last_message"] = "暂无消息"
    return d


async def get_conversations_with_last_message(session) -> list[dict[str, Any]]:
    from sqlalchemy import select

    res = await session.execute(select(Conversation).order_by(Conversation.updated_at.desc()))
    convs = res.scalars().all()
    return [await pack_single_conversation(session, c) for c in convs]


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/status")
async def status() -> dict[str, Any]:
    from app.config import settings

    return {
        "settings": {
            "server_url": settings.nekro_server_url,
            "platform": settings.webchat_platform,
        },
        "client": client.get_stats(),
    }


@router.get("/api/conversations")
async def api_conversations() -> dict[str, Any]:
    async with SessionLocal() as session:
        items = await get_conversations_with_last_message(session)
        return {"items": items}


@router.post("/api/conversations")
async def api_create_conversation(payload: dict[str, str]) -> dict[str, Any]:
    conversation = await create_conversation(payload.get("channel_name", "新对话"))
    await ensure_subscribed(conversation.channel_id)
    async with SessionLocal() as session:
        return await pack_single_conversation(session, conversation)


@router.patch("/api/conversations/{channel_id}")
async def api_update_conversation(channel_id: str, payload: dict[str, str]) -> dict[str, Any]:
    conversation = await update_conversation_profile(channel_id, payload)
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在")
    async with SessionLocal() as session:
        return await pack_single_conversation(session, conversation)


@router.get("/api/conversations/{channel_id}/messages")
async def api_messages(channel_id: str, before_id: int | None = None, limit: int = 50) -> dict[str, Any]:
    conversation = await get_conversation(channel_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在")
    rows = await list_recent_messages(channel_id, before_id=before_id, limit=limit)
    return {"items": [message_payload(row, conversation) for row in rows]}


@router.post("/api/upload")
async def api_upload(file_data: UploadFile = FastAPIFile(...)) -> dict[str, Any]:
    mime = file_data.content_type or "application/octet-stream"
    target, file_url = get_upload_path(file_data.filename or "file", mime)
    with target.open("wb") as out:
        shutil.copyfileobj(file_data.file, out)
    return {
        "file_url": file_url,
        "file_name": Path(file_data.filename or "file").name,
        "mime_type": mime,
        "file_size": target.stat().st_size,
        "file_path": str(target),
    }
