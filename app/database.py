from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import settings


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    channel_name: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128), default="")
    user_name: Mapped[str] = mapped_column(String(128), default="")
    user_avatar: Mapped[str] = mapped_column(Text, default="")
    ai_name: Mapped[str] = mapped_column(String(128), default="")
    ai_avatar: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="conversation")


class ChatMessage(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    message_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(32))
    sender_id: Mapped[str] = mapped_column(String(128))
    sender_name: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text, default="")
    file_url: Mapped[str] = mapped_column(Text, default="")
    file_name: Mapped[str] = mapped_column(Text, default="")
    mime_type: Mapped[str] = mapped_column(String(128), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


def _ensure_sqlite_parent() -> None:
    prefix = "sqlite+aiosqlite:///"
    if not settings.webchat_database_url.startswith(prefix):
        return
    db_path = settings.webchat_database_url.removeprefix(prefix)
    if db_path.startswith(":memory:"):
        return
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_parent()
engine = create_async_engine(settings.webchat_database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def _ensure_columns() -> None:
    """SQLite 轻量迁移：给已有 demo 数据库补列。"""
    async with engine.begin() as conn:
        rows = await conn.execute(sql_text("PRAGMA table_info(conversations)"))
        conversation_cols = {row[1] for row in rows.fetchall()}
        conversation_adds = {
            "user_id": "TEXT DEFAULT ''",
            "user_name": "TEXT DEFAULT ''",
            "user_avatar": "TEXT DEFAULT ''",
            "ai_name": "TEXT DEFAULT ''",
            "ai_avatar": "TEXT DEFAULT ''",
        }
        for name, ddl in conversation_adds.items():
            if name not in conversation_cols:
                await conn.execute(sql_text(f"ALTER TABLE conversations ADD COLUMN {name} {ddl}"))

        rows = await conn.execute(sql_text("PRAGMA table_info(messages)"))
        message_cols = {row[1] for row in rows.fetchall()}
        message_adds = {
            "file_url": "TEXT DEFAULT ''",
            "file_name": "TEXT DEFAULT ''",
            "mime_type": "TEXT DEFAULT ''",
            "file_size": "INTEGER DEFAULT 0",
        }
        for name, ddl in message_adds.items():
            if name not in message_cols:
                await conn.execute(sql_text(f"ALTER TABLE messages ADD COLUMN {name} {ddl}"))


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _ensure_columns()
    async with SessionLocal() as session:
        await get_or_create_conversation(
            session,
            channel_id=settings.webchat_channel_id,
            channel_name=settings.webchat_channel_name,
        )
        await session.commit()


async def get_or_create_conversation(
    session: AsyncSession,
    *,
    channel_id: str,
    channel_name: str | None = None,
) -> Conversation:
    result = await session.execute(select(Conversation).where(Conversation.channel_id == channel_id))
    conversation = result.scalar_one_or_none()
    if conversation:
        if channel_name:
            conversation.channel_name = channel_name
        conversation.user_id = conversation.user_id or settings.webchat_user_id
        conversation.user_name = conversation.user_name or settings.webchat_user_name
        conversation.ai_name = conversation.ai_name or settings.webchat_bot_name
        conversation.updated_at = datetime.utcnow()
        return conversation

    conversation = Conversation(
        channel_id=channel_id,
        channel_name=channel_name or channel_id,
        user_id=settings.webchat_user_id,
        user_name=settings.webchat_user_name,
        user_avatar="",
        ai_name=settings.webchat_bot_name,
        ai_avatar="",
    )
    session.add(conversation)
    await session.flush()
    return conversation


async def create_conversation(channel_name: str, user_id: str | None = None, user_name: str | None = None) -> Conversation:
    channel_id = f"webchat_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    async with SessionLocal() as session:
        conversation = Conversation(
            channel_id=channel_id,
            channel_name=channel_name.strip() or "新对话",
            user_id=user_id if user_id else settings.webchat_user_id,
            user_name=user_name if user_name else settings.webchat_user_name,
            user_avatar="",
            ai_name=settings.webchat_bot_name,
            ai_avatar="",
        )
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)
        return conversation


async def list_conversations() -> list[Conversation]:
    async with SessionLocal() as session:
        result = await session.execute(select(Conversation).order_by(Conversation.updated_at.desc()))
        return list(result.scalars().all())


async def get_conversation(channel_id: str) -> Conversation | None:
    async with SessionLocal() as session:
        result = await session.execute(select(Conversation).where(Conversation.channel_id == channel_id))
        return result.scalar_one_or_none()


async def update_conversation_profile(
    channel_id: str,
    values: dict[str, str],
) -> Conversation | None:
    async with SessionLocal() as session:
        conversation = await get_or_create_conversation(
            session,
            channel_id=channel_id,
            channel_name=values.get("channel_name"),
        )
        for key in ("channel_name", "user_id", "user_name", "user_avatar", "ai_name", "ai_avatar"):
            if key in values:
                setattr(conversation, key, values[key].strip())
        conversation.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(conversation)
        return conversation


async def save_message(
    *,
    channel_id: str,
    role: Literal["user", "assistant", "system"],
    message_id: str,
    sender_id: str,
    sender_name: str,
    content: str = "",
    file_url: str = "",
    file_name: str = "",
    mime_type: str = "",
    file_size: int = 0,
) -> ChatMessage:
    async with SessionLocal() as session:
        conversation = await get_or_create_conversation(session, channel_id=channel_id)
        message = ChatMessage(
            conversation_id=conversation.id,
            message_id=message_id,
            role=role,
            sender_id=sender_id,
            sender_name=sender_name,
            content=content,
            file_url=file_url,
            file_name=file_name,
            mime_type=mime_type,
            file_size=file_size,
        )
        conversation.updated_at = datetime.utcnow()
        session.add(message)
        await session.commit()
        await session.refresh(message)
        return message


async def list_recent_messages(channel_id: str, before_id: int | None = None, limit: int = 50) -> list[ChatMessage]:
    async with SessionLocal() as session:
        conversation = await get_or_create_conversation(session, channel_id=channel_id)
        stmt = select(ChatMessage).where(ChatMessage.conversation_id == conversation.id)
        if before_id is not None:
            stmt = stmt.where(ChatMessage.id < before_id)
        stmt = stmt.order_by(ChatMessage.id.desc()).limit(limit)
        result = await session.execute(stmt)
        return list(reversed(result.scalars().all()))


def conversation_to_dict(conversation: Conversation) -> dict[str, Any]:
    return {
        "id": conversation.id,
        "channel_id": conversation.channel_id,
        "channel_name": conversation.channel_name,
        "user_id": conversation.user_id,
        "user_name": conversation.user_name,
        "user_avatar": conversation.user_avatar,
        "ai_name": conversation.ai_name,
        "ai_avatar": conversation.ai_avatar,
        "updated_at": int(conversation.updated_at.timestamp()),
    }
