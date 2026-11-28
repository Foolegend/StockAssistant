#!/usr/bin/env bash
# StockAssistant 后端启动脚本（Linux / macOS）
#
# 启动 Bun 编写的 HTTP/SSE API 服务，监听 127.0.0.1:8015。
# 智能分析由 stock-server/src 中的 StockAssistant CLI 实现。
#
# 若本机没有 Bun，脚本会自动下载安装到 ${BUN_INSTALL:-$HOME/.bun}。

# 本脚本使用 bash 专有语法（[[ ]]、数组、BASH_SOURCE）。
# 被 sh/dash 调用时（例如 `sh start_server.sh`）自动切回 bash。
if [ -z "${BASH_VERSION:-}" ]; then
    exec bash "$0" "$@"
fi

set -euo pipefail

BACK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${INVEST_SERVER_PORT:-8015}"
HOST="${INVEST_SERVER_HOST:-127.0.0.1}"
BUN_INSTALL_DIR="${BUN_INSTALL:-$HOME/.bun}"

# --- Bun --------------------------------------------------------------------

# 解析已存在的 bun 可执行文件路径，找不到则返回非 0
resolve_bun() {
    if [[ -n "${INVESTAI_BUN:-}" && -x "${INVESTAI_BUN}" ]]; then
        echo "$INVESTAI_BUN"
        return 0
    fi
    if command -v bun >/dev/null 2>&1; then
        command -v bun
        return 0
    fi
    if [[ -x "$BUN_INSTALL_DIR/bin/bun" ]]; then
        echo "$BUN_INSTALL_DIR/bin/bun"
        return 0
    fi
    return 1
}

# 下载并安装 Bun（无需 root，安装到用户目录）
#
# 安装顺序：
#   1. bun.sh 官方安装脚本（bun.sh 推荐方式）
#   2. GitHub 官方发布页的 zip 包
#   3. npmmirror 镜像的 zip 包（国内更快）
install_bun() {
    local os arch variant pkg tag tmpdir url archive bin_path

    case "$(uname -s)" in
        Linux)  os="linux" ;;
        Darwin) os="darwin" ;;
        *) echo "不支持的操作系统：$(uname -s)" >&2; return 1 ;;
    esac

    case "$(uname -m)" in
        x86_64|amd64)  arch="x64" ;;
        arm64|aarch64) arch="aarch64" ;;
        *) echo "不支持的 CPU 架构：$(uname -m)" >&2; return 1 ;;
    esac

    # 不支持 AVX2 的 x86 CPU 需要 baseline 版本
    variant=""
    if [[ "$arch" == "x64" ]] && ! grep -qw avx2 /proc/cpuinfo 2>/dev/null; then
        variant="-baseline"
    fi
    pkg="bun-${os}-${arch}${variant}"

    if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
        echo "缺少 curl / wget，无法自动安装 Bun" >&2
        return 1
    fi

    # 解析版本号：GitHub API -> npmmirror 目录列表 -> 固定版本兜底
    tag=""
    if command -v curl >/dev/null 2>&1; then
        tag="$(curl -fsSL --max-time 15 https://api.github.com/repos/oven-sh/bun/releases/latest 2>/dev/null \
               | sed -nE 's/.*"tag_name"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' | head -n1 || true)"
    else
        tag="$(wget -qO- --timeout=15 https://api.github.com/repos/oven-sh/bun/releases/latest 2>/dev/null \
               | sed -nE 's/.*"tag_name"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' | head -n1 || true)"
    fi
    if [[ -z "$tag" ]]; then
        tag="bun-v1.4.5"
        echo "   ℹ️  无法获取最新版本号，使用固定版本 $tag"
    else
        echo "   ℹ️  目标版本：$tag"
    fi

    # 1) 官方安装脚本
    if install_bun_official "$tag"; then
        return 0
    fi
    echo "   ↩️  官方安装脚本不可用，改为直接下载 zip 包 ..."

    # 2) / 3) 直接下载 zip 包
    tmpdir="$(mktemp -d)"
    archive="$tmpdir/bun.zip"

    local -a urls=(
        "https://github.com/oven-sh/bun/releases/download/${tag}/${pkg}.zip"
        "https://registry.npmmirror.com/-/binary/bun/${tag}/${pkg}.zip"
    )

    for url in "${urls[@]}"; do
        echo "   ⬇️  下载 ${pkg}.zip <- ${url%%/bun-*}..."
        if command -v curl >/dev/null 2>&1; then
            if [[ -t 2 ]]; then
                curl -fL --progress-bar --connect-timeout 15 --max-time 900 -o "$archive" "$url" && break
            else
                curl -fL -sS --connect-timeout 15 --max-time 900 -o "$archive" "$url" && break
            fi
        else
            if [[ -t 2 ]]; then
                wget --show-progress --timeout=15 -O "$archive" "$url" && break
            else
                wget -nv --timeout=15 -O "$archive" "$url" && break
            fi
        fi
        rm -f "$archive"
    done

    if [[ ! -s "$archive" ]]; then
        echo "Bun 下载失败，请手动安装：https://bun.sh" >&2
        rm -rf "$tmpdir"
        return 1
    fi

    # 解压
    if command -v unzip >/dev/null 2>&1; then
        unzip -o -q "$archive" -d "$tmpdir" || { rm -rf "$tmpdir"; return 1; }
    elif command -v python3 >/dev/null 2>&1; then
        python3 -c "import sys,zipfile;zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$archive" "$tmpdir" \
            || { rm -rf "$tmpdir"; return 1; }
    else
        echo "缺少 unzip / python3，无法解压 Bun" >&2
        rm -rf "$tmpdir"
        return 1
    fi

    bin_path="$(find "$tmpdir" -type f -name bun 2>/dev/null | head -n1)"
    if [[ -z "$bin_path" ]]; then
        echo "解压后未找到 bun 可执行文件" >&2
        rm -rf "$tmpdir"
        return 1
    fi

    mkdir -p "$BUN_INSTALL_DIR/bin"
    cp -f "$bin_path" "$BUN_INSTALL_DIR/bin/bun"
    chmod +x "$BUN_INSTALL_DIR/bin/bun"
    rm -rf "$tmpdir"

    "$BUN_INSTALL_DIR/bin/bun" --version >/dev/null 2>&1 || {
        echo "安装后的 Bun 无法执行" >&2
        return 1
    }
    return 0
}

