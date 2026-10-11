# 设置编码，防止中文乱码
$OutputEncoding = [System.Text.Encoding]::UTF8

# 1. 进入前端项目根目录
$FRONT_ROOT = $PSScriptRoot
Set-Location $FRONT_ROOT

# 2. 检查并安装依赖 (仅在 node_modules 不存在时安装)
if (-not (Test-Path "node_modules")) {
    Write-Host "📦 未检测到 node_modules，正在安装依赖..." -ForegroundColor Yellow
    npm install
}

# --- 新增：强制释放 4200 端口 ---
Write-Host "🔍 检查并清理端口 4200..." -ForegroundColor Gray
$proc = Get-NetTCPConnection -LocalPort 4200 -ErrorAction SilentlyContinue
if ($proc) {
    Write-Host "Stopping existing process on port 4200 (PID: $($proc.OwningProcess))..." -ForegroundColor Yellow
    Stop-Process -Id $proc.OwningProcess -Force -ErrorAction SilentlyContinue
}
# -----------------------------

# 3. 启动 Angular 开发服务器
Write-Host "--------------------------------------------------" -ForegroundColor Cyan
Write-Host "🚀 正在启动 StockAssistant 前端界面..." -ForegroundColor Cyan
Write-Host "访问地址: http://localhost:4200" -ForegroundColor Yellow
Write-Host "--------------------------------------------------" -ForegroundColor Cyan

# 使用 npx 确保调用本地安装的 ng 命令
npx ng serve --host 0.0.0.0 --port 4200
