"""★ 核心：连接生命周期、进出房、广播、心跳、进出提示抑制。

50 人档的有意简化（见方案文档「50 人档简化清单」）：
  - 广播直接 `asyncio.gather` 全量 fan-out，**不做** 100ms 攒批
  - presence 变化**立即广播**，不做 1s 节流
  - 重连**不下发增量 sync**，直接给完整 welcome（200 条 ≈ 30KB，一次发完更可靠）

两个必须绕开的坑：
  1. 不要用 `wait_for(ws.receive_text())` 驱动心跳 —— 超时取消会打断底层接收队列，
     可能丢消息。改为「主循环阻塞 receive + 独立心跳任务」。
  2. 多个协程并发 `ws.send_text()` 会破坏 WebSocket 帧。所有发送统一走 `Conn.send_lock`。
"""
import asyncio
import json
import secrets
import time

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from . import config, logger, message
from .identity import gen_user_id, make_token, normalize_nick, verify_token
from .protocol import parse
from .ratelimit import TokenBucket
from .room import Room
from .sanitize import clean_text

NICK_ERR = {
    "BAD_TYPE": "昵称格式不正确",
    "EMPTY": "昵称不能为空",
    "TOO_SHORT": f"昵称至少 {config.NICK_MIN_LEN} 个字符",
    "TOO_LONG": f"昵称最多 {config.NICK_MAX_LEN} 个字符",
}


class Conn:
    __slots__ = ("id", "ws", "ip", "user_id", "nick", "bucket",
                 "last_seen", "closed", "send_lock", "hb_task")

    def __init__(self, conn_id: str, ws: WebSocket, ip: str):
        self.id = conn_id
        self.ws = ws
        self.ip = ip
        self.user_id = ""
        self.nick = ""
        self.bucket = TokenBucket(config.RATE_CAPACITY, config.RATE_REFILL)
        self.last_seen = time.monotonic()
        self.closed = False
        self.send_lock = asyncio.Lock()
        self.hb_task: asyncio.Task | None = None


