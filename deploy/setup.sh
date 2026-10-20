#!/usr/bin/env bash
# 服务器上执行的一键初始化脚本（Ubuntu 20.04 / 22.04 / 24.04）
#
# 前置：代码已经放到 /opt/chat（server/ 与 web/ 两个目录）
# 用法：sudo bash /opt/chat/deploy/setup.sh
#
# 幂等：重复执行不会破坏已有配置，只会刷新依赖与重启服务。

set -euo pipefail

APP_DIR=/opt/chat
VENV="$APP_DIR/server/.venv"
CONF_SRC="$APP_DIR/deploy"
NGINX_CONF=/etc/nginx/conf.d/chat.conf
SERVICE=/etc/systemd/system/chat.service
LISTEN_PORT=8080

info() { echo -e "\033[32m>>>\033[0m $*"; }
warn() { echo -e "\033[33m!!\033[0m  $*"; }
die()  { echo -e "\033[31m✗\033[0m   $*"; exit 1; }

# ---------- 0. 前置检查 ----------
[ "$(id -u)" -eq 0 ] || die "请用 root 执行：sudo bash $0"
[ -d "$APP_DIR/server" ] || die "找不到 $APP_DIR/server，先把代码上传到 /opt/chat"
[ -d "$APP_DIR/web" ]    || die "找不到 $APP_DIR/web，先把代码上传到 /opt/chat"

PY=python3
command -v $PY >/dev/null || die "系统没有 python3"

PY_VER=$($PY -c 'import sys; print("%d.%d" % sys.version_info[:2])')
PY_MAJOR=$($PY -c 'import sys; print(sys.version_info[0])')
PY_MINOR=$($PY -c 'import sys; print(sys.version_info[1])')
info "Python 版本：$PY_VER"
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]; }; then
    die "需要 Python >= 3.9（websockets 14 的硬性要求）。
    Ubuntu 20.04 自带 3.8，请升级：
      sudo add-apt-repository ppa:deadsnakes/ppa -y
      sudo apt install -y python3.11 python3.11-venv
      然后改本脚本开头的 PY=python3.11 再跑一次"
fi

# ---------- 0.5 换行符自愈 ----------
# 这些文件若在 Windows 上被编辑过再上传会变成 CRLF，bash 执行时报错
# "$'\r': command not found"（shebang 行还会变成 bad interpreter）。
# 统一转成 LF，对已经是 LF 的文件无副作用。
find "$APP_DIR/deploy" -maxdepth 1 -type f \
     \( -name '*.sh' -o -name '*.conf' -o -name '*.service' \) \
     -exec sed -i 's/\r$//' {} + 2>/dev/null || true

# ---------- 1. 系统依赖 ----------
info "安装系统依赖（nginx / python3-venv / pip）..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx "$PY-venv" "$PY-pip" curl >/dev/null
info "系统依赖就绪"

# ---------- 2. 专用用户 ----------
if ! id -u chat >/dev/null 2>&1; then
    info "创建系统用户 chat（不允许登录）..."
    useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin chat
else
    info "用户 chat 已存在，跳过"
fi

# ---------- 3. 虚拟环境 + 依赖 ----------
if [ ! -d "$VENV" ]; then
    info "创建虚拟环境 $VENV ..."
    "$PY" -m venv "$VENV"
fi
info "安装 Python 依赖..."
"$VENV/bin/python" -m pip install --upgrade pip -q
"$VENV/bin/pip" install -q -r "$APP_DIR/server/requirements.txt"

# uvicorn[standard] 带 uvloop/httptools（Rust 扩展）。服务器若无编译环境会失败，
# 这里自动回退到纯 Python 版，保证服务能起来。
if ! "$VENV/bin/python" -c "import uvloop" 2>/dev/null; then
    warn "uvloop 不可用，uvicorn 将用 asyncio 事件循环（50 人规模无影响）"
fi
info "依赖就绪：$("$VENV/bin/python" -c 'import fastapi,uvicorn,websockets;print("fastapi+uvicorn+websockets OK")')"

# ---------- 4. Nginx ----------
info "部署 nginx 配置..."
if [ -f "$NGINX_CONF" ]; then
    cp "$NGINX_CONF" "$NGINX_CONF.bak.$(date +%s)"
    warn "已备份旧配置"
fi
cp "$CONF_SRC/nginx.conf" "$NGINX_CONF"
# 去掉 Ubuntu 默认站点对 80 的占用（我们只用 8080，留着也无妨）
rm -f /etc/nginx/sites-enabled/default
nginx -t || die "nginx 配置校验失败，请看上面的报错"

# ---------- 5. systemd ----------
info "部署 systemd 服务..."
cp "$CONF_SRC/chat.service" "$SERVICE"

# ---------- 6. 权限 ----------
info "修正文件权限..."
chown -R chat:chat "$APP_DIR"
chmod -R u+rwX,go=rX "$APP_DIR"

# ---------- 7. 启动 ----------
systemctl daemon-reload
systemctl enable chat nginx >/dev/null
systemctl restart nginx
systemctl restart chat

# ---------- 8. 防火墙（如果开了 ufw）----------
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
    info "ufw 放行 $LISTEN_PORT ..."
    ufw allow "$LISTEN_PORT/tcp" comment 'chat platform' >/dev/null || true
fi

# ---------- 9. 自检 ----------
sleep 2
echo ""
info "========== 自检 =========="
if systemctl is-active --quiet chat; then
    echo "  服务状态      : $(systemctl is-active chat)"
else
    warn "服务未运行，看日志：journalctl -u chat -n 50 --no-pager"
fi

HEALTH=$(curl -s --max-time 5 http://127.0.0.1:3000/healthz || echo "")
if [ -n "$HEALTH" ]; then
    echo "  应用直连 healthz : $HEALTH"
else
    warn "直连 3000 端口无响应，检查：journalctl -u chat -n 50"
fi

PROXY=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:$LISTEN_PORT/ || echo "000")
echo "  经 nginx($LISTEN_PORT) : HTTP $PROXY"

echo ""
info "完成。访问地址： http://$(curl -s --max-time 3 ifconfig.me 2>/dev/null || echo '<服务器公网IP>'):$LISTEN_PORT"
warn "别忘了去云控制台的安全组放行 $LISTEN_PORT 端口，否则外网连不上"
info "常用命令：
  journalctl -u chat -f                 # 实时看日志
  systemctl restart chat                # 重启服务
  bash $APP_DIR/deploy/update.sh        # 更新代码"
