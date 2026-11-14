#!/usr/bin/env bash
# StockAssistant 后端启动脚本（Linux / macOS）
set -euo pipefail

# 统一编码，避免中文日志乱码
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

# 日志只写入 logs/stock-server.log，不在控制台刷屏
# 需要临时查看控制台日志时：STOCK_LOG_CONSOLE=1 ./start_server.sh
export STOCK_LOG_CONSOLE="${STOCK_LOG_CONSOLE:-0}"

PORT=8015
HOST=127.0.0.1

# 1. 脚本所在目录（stock-server）
BACK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 2. 保证 python 能找到 src 包
export PYTHONPATH="$BACK_ROOT"

# 3. Python 解释器：优先用 STOCK_PYTHON 指定的路径，其次项目根目录的 .venv
PROJECT_ROOT="$(dirname "$BACK_ROOT")"
REQ_FILE="$BACK_ROOT/requirements.txt"

if [[ -n "${STOCK_PYTHON:-}" ]]; then
    PYTHON_EXE="$STOCK_PYTHON"
    if [[ ! -x "$PYTHON_EXE" ]]; then
        echo "❌ STOCK_PYTHON 指定的解释器不可执行: $PYTHON_EXE" >&2
        exit 1
    fi
else
    PYTHON_EXE="$PROJECT_ROOT/.venv/bin/python"
    if [[ ! -x "$PYTHON_EXE" ]]; then
        echo "📦 未找到虚拟环境 $PROJECT_ROOT/.venv，正在创建..."
        if ! command -v python3 >/dev/null 2>&1; then
            echo "❌ 未找到 python3，请先安装 Python 3.12+（或用 STOCK_PYTHON 指定解释器）" >&2
            exit 1
        fi
        if ! python3 -m venv "$PROJECT_ROOT/.venv"; then
            echo "❌ 创建虚拟环境失败，请确认已安装 python3-venv" >&2
            exit 1
        fi
    fi
fi

# 4. 依赖缺失时根据 requirements.txt 自动安装
if ! "$PYTHON_EXE" -c "import uvicorn, fastapi, langchain, langgraph, deepagents" >/dev/null 2>&1; then
    if [[ ! -f "$REQ_FILE" ]]; then
        echo "❌ 当前 Python 环境缺少依赖，且未找到 $REQ_FILE" >&2
        exit 1
    fi
    echo "📦 检测到依赖缺失，正在安装 $REQ_FILE ..."
    if ! "$PYTHON_EXE" -m pip install -r "$REQ_FILE"; then
        echo "❌ 依赖安装失败，请检查网络或手动执行：" >&2
        echo "   $PYTHON_EXE -m pip install -r $REQ_FILE" >&2
        exit 1
    fi
fi

# 5. 检查并释放端口
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

# 6. 启动 Uvicorn 服务
echo "--------------------------------------------------"
echo "🚀 正在启动 StockAssistant 后端服务..."
echo "端口:     http://$HOST:$PORT"
echo "API 文档: http://$HOST:$PORT/docs"
echo "日志文件: $BACK_ROOT/logs/stock-server.log"
echo "--------------------------------------------------"

# --no-access-log: 请求日志由应用的请求中间件统一输出（带 request id 与耗时）
cd "$BACK_ROOT"
exec "$PYTHON_EXE" -m uvicorn src.api.chat:app \
    --host "$HOST" --port "$PORT" --no-access-log
