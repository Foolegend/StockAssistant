# 设置编码，防止中文乱码（控制台与 Python 输出都统一为 UTF-8）
chcp 65001 > $null
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# 日志只写入 logs\stock-server.log，不在控制台刷屏
# 需要临时查看控制台日志时先设置 $env:STOCK_LOG_CONSOLE = "1"
if ([string]::IsNullOrEmpty($env:STOCK_LOG_CONSOLE)) { $env:STOCK_LOG_CONSOLE = "0" }

# 1. 获取当前脚本所在目录 (d:\project\StockAssistant\stock-server)
$BACK_ROOT = $PSScriptRoot

# 2. 设置 PYTHONPATH，确保 python 能找到 src 包
$env:PYTHONPATH = $BACK_ROOT

# 3. 定义虚拟环境中的 python 路径 (相对于项目根目录)
$PROJECT_ROOT = Split-Path -Parent $BACK_ROOT
$PYTHON_EXE = Join-Path $PROJECT_ROOT ".venv\Scripts\python.exe"
$REQ_FILE = Join-Path $BACK_ROOT "requirements.txt"

if (-not (Test-Path $PYTHON_EXE)) {
    Write-Host "📦 未找到虚拟环境，正在创建..." -ForegroundColor Yellow
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv (Join-Path $PROJECT_ROOT ".venv")
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv (Join-Path $PROJECT_ROOT ".venv")
    }
    else {
        Write-Host "❌ 未找到 Python，请先安装 Python 3.12+" -ForegroundColor Red
        exit 1
    }
    if (-not (Test-Path $PYTHON_EXE)) {
        Write-Host "❌ 创建虚拟环境失败，请检查 Python 安装" -ForegroundColor Red
        exit 1
    }
}

# 4. 依赖缺失时根据 requirements.txt 自动安装
& $PYTHON_EXE -c "import uvicorn, fastapi, langchain, langgraph, deepagents" 2>$null
if ($LASTEXITCODE -ne 0) {
    if (-not (Test-Path $REQ_FILE)) {
        Write-Host "❌ 当前 Python 环境缺少依赖，且未找到 $REQ_FILE" -ForegroundColor Red
        exit 1
    }
    Write-Host "📦 检测到依赖缺失，正在安装 requirements.txt ..." -ForegroundColor Yellow
    & $PYTHON_EXE -m pip install -r $REQ_FILE
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ 依赖安装失败，请检查网络或手动执行：" -ForegroundColor Red
        Write-Host "   $PYTHON_EXE -m pip install -r $REQ_FILE" -ForegroundColor Yellow
        exit 1
    }
}

# --- 新增：强制释放 8015 端口 ---
Write-Host "🔍 检查并清理端口 8015..." -ForegroundColor Gray
$proc = Get-NetTCPConnection -LocalPort 8015 -ErrorAction SilentlyContinue
if ($proc) {
    Write-Host "Stopping existing process on port 8015 (PID: $($proc.OwningProcess))..." -ForegroundColor Yellow
    Stop-Process -Id $proc.OwningProcess -Force -ErrorAction SilentlyContinue
}
# -----------------------------

# 5. 启动 Uvicorn 服务
Write-Host "--------------------------------------------------" -ForegroundColor Cyan
Write-Host "🚀 正在启动 StockAssistant 后端服务..." -ForegroundColor Cyan
Write-Host "端口: http://127.0.0.1:8015" -ForegroundColor Yellow
Write-Host "API 文档: http://127.0.0.1:8015/docs" -ForegroundColor Yellow
Write-Host "日志文件: $BACK_ROOT\logs\stock-server.log" -ForegroundColor Yellow
Write-Host "--------------------------------------------------" -ForegroundColor Cyan

# --no-access-log: 请求日志由应用的请求中间件统一输出（带 request id 与耗时）
& $PYTHON_EXE -m uvicorn src.api.chat:app --host 127.0.0.1 --port 8015 --no-access-log
