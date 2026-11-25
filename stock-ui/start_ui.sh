#!/usr/bin/env bash
# StockAssistant 前端启动脚本（Linux / macOS）
#
# Angular CLI 对 Node.js 有最低版本要求（读取 @angular/cli 的 engines.node）。
# 若当前 Node 版本不满足要求，脚本会自动下载安装一个兼容版本到
# ${STOCK_NODE_HOME:-$HOME/.local/share/stockassistant-node} 并优先使用它。
set -euo pipefail

PORT=4200
STOCK_NODE_HOME="${STOCK_NODE_HOME:-$HOME/.local/share/stockassistant-node}"

# 1. 进入前端项目根目录
FRONT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$FRONT_ROOT"

# --- Node.js 版本 ----------------------------------------------------------
# 引擎要求形如 "^22.22.3 || ^24.15.0 || >=26.0.0"
NODE_OPS=()
NODE_MINS=()
NODE_REQ=""

read_angular_node_requirements() {
    local pkg="$FRONT_ROOT/node_modules/@angular/cli/package.json" engines normalized part op min
    NODE_OPS=()
    NODE_MINS=()
    NODE_REQ=""
    [[ -f "$pkg" ]] || return 0
    # engines 字段可能跨行，先压成一行再解析
    engines="$(tr '\n' ' ' <"$pkg" \
        | sed -nE 's/.*"engines"[[:space:]]*:[[:space:]]*\{[^}]*"node"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' \
        | head -n1)"
    [[ -n "$engines" ]] || return 0
    normalized="${engines//||/ }"
    NODE_REQ="$engines"
    for part in $normalized; do
        case "$part" in
            '^'*) op="^"; min="${part#^}" ;;
            '>='*) op=">="; min="${part#>=}" ;;
            v*) op="^"; min="${part#v}" ;;
            *) continue ;;
        esac
        # 只保留形如 X.Y.Z 的最低版本
        [[ "$min" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
        NODE_OPS+=("$op")
        NODE_MINS+=("$min")
    done
}

# 版本比较：$1 >= $2 ?
version_ge() {
    [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" == "$2" ]]
}

# $1 是否满足 Angular 的 Node 版本要求
node_compatible() {
    local ver="$1" i op min
    [[ ${#NODE_MINS[@]} -gt 0 ]] || return 0
    for i in "${!NODE_MINS[@]}"; do
        op="${NODE_OPS[$i]}"
        min="${NODE_MINS[$i]}"
        case "$op" in
            '^')
                if [[ "${ver%%.*}" == "${min%%.*}" ]] && version_ge "$ver" "$min"; then
                    return 0
                fi
                ;;
            '>=')
                if version_ge "$ver" "$min"; then
                    return 0
                fi
                ;;
        esac
    done
    return 1
}

# 从 nodejs.org 版本列表里挑一个满足要求的版本（优先 LTS）
pick_node_version() {
    local index entry ver lts pass
    index="$(curl -fsSL --max-time 30 https://nodejs.org/dist/index.json 2>/dev/null || true)"
    if [[ -n "$index" ]]; then
        for pass in lts any; do
            while read -r entry; do
                ver="$(sed -nE 's/.*"version":"(v[0-9]+\.[0-9]+\.[0-9]+)".*/\1/p' <<<"$entry")"
                lts="$(sed -nE 's/.*"lts":("[^"]*"|true|false).*/\1/p' <<<"$entry")"
                [[ -n "$ver" ]] || continue
                [[ "$pass" == "lts" && "$lts" == "false" ]] && continue
                if node_compatible "${ver#v}"; then
                    echo "${ver#v}"
                    return 0
                fi
            done < <(printf '%s' "$index" | tr '}' '\n')
        done
    fi
    # 离线兜底
    for ver in 26.13.1 24.15.0 22.22.3; do
        if node_compatible "$ver"; then
            echo "$ver"
            return 0
        fi
    done
    return 1
}

# 下载并安装官方 Node.js 发行包
install_node() {
    local ver="$1" os arch ext file url tmpdir target
    case "$(uname -s)" in
        Linux)  os="linux" ;;
        Darwin) os="darwin" ;;
        *) echo "不支持的操作系统：$(uname -s)" >&2; return 1 ;;
    esac
    case "$(uname -m)" in
        x86_64|amd64)  arch="x64" ;;
        arm64|aarch64) arch="arm64" ;;
        *) echo "不支持的 CPU 架构：$(uname -m)" >&2; return 1 ;;
    esac
    ext="tar.xz"
    [[ "$os" == "darwin" ]] && ext="tar.gz"
    file="node-v${ver}-${os}-${arch}.${ext}"

    target="$STOCK_NODE_HOME/v${ver}"
    if [[ -x "$target/bin/node" ]]; then
        export PATH="$target/bin:$PATH"
        return 0
    fi

    tmpdir="$(mktemp -d)"
    local -a urls=(
        "https://nodejs.org/dist/v${ver}/${file}"
        "https://npmmirror.com/mirrors/node/v${ver}/${file}"
    )
    for url in "${urls[@]}"; do
        echo "   ⬇️  下载 ${file} <- ${url%%/v${ver}*}..."
        if command -v curl >/dev/null 2>&1; then
            if [[ -t 2 ]]; then
                curl -fL --progress-bar --connect-timeout 15 --max-time 900 -o "$tmpdir/$file" "$url" && break
            else
                curl -fL -sS --connect-timeout 15 --max-time 900 -o "$tmpdir/$file" "$url" && break
            fi
        else
            if [[ -t 2 ]]; then
                wget --show-progress --timeout=15 -O "$tmpdir/$file" "$url" && break
            else
                wget -nv --timeout=15 -O "$tmpdir/$file" "$url" && break
            fi
        fi
        rm -f "$tmpdir/$file"
    done

    if [[ ! -s "$tmpdir/$file" ]]; then
        echo "Node.js 下载失败，请手动安装：https://nodejs.org/" >&2
        rm -rf "$tmpdir"
        return 1
    fi

    mkdir -p "$target"
    if [[ "$ext" == "tar.xz" ]]; then
        tar -xJf "$tmpdir/$file" -C "$target" --strip-components=1 || { rm -rf "$tmpdir" "$target"; return 1; }
    else
        tar -xzf "$tmpdir/$file" -C "$target" --strip-components=1 || { rm -rf "$tmpdir" "$target"; return 1; }
    fi
    rm -rf "$tmpdir"

    [[ -x "$target/bin/node" ]] || { echo "解压后未找到 node 可执行文件" >&2; return 1; }
    export PATH="$target/bin:$PATH"
    return 0
}

