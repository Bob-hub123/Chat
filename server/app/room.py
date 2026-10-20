"""房间：seq 分配 + 历史缓冲 + 成员表。

v1 只有一个公共房（lobby），但类本身支持多实例，将来加房间/频道不用改结构。
"""
import time
from dataclasses import dataclass, field

from . import config
from .ringbuf import RingBuffer


@dataclass
class Member:
    nick: str
    conns: set[str] = field(default_factory=set)     # 同一用户可能开多个标签页
    last_seen: float = field(default_factory=time.time)


class Room:
    def __init__(self, name: str = "lobby", history_size: int | None = None):
        self.name = name
        self._seq = 0
        self.history = RingBuffer(history_size or config.HISTORY_SIZE)
        self.members: dict[str, Member] = {}

    # ---------- seq ----------
    @property
    def seq(self) -> int:
        return self._seq

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def record(self, frame: dict) -> int:
        """分配 seq、补齐 ts/kind，写入历史缓冲。返回 seq。"""
        seq = self.next_seq()
        frame["seq"] = seq
        frame.setdefault("ts", int(time.time() * 1000))
        frame.setdefault("kind", "chat")
        self.history.append(frame)
        return seq

    # ---------- 成员 ----------
    def join(self, user_id: str, nick: str, conn_id: str) -> None:
        m = self.members.get(user_id)
        if m is None:
            m = Member(nick=nick)
            self.members[user_id] = m
        else:
            m.nick = nick
        m.conns.add(conn_id)
        m.last_seen = time.time()

    def leave(self, user_id: str, conn_id: str) -> bool:
        """移除一条连接。返回该 userId 是否还有剩余连接。"""
        m = self.members.get(user_id)
        if m is None:
            return False
        m.conns.discard(conn_id)
        m.last_seen = time.time()
        if not m.conns:
            del self.members[user_id]
            return False
        return True

    def rename(self, user_id: str, nick: str) -> str:
        """改名，返回旧昵称（用于生成系统提示）。"""
        m = self.members.get(user_id)
        old = m.nick if m else ""
        if m:
            m.nick = nick
        return old

    def nick_of(self, user_id: str) -> str:
        m = self.members.get(user_id)
        return m.nick if m else ""

    def member_list(self) -> list[dict]:
        return [{"uid": uid, "nick": m.nick} for uid, m in self.members.items()]

    @property
    def online(self) -> int:
        return len(self.members)
