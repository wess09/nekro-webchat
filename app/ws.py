from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from nekro_agent_sse_sdk import ReceiveMessage
from nekro_agent_sse_sdk import file as sdk_file, image, text
from nekro_agent_sse_sdk.models import MessageSegmentUnion

from app.config import settings
from app.database import (
    Conversation,
    SessionLocal,
    create_conversation,
    get_conversation,
    list_conversations,
    list_recent_messages,
    save_message,
)
from app.hub import hub
from app.routes import get_conversations_with_last_message
from app.sse_client import client, ensure_subscribed
from app.utils import message_payload
from app.auth import get_ws_user

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(default="")) -> None:
    """
    前端页面与后端进行全双工通信的核心 WebSocket 路由。
    处理：客户端连接初始化、接收用户发出的消息、下发历史记录等。
    需要通过 ?token=xxx 查询参数传递 JWT 令牌进行认证。
    """
    try:
        # 0. 验证用户身份
        user = await get_ws_user(token)
        if not user:
            await websocket.accept()
            await websocket.close(code=4001, reason="未认证或令牌无效")
            return

        # 1. 建立连接，并默认加入最近使用的第一个会话
        async with SessionLocal() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(Conversation)
                .where(Conversation.user_id == str(user.id))
                .order_by(Conversation.updated_at.desc())
            )
            conversations = result.scalars().all()

            if not conversations:
                current = await create_conversation(
                    channel_name="默认对话",
                    user_id=str(user.id),
                    user_name=user.display_name or user.username
                )
                await ensure_subscribed(current.channel_id)
            else:
                current = conversations[0]

            await hub.connect(websocket, current.channel_id)
            
            # 2. 推送当前与 NekroAgent 平台的连接健康状态
            await websocket.send_json({"type": "status", "connected": client.running})

            # 3. 推送最新的侧边栏会话列表
            ws_items = await get_conversations_with_last_message(session, str(user.id))
            await websocket.send_json({"type": "conversations", "items": ws_items})

        # 4. 推送当前会话的历史聊天记录
        rows = await list_recent_messages(current.channel_id)
        await websocket.send_json(
            {"type": "history", "channel_id": current.channel_id, "items": [message_payload(row, current) for row in rows]},
        )

        # 5. 持续循环监听来自前端浏览器的操作
        while True:
            payload = await websocket.receive_json()
            action = payload.get("action", "send")

            # 动作 A: 用户在前端切换了聊天会话
            if action == "select":
                channel_id = str(payload.get("channel_id", ""))
                conversation = await get_conversation(channel_id)
                if not conversation or conversation.user_id != str(user.id):
                    await websocket.send_json({"type": "error", "message": "会话不存在或无权访问"})
                    continue
                current = conversation
                
                # 更新长连接中的“关注频道”标记
                await hub.set_channel(websocket, channel_id)
                rows = await list_recent_messages(channel_id)
                # 重新推送该会话的历史记录给前端
                await websocket.send_json(
                    {"type": "history", "channel_id": channel_id, "items": [message_payload(row, current) for row in rows]},
                )
                continue

            # 动作 B: 用户在前端发送消息
            if action != "send":
                continue

            content = str(payload.get("content", "")).strip()
            file_info = payload.get("file") if isinstance(payload.get("file"), dict) else None
            
            # 发空消息直接忽略
            if not content and not file_info:
                continue

            conversation = await get_conversation(str(payload.get("channel_id") or current.channel_id))
            if not conversation:
                await websocket.send_json({"type": "error", "message": "会话不存在"})
                continue
            current = conversation
            
            # 确保长连接对该频道处于订阅状态
            await ensure_subscribed(conversation.channel_id)

            # 保存用户发送的消息到本地 DB
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
            
            # 广播给当前会话下的其它前端客户端（例如多开网页同步）
            await hub.broadcast(conversation.channel_id, message_payload(saved, conversation))

            # 拼装为 NekroAgent SDK 认可的消息段
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

            # 通过 SDK 上报给 AI 代理服务端，触发 AI 的业务流
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
        # 前端连接意外断开或刷新，安全移除
        await hub.disconnect(websocket)

