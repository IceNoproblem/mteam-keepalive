#!/bin/bash
# mteam-keepalive · 一键安装
#
# 做四件事：
#   1. 检查系统依赖（node / 浏览器）
#   2. 下载私有 node 运行时 + puppeteer-core（不依赖任何全局安装）
#   3. 生成 config.json（若不存在）
#   4. 安装并加载 launchd 定时任务
#
# 用法:./install.sh
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE"

LABEL="com.keepalive.mteam.visit"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

say "1/5 检查系统依赖"

command -v curl >/dev/null || die "需要 curl"
say "curl: ok"

if ! command -v node >/dev/null; then
    warn "系统未找到 node，将下载私有运行时到 runtime/node"
else
    say "node: $(node -v) (系统)"
fi

BROWSER_FOUND=0
for p in "/Applications/Microsoft Edge.app" "/Applications/Google Chrome.app" \
         "$HOME/Applications/Microsoft Edge.app" "$HOME/Applications/Google Chrome.app"; do
    if [ -d "$p" ]; then
        say "浏览器: $p"
        BROWSER_FOUND=1
        break
    fi
done
[ "$BROWSER_FOUND" = "1" ] || die "未找到 Edge 或 Chrome，请先安装其一"

PY="/usr/bin/python3"
[ -x "$PY" ] || die "未找到 /usr/bin/python3"

say "2/5 准备 node 运行时与 puppeteer-core"

mkdir -p runtime node_modules

if [ ! -x runtime/node ]; then
    say "下载 Node.js (darwin-arm64 / x64 自动判断)..."
    ARCH="$(uname -m)"
    case "$ARCH" in
        arm64) NARCH="arm64" ;;
        *)     NARCH="x64" ;;
    esac
    NVER="v22.12.0"
    TGZ="node-${NVER}-darwin-${NARCH}.tar.gz"
    URL="https://nodejs.org/dist/${NVER}/${TGZ}"
    TMP="$(mktemp -d)"
    curl -fsSL "$URL" -o "$TMP/$TGZ" || die "Node 下载失败：$URL"
    tar -xzf "$TMP/$TGZ" -C "$TMP"
    mv "$TMP/node-${NVER}-darwin-${NARCH}/bin/node" runtime/node
    chmod +x runtime/node
    rm -rf "$TMP"
    say "node 运行时就绪: $(runtime/node -v)"
else
    say "node 运行时已存在: $(runtime/node -v)"
fi

if [ ! -d node_modules/puppeteer-core ]; then
    say "安装 puppeteer-core（仅驱动本机浏览器，不下载 Chromium）..."
    PKGJSON="$BASE/package.json"
    if [ ! -f "$PKGJSON" ]; then
        cat > "$PKGJSON" <<'EOF'
{
  "name": "mteam-keepalive",
  "version": "1.0.0",
  "private": true,
  "description": "浏览器自动化 keepalive 任务驱动",
  "dependencies": {
    "puppeteer-core": "^23.0.0"
  }
}
EOF
    fi
    if command -v npm >/dev/null; then
        npm install --omit=dev --no-audit --no-fund
    else
        say "系统无 npm，改用 runtime/npm 或请手动安装 puppeteer-core"
        die "请安装 Node.js（含 npm）后重跑本脚本"
    fi
    say "puppeteer-core 就绪"
else
    say "puppeteer-core 已存在"
fi

say "3/5 准备配置"

if [ ! -f config.json ]; then
    cp config.example.json config.json
    chmod 600 config.json
    say "已生成 config.json（权限 600），请编辑填入站点账号与邮箱授权码"
else
    say "config.json 已存在，保持不变"
fi

say "4/5 安装 launchd 定时任务"

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s#__BASE__#$BASE#g" -e "s#__LABEL__#$LABEL#g" \
    "$(dirname "$0")/assets/com.keepalive.mteam.visit.plist.in" > "$PLIST"

# 先卸载旧的（若存在），避免重复加载报错
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL" 2>/dev/null || true

say "定时任务已加载: $LABEL"
launchctl list | grep -F "$LABEL" >/dev/null \
    && say "launchctl list 可见，任务运行中" \
    || warn "launchctl list 未看到该任务，可稍后重试或手动 kickstart"

say "5/5 完成"
cat <<EOF

下一步：
  1) 编辑配置：$BASE/config.json
     - site_user / site_pass      站点账号密码
     - mail_user / mail_auth_code 邮箱与授权码（授权码非登录密码）
     - proxy                      需要代理才填，否则留空
  2) 先在日常 Edge/Chrome 里登录一次站点（用于免验证码同步）
     $BASE/sync_auth.py
  3) 手动跑一次验证：
     $BASE/run_task.sh
     tail -60 $BASE/logs/launchd.log

常用运维命令：
  立即触发:  launchctl kickstart gui/$(id -u)/$LABEL
  查看状态:  launchctl list | grep keepalive
  卸载任务:  $BASE/uninstall.sh

EOF