from __future__ import annotations

import base64
import uuid

from nekro_agent_sse_sdk import ChannelInfo, UserInfo, SSEClient
from nekro_agent_sse_sdk.models import (
    GetChannelInfoRequest,
    GetSelfInfoRequest,
    GetUserInfoRequest,
    SendMessageRequest,
    SendMessageResponse,
)

from app.config import settings
from app.database import (
    get_conversation,
    list_conversations,
    save_message,
    update_conversation_profile,
)
from app.hub import hub
from app.utils import get_upload_path, message_payload, segment_text


class WebChatSSEClient(SSEClient):
    """
    继承自 NekroAgent SDK 的 SSEClient。
    负责处理来自 NekroAgent 服务端下发的各种异步指令（通过 SSE 事件流推送）。
    """

    async def _handle_send_message(
        self,
        _event_type: str,
        data: SendMessageRequest,
    ) -> SendMessageResponse:
        """
        核心回调：当 AI 代理决定向当前平台发送一条消息时触发。
        """
        # 1. 查找或创建会话
        conversation = await get_conversation(data.channel_id)
        if not conversation:
            conversation = await update_conversation_profile(
                data.channel_id,
                {"channel_name": data.channel_name or data.channel_id},
            )
        assert conversation is not None

        # 2. 提取消息段文本
        content = segment_text(data.segments)
        message_id = f"ai_{uuid.uuid4().hex}"
        file_url = ""
        file_name = ""
        mime_type = ""
        file_size = 0

        # 3. 检查是否有文件或图片段需要处理
        for segment in data.segments:
            seg_type = getattr(getattr(segment, "type", ""), "value", getattr(segment, "type", ""))
            if seg_type not in {"image", "file"}:
                continue
            
            # 如果是 Base64 形式，需要解码并保存到本地
            base64_url = getattr(segment, "base64_url", None)
            if not base64_url:
                # 已经是普通 URL，直接使用
                file_url = getattr(segment, "url", "") or ""
                file_name = getattr(segment, "name", "") or ""
                mime_type = getattr(segment, "mime_type", "") or ""
                file_size = int(getattr(segment, "size", 0) or 0)
                break
                
            # 解码 Base64 文件
            meta, b64 = base64_url.split(",", 1)
            mime_type = meta.removeprefix("data:").split(";", 1)[0] or "application/octet-stream"
            suffix = getattr(segment, "suffix", "") or ""
            file_name = getattr(segment, "name", "") or f"{message_id}{suffix}"
            
            target, file_url = get_upload_path(file_name, mime_type)
            data_bytes = base64.b64decode(b64)
            target.write_bytes(data_bytes)
            file_size = len(data_bytes)
            break

        # 4. 存入数据库
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
        
        # 5. 通过 WebSocket 实时推送到前端页面
        await hub.broadcast(data.channel_id, message_payload(saved, conversation))
        
        return SendMessageResponse(message_id=message_id, success=True)

    async def _handle_get_user_info(
        self,
        _event_type: str,
        data: GetUserInfoRequest,
    ) -> UserInfo:
        """
        回调：当 AI 代理向服务端查询当前平台中某个用户的信息时触发。
        """
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
        """
        回调：当 AI 代理查询聊天群组/频道的信息时触发。
        """
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
        """
        回调：当 AI 代理查询它自己在这个聊天平台上的身份时触发。
        """
        conversations = await list_conversations()
        conversation = conversations[0] if conversations else None
        return UserInfo(
            user_id="webchat_bot",
            user_name=(conversation.ai_name if conversation else settings.webchat_bot_name),
            user_avatar=(conversation.ai_avatar if conversation else None) or None,
            user_nickname=(conversation.ai_name if conversation else settings.webchat_bot_name),
            platform_name=settings.webchat_platform,
        )


# 初始化全局单例客户端
client = WebChatSSEClient(
    server_url=settings.nekro_server_url,
    platform=settings.webchat_platform,
    client_name="nekro-webchat",
    client_version="0.2.0",
    access_key=settings.nekro_access_key or None,
)


async def ensure_subscribed(channel_id: str) -> None:
    """
    辅助函数：确保长连接客户端已经成功订阅了该频道的事件监听。
    """
    if client.running and client.session and client.client_id and channel_id not in client.subscribed_channels:
        await client.subscribe_channel(channel_id)

