# StockAssistant 后端启动脚本（Windows）
#
# 启动 Bun 编写的 HTTP/SSE API 服务，监听 127.0.0.1:8015。
# 智能分析由 stock-server/src 中的 StockAssistant CLI 实现。
$ErrorActionPreference = 'Stop'

chcp 65001 > $null
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$BackRoot = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$Port = if ($env:INVEST_SERVER_PORT) { [int]$env:INVEST_SERVER_PORT } else { 8015 }
$ServerHost = if ($env:INVEST_SERVER_HOST) { $env:INVEST_SERVER_HOST } else { '127.0.0.1' }

function Resolve-BunExe {
    if ($env:INVESTAI_BUN -and (Test-Path $env:INVESTAI_BUN)) { return $env:INVESTAI_BUN }
    $candidates = @(
        (Join-Path $env:APPDATA 'npm\node_modules\bun\bin\bun.exe'),
        (Join-Path $env:USERPROFILE '.bun\bin\bun.exe')
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    $command = Get-Command bun -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

$bun = Resolve-BunExe
if (-not $bun) {
    # 未检测到 Bun：尝试用 npm 自动安装（npm 自带 bun 包，会下载对应平台的二进制）
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if ($npm) {
        Write-Host '⚠️  未检测到 Bun，正在通过 npm 自动安装 ...' -ForegroundColor Yellow
        & npm install -g bun
        if ($LASTEXITCODE -eq 0) {
            $bun = Resolve-BunExe
        }
    }
}
if (-not $bun) {
    Write-Host '❌ 未找到 Bun，请先安装：https://bun.sh' -ForegroundColor Red
    exit 1
}

if (-not (Test-Path (Join-Path $BackRoot 'node_modules'))) {
    Write-Host '📦 未检测到依赖，正在执行 bun install ...' -ForegroundColor Yellow
    Push-Location $BackRoot
    try {
        & $bun install
        if ($LASTEXITCODE -ne 0) {
            throw "bun install 失败 (exit $LASTEXITCODE)"
        }
    }
    finally {
        Pop-Location
    }
}

# 释放占用端口的旧进程（如果存在）
$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "⚠️  端口 $Port 已被占用，正在释放 ..." -ForegroundColor Yellow
    $existing | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
        & taskkill /PID $_ /T /F *> $null
    }
    Start-Sleep -Milliseconds 300
}

$env:INVEST_SERVER_PORT = "$Port"
$env:INVEST_SERVER_HOST = $ServerHost

Push-Location $BackRoot
try {
    Write-Host "▶️  StockAssistant 后端启动中：http://$ServerHost`:$Port" -ForegroundColor Cyan
    & $bun run terminal/server.ts
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