# bun.sh 官方安装脚本：自动处理架构 / CPU 指令集差异
install_bun_official() {
    local tag="$1" tmpdir script
    tmpdir="$(mktemp -d)"
    script="$tmpdir/install.sh"

    echo "   ⬇️  获取官方安装脚本 https://bun.sh/install ..."
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --max-time 60 -o "$script" https://bun.sh/install || { rm -rf "$tmpdir"; return 1; }
    else
        wget -q --timeout=60 -O "$script" https://bun.sh/install || { rm -rf "$tmpdir"; return 1; }
    fi

    echo "   ⬇️  运行官方安装脚本（安装到 $BUN_INSTALL_DIR）..."
    if BUN_INSTALL="$BUN_INSTALL_DIR" bash "$script" "$tag" >/dev/null; then
        rm -rf "$tmpdir"
        [[ -x "$BUN_INSTALL_DIR/bin/bun" ]] && return 0
        return 1
    fi
    rm -rf "$tmpdir"
    return 1
}

BUN_EXE="$(resolve_bun || true)"
if [[ -z "$BUN_EXE" ]]; then
    echo "⚠️  未检测到 Bun，正在自动安装（无需 root）..."
    if install_bun; then
        BUN_EXE="$BUN_INSTALL_DIR/bin/bun"
        echo "✅ Bun 已安装到 $BUN_INSTALL_DIR/bin (版本 $("$BUN_EXE" --version))"
        echo "   提示：把 export PATH=\"$BUN_INSTALL_DIR/bin:\$PATH\" 加入 ~/.bashrc 可全局使用 bun"
    else
        echo "❌ Bun 自动安装失败，请手动安装：https://bun.sh" >&2
        exit 1
    fi
fi
export PATH="$(dirname "$BUN_EXE"):$PATH"

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
