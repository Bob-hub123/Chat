"""身份：昵称规范化、userId 生成、token 签发与校验。

token 形如 "<userId>.<hmac 前 16 位>"。签名密钥在**进程启动时随机生成**，
所以进程重启后所有旧 token 自动失效，用户会重新填一次昵称 ——
这正好符合"退出即清空"的产品语义，也省掉了密钥管理。
"""
import hmac
import secrets
from hashlib import sha256

from . import config
from .sanitize import strip_control

_SECRET = secrets.token_bytes(32)


def normalize_nick(raw):
    """昵称规范化。返回 (是否合法, 昵称, 错误码)。

    Python 的 len() 统计的是码点数（emoji 通常算 1 个字符），
    与前端 Array.from(nick).length 一致，不会出现"emoji 算 2 个字"的偏差。
    """
    if not isinstance(raw, str):
        return False, "", "BAD_TYPE"

    s = " ".join(strip_control(raw).split())   # 剥离控制字符 + 合并连续空白 + 去首尾
    if not s:
        return False, "", "EMPTY"
    if len(s) < config.NICK_MIN_LEN:
        return False, "", "TOO_SHORT"
    if len(s) > config.NICK_MAX_LEN:
        return False, "", "TOO_LONG"
    return True, s, ""


def gen_user_id() -> str:
    return "u_" + secrets.token_hex(4)


def _sign(user_id: str) -> str:
    return hmac.new(_SECRET, user_id.encode("utf-8"), sha256).hexdigest()[:16]


def make_token(user_id: str) -> str:
    return f"{user_id}.{_sign(user_id)}"


def verify_token(token) -> str | None:
    """校验 token，通过则返回 userId，否则 None。"""
    if not isinstance(token, str):
        return None
    user_id, sep, sig = token.rpartition(".")
    if not sep or not user_id or not sig:
        return None
    if not hmac.compare_digest(_sign(user_id), sig):
        return None
    return user_id
