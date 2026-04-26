from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File as FastAPIFile, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.auth import User, get_current_user, get_optional_current_user, get_ws_user
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
from app.utils import cleanup_uploaded_files, get_upload_path, message_payload, resolve_sender_avatars

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
    
    from app.auth import User
    if conv.user_id:
        u_res = await session.execute(select(User).where(User.id == conv.user_id))
        owner_user = u_res.scalar_one_or_none()
        if owner_user:
            d["ai_avatar"] = owner_user.ai_avatar or ""
    
    # 格式化最后一条消息的展示文本
    if last_msg:
        if last_msg.file_url:
            suffix = Path(last_msg.file_name or last_msg.file_url or "").suffix.lower()
            is_image = (last_msg.mime_type or "").startswith("image/") or suffix in {
                ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif"
            }
            if is_image:
                content = (last_msg.content or "").strip()
                if content.startswith("[表情包]"):
                    sticker_name = content.removeprefix("[表情包]").strip() or Path(last_msg.file_name or "").stem or "表情"
                    d["last_message"] = f"[动画表情] {sticker_name}"
                else:
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


@router.delete("/api/conversations/{channel_id}")
async def api_delete_conversation(channel_id: str, _user: User = Depends(get_current_user)) -> dict[str, Any]:
    """用户删除单个对话会话。"""
    from app.database import SessionLocal, Conversation, ChatMessage, ConversationMember
    from sqlalchemy import delete, select
    
    async with SessionLocal() as session:
        # 1. 映射 channel_id 获取 UUID
        res = await session.execute(select(Conversation).where(Conversation.channel_id == channel_id))
        conversation = res.scalar_one_or_none()
        if not conversation:
            raise HTTPException(status_code=404, detail="会话不存在")
            
        conv_id = conversation.id
        
        # 2. 移除级联关联信息
        await session.execute(delete(ChatMessage).where(ChatMessage.conversation_id == conv_id))
        await session.execute(delete(ConversationMember).where(ConversationMember.conversation_id == conv_id))
        await session.execute(delete(Conversation).where(Conversation.id == conv_id))
        await session.commit()
    return {"status": "ok"}


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
    avatars = await resolve_sender_avatars(rows)
    return {"items": [message_payload(row, conversation, sender_avatar=avatars.get(row.sender_id, "")) for row in rows]}


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
    channel_id: str | None = Form(None),
    _user: User = Depends(get_current_user)
) -> dict[str, Any]:
    """
    通用文件/图片上传接口。
    文件保存在 /data/user/uid/uploads/频道ID
    如果在群聊则保存在群主的 UID 下
    """
    from app.config import settings
    from app.database import SessionLocal, Conversation
    from sqlalchemy import select
    import uuid
    from fastapi import HTTPException
    import shutil

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
    uid = str(_user.id)
    cid = channel_id or "default"

    # 3. 确定群主 UID 或是拥有者 UID
    if channel_id:
        async with SessionLocal() as session:
            res = await session.execute(select(Conversation).where(Conversation.id == channel_id))
            conversation = res.scalar_one_or_none()
            if conversation and conversation.user_id:
                uid = conversation.user_id

    # 路径拼接
    base_dir = Path(__file__).resolve().parent.parent
    upload_dir = base_dir / "data" / "user" / uid / "uploads" / cid
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    ext = Path(file_data.filename or "file").suffix
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    target = upload_dir / unique_filename

    with target.open("wb") as out:
        shutil.copyfileobj(file_data.file, out)

    file_url = f"/data/user/{uid}/uploads/{cid}/{unique_filename}"
    return {
        "file_url": file_url,
        "file_name": Path(file_data.filename or "file").name,
        "mime_type": mime,
        "file_size": target.stat().st_size,
        "file_path": str(target),
    }

