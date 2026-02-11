import asyncio
import json
from typing import Any, Callable, Optional

import websockets


class DerivAPIError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class DerivWS:
    def __init__(self, app_id: str):
        self.app_id = app_id
        self.uri = f"wss://ws.derivws.com/websockets/v3?app_id={app_id}"
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._req_id = 1
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self.ws = await websockets.connect(self.uri, ping_interval=None)

    async def close(self) -> None:
        if self.ws:
            await self.ws.close()
            self.ws = None

    async def request(self, payload: dict) -> dict:
        if not self.ws:
            raise RuntimeError("WebSocket not connected")
        async with self._lock:
            req_id = self._req_id
            self._req_id += 1
            payload = dict(payload)
            payload["req_id"] = req_id
            await self.ws.send(json.dumps(payload))
            while True:
                raw = await self.ws.recv()
                msg = json.loads(raw)
                if msg.get("req_id") != req_id:
                    continue
                if "error" in msg:
                    err = msg["error"]
                    raise DerivAPIError(err.get("code", "error"), err.get("message", ""))
                return msg

    async def subscribe_until(self, payload: dict, stop_predicate: Callable[[dict], bool]) -> dict:
        if not self.ws:
            raise RuntimeError("WebSocket not connected")
        async with self._lock:
            req_id = self._req_id
            self._req_id += 1
            payload = dict(payload)
            payload["subscribe"] = 1
            payload["req_id"] = req_id
            await self.ws.send(json.dumps(payload))
            last_msg = None
            subscription_id = None
            while True:
                raw = await self.ws.recv()
                msg = json.loads(raw)
                if msg.get("req_id") != req_id:
                    continue
                if "error" in msg:
                    err = msg["error"]
                    raise DerivAPIError(err.get("code", "error"), err.get("message", ""))
                last_msg = msg
                sub = msg.get("subscription")
                if sub and sub.get("id"):
                    subscription_id = sub.get("id")
                if stop_predicate(msg):
                    break
            if subscription_id:
                try:
                    await self.request({"forget": subscription_id})
                except Exception:
                    pass
            return last_msg

    async def ping_loop(self, interval_sec: int = 20) -> None:
        if not self.ws:
            return
        while True:
            try:
                await self.request({"ping": 1})
            except Exception:
                return
            await asyncio.sleep(interval_sec)
