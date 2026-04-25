from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket


class BrowserHub:
    def __init__(self) -> None:
        self._clients: dict[WebSocket, str] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, channel_id: str) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients[websocket] = channel_id

    async def set_channel(self, websocket: WebSocket, channel_id: str) -> None:
        async with self._lock:
            if websocket in self._clients:
                self._clients[websocket] = channel_id

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.pop(websocket, None)

    async def broadcast(self, channel_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
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
                stale.append(client)

        if stale:
            async with self._lock:
                for client in stale:
                    self._clients.pop(client, None)


hub = BrowserHub()
