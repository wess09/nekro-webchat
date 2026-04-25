from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from nekro_agent_sse_sdk import ReceiveMessage
from nekro_agent_sse_sdk import file as sdk_file, image, text
from nekro_agent_sse_sdk.models import MessageSegmentUnion

from app.config import settings
from app.database import (
    SessionLocal,
    get_conversation,
    list_conversations,
    list_recent_messages,
    save_message,
)
from app.hub import hub
from app.routes import get_conversations_with_last_message
from app.sse_client import client, ensure_subscribed
from app.utils import message_payload

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    try:
        conversations = await list_conversations()
        current = conversations[0]
        await hub.connect(websocket, current.channel_id)
        await websocket.send_json({"type": "status", "connected": client.running})

        async with SessionLocal() as session:
            ws_items = await get_conversations_with_last_message(session)
            await websocket.send_json({"type": "conversations", "items": ws_items})

        rows = await list_recent_messages(current.channel_id)
        await websocket.send_json(
            {"type": "history", "channel_id": current.channel_id, "items": [message_payload(row, current) for row in rows]},
        )

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
