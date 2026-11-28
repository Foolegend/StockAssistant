#!/usr/bin/env bash
# StockAssistant 一键启动：Bun 后端 + Angular 前端（Linux / macOS）
# 后端在后台运行，前端占用当前终端；Ctrl+C 会同时停止两者。

# 本脚本使用 bash 专有语法（[[ ]]、(( ))、/dev/tcp、BASH_SOURCE）。
# 被 sh/dash 调用时（例如 `sh start_all.sh`）自动切回 bash。
if [ -z "${BASH_VERSION:-}" ]; then
    exec bash "$0" "$@"
fi

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_PORT=8015
SERVER_PID=""

cleanup() {
    if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
        echo
        echo "🛑 正在停止后端服务 (PID: $SERVER_PID)..."
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

echo "▶️  启动后端..."
"$ROOT/stock-server/start_server.sh" &
SERVER_PID=$!

echo "⏳ 等待后端监听 127.0.0.1:$SERVER_PORT ..."
# 首次运行可能需要自动安装 Bun + 安装依赖，放宽等待时间到 5 分钟
WAIT_SECONDS=300
for i in $(seq 1 "$WAIT_SECONDS"); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$SERVER_PORT") 2>/dev/null; then
        echo "✅ 后端已就绪"
        break
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "❌ 后端启动失败，请查看上方日志" >&2
        wait "$SERVER_PID" 2>/dev/null || true
        SERVER_PID=""
        exit 1
    fi
    if (( i % 30 == 0 )); then
        echo "   …仍在等待（${i}s / ${WAIT_SECONDS}s），首次启动可能在安装 Bun 与依赖"
    fi
    sleep 1
done

if ! (exec 3<>"/dev/tcp/127.0.0.1/$SERVER_PORT") 2>/dev/null; then
    echo "❌ 等待后端启动超时（${WAIT_SECONDS}s）" >&2
    exit 1
fi

echo "▶️  启动前端..."
"$ROOT/stock-ui/start_ui.sh"
