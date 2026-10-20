"""服务端 → 客户端 的全部帧构造。

集中在一处，便于和 web/index.html 的 handleServer() 逐字段对齐。
"""
import time


def now_ms() -> int:
    return int(time.time() * 1000)


def chat(uid: str, nick: str, text: str) -> dict:
    return {"t": "chat", "uid": uid, "nick": nick, "text": text, "ts": now_ms()}


def system(uid: str, text: str, self_text: str) -> dict:
    """系统提示（进入 / 离开 / 改名）。

    text  = 其他人看到的："张三 加入了聊天室"
    self  = 当事人自己看到的："你加入了聊天室"
    客户端按 `uid === me.userId` 选一条渲染，避免出现"自己看到自己名字"的别扭感。
    """
    return {"t": "sys", "kind": "sys", "uid": uid, "text": text,
            "self": self_text, "ts": now_ms()}


def welcome(user_id: str, token: str, nick: str, members: list[dict],
            online: int, seq: int, history: list[dict]) -> dict:
    return {"t": "welcome", "userId": user_id, "token": token, "nick": nick,
            "members": members, "online": online, "seq": seq, "history": history}


def ack(cid: str, seq: int) -> dict:
    return {"t": "ack", "cid": cid, "seq": seq, "ts": now_ms()}


def presence(online: int, members: list[dict]) -> dict:
    return {"t": "presence", "online": online, "members": members}


def ping() -> dict:
    return {"t": "ping", "ts": now_ms()}


def pong(ts) -> dict:
    return {"t": "pong", "ts": ts}


def err(code: str, msg: str, cid: str | None = None) -> dict:
    frame = {"t": "err", "code": code, "msg": msg}
    if cid:
        frame["cid"] = cid
    return frame
