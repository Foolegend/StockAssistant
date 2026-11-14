# StockAssistant 一键启动：后端 + 前端（Windows）
# 后端在后台运行，前端占用当前终端；Ctrl+C 会同时停止两者。
$ErrorActionPreference = "Stop"

# 控制台统一为 UTF-8，防止中文乱码
chcp 65001 > $null
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ROOT = $PSScriptRoot
$ServerHost = "127.0.0.1"
$ServerPort = 8015
$serverProc = $null

function Stop-Backend {
    if ($null -eq $script:serverProc) { return }
    $proc = $script:serverProc
    $script:serverProc = $null
    if (-not $proc.HasExited) {
        Write-Host ""
        Write-Host "🛑 正在停止后端服务 (PID: $($proc.Id))..." -ForegroundColor Yellow
        # /T 一并结束 uvicorn 子进程，/F 强制结束
        & taskkill /PID $proc.Id /T /F *> $null
        Start-Sleep -Milliseconds 500
    }
}

function Test-PortOpen {
    param(
        [string]$TargetHost,
        [int]$Port,
        [int]$TimeoutMs = 500
    )
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect($TargetHost, $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) { return $false }
        $client.EndConnect($iar)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

# Ctrl+C 等终止情况也尽量清理后端进程
trap {
    Stop-Backend
    exit 1
}

try {
    $shellPath = (Get-Process -Id $PID).Path
    if (-not $shellPath) { $shellPath = "powershell.exe" }

    Write-Host "▶️  启动后端..." -ForegroundColor Cyan
    $serverScript = Join-Path $ROOT "stock-server\start_server.ps1"
    $serverProc = Start-Process -FilePath $shellPath `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$serverScript`"") `
        -NoNewWindow -PassThru

    Write-Host "⏳ 等待后端监听 ${ServerHost}:$ServerPort ..." -ForegroundColor Gray
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        if ($serverProc.HasExited) {
            throw "后端启动失败（退出码 $($serverProc.ExitCode)），请查看上方日志"
        }
        if (Test-PortOpen $ServerHost $ServerPort) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        throw "等待后端启动超时（60s）"
    }
    Write-Host "✅ 后端已就绪" -ForegroundColor Green

    Write-Host "▶️  启动前端..." -ForegroundColor Cyan
    & (Join-Path $ROOT "stock-ui\start_ui.ps1")
}
catch {
    Write-Host "❌ $_" -ForegroundColor Red
    Stop-Backend
    exit 1
}
finally {
    Stop-Backend
}