class Hub:
    def __init__(self):
        self.room = Room()
        self.conns: dict[str, Conn] = {}
        self._leave_tasks: dict[str, asyncio.Task] = {}
        self._known: dict[str, dict] = {}    # userId -> {nick, seen}，用于重连恢复昵称
        self._gone_at: dict[str, float] = {}  # userId -> 真正离开的时刻（抑制窗口用）
        self._started = time.time()

    # ================= 连接入口 =================
    async def handle(self, ws: WebSocket, ip: str = "") -> None:
        if len(self.conns) >= config.MAX_CONNECTIONS:
            await self._safe_close(ws, 1013, "room full")
            logger.warn("reject_full", total=len(self.conns))
            return
        if self._count_ip(ip) >= config.MAX_CONN_PER_IP:
            await self._safe_close(ws, 1013, "too many connections")
            logger.warn("reject_ip", ip=logger.ip_hash(ip))
            return

        conn = Conn(secrets.token_hex(6), ws, ip)
        self.conns[conn.id] = conn
        logger.info("conn_open", cid=conn.id, ip=logger.ip_hash(ip), total=len(self.conns))
        try:
            await self._handshake(conn)
            if conn.user_id:
                conn.hb_task = asyncio.create_task(self._heartbeat(conn))
                await self._main_loop(conn)
        except WebSocketDisconnect:
            pass
        except Exception as exc:                       # noqa: BLE001
            logger.warn("conn_error", cid=conn.id, err=type(exc).__name__)
        finally:
            await self._teardown(conn)

    # ================= 握手 =================
    async def _handshake(self, conn: Conn) -> None:
        """等待第一条 hello/join。超时未完成则断开（防空连接占位）。"""
        deadline = time.monotonic() + config.HANDSHAKE_TIMEOUT
        while conn.user_id == "":
            try:
                raw = await asyncio.wait_for(
                    conn.ws.receive_text(), timeout=max(0.5, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                logger.info("handshake_timeout", cid=conn.id)
                return

            conn.last_seen = time.monotonic()
            obj = parse(raw)
            if not obj:
                continue

            if obj["t"] == "hello":
                user_id = verify_token(obj.get("token"))
                if not user_id:
                    await self._send(conn, message.err("BAD_TOKEN", "身份已失效，请重新进入"))
                    continue
                known = self._known.get(user_id, {}).get("nick") or obj.get("nick") or ""
                ok, nick, code = normalize_nick(known)
                if not ok:
                    await self._send(conn, message.err("BAD_TOKEN", "身份已失效，请重新进入"))
                    continue
                await self._enter(conn, user_id, nick)

            elif obj["t"] == "join":
                ok, nick, code = normalize_nick(obj.get("nick"))
                if not ok:
                    await self._send(conn, message.err("BAD_NICK", NICK_ERR.get(code, "昵称不合法")))
                    continue
                await self._enter(conn, gen_user_id(), nick)

    async def _enter(self, conn: Conn, user_id: str, nick: str) -> None:
        conn.user_id = user_id
        conn.nick = nick
        self._known[user_id] = {"nick": nick, "seen": time.time()}
        self._cancel_leave_task(user_id)          # 刷新页面时取消待执行的离开广播

        # 抑制窗口：60s 内重连（刷新/切后台回来）不重复广播进入提示
        fresh = (time.time() - self._gone_at.get(user_id, 0)) >= config.JOIN_SUPPRESS_WINDOW
        self.room.join(user_id, nick, conn.id)

        await self._send(conn, message.welcome(
            user_id=user_id, token=make_token(user_id), nick=nick,
            members=self.room.member_list(), online=self.room.online,
            seq=self.room.seq, history=self.room.history.snapshot()))

        if fresh:
            await self._sys(user_id, f"{nick} 加入了聊天室", "你加入了聊天室")
        await self._broadcast_presence()
        logger.info("join", uid=user_id, nick_len=len(nick), suppressed=not fresh)

    # ================= 主循环 =================
    async def _main_loop(self, conn: Conn) -> None:
        while not conn.closed:
            try:
                raw = await conn.ws.receive_text()
            except WebSocketDisconnect:
                return
            conn.last_seen = time.monotonic()

            obj = parse(raw)
            if not obj:
                continue
            t = obj["t"]
            if t == "msg":
                await self._on_msg(conn, obj)
            elif t == "ping":
                await self._send(conn, message.pong(obj.get("ts")))
            elif t == "rename":
                await self._on_rename(conn, obj)
            # "pong" 无需处理：last_seen 已在上面刷新

    async def _on_msg(self, conn: Conn, obj: dict) -> None:
        cid = obj.get("cid")
        if not isinstance(cid, str) or not cid:
            return

        if not conn.bucket.take():
            await self._send(conn, message.err("RATE_LIMIT", "发送太频繁了", cid))
            logger.info("rate_limited", uid=conn.user_id)
            return

        ok, text, code = clean_text(obj.get("text"), config.MSG_MAX_LEN, keep_newline=True)
        if not ok:
            await self._send(conn, message.err("EMPTY_MSG", "消息不能为空", cid))
            return

        frame = message.chat(conn.user_id, conn.nick, text)
        seq = self.room.record(frame)
        # 排除发送者自己的连接：他已经在本地乐观上屏，靠 ack 确认，
        # 再回灌一次会造成重复渲染。同一用户的其他标签页仍会收到。
        await self._broadcast(frame, exclude=conn.id)
        await self._send(conn, message.ack(cid, seq))
        logger.info("msg", uid=conn.user_id, len=len(text), seq=seq,
                    truncated=(code == "TRUNCATED"))

    async def _on_rename(self, conn: Conn, obj: dict) -> None:
        ok, nick, code = normalize_nick(obj.get("nick"))
        if not ok:
            await self._send(conn, message.err("BAD_NICK", NICK_ERR.get(code, "昵称不合法")))
            return
        old = self.room.rename(conn.user_id, nick)
        conn.nick = nick
        self._known[conn.user_id] = {"nick": nick, "seen": time.time()}
        if old and old != nick:
            await self._sys(conn.user_id, f"{old} 改名为 {nick}", f"你改名为 {nick}")
        await self._broadcast_presence()
        logger.info("rename", uid=conn.user_id, nick_len=len(nick))

    # ================= 心跳 =================
    async def _heartbeat(self, conn: Conn) -> None:
        """每 20s 下发一次应用层 ping；超过 60s 没收到任何帧则判死。

        之所以用应用层 ping 而不是协议层 ping 帧：Starlette 的 WebSocket API
        不暴露 ping/pong 帧，浏览器端也收不到 pong。
        """
        try:
            while not conn.closed:
                await asyncio.sleep(config.SERVER_PING_INTERVAL)
                idle = time.monotonic() - conn.last_seen
                if idle > config.DEAD_TIMEOUT:
                    logger.info("conn_dead", cid=conn.id, idle=round(idle))
                    async with conn.send_lock:
                        conn.closed = True
                        await self._safe_close(conn.ws, 1001, "dead")
                    return
                await self._send(conn, message.ping())
        except asyncio.CancelledError:
            return

    # ================= 断开与离开 =================
    async def _teardown(self, conn: Conn) -> None:
        if conn.hb_task and not conn.hb_task.done():
            conn.hb_task.cancel()
        self.conns.pop(conn.id, None)
        conn.closed = True

        user_id = conn.user_id
        if not user_id:
            logger.info("conn_close_unauth", cid=conn.id, total=len(self.conns))
            return

        still_online = self.room.leave(user_id, conn.id)
        logger.info("conn_close", uid=user_id, total=len(self.conns))
        if still_online:
            return    # 同一用户还有其他标签页，不算离开
        self._schedule_leave(user_id, conn.nick)

    def _schedule_leave(self, user_id: str, nick: str) -> None:
        old = self._leave_tasks.pop(user_id, None)
        if old and not old.done():
            old.cancel()
        self._leave_tasks[user_id] = asyncio.create_task(self._leave_after_grace(user_id, nick))

    def _cancel_leave_task(self, user_id: str) -> None:
        task = self._leave_tasks.pop(user_id, None)
        if task and not task.done():
            task.cancel()

    async def _leave_after_grace(self, user_id: str, nick: str) -> None:
        """连接关闭后延迟 10s 再判定"真正离开"，给刷新页面留出重连时间。"""
        try:
            await asyncio.sleep(config.LEAVE_GRACE)
        except asyncio.CancelledError:
            return
        self._leave_tasks.pop(user_id, None)
        if user_id in self.room.members:
            return    # 期间已经重连，不是真离开
        self._gone_at[user_id] = time.time()
        await self._sys(user_id, f"{nick} 离开了聊天室", "你离开了聊天室")
        await self._broadcast_presence()
        logger.info("leave", uid=user_id)

    # ================= 发送 =================
    async def _send(self, conn: Conn, frame: dict) -> bool:
        return await self._raw(conn, json.dumps(frame, ensure_ascii=False, separators=(",", ":")))

    async def _raw(self, conn: Conn, data: str) -> bool:
        if conn.closed:
            return False
        async with conn.send_lock:
            try:
                await asyncio.wait_for(conn.ws.send_text(data), config.SEND_TIMEOUT)
                return True
            except Exception:                          # noqa: BLE001
                conn.closed = True
                return False

    async def _broadcast(self, frame: dict, exclude: str | None = None) -> None:
        targets = [c for c in self.conns.values()
                   if c.user_id and not c.closed and c.id != exclude]
        if not targets:
            return
        data = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        results = await asyncio.gather(*[self._raw(c, data) for c in targets],
                                       return_exceptions=True)
        for conn, res in zip(targets, results):
            if res is not True:
                await self._safe_close(conn.ws, 1011, "send failed")
                logger.info("kick_slow_client", cid=conn.id)

    async def _sys(self, user_id: str, text: str, self_text: str) -> None:
        frame = message.system(user_id, text, self_text)
        self.room.record(frame)           # 进出/改名记录也进历史，新用户能看到上下文
        await self._broadcast(frame)

    async def _broadcast_presence(self) -> None:
        await self._broadcast(message.presence(self.room.online, self.room.member_list()))

    # ================= 杂项 =================
    def _count_ip(self, ip: str) -> int:
        if not ip:
            return 0
        return sum(1 for c in self.conns.values() if c.ip == ip)

    @staticmethod
    async def _safe_close(ws: WebSocket, code: int, reason: str) -> None:
        try:
            await ws.close(code=code, reason=reason)
        except Exception:                              # noqa: BLE001
            pass

    @property
    def uptime(self) -> float:
        return round(time.time() - self._started, 1)


hub = Hub()          # 单房间，模块级单例
