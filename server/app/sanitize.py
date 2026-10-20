"""文本清洗（XSS 防护的第一层）。

策略：**存原文，不转义**。前端一律通过 `textContent` 渲染，因此服务端不需要
（也不应该）做 HTML 转义 —— 那会让用户看到字面量的 `&lt;script&gt;`。
服务端只负责：类型校验、控制字符剥离、长度截断、空白归一化。

三层纵深防御：
  1. 这里：剥离控制字符 / 零宽字符 / 双向文本控制符（可用于伪装昵称）
  2. 前端：`textContent` 渲染，永不 `innerHTML`
  3. CSP 响应头（Nginx 配）
"""
from . import config

# 控制字符 C0/C1 + 零宽 + 双向文本控制符 + BOM
_BAD_ORDS = (
    set(range(0x00, 0x20))
    | set(range(0x7F, 0xA0))
    | {
        0x200B, 0x200C, 0x200D, 0x200E, 0x200F,  # 零宽 / LRM / RLM
        0x202A, 0x202B, 0x202C, 0x202D, 0x202E,  # bidi 覆写
        0x2060,                                   # word joiner
        0xFEFF,                                   # BOM / 零宽不换行
    }
)
_KEEP_NEWLINE = {0x0A, 0x0D}

_TABLE_STRICT = {o: None for o in _BAD_ORDS}
_TABLE_KEEP_NL = {o: None for o in _BAD_ORDS if o not in _KEEP_NEWLINE}


def strip_control(s: str, keep_newline: bool = False) -> str:
    """剥离控制字符。keep_newline=True 时保留 \\n（聊天正文需要换行）。"""
    return s.translate(_TABLE_KEEP_NL if keep_newline else _TABLE_STRICT)


def clean_text(raw, max_len: int | None = None, keep_newline: bool = False):
    """清洗一段正文。返回 (是否可用, 清洗后文本, 附加码)。

    附加码可能是 "TRUNCATED"（被截断，但不是错误，照样发送）。
    """
    if max_len is None:
        max_len = config.MSG_MAX_LEN
    if not isinstance(raw, str):
        return False, "", "BAD_TYPE"

    s = strip_control(raw, keep_newline)
    if keep_newline:
        s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.strip()

    if not s:
        return False, "", "EMPTY"

    code = ""
    if len(s) > max_len:
        s = s[:max_len].rstrip()
        code = "TRUNCATED"
    return True, s, code