@router.post("/api/conversations/{channel_id}/leave")
async def leave_conversation(
    channel_id: str,
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    from app.database import SessionLocal, get_conversation, ConversationMember, Conversation
    from sqlalchemy import delete, select
    from fastapi import HTTPException

    async with SessionLocal() as session:
        result = await session.execute(select(Conversation).where(Conversation.channel_id == channel_id))
        conversation = result.scalar_one_or_none()
        if not conversation:
            raise HTTPException(status_code=404, detail="会话不存在")
        
        if conversation.user_id == str(current_user.id):
            raise HTTPException(status_code=400, detail="群主不能退出群聊")

        await session.execute(
            delete(ConversationMember).where(
                ConversationMember.conversation_id == conversation.id,
                ConversationMember.user_id == str(current_user.id),
            )
        )
        await session.commit()
    return {"detail": "已成功退出群聊"}

@router.get("/api/conversations/{channel_id}/members")
async def list_conversation_members(
    channel_id: str,
    current_user: User = Depends(get_current_user)
) -> list[dict[str, Any]]:
    from app.database import SessionLocal, Conversation, ConversationMember
    from app.auth import User as DBUser
    from sqlalchemy import select
    from fastapi import HTTPException

    async with SessionLocal() as session:
        res = await session.execute(select(Conversation).where(Conversation.channel_id == channel_id))
        conv = res.scalar_one_or_none()
        if not conv:
            raise HTTPException(status_code=404, detail="会话不存在")

        owner_res = await session.execute(select(DBUser).where(DBUser.id == conv.user_id))
        owner_user = owner_res.scalar_one_or_none()

        members_res = await session.execute(select(ConversationMember).where(ConversationMember.conversation_id == conv.id))
        members = members_res.scalars().all()

        ret = []
        if owner_user:
            ret.append({
                "user_id": str(owner_user.id),
                "display_name": owner_user.display_name or owner_user.username,
                "avatar": owner_user.avatar or "/static/user.png",
                "is_owner": True
            })

        member_ids = [m.user_id for m in members if str(m.user_id) != str(conv.user_id)]
        
        if member_ids:
            user_avatars_res = await session.execute(select(DBUser).where(DBUser.id.in_(member_ids)))
            users_map = {str(u.id): u for u in user_avatars_res.scalars().all()}
            
            for m in members:
                if str(m.user_id) == str(conv.user_id):
                    continue
                u = users_map.get(str(m.user_id))
                ret.append({
                    "user_id": str(m.user_id),
                    "display_name": u.display_name if u else (m.user_name or "未知成员"),
                    "avatar": (u.avatar if u else "") or "/static/user.png",
                    "is_owner": False
                })
        else:
            # 如果没人，只有群主
            pass

        return ret


@router.delete("/api/conversations/{channel_id}/members/{user_id}")
async def remove_conversation_member(
    channel_id: str,
    user_id: str,
    current_user: User = Depends(get_current_user)
) -> dict[str, str]:
    from app.database import SessionLocal, Conversation, ConversationMember
    from sqlalchemy import delete, select
    from fastapi import HTTPException

    async with SessionLocal() as session:
        res = await session.execute(select(Conversation).where(Conversation.channel_id == channel_id))
        conv = res.scalar_one_or_none()
        if not conv:
            raise HTTPException(status_code=404, detail="会话不存在")
        
        if str(conv.user_id) != str(current_user.id):
            raise HTTPException(status_code=403, detail="只有群主可以移出成员")
        
        if str(user_id) == str(current_user.id):
            raise HTTPException(status_code=400, detail="不能移出群主自己")

        await session.execute(
            delete(ConversationMember).where(
                ConversationMember.conversation_id == conv.id,
                ConversationMember.user_id == user_id
            )
        )
        await session.commit()
    return {"detail": "已移出群成员"}


@router.get("/api/download")
async def api_download_file(
    path: str,
    name: str | None = None,
    current_user: User | None = Depends(get_optional_current_user),
    token: str | None = None,
):
    """
    专门为绕过 iOS 平台 download 属性限制而设计的文件流式下载器。
    """
    from fastapi.responses import FileResponse
    from urllib.parse import unquote
    from fastapi import HTTPException
 
    # 普通 <a href> 下载不会附带 Authorization 头，这里允许前端在查询参数中附带 token。
    user = current_user
    if user is None and token:
        user = await get_ws_user(token)
    if user is None:
        raise HTTPException(status_code=401, detail="未提供认证令牌")

    path_str = unquote(path)
    if not path_str.startswith("/data/"):
         raise HTTPException(status_code=403, detail="禁止访问该路径")
         
    relative_path = path_str.lstrip("/")
    base_dir = Path(__file__).resolve().parent.parent
    physical_path = base_dir / relative_path
    
    if not physical_path.exists() or not physical_path.is_file():
         raise HTTPException(status_code=404, detail="文件不存在或已被自动清理")
         
    download_name = name if name else physical_path.name
    
    return FileResponse(
         path=physical_path,
         filename=download_name,
         media_type="application/octet-stream"
    )


@router.get("/static/user.png")
async def get_static_user_png():
    from fastapi.responses import FileResponse
    base_dir = Path(__file__).resolve().parent.parent
    return FileResponse(base_dir / "static" / "user.png")


@router.get("/static/ai.png")
async def get_static_ai_png():
    from fastapi.responses import FileResponse
    base_dir = Path(__file__).resolve().parent.parent
    return FileResponse(base_dir / "static" / "ai.png")
