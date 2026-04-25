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
    ensure_conversation_invite_key,
    get_conversation,
    get_or_create_user_default_conversation,
    join_conversation_by_invite_key,
    list_recent_messages,
    list_user_conversations,
    update_conversation_profile,
    user_can_access_conversation,
)
from app.sse_client import client, ensure_subscribed
from app.utils import cleanup_uploaded_files, get_upload_path, message_payload

BASE_DIR = Path(__file__).resolve().parent.parent
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
    按更新时间倒序获取当前账号绑定的 chatkey 列表。
    """
    convs = await list_user_conversations(user_id)
    return [await pack_single_conversation(session, c) for c in convs]


# ---------------------------------------------------------------------------
# HTTP 路由接口
# ---------------------------------------------------------------------------

@router.get("/")
async def index() -> FileResponse:
    """提供单页面应用的首页 HTML 载入。"""
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/invite/{invite_key}")
async def invite_page(invite_key: str) -> FileResponse:
    """邀请链接入口，实际加入逻辑由登录后的前端调用 API 完成。"""
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
    user_id = str(_user.id)
    user_name = _user.display_name or _user.username
    async with SessionLocal() as session:
        items = await get_conversations_with_last_message(session, user_id)
        
        # 每个账号有一个稳定且强绑定的默认 chatkey。
        if not items:
            conversation = await get_or_create_user_default_conversation(
                user_id=user_id,
                user_name=user_name,
            )
            await ensure_subscribed(conversation.channel_id)
            items = [await pack_single_conversation(session, conversation)]
            
        return {"items": items}


@router.post("/api/conversations")
async def api_create_conversation(payload: dict[str, str], _user: User = Depends(get_current_user)) -> dict[str, Any]:
    """用户在前端主动创建一个新的 AI 对话。"""
    user_id = str(_user.id)
    conversation = await create_conversation(
        payload.get("channel_name", "新对话"),
        user_id=user_id,
        user_name=_user.display_name or _user.username,
        kind="direct",
    )
    # 在 SSE 客户端向 NekroAgent 监听此频道
    await ensure_subscribed(conversation.channel_id)
    async with SessionLocal() as session:
        return await pack_single_conversation(session, conversation)


@router.post("/api/groups")
async def api_create_group(payload: dict[str, str], _user: User = Depends(get_current_user)) -> dict[str, Any]:
    """创建一个群聊频道，可通过邀请链接让其他账号加入。"""
    user_id = str(_user.id)
    conversation = await create_conversation(
        payload.get("channel_name", "新群聊"),
        user_id=user_id,
        user_name=_user.display_name or _user.username,
        kind="group",
    )
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
    if not conversation or not await user_can_access_conversation(channel_id, str(_user.id)):
        raise HTTPException(status_code=403, detail="会话不存在或无权访问")
        
    rows = await list_recent_messages(channel_id, before_id=before_id, limit=limit)
    return {"items": [message_payload(row, conversation) for row in rows]}


@router.get("/api/conversations/{channel_id}/invite")
async def api_conversation_invite(channel_id: str, _user: User = Depends(get_current_user)) -> dict[str, Any]:
    """获取当前账号可访问群聊的邀请信息。"""
    conversation = await get_conversation(channel_id)
    if not conversation or not await user_can_access_conversation(channel_id, str(_user.id)):
        raise HTTPException(status_code=403, detail="会话不存在或无权访问")
    if conversation.kind != "group":
        raise HTTPException(status_code=400, detail="只有群聊可以生成邀请链接")

    conversation = await ensure_conversation_invite_key(channel_id)
    assert conversation is not None
    return {
        "channel_id": conversation.channel_id,
        "invite_key": conversation.invite_key,
        "invite_path": f"/invite/{conversation.invite_key}",
    }


@router.post("/api/invite/{invite_key}/join")
async def api_join_invite(invite_key: str, _user: User = Depends(get_current_user)) -> dict[str, Any]:
    """通过邀请 key 加入群聊。"""
    conversation = await join_conversation_by_invite_key(
        invite_key=invite_key,
        user_id=str(_user.id),
        user_name=_user.display_name or _user.username,
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="邀请链接无效或已过期")

    await ensure_subscribed(conversation.channel_id)
    async with SessionLocal() as session:
        return await pack_single_conversation(session, conversation)


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
