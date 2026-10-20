"""客户端帧解析与基础校验。

只做"能不能用"的判断（是否合法 JSON、是否对象、是否带 t 字段、是否超长），
业务字段的校验交给 hub 各分支。任何不合法都返回 None，由调用方忽略或断开。
"""
import json

from . import config


def parse(raw: str) -> dict | None:
    if not isinstance(raw, str):
        return None
    if len(raw) > config.MAX_FRAME_BYTES:
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    if not isinstance(obj.get("t"), str):
        return None
    return obj
