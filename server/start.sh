#!/usr/bin/env bash
# 一键启动本地开发服务器（Git Bash / Linux / macOS 通用）
#
# 用法：
#   ./start.sh              # 默认 127.0.0.1:3000
#   ./start.sh 0.0.0.0 8080 # 指定监听地址与端口（局域网其他设备可访问时用 0.0.0.0）
#
# 首次运行会自动创建 .venv 并安装依赖，之后直接复用。

set -euo pipefail

HOST="${1:-127.0.0.1}"
PORT="${2:-3000}"
PY="${PY:-py -3.13}"   # 若 py 启动器不可用，改成 PY=python3 或 PY=python

cd "$(dirname "$0")"

# 1) 虚拟环境不存在则创建
if [ ! -d .venv ]; then
  echo ">>> 创建虚拟环境 .venv ..."
  $PY -m venv .venv
fi

# 2) 激活
# shellcheck disable=SC1091
source .venv/Scripts/activate 2>/dev/null || source .venv/bin/activate

# 3) 依赖未装则安装
if ! python -c "import fastapi, uvicorn, websockets" 2>/dev/null; then
  echo ">>> 安装依赖 ..."
  python -m pip install --upgrade pip -q
  pip install -r requirements.txt
fi

# 4) 启动（WebSocket 场景必须单 worker，不要加 --workers）
echo ">>> 启动 http://$HOST:$PORT"
exec uvicorn app.main:app --host "$HOST" --port "$PORT" --ws websockets
