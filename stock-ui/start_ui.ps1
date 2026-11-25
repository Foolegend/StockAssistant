# 设置编码，防止中文乱码
$OutputEncoding = [System.Text.Encoding]::UTF8

# 1. 进入前端项目根目录
$FRONT_ROOT = $PSScriptRoot
Set-Location $FRONT_ROOT

# 2. 检查运行环境
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "❌ 未找到 npm，请先安装 Node.js（安装包自带 npm）" -ForegroundColor Red
    exit 1
}

# 2.1 检查 Node 版本是否满足 Angular CLI 的要求（engines.node）
function Get-AngularNodeRequirement {
    $pkg = Join-Path $FRONT_ROOT 'node_modules\@angular\cli\package.json'
    if (-not (Test-Path $pkg)) { return $null }
    try {
        $json = Get-Content $pkg -Raw | ConvertFrom-Json
        if ($json.engines -and $json.engines.node) { return $json.engines.node }
    }
    catch { }
    return $null
}

function Test-NodeCompatible {
    param([string]$Version, [string]$Requirement)
    if (-not $Requirement) { return $true }
    try { $current = [version]$Version.TrimStart('v') } catch { return $true }
    foreach ($part in ($Requirement -split '\|\|')) {
        $p = $part.Trim()
        if ($p.StartsWith('^')) {
            $min = [version]$p.Substring(1)
            if ($current.Major -eq $min.Major -and $current -ge $min) { return $true }
        }
        elseif ($p.StartsWith('>=')) {
            $min = [version]$p.Substring(2)
            if ($current -ge $min) { return $true }
        }
    }
    return $false
}

$nodeRequirement = Get-AngularNodeRequirement
if ($nodeRequirement) {
    $nodeVersion = (& node -v) -replace '^v', ''
    if (-not (Test-NodeCompatible -Version $nodeVersion -Requirement $nodeRequirement)) {
        Write-Host "❌ 当前 Node.js 版本 v$nodeVersion 不满足 Angular CLI 要求（需要 $nodeRequirement）" -ForegroundColor Red
        Write-Host "   请升级 Node.js 后重试：https://nodejs.org/ （或 winget install OpenJS.NodeJS.LTS）" -ForegroundColor Yellow
        exit 1
    }
}

# 3. 检查并安装/同步依赖
#    - node_modules 不存在：全新安装（有 lock 文件时用 npm ci 保证一致）
#    - package.json / package-lock.json 比 node_modules 中的记录更新：增量同步
$nodeModules = Join-Path $FRONT_ROOT "node_modules"
$lockFile = Join-Path $FRONT_ROOT "package-lock.json"
$pkgFile = Join-Path $FRONT_ROOT "package.json"
$marker = Join-Path $nodeModules ".package-lock.json"

$needInstall = $false
$reason = ""

if (-not (Test-Path $nodeModules)) {
    $needInstall = $true
    $reason = "未检测到 node_modules"
}
elseif (-not (Test-Path $marker)) {
    $needInstall = $true
    $reason = "node_modules 不完整"
}
else {
    $stale = $false
    if ((Test-Path $lockFile) -and ((Get-Item $lockFile).LastWriteTime -gt (Get-Item $marker).LastWriteTime)) {
        $stale = $true
    }
    if ((Test-Path $pkgFile) -and ((Get-Item $pkgFile).LastWriteTime -gt (Get-Item $marker).LastWriteTime)) {
        $stale = $true
    }
    if ($stale) {
        $needInstall = $true
        $reason = "依赖清单有更新"
    }
}

if ($needInstall) {
    Write-Host "📦 $reason，正在安装前端依赖..." -ForegroundColor Yellow
    if ((-not (Test-Path $nodeModules)) -and (Test-Path $lockFile)) {
        & npm ci --no-audit --no-fund
    }
    else {
        & npm install --no-audit --no-fund
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ 前端依赖安装失败，请检查网络或手动执行 npm install" -ForegroundColor Red
        exit 1
    }
}

# --- 新增：强制释放 4200 端口 ---
Write-Host "🔍 检查并清理端口 4200..." -ForegroundColor Gray
$proc = Get-NetTCPConnection -LocalPort 4200 -ErrorAction SilentlyContinue
if ($proc) {
    Write-Host "Stopping existing process on port 4200 (PID: $($proc.OwningProcess))..." -ForegroundColor Yellow
    Stop-Process -Id $proc.OwningProcess -Force -ErrorAction SilentlyContinue
}
# -----------------------------

# 4. 启动 Angular 开发服务器
Write-Host "--------------------------------------------------" -ForegroundColor Cyan
Write-Host "🚀 正在启动 StockAssistant 前端界面..." -ForegroundColor Cyan
Write-Host "访问地址: http://localhost:4200" -ForegroundColor Yellow
Write-Host "--------------------------------------------------" -ForegroundColor Cyan

# 使用 npx 确保调用本地安装的 ng 命令
npx ng serve --host 0.0.0.0 --port 4200
