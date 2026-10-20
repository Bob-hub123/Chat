"""全局配置。

所有值都可以用环境变量覆盖，部署时通过 docker-compose 的 environment 传入。
并发目标 50 人，所以各项阈值只留 4 倍余量（防脚本刷连接），不做铺张的限制。
"""
import os


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


# ---- 服务 ----
HOST = os.getenv("HOST", "127.0.0.1")
PORT = _i("PORT", 3000)
WS_PATH = os.getenv("WS_PATH", "/ws")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ---- 连接上限 ----
MAX_CONNECTIONS = _i("MAX_CONNECTIONS", 200)   # 50 人 × 4 倍余量
MAX_CONN_PER_IP = _i("MAX_CONN_PER_IP", 20)
MAX_FRAME_BYTES = _i("MAX_FRAME_BYTES", 4096)  # 单帧上限，防超大 payload

# ---- 内容长度 ----
NICK_MIN_LEN = _i("NICK_MIN_LEN", 1)
NICK_MAX_LEN = _i("NICK_MAX_LEN", 16)
MSG_MAX_LEN = _i("MSG_MAX_LEN", 500)

# ---- 历史 ----
HISTORY_SIZE = _i("HISTORY_SIZE", 200)         # deque maxlen，进房全量下发

# ---- 心跳与超时（秒）----
SERVER_PING_INTERVAL = _f("SERVER_PING_INTERVAL", 20)   # 服务端下发 ping 的间隔
DEAD_TIMEOUT = _f("DEAD_TIMEOUT", 60)                   # 超过这么久没收到任何帧即判死
HANDSHAKE_TIMEOUT = _f("HANDSHAKE_TIMEOUT", 15)         # 连上后多久内必须完成 hello/join
LEAVE_GRACE = _f("LEAVE_GRACE", 10)                     # 断开后延迟判定"真正离开"
JOIN_SUPPRESS_WINDOW = _f("JOIN_SUPPRESS_WINDOW", 60)   # 该窗口内重连不再广播进入提示
SEND_TIMEOUT = _f("SEND_TIMEOUT", 5)                    # 单条发送超时（慢客户端保护）

# ---- 限流 ----
RATE_CAPACITY = _i("RATE_CAPACITY", 5)         # 令牌桶容量（允许连发的条数）
RATE_REFILL = _f("RATE_REFILL", 1)             # 每秒补充的令牌数
