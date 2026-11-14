#!/usr/bin/env bash
# StockAssistant 前端启动脚本（Linux / macOS）
set -euo pipefail

PORT=4200

# 1. 进入前端项目根目录
FRONT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$FRONT_ROOT"

# 2. 检查运行环境
if ! command -v npm >/dev/null 2>&1; then
    echo "❌ 未找到 npm，请先安装 Node.js + npm：" >&2
    echo "   Ubuntu: sudo apt-get install -y nodejs npm" >&2
    exit 1
fi

# 3. 检查并安装/同步依赖
#    - node_modules 不存在：全新安装（有 lock 文件时用 npm ci 保证一致）
#    - package.json / package-lock.json 比 node_modules 中的记录更新：增量同步
MARKER="node_modules/.package-lock.json"
NEED_INSTALL=0
REASON=""

if [[ ! -d node_modules ]]; then
    NEED_INSTALL=1
    REASON="未检测到 node_modules"
elif [[ ! -f "$MARKER" ]]; then
    NEED_INSTALL=1
    REASON="node_modules 不完整"
elif [[ -f package-lock.json && package-lock.json -nt "$MARKER" ]]; then
    NEED_INSTALL=1
    REASON="package-lock.json 有更新"
elif [[ -f package.json && package.json -nt "$MARKER" ]]; then
    NEED_INSTALL=1
    REASON="package.json 有更新"
fi

if (( NEED_INSTALL )); then
    echo "📦 $REASON，正在安装前端依赖..."
    if [[ ! -d node_modules && -f package-lock.json ]]; then
        install_cmd=(npm ci --no-audit --no-fund)
    else
        install_cmd=(npm install --no-audit --no-fund)
    fi
    if ! "${install_cmd[@]}"; then
        echo "❌ 前端依赖安装失败，请检查网络或手动执行 npm install" >&2
        exit 1
    fi
fi

# 4. 检查并释放端口
free_port() {
    local port="$1" pids=""
    if command -v lsof >/dev/null 2>&1; then
        pids="$(lsof -ti "tcp:$port" 2>/dev/null || true)"
    elif command -v fuser >/dev/null 2>&1; then
        pids="$(fuser "$port/tcp" 2>/dev/null || true)"
    elif command -v ss >/dev/null 2>&1; then
        pids="$(ss -lptnH "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u || true)"
    fi
    if [[ -n "$pids" ]]; then
        echo "⚠️  停止占用端口 $port 的进程 (PID: $pids)"
        # shellcheck disable=SC2086
        kill -9 $pids 2>/dev/null || true
        sleep 1
    fi
}

echo "🔍 检查并清理端口 $PORT..."
free_port "$PORT"

# 5. 启动 Angular 开发服务器
echo "--------------------------------------------------"
echo "🚀 正在启动 StockAssistant 前端界面..."
echo "访问地址: http://localhost:$PORT"
echo "--------------------------------------------------"

exec npx ng serve --host 0.0.0.0 --port "$PORT"
