#!/usr/bin/env bash
# 指数走势技能的脚本启动器：自动挑选一个依赖齐全的 Python。
#
# 用法：
#   bash ~/.claude/skills/index-trend-chart/scripts/run.sh fetch_index_data.py --index 上证指数
#   bash ~/.claude/skills/index-trend-chart/scripts/run.sh build_index_chart.py --csv ... --name 上证指数
#
# 依赖：akshare、pandas、matplotlib、openpyxl
# 若都不可用，会提示安装命令（可用 PYTHON_BIN 指定解释器）。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${1:-}"
if [ -z "$SCRIPT" ]; then
  echo "用法: run.sh <脚本名> [参数...]" >&2
  exit 2
fi
shift

CANDIDATES=(
  "${PYTHON_BIN:-}"
  "python3"
  "python"
  "${HOME}/.venv/bin/python"
  "${HOME}/.local/bin/python"
  "${HOME}/github/StockAssistant/.venv/bin/python"
)

for cand in "${CANDIDATES[@]}"; do
  [ -z "${cand}" ] && continue
  if command -v "${cand}" >/dev/null 2>&1 || [ -x "${cand}" ]; then
    if "${cand}" -c "import akshare, pandas, matplotlib, openpyxl" >/dev/null 2>&1; then
      exec "${cand}" "${DIR}/${SCRIPT}" "$@"
    fi
  fi
done

echo "找不到依赖齐全的 Python（需要 akshare / pandas / matplotlib / openpyxl）。" >&2
echo "请安装：python3 -m pip install --user akshare matplotlib openpyxl" >&2
echo "或用 PYTHON_BIN=/path/to/python 指定解释器后重试。" >&2
exit 1
