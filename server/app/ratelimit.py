"""令牌桶限流。每个连接持有独立的一个桶，用于防刷屏。

容量 5 / 每秒补充 1：允许一口气连发 5 条，之后稳定在 1 条/秒，
既不影响正常对话节奏，也能挡住脚本刷屏。
"""
import time


class TokenBucket:
    __slots__ = ("capacity", "refill", "_tokens", "_last")

    def __init__(self, capacity: int, refill: float):
        self.capacity = float(capacity)
        self.refill = float(refill)
        self._tokens = float(capacity)
        self._last = time.monotonic()

    def take(self, n: int = 1) -> bool:
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.refill)
        self._last = now
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

    def retry_after(self) -> float:
        """距下一次可取令牌还需多少秒（用于 err 提示）。"""
        if self.refill <= 0:
            return 0.0
        return max(0.0, (1.0 - self._tokens) / self.refill)
