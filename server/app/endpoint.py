"""/ws 路由。

这里只做三件事：accept、取真实 IP、把连接交给 hub。业务逻辑全在 hub 里。
"""
from fastapi import APIRouter, WebSocket

from . import config
from .hub import hub

router = APIRouter()


def _client_ip(ws: WebSocket) -> str:
    """Nginx 反代下取 X-Forwarded-For 的第一个 IP；直连时取 socket 对端。"""
    xff = ws.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return ws.client.host if ws.client else ""


@router.websocket(config.WS_PATH)
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    await hub.handle(ws, ip=_client_ip(ws))
