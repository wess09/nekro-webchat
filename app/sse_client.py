from __future__ import annotations

import base64
import time
import uuid
from typing import Any

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from nekro_agent_sse_sdk.models import RequestType
        self._blocked_chunk_ids: dict[str, float] = {}
        # 替换分片文件处理
        self.register_handler(
            RequestType.FILE_CHUNK.value,
            self._intercept_file_chunk
        )

    def _mark_blocked_chunk_once(self, chunk_id: str) -> bool:
        now = time.monotonic()
        self._blocked_chunk_ids = {
            key: expires_at
            for key, expires_at in self._blocked_chunk_ids.items()
            if expires_at > now
        }
        if chunk_id in self._blocked_chunk_ids:
            return False
        self._blocked_chunk_ids[chunk_id] = now + 600
        return True

    async def _notify_file_blocked(
        self,
        *,
        conversation,
        filename: str | None,
        file_size: int,
        message_id_prefix: str = "ai_err",
    ) -> None:
        """Store and push a visible notice when an AI file is blocked."""
        display_name = filename or "未命名文件"
        size_mb = file_size / 1024 / 1024
        content = (
            f"[AI 试图发送文件「{display_name}」，但其大小（{size_mb:.2f}MB）"
            f"超出了平台 {settings.max_upload_size_mb}MB 的上限，已被系统拦截。]"
        )
        saved = await save_message(
            channel_id=conversation.channel_id,
            role="assistant",
            message_id=f"{message_id_prefix}_{uuid.uuid4().hex[:8]}",
            sender_id="webchat_bot",
            sender_name=conversation.ai_name or settings.webchat_bot_name,
            content=content,
        )
        payload = message_payload(saved, conversation)
        await hub.broadcast(conversation.channel_id, payload)
        await hub.broadcast(
            conversation.channel_id,
            {
                "type": "notification",
                "level": "warning",
                "message": content.strip("[]"),
                "channel_id": conversation.channel_id,
            },
        )

    async def _intercept_file_chunk(self, event_type: str, data: Any) -> Any:
        """
        在大文件分片到达时，立刻截断不合规的文件上传行为。
        """
        from app.config import settings
        from nekro_agent_sse_sdk.models import ChunkData, FileChunkResponse

        chunk_data = ChunkData(**data) if isinstance(data, dict) else data
        max_bytes = settings.max_upload_size_mb * 1024 * 1024

        if settings.max_upload_size_mb > 0 and chunk_data.total_size > max_bytes:
            if self._mark_blocked_chunk_once(chunk_data.chunk_id):
                self.logger.warning(
                    f"分片文件「{chunk_data.filename}」因体积（{chunk_data.total_size} 字节）超标，直接拦截！"
                )
                # 发送前端 UI 提示
                try:
                    conversations = await list_conversations()
                    for conv in conversations:
                        await self._notify_file_blocked(
                            conversation=conv,
                            filename=chunk_data.filename,
                            file_size=chunk_data.total_size,
                        )
                except Exception as e:
                    self.logger.error(f"广播大文件拦截消息失败: {e}")

            return FileChunkResponse(success=False, error="文件体积过大，已被系统拦截", message=None)

        return await self._chunk_receiver.handle_file_chunk(data)

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
            max_bytes = settings.max_upload_size_mb * 1024 * 1024

            if not base64_url:
                # 已经是普通 URL，直接使用
                file_url = getattr(segment, "url", "") or ""
                file_name = getattr(segment, "name", "") or ""
                mime_type = getattr(segment, "mime_type", "") or ""
                file_size = int(getattr(segment, "size", 0) or 0)
                
                # 若大小为 0，可能没有传 size 参数，如果刚好被 chunk_receiver 保存到根目录，尝试探测真实大小
                import os
                if file_size == 0 and file_name and os.path.exists(file_name):
                    file_size = os.path.getsize(file_name)
                    
                if settings.max_upload_size_mb > 0 and file_size > max_bytes:
                    notice = f"AI 发送的文件「{file_name}」大小超出 {settings.max_upload_size_mb}MB 限制，已被系统拦截"
                    content = f"[{notice}]\n\n" + content
                    file_url = ""
                    if file_name and os.path.exists(file_name):
                        try:
                            os.remove(file_name)
                        except OSError:
                            pass
                    file_name = ""
                    mime_type = ""
                    file_size = 0
                    await hub.broadcast(
                        data.channel_id,
                        {
                            "type": "notification",
                            "level": "warning",
                            "message": notice,
                            "channel_id": data.channel_id,
                        },
                    )
                else:
                    # 如果未被拦截，且文件由 chunk_receiver 写入到了根目录，我们将其挪到 uploads 目录并生成正确的 HTTP URL
                    if file_name and os.path.exists(file_name):
                        target, correct_file_url = get_upload_path(file_name, mime_type)
                        try:
                            import shutil
                            shutil.move(file_name, str(target))
                            file_url = correct_file_url
                            file_size = target.stat().st_size
                        except Exception:
                            pass
                break
                
            # 解码 Base64 文件
            meta, b64 = base64_url.split(",", 1)
            mime_type = meta.removeprefix("data:").split(";", 1)[0] or "application/octet-stream"
            suffix = getattr(segment, "suffix", "") or ""
            file_name = getattr(segment, "name", "") or f"{message_id}{suffix}"
            
            data_bytes = base64.b64decode(b64)
            file_size = len(data_bytes)

            if settings.max_upload_size_mb > 0 and file_size > max_bytes:
                notice = f"AI 发送的文件「{file_name}」大小超出 {settings.max_upload_size_mb}MB 限制，已被系统拦截"
                content = f"[{notice}]\n\n" + content
                file_url = ""
                file_name = ""
                mime_type = ""
                file_size = 0
                await hub.broadcast(
                    data.channel_id,
                    {
                        "type": "notification",
                        "level": "warning",
                        "message": notice,
                        "channel_id": data.channel_id,
                    },
                )
            else:
                target, file_url = get_upload_path(file_name, mime_type)
                target.write_bytes(data_bytes)
                
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

    async def _on_file_received(
        self, filename: str, file_bytes: bytes, mime_type: str, _file_type: str
    ) -> None:
        """
        覆写 SDK 默认行为：当分块大文件组装完成后，将文件归档到 uploads 目录，
        存入数据库，并通过 WebSocket 实时推送消息到前端。
        """
        file_size = len(file_bytes)
        max_bytes = settings.max_upload_size_mb * 1024 * 1024

        # 若文件超过限制，丢弃并向前端播报拦截通知
        if settings.max_upload_size_mb > 0 and file_size > max_bytes:
            self.logger.warning(f"组装完成的文件「{filename}」（{file_size} 字节）超限，已丢弃")
            try:
                conversations = await list_conversations()
                for conv in conversations:
                    await self._notify_file_blocked(
                        conversation=conv,
                        filename=filename,
                        file_size=file_size,
                    )
            except Exception as e:
                self.logger.error(f"广播拦截通知失败: {e}")
            return

        # 文件合规，归档到 uploads 目录
        try:
            import uuid as _uuid
            target, file_url = get_upload_path(filename, mime_type)
            target.write_bytes(file_bytes)
        except Exception:
            self.logger.exception(f"保存文件「{filename}」失败")
            return

        # 存入数据库并推送到前端
        try:
            conversations = await list_conversations()
            for conv in conversations:
                msg_id = f"ai_{_uuid.uuid4().hex}"
                saved = await save_message(
                    channel_id=conv.channel_id,
                    role="assistant",
                    message_id=msg_id,
                    sender_id="webchat_bot",
                    sender_name=conv.ai_name or settings.webchat_bot_name,
                    content=f"[文件] {filename}",
                    file_url=file_url,
                    file_name=filename,
                    mime_type=mime_type,
                    file_size=file_size,
                )
                await hub.broadcast(conv.channel_id, message_payload(saved, conv))
        except Exception as e:
            self.logger.error(f"大文件入库广播失败: {e}")


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
