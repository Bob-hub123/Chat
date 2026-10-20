"""/healthz 与 /metrics：给进程守护、手工排查、将来的监控用。

注意这里**不暴露任何用户数据**，只有聚合计数。
"""
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from .hub import hub

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    return {
        "ok": True,
        "online": hub.room.online,
        "conns": len(hub.conns),
        "seq": hub.room.seq,
        "uptime": hub.uptime,
    }


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> str:
    """极简文本指标。要接 Prometheus 时再换成 exposition 格式，接口不变。"""
    return (
        f"chat_online {hub.room.online}\n"
        f"chat_connections {len(hub.conns)}\n"
        f"chat_seq {hub.room.seq}\n"
        f"chat_uptime_seconds {hub.uptime}\n"
    )
