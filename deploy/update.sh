#!/usr/bin/env bash
# 更新代码并重启服务（在服务器上执行，需 root）
#
# 用法：
#   sudo bash /opt/chat/deploy/update.sh              # 重启服务
#   sudo bash /opt/chat/deploy/update.sh --pull       # 先 git pull 拉最新代码
#   sudo bash /opt/chat/deploy/update.sh --pull --deps# 拉代码 + 重装依赖
#
# 注意：重启会清空所有在线成员与历史消息（本项目不持久化）。

set -euo pipefail

APP_DIR=/opt/chat
VENV="$APP_DIR/server/.venv"

info() { echo -e "\033[32m>>>\033[0m $*"; }

[ "$(id -u)" -eq 0 ] || { echo "请用 root 执行"; exit 1; }

# --pull : 先从 git 拉最新代码
# --deps : 重装 Python 依赖
PULL=0; DEPS=0
for arg in "$@"; do
    case "$arg" in
        --pull) PULL=1 ;;
        --deps) DEPS=1 ;;
        *) echo "未知参数：$arg（可用：--pull / --deps）"; exit 1 ;;
    esac
done

if [ "$PULL" -eq 1 ]; then
    info "拉取最新代码..."
    # /opt/chat 的属主是 chat，但这里用 root 执行 git，需标记为安全目录
    git config --global --add safe.directory "$APP_DIR"
    git -C "$APP_DIR" pull --ff-only
fi

if [ "$DEPS" -eq 1 ]; then
    info "更新 Python 依赖..."
    "$VENV/bin/pip" install -q -r "$APP_DIR/server/requirements.txt"
fi

info "修正权限..."
chown -R chat:chat "$APP_DIR"

info "同步 nginx 配置..."
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/conf.d/chat.conf
nginx -t

info "同步 systemd 配置..."
cp "$APP_DIR/deploy/chat.service" /etc/systemd/system/chat.service
systemctl daemon-reload

info "重启服务..."
systemctl reload nginx
systemctl restart chat
sleep 2

if systemctl is-active --quiet chat; then
    info "服务运行中：$(systemctl is-active chat)"
    curl -s --max-time 5 http://127.0.0.1:3000/healthz || true
    echo ""
else
    echo "服务未起来，看日志：journalctl -u chat -n 50 --no-pager"
    exit 1
fi