# 确保 node / npm 版本满足 Angular CLI 要求
ensure_node() {
    local current="" cand ver
    read_angular_node_requirements

    if command -v node >/dev/null 2>&1; then
        current="$(node -v 2>/dev/null | sed 's/^v//')"
        if [[ -n "$current" ]] && node_compatible "$current"; then
            return 0
        fi
        echo "⚠️  当前 Node.js 版本 v$current 不满足 Angular CLI 要求（需要 $NODE_REQ）"
    fi

    # 本机可能已安装其它版本（nvm / fnm / volta / asdf / 之前自动安装的）
    local -a candidates=(
        "$STOCK_NODE_HOME"/v*/bin/node
        "$HOME"/.nvm/versions/node/*/bin/node
        "$HOME"/.volta/tools/image/node/*/bin/node
        "$HOME"/.local/share/fnm/node-versions/*/installation/bin/node
        "$HOME"/.asdf/installs/nodejs/*/bin/node
        /usr/local/bin/node
    )
    shopt -s nullglob
    for cand in "${candidates[@]}"; do
        [[ -x "$cand" ]] || continue
        ver="$("$cand" -v 2>/dev/null | sed 's/^v//')"
        [[ -n "$ver" ]] || continue
        if node_compatible "$ver"; then
            echo "✅ 使用已安装的 Node.js v$ver ($(dirname "$cand"))"
            export PATH="$(dirname "$cand"):$PATH"
            shopt -u nullglob
            return 0
        fi
    done
    shopt -u nullglob

    # 兜底：下载官方发行包
    echo "📦 正在准备兼容版本的 Node.js ..."
    ver="$(pick_node_version || true)"
    if [[ -z "$ver" ]]; then
        echo "❌ 无法确定兼容的 Node.js 版本，请手动升级：https://nodejs.org/" >&2
        return 1
    fi
    echo "   ℹ️  目标版本：v$ver"
    install_node "$ver" || return 1
    echo "✅ Node.js v$(node -v | sed 's/^v//') 已就绪（$STOCK_NODE_HOME/v$ver）"
    return 0
}

if ! ensure_node; then
    echo "❌ Node.js 环境准备失败" >&2
    exit 1
fi

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

# 3.5 依赖装好后重新校验 Node 版本（首次运行时 @angular/cli 才刚安装，
#     它的 engines.node 要求此时才能读到）
if ! ensure_node; then
    echo "❌ Node.js 环境准备失败" >&2
    exit 1
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
