"""应用组装：路由挂载 + 静态首页 + 启动/停止日志。

运行方式（在 server/ 目录下）：
    uvicorn app.main:app --host 0.0.0.0 --port 3000 --ws websockets

WebSocket 场景**必须单 worker**：成员表与历史都在进程内存里，多 worker 会导致
同一房间的用户看不到彼此（除非用 Nginx ip_hash 做会话粘性）。
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse

from . import config, endpoint, health, logger
from .hub import hub

WEB_INDEX = Path(__file__).resolve().parent.parent.parent / "web" / "index.html"

logger.setup()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("server_start", host=config.HOST, port=config.PORT,
                max_conn=config.MAX_CONNECTIONS, ws_path=config.WS_PATH)
    yield
    logger.info("server_stop", uptime=hub.uptime)


app = FastAPI(title="Chat Platform", version="0.1.0",
              docs_url=None, redoc_url=None, lifespan=lifespan)

app.include_router(health.router)
app.include_router(endpoint.router)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_INDEX, media_type="text/html; charset=utf-8")


@app.get("/favicon.ico")
async def favicon() -> Response:
    """避免浏览器每次都来要一次 favicon 打 404 噪音。"""
    return Response(status_code=204)
