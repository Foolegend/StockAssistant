#!/usr/bin/env bash
# StockAssistant 后端启动脚本（Linux / macOS）
#
# 启动 Bun 编写的 HTTP/SSE API 服务，监听 127.0.0.1:8015。
# 智能分析由 stock-server/src 中的 StockAssistant CLI 实现。
set -euo pipefail

BACK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${INVEST_SERVER_PORT:-8015}"
HOST="${INVEST_SERVER_HOST:-127.0.0.1}"

# --- Bun --------------------------------------------------------------------
if [[ -n "${INVESTAI_BUN:-}" && -x "${INVESTAI_BUN}" ]]; then
    BUN_EXE="${INVESTAI_BUN}"
elif command -v bun >/dev/null 2>&1; then
    BUN_EXE="$(command -v bun)"
elif [[ -x "$HOME/.bun/bin/bun" ]]; then
    BUN_EXE="$HOME/.bun/bin/bun"
else
    echo "❌ 未找到 Bun，请先安装：https://bun.sh" >&2
    exit 1
fi

# --- 依赖 -------------------------------------------------------------------
if [[ ! -d "$BACK_ROOT/node_modules" ]]; then
    echo "📦 未检测到依赖，正在执行 bun install ..."
    (cd "$BACK_ROOT" && "$BUN_EXE" install)
fi

# --- 端口 -------------------------------------------------------------------
if command -v lsof >/dev/null 2>&1 && lsof -ti "tcp:$PORT" >/dev/null 2>&1; then
    echo "⚠️  端口 $PORT 已被占用，正在尝试释放 ..." >&2
    lsof -ti "tcp:$PORT" | xargs -r kill -9 || true
    sleep 0.3
fi

export INVEST_SERVER_PORT="$PORT"
export INVEST_SERVER_HOST="$HOST"

cd "$BACK_ROOT"
echo "▶️  StockAssistant 后端启动中：http://$HOST:$PORT"
exec "$BUN_EXE" run terminal/server.ts
