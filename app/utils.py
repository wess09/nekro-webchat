from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Any

from nekro_agent_sse_sdk.models import MessageSegmentUnion

from app.config import settings
from app.database import ChatMessage, Conversation

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def get_upload_path(filename: str, mime_type: str) -> tuple[Path, str]:
    """
    根据文件名和 mime_type 生成分类文件夹及日期文件夹
    返回: (本地绝对路径 Path, 对外相对 URL 字符串)
    """
    from datetime import datetime

    if mime_type.startswith("image/"):
        category = "images"
    elif mime_type.startswith("video/"):
        category = "videos"
    elif mime_type.startswith("audio/"):
        category = "audios"
    else:
        category = "documents"

    date_str = datetime.now().strftime("%Y%m%d")
    target_dir = UPLOAD_DIR / category / date_str
    target_dir.mkdir(parents=True, exist_ok=True)

    unique_prefix = uuid.uuid4().hex[:12]
    safe_name = Path(filename).name
    target_file_name = f"{unique_prefix}_{safe_name}"

    target_path = target_dir / target_file_name
    relative_url = f"/uploads/{category}/{date_str}/{target_file_name}"

    return target_path, relative_url


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
