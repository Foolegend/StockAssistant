# 设置编码，防止中文乱码
$OutputEncoding = [System.Text.Encoding]::UTF8

# 1. 获取当前脚本所在目录 (d:\project\StockAssistant\stock-server)
$BACK_ROOT = $PSScriptRoot

# 2. 设置 PYTHONPATH，确保 python 能找到 src 包
$env:PYTHONPATH = $BACK_ROOT

# 3. 定义虚拟环境中的 python 路径 (相对于项目根目录)
$PROJECT_ROOT = Split-Path -Parent $BACK_ROOT
$PYTHON_EXE = Join-Path $PROJECT_ROOT ".venv\Scripts\python.exe"

# --- 新增：强制释放 8015 端口 ---
Write-Host "🔍 检查并清理端口 8015..." -ForegroundColor Gray
$proc = Get-NetTCPConnection -LocalPort 8015 -ErrorAction SilentlyContinue
if ($proc) {
    Write-Host "Stopping existing process on port 8015 (PID: $($proc.OwningProcess))..." -ForegroundColor Yellow
    Stop-Process -Id $proc.OwningProcess -Force -ErrorAction SilentlyContinue
}
# -----------------------------

# 4. 启动 Uvicorn 服务
Write-Host "--------------------------------------------------" -ForegroundColor Cyan
Write-Host "🚀 正在启动 StockAssistant 后端服务..." -ForegroundColor Cyan
Write-Host "端口: http://127.0.0.1:8015" -ForegroundColor Yellow
Write-Host "API 文档: http://127.0.0.1:8015/docs" -ForegroundColor Yellow
Write-Host "--------------------------------------------------" -ForegroundColor Cyan

& $PYTHON_EXE -m uvicorn src.api.chat:app --host 127.0.0.1 --port 8015
