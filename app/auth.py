"""
用户认证模块：注册 / 登录 / JWT 令牌 / 密码哈希。
使用 bcrypt 进行密码加密，python-jose 签发 JWT。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
import bcrypt
from pydantic import BaseModel, Field
from sqlalchemy import String, DateTime, select
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.database import Base, SessionLocal

# ---------------------------------------------------------------------------
# 密码哈希工具
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """使用 bcrypt 对明文密码进行哈希。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """验证明文密码是否与哈希值匹配。"""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))

# ---------------------------------------------------------------------------
# JWT 配置
# ---------------------------------------------------------------------------

SECRET_KEY = settings.webchat_jwt_secret
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = settings.webchat_jwt_expire_minutes

# ---------------------------------------------------------------------------
# ORM 模型
# ---------------------------------------------------------------------------


import uuid

class User(Base):
    """用户账号表。"""
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    hashed_password: Mapped[str] = mapped_column(String(256))
    avatar: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Pydantic 请求/响应模型
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    """注册请求体。"""
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    display_name: str = Field(default="", max_length=64)


class LoginRequest(BaseModel):
    """登录请求体。"""
    username: str
    password: str


class TokenResponse(BaseModel):
    """登录成功后返回的令牌。"""
    access_token: str
    token_type: str = "bearer"
    user: dict[str, Any]


class UserResponse(BaseModel):
    """用户公开信息。"""
    id: str
    username: str
    display_name: str
    avatar: str


# ---------------------------------------------------------------------------
# JWT 工具函数
# ---------------------------------------------------------------------------


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """签发 JWT 令牌。"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def _user_to_dict(user: User) -> dict[str, Any]:
    """将 User ORM 对象转为前端需要的字典。"""
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name or user.username,
        "avatar": user.avatar or "",
    }


# ---------------------------------------------------------------------------
# FastAPI 依赖：从请求中提取当前用户
# ---------------------------------------------------------------------------

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


async def get_current_user(token: str | None = Depends(oauth2_scheme)) -> User:
    """
    HTTP 路由依赖：从 Authorization: Bearer <token> 中解析当前登录用户。
    """
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供认证令牌")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub_val = payload.get("sub")
        if sub_val is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效令牌")
        user_id = str(sub_val)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌已过期或无效")

    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return user


async def get_ws_user(token: str | None) -> User | None:
    """
    WebSocket 专用：从查询参数中的 token 解析用户，解析失败返回 None。
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub_val = payload.get("sub")
        if sub_val is None:
            return None
        user_id = str(sub_val)
    except JWTError:
        return None

    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse)
async def register(body: RegisterRequest) -> TokenResponse:
    """
    用户注册：创建新账号并自动签发令牌（注册即登录）。
    """
    async with SessionLocal() as session:
        # 检查用户名是否已存在
        exists = await session.execute(select(User).where(User.username == body.username))
        if exists.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="用户名已被注册")

        user = User(
            username=body.username,
            display_name=body.display_name.strip() or body.username,
            hashed_password=hash_password(body.password),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token, user=_user_to_dict(user))


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse:
    """
    用户登录：验证密码后签发 JWT。
    """
    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.username == body.username))
        user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token, user=_user_to_dict(user))


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    """
    获取当前登录用户信息（用于前端刷新页面后恢复登录状态）。
    """
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        display_name=current_user.display_name or current_user.username,
        avatar=current_user.avatar or "",
    )
