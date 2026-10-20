"""消息环形缓冲（最近 N 条）。

纯内存 `deque(maxlen=N)`，**进程退出即全部清空** —— 这是预期行为，不是缺陷。
若将来要持久化，只需把 append 换成"写 deque + 异步落 SQLite"，接口不变。
"""
from collections import deque

from . import config


class RingBuffer:
    def __init__(self, maxlen: int | None = None):
        self._buf: deque[dict] = deque(maxlen=maxlen or config.HISTORY_SIZE)

    def append(self, item: dict) -> None:
        self._buf.append(item)

    def snapshot(self) -> list[dict]:
        return list(self._buf)

    def __len__(self) -> int:
        return len(self._buf)

    @property
    def maxlen(self) -> int | None:
        return self._buf.maxlen
