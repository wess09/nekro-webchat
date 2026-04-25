from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket


class BrowserHub:
    """
    WebSocket 连接管理器 (发布/订阅中心)。
    用于维护当前所有与前端浏览器建立的 WebSocket 连接，
    并支持按照频道(Channel)向前端实时广播消息。
    """
    def __init__(self) -> None:
        # 存储映射关系：{ WebSocket连接: 当前激活的会话频道ID }
        self._clients: dict[WebSocket, str] = {}
        # 协程锁，防止并发操作字典导致线程不安全（实际上是协程不安全）
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, channel_id: str) -> None:
        """接受新的 WebSocket 连接，并绑定到指定的默认频道。"""
        await websocket.accept()
        async with self._lock:
            self._clients[websocket] = channel_id

    async def set_channel(self, websocket: WebSocket, channel_id: str) -> None:
        """更新某个 WebSocket 连接当前正在关注的频道（如：用户在前端切换了聊天窗口）。"""
        async with self._lock:
            if websocket in self._clients:
                self._clients[websocket] = channel_id

    async def disconnect(self, websocket: WebSocket) -> None:
        """断开并移除一个 WebSocket 连接。"""
        async with self._lock:
            self._clients.pop(websocket, None)

    async def broadcast(self, channel_id: str, payload: dict[str, Any]) -> None:
        """
        向所有【当前正处于指定频道】的前端客户端广播 JSON 数据。
        如果发送失败，说明连接已断开，会将其自动清理。
        """
        async with self._lock:
            # 筛选出当前激活频道匹配的客户端
            clients = [
                client
                for client, active_channel in self._clients.items()
                if active_channel == channel_id
            ]

        stale: list[WebSocket] = []
        for client in clients:
            try:
                await client.send_json(payload)
            except Exception:
                # 记录发送失败的死连接
                stale.append(client)

        # 集中清理死连接
        if stale:
            async with self._lock:
                for client in stale:
                    self._clients.pop(client, None)


# 导出全局单例
hub = BrowserHub()

