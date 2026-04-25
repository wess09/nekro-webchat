from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File as FastAPIFile, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.auth import User, get_current_user
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
from app.utils import cleanup_uploaded_files, get_upload_path, message_payload

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = BASE_DIR / "static"

router = APIRouter()


# ---------------------------------------------------------------------------
# 会话列表辅助函数
# ---------------------------------------------------------------------------

async def pack_single_conversation(session, conv: Conversation) -> dict[str, Any]:
    """
    组装单个会话的详细信息，并附加上最后一条聊天消息的预览。
    """
    from sqlalchemy import select

    # 查询当前会话的最新一条消息
    msg_res = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conv.id)
        .order_by(ChatMessage.id.desc())
        .limit(1)
    )
    last_msg = msg_res.scalar_one_or_none()
    d = conversation_to_dict(conv)
    
    # 格式化最后一条消息的展示文本
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


async def get_conversations_with_last_message(session, user_id: str) -> list[dict[str, Any]]:
    """
    按更新时间倒序获取指定用户的会话列表。
    """
    from sqlalchemy import select

    res = await session.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
    )
    convs = res.scalars().all()
    return [await pack_single_conversation(session, c) for c in convs]


# ---------------------------------------------------------------------------
# HTTP 路由接口
# ---------------------------------------------------------------------------

@router.get("/")
async def index() -> FileResponse:
    """提供单页面应用的首页 HTML 载入。"""
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/status")
async def status() -> dict[str, Any]:
    """获取应用配置，以及与 NekroAgent 平台的连接健康状态。"""
    from app.config import settings

    return {
        "settings": {
            "server_url": settings.nekro_server_url,
            "platform": settings.webchat_platform,
        },
        "client": client.get_stats(),
    }


@router.get("/api/conversations")
async def api_conversations(_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """获取完整的会话历史列表，带最新一条消息预览。"""
    async with SessionLocal() as session:
        items = await get_conversations_with_last_message(session, str(_user.id))
        
        # 如果当前用户没有任何对话，则自动帮他创建一个默认对话
        if not items:
            conversation = await create_conversation(
                channel_name="默认对话",
                user_id=str(_user.id),
                user_name=_user.display_name or _user.username
            )
            await ensure_subscribed(conversation.channel_id)
            items = [await pack_single_conversation(session, conversation)]
            
        return {"items": items}


@router.post("/api/conversations")
async def api_create_conversation(payload: dict[str, str], _user: User = Depends(get_current_user)) -> dict[str, Any]:
    """用户在前端主动发起一个新的聊天频道。"""
    conversation = await create_conversation(
        payload.get("channel_name", "新对话"),
        user_id=str(_user.id),
        user_name=_user.display_name or _user.username
    )
    # 在 SSE 客户端向 NekroAgent 监听此频道
    await ensure_subscribed(conversation.channel_id)
    async with SessionLocal() as session:
        return await pack_single_conversation(session, conversation)


@router.patch("/api/conversations/{channel_id}")
async def api_update_conversation(channel_id: str, payload: dict[str, str], _user: User = Depends(get_current_user)) -> dict[str, Any]:
    """修改某个会话的属性（如：AI昵称、用户头像等）。"""
    conversation = await get_conversation(channel_id)
    if not conversation or conversation.user_id != str(_user.id):
        raise HTTPException(status_code=403, detail="会话不存在或无权访问")
    
    conversation = await update_conversation_profile(channel_id, payload)
    async with SessionLocal() as session:
        return await pack_single_conversation(session, conversation)


@router.get("/api/conversations/{channel_id}/messages")
async def api_messages(channel_id: str, before_id: int | None = None, limit: int = 50, _user: User = Depends(get_current_user)) -> dict[str, Any]:
    """获取指定会话的历史聊天记录（支持分页）。"""
    conversation = await get_conversation(channel_id)
    if not conversation or conversation.user_id != str(_user.id):
        raise HTTPException(status_code=403, detail="会话不存在或无权访问")
        
    rows = await list_recent_messages(channel_id, before_id=before_id, limit=limit)
    return {"items": [message_payload(row, conversation) for row in rows]}


@router.post("/api/upload")
async def api_upload(
    background_tasks: BackgroundTasks,
    file_data: UploadFile = FastAPIFile(...), 
    _user: User = Depends(get_current_user)
) -> dict[str, Any]:
    """
    通用文件/图片上传接口。
    前端拖拽/选中文件后在此流式落盘，并生成可直接渲染的 URL。
    """
    from app.config import settings

    # 1. 限制上传文件的最大大小
    if settings.max_upload_size_mb > 0:
        max_bytes = settings.max_upload_size_mb * 1024 * 1024
        file_data.file.seek(0, 2)
        file_size = file_data.file.tell()
        file_data.file.seek(0)
        if file_size > max_bytes:
            raise HTTPException(status_code=413, detail=f"文件体积过大，最大限制为 {settings.max_upload_size_mb}MB")

    # 2. 异步自动清理
    background_tasks.add_task(cleanup_uploaded_files)

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

