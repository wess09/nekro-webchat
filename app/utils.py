from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Any

from nekro_agent_sse_sdk.models import MessageSegmentUnion

from app.config import settings
from app.database import ChatMessage, Conversation

# 定义文件上传的根目录
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def get_upload_path(filename: str, mime_type: str) -> tuple[Path, str]:
    """
    根据上传文件的文件名和 MIME 类型生成分类文件夹及按日期归档的路径。
    
    :param filename: 原始文件名
    :param mime_type: 文件的 MIME 类型 (如 'image/png')
    :return: 包含两个元素的元组:
             1. Path: 本地磁盘保存的绝对路径
             2. str: 对外的相对 HTTP 访问 URL (如 '/uploads/images/20260426/xxx.png')
    """
    from datetime import datetime

    # 按照媒体类型进行一级分类
    if mime_type.startswith("image/"):
        category = "images"
    elif mime_type.startswith("video/"):
        category = "videos"
    elif mime_type.startswith("audio/"):
        category = "audios"
    else:
        category = "documents"

    # 按年月日创建二级目录
    date_str = datetime.now().strftime("%Y%m%d")
    target_dir = UPLOAD_DIR / category / date_str
    target_dir.mkdir(parents=True, exist_ok=True)

    # 拼接唯一文件名防止覆盖
    unique_prefix = uuid.uuid4().hex[:12]
    safe_name = Path(filename).name
    target_file_name = f"{unique_prefix}_{safe_name}"

    target_path = target_dir / target_file_name
    relative_url = f"/uploads/{category}/{date_str}/{target_file_name}"

    return target_path, relative_url


def message_payload(message: ChatMessage, conversation: Conversation | None = None, sender_avatar: str = "") -> dict[str, Any]:
    """
    将数据库中的 `ChatMessage` 模型格式化为前端可以直接消费的 JSON 消息载体。
    
    :param message: 数据库消息实例
    :param conversation: 消息所属的会话实例
    :param sender_avatar: 消息发送者的头像 URL
    """
    return {
        "type": "message",
        "id": message.id,
        "role": message.role,
        "message_id": message.message_id,
        "channel_id": conversation.channel_id if conversation else "",
        "channel_name": conversation.channel_name if conversation else "",
        "sender_id": message.sender_id,
        "sender_name": message.sender_name,
        "sender_avatar": sender_avatar,
        "content": message.content,
        "file_url": message.file_url,
        "file_name": message.file_name,
        "mime_type": message.mime_type,
        "file_size": message.file_size,
        "timestamp": int(message.created_at.timestamp()),
    }


def segment_text(segments: list[MessageSegmentUnion]) -> str:
    """
    将 NekroAgent SDK 返回的复杂富文本消息段 (Segments) 转换为纯文本预览字符串。
    (例如：将艾特、图片、文件转为中括号文本预览)
    """
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


async def resolve_sender_avatars(messages: list[ChatMessage]) -> dict[str, str]:
    """
    批量查询消息发送者的头像 URL。
    返回 {sender_id: avatar_url} 的字典。
    """
    from sqlalchemy import select
    from app.auth import User
    from app.database import SessionLocal

    sender_ids = list(set(m.sender_id for m in messages if m.sender_id and m.role == "user"))
    if not sender_ids:
        return {}
    
    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.id.in_(sender_ids)))
        users = result.scalars().all()
    
    return {u.id: (u.avatar or "") for u in users}


def cleanup_uploaded_files() -> None:
    """
    根据配置，清理服务器上超时的、或超出总容量限制的上传文件。
    """
    from datetime import datetime, timedelta
    import os

    # 1. 按照时间清理
    if settings.cleanup_max_file_age_days > 0:
        cutoff_time = datetime.now() - timedelta(days=settings.cleanup_max_file_age_days)
        for root, dirs, files in os.walk(UPLOAD_DIR):
            for f in files:
                f_path = Path(root) / f
                try:
                    if datetime.fromtimestamp(f_path.stat().st_mtime) < cutoff_time:
                        f_path.unlink()
                except OSError:
                    pass

    # 2. 按照总大小清理 (保留最近的文件，删除最旧的文件)
    if settings.cleanup_max_total_size_mb > 0:
        max_total_bytes = settings.cleanup_max_total_size_mb * 1024 * 1024
        
        # 收集所有的文件及其最后修改时间
        all_files = []
        total_size = 0
        for root, dirs, files in os.walk(UPLOAD_DIR):
            for f in files:
                f_path = Path(root) / f
                try:
                    stat_res = f_path.stat()
                    all_files.append({
                        "path": f_path,
                        "mtime": stat_res.st_mtime,
                        "size": stat_res.st_size
                    })
                    total_size += stat_res.st_size
                except OSError:
                    pass
                
        # 如果超出总大小，按修改时间排序（从旧到新），依次删除直到满足大小
        if total_size > max_total_bytes:
            all_files.sort(key=lambda x: x["mtime"])
            for item in all_files:
                if total_size <= max_total_bytes:
                    break
                try:
                    item["path"].unlink()
                    total_size -= item["size"]
                except OSError:
                    pass

