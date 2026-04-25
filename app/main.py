from __future__ import annotations

import asyncio
import base64
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File as FastAPIFile, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from nekro_agent_sse_sdk import ChannelInfo, ReceiveMessage, SSEClient, UserInfo, file as sdk_file, image, text
from nekro_agent_sse_sdk.models import (
    GetChannelInfoRequest,
    GetSelfInfoRequest,
    GetUserInfoRequest,
    MessageSegmentUnion,
    SendMessageRequest,
    SendMessageResponse,
)

from app.config import settings
from app.database import (
    ChatMessage,
    Conversation,
    conversation_to_dict,
    create_conversation,
    get_conversation,
    init_db,
    list_conversations,
    list_recent_messages,
    save_message,
    update_conversation_profile,
)

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class BrowserHub:
    def __init__(self) -> None:
        self._clients: dict[WebSocket, str] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, channel_id: str) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients[websocket] = channel_id

    async def set_channel(self, websocket: WebSocket, channel_id: str) -> None:
        async with self._lock:
            if websocket in self._clients:
                self._clients[websocket] = channel_id

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.pop(websocket, None)

    async def broadcast(self, channel_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            clients = [client for client, active_channel in self._clients.items() if active_channel == channel_id]

        stale: list[WebSocket] = []
        for client in clients:
            try:
                await client.send_json(payload)
            except Exception:
                stale.append(client)

        if stale:
            async with self._lock:
                for client in stale:
                    self._clients.pop(client, None)


hub = BrowserHub()


def message_payload(message: ChatMessage, conversation: Conversation | None = None) -> dict[str, Any]:
    return {
        "type": "message",
        "id": message.id,
        "role": message.role,
        "message_id": message.message_id,
        "channel_id": conversation.channel_id if conversation else "",
        "channel_name": conversation.channel_name if conversation else "",
        "sender_id": message.sender_id,
        "sender_name": message.sender_name,
        "content": message.content,
        "file_url": message.file_url,
        "file_name": message.file_name,
        "mime_type": message.mime_type,
        "file_size": message.file_size,
        "timestamp": int(message.created_at.timestamp()),
    }


def segment_text(segments: list[MessageSegmentUnion]) -> str:
    parts: list[str] = []
    for segment in segments:
        seg_type = getattr(getattr(segment, "type", ""), "value", getattr(segment, "type", ""))
        if seg_type == "text":
            parts.append(getattr(segment, "content", ""))
        elif seg_type == "image":
            parts.append(f"[图片] {getattr(segment, 'name', '') or ''}".strip())
        elif seg_type == "file":
            parts.append(f"[文件] {getattr(segment, 'name', '') or ''}".strip())
        elif seg_type == "at":
            parts.append(f"@{getattr(segment, 'nickname', '') or getattr(segment, 'user_id', '')}")
    return "\n".join(part for part in parts if part).strip()


async def ensure_subscribed(channel_id: str) -> None:
    if client.running and client.session and client.client_id and channel_id not in client.subscribed_channels:
        await client.subscribe_channel(channel_id)


class WebChatSSEClient(SSEClient):
    async def _handle_send_message(
        self,
        _event_type: str,
        data: SendMessageRequest,
    ) -> SendMessageResponse:
        conversation = await get_conversation(data.channel_id)
        if not conversation:
            conversation = await update_conversation_profile(
                data.channel_id,
                {"channel_name": data.channel_name or data.channel_id},
            )
        assert conversation is not None

        content = segment_text(data.segments)
        message_id = f"ai_{uuid.uuid4().hex}"
        file_url = ""
        file_name = ""
        mime_type = ""
        file_size = 0

        for segment in data.segments:
            seg_type = getattr(getattr(segment, "type", ""), "value", getattr(segment, "type", ""))
            if seg_type not in {"image", "file"}:
                continue
            base64_url = getattr(segment, "base64_url", None)
            if not base64_url:
                file_url = getattr(segment, "url", "") or ""
                file_name = getattr(segment, "name", "") or ""
                mime_type = getattr(segment, "mime_type", "") or ""
                file_size = int(getattr(segment, "size", 0) or 0)
                break
            meta, b64 = base64_url.split(",", 1)
            mime_type = (meta.removeprefix("data:").split(";", 1)[0] or "application/octet-stream")
            suffix = getattr(segment, "suffix", "") or ""
            file_name = getattr(segment, "name", "") or f"{message_id}{suffix}"
            target = UPLOAD_DIR / f"{message_id}_{Path(file_name).name}"
            data_bytes = base64.b64decode(b64)
            target.write_bytes(data_bytes)
            file_url = f"/uploads/{target.name}"
            file_size = len(data_bytes)
            break

        saved = await save_message(
            channel_id=data.channel_id,
            role="assistant",
            message_id=message_id,
            sender_id="webchat_bot",
            sender_name=conversation.ai_name or settings.webchat_bot_name,
            content=content,
            file_url=file_url,
            file_name=file_name,
            mime_type=mime_type,
            file_size=file_size,
        )
        await hub.broadcast(data.channel_id, message_payload(saved, conversation))
        return SendMessageResponse(message_id=message_id, success=True)

    async def _handle_get_user_info(
        self,
        _event_type: str,
        data: GetUserInfoRequest,
    ) -> UserInfo:
        conversations = await list_conversations()
        conversation = conversations[0] if conversations else None
        return UserInfo(
            user_id=data.user_id,
            user_name=(conversation.user_name if conversation else settings.webchat_user_name),
            user_avatar=(conversation.user_avatar if conversation else None) or None,
            user_nickname=(conversation.user_name if conversation else settings.webchat_user_name),
            platform_name=settings.webchat_platform,
        )

    async def _handle_get_channel_info(
        self,
        _event_type: str,
        data: GetChannelInfoRequest,
    ) -> ChannelInfo:
        conversation = await get_conversation(data.channel_id)
        return ChannelInfo(
            channel_id=data.channel_id,
            channel_name=conversation.channel_name if conversation else data.channel_id,
            channel_avatar=None,
            member_count=1,
            owner_id=(conversation.user_id if conversation else settings.webchat_user_id),
            is_admin=True,
        )

    async def _handle_get_self_info(
        self,
        _event_type: str,
        _data: GetSelfInfoRequest,
    ) -> UserInfo:
        conversations = await list_conversations()
        conversation = conversations[0] if conversations else None
        return UserInfo(
            user_id="webchat_bot",
            user_name=(conversation.ai_name if conversation else settings.webchat_bot_name),
            user_avatar=(conversation.ai_avatar if conversation else None) or None,
            user_nickname=(conversation.ai_name if conversation else settings.webchat_bot_name),
            platform_name=settings.webchat_platform,
        )


client = WebChatSSEClient(
    server_url=settings.nekro_server_url,
    platform=settings.webchat_platform,
    client_name="nekro-webchat",
    client_version="0.2.0",
    access_key=settings.nekro_access_key or None,
)

app = FastAPI(title="Nekro WebChat")
FRONTEND_STATIC = BASE_DIR / "frontend" / "static"
app.mount("/static", StaticFiles(directory=FRONTEND_STATIC if FRONTEND_STATIC.exists() else STATIC_DIR), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.on_event("startup")
async def startup() -> None:
    await init_db()
    await client.start()
    for conversation in await list_conversations():
        await ensure_subscribed(conversation.channel_id)


@app.on_event("shutdown")
async def shutdown() -> None:
    await client.stop()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
async def status() -> dict[str, Any]:
    return {
        "settings": {
            "server_url": settings.nekro_server_url,
            "platform": settings.webchat_platform,
        },
        "client": client.get_stats(),
    }


@app.get("/api/conversations")
async def api_conversations() -> dict[str, Any]:
    conversations = await list_conversations()
    return {"items": [conversation_to_dict(item) for item in conversations]}


@app.post("/api/conversations")
async def api_create_conversation(payload: dict[str, str]) -> dict[str, Any]:
    conversation = await create_conversation(payload.get("channel_name", "新对话"))
    await ensure_subscribed(conversation.channel_id)
    return conversation_to_dict(conversation)


@app.patch("/api/conversations/{channel_id}")
async def api_update_conversation(channel_id: str, payload: dict[str, str]) -> dict[str, Any]:
    conversation = await update_conversation_profile(channel_id, payload)
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conversation_to_dict(conversation)


@app.get("/api/conversations/{channel_id}/messages")
async def api_messages(channel_id: str, limit: int = 80) -> dict[str, Any]:
    conversation = await get_conversation(channel_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在")
    rows = await list_recent_messages(channel_id, limit=limit)
    return {"items": [message_payload(row, conversation) for row in rows]}


@app.post("/api/upload")
async def api_upload(file_data: UploadFile = FastAPIFile(...)) -> dict[str, Any]:
    safe_name = Path(file_data.filename or "file").name
    target = UPLOAD_DIR / f"{uuid.uuid4().hex}_{safe_name}"
    with target.open("wb") as out:
        shutil.copyfileobj(file_data.file, out)
    return {
        "file_url": f"/uploads/{target.name}",
        "file_name": safe_name,
        "mime_type": file_data.content_type or "application/octet-stream",
        "file_size": target.stat().st_size,
        "file_path": str(target),
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    conversations = await list_conversations()
    current = conversations[0]
    await hub.connect(websocket, current.channel_id)
    await websocket.send_json({"type": "status", "connected": client.running})
    await websocket.send_json({"type": "conversations", "items": [conversation_to_dict(item) for item in conversations]})

    rows = await list_recent_messages(current.channel_id)
    await websocket.send_json(
        {"type": "history", "channel_id": current.channel_id, "items": [message_payload(row, current) for row in rows]},
    )

    try:
        while True:
            payload = await websocket.receive_json()
            action = payload.get("action", "send")

            if action == "select":
                channel_id = str(payload.get("channel_id", ""))
                conversation = await get_conversation(channel_id)
                if not conversation:
                    await websocket.send_json({"type": "error", "message": "会话不存在"})
                    continue
                current = conversation
                await hub.set_channel(websocket, channel_id)
                rows = await list_recent_messages(channel_id)
                await websocket.send_json(
                    {"type": "history", "channel_id": channel_id, "items": [message_payload(row, current) for row in rows]},
                )
                continue

            if action != "send":
                continue

            content = str(payload.get("content", "")).strip()
            file_info = payload.get("file") if isinstance(payload.get("file"), dict) else None
            if not content and not file_info:
                continue

            conversation = await get_conversation(str(payload.get("channel_id") or current.channel_id))
            if not conversation:
                await websocket.send_json({"type": "error", "message": "会话不存在"})
                continue
            current = conversation
            await ensure_subscribed(conversation.channel_id)

            message_id = f"web_{uuid.uuid4().hex}"
            saved = await save_message(
                channel_id=conversation.channel_id,
                role="user",
                message_id=message_id,
                sender_id=conversation.user_id or settings.webchat_user_id,
                sender_name=conversation.user_name or settings.webchat_user_name,
                content=content,
                file_url=str(file_info.get("file_url", "")) if file_info else "",
                file_name=str(file_info.get("file_name", "")) if file_info else "",
                mime_type=str(file_info.get("mime_type", "")) if file_info else "",
                file_size=int(file_info.get("file_size", 0)) if file_info else 0,
            )
            await hub.broadcast(conversation.channel_id, message_payload(saved, conversation))

            segments: list[MessageSegmentUnion] = []
            if content:
                segments.append(text(content))
            if file_info:
                file_path = str(file_info.get("file_path", ""))
                mime_type = str(file_info.get("mime_type", "application/octet-stream"))
                file_name = str(file_info.get("file_name", "file"))
                if mime_type.startswith("image/"):
                    segments.append(image(file_path=file_path, name=file_name, mime_type=mime_type))
                else:
                    segments.append(sdk_file(file_path=file_path, name=file_name, mime_type=mime_type))

            ok = await client.send_message(
                conversation.channel_id,
                ReceiveMessage(
                    msg_id=message_id,
                    from_id=conversation.user_id or settings.webchat_user_id,
                    from_name=conversation.user_name or settings.webchat_user_name,
                    from_nickname=conversation.user_name or settings.webchat_user_name,
                    is_to_me=True,
                    is_self=False,
                    raw_content=content,
                    channel_id=conversation.channel_id,
                    channel_name=conversation.channel_name,
                    platform_name=settings.webchat_platform,
                    segments=segments,
                    timestamp=int(time.time()),
                ),
            )
            if not ok:
                await websocket.send_json({"type": "error", "message": client.last_error or "发送到 NekroAgent 失败"})
    except WebSocketDisconnect:
        await hub.disconnect(websocket)
