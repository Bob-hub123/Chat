"""JSON 行日志。

约定：**只记元数据，绝不记消息正文**（连接数、userId、IP 哈希、消息长度）。
匿名聊天室记全文既无必要也有隐私风险。
"""
import hashlib
import json
import logging
import sys
import time

from . import config

_logger = logging.getLogger("chat")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {
            "ts": int(time.time() * 1000),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        kv = getattr(record, "kv", None)
        if isinstance(kv, dict):
            d.update(kv)
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, ensure_ascii=False)


def setup() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    _logger.handlers.clear()
    _logger.addHandler(handler)
    _logger.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    _logger.propagate = False


def emit(level: int, event: str, **kv) -> None:
    _logger.log(level, event, extra={"kv": kv})


def info(event: str, **kv) -> None:
    emit(logging.INFO, event, **kv)


def warn(event: str, **kv) -> None:
    emit(logging.WARNING, event, **kv)


def error(event: str, **kv) -> None:
    emit(logging.ERROR, event, **kv)


def ip_hash(ip: str | None) -> str:
    """IP 只留哈希前 8 位，日志中不出现真实 IP。"""
    return hashlib.sha256((ip or "").encode("utf-8")).hexdigest()[:8]
