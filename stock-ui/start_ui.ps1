# 设置编码，防止中文乱码
$OutputEncoding = [System.Text.Encoding]::UTF8

# 1. 进入前端项目根目录
$FRONT_ROOT = $PSScriptRoot
Set-Location $FRONT_ROOT

# 2. 检查运行环境（Node.js 环境在 2.1 中按需准备）

# 2.1 检查 Node 版本是否满足 Angular CLI 的要求（engines.node）
#     - 当前版本满足：直接使用
#     - 不满足：优先复用本机已安装的其它版本（nvm-windows / fnm / volta / 之前自动安装的）
#     - 都没有：自动下载官方发行包到本地目录（与 start_ui.sh 行为一致）
$STOCK_NODE_HOME = if ($env:STOCK_NODE_HOME) { $env:STOCK_NODE_HOME } else { Join-Path $env:LOCALAPPDATA 'stockassistant-node' }
$script:NodeDir = $null
$script:NpmCmd = $null
$script:NpxCmd = $null

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
    if (-not $Version) { return $false }
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

# 读取某个 node.exe 的版本（失败返回 $null）
function Get-NodeFileVersion {
    param([string]$NodeExe)
    if (-not $NodeExe -or -not (Test-Path -LiteralPath $NodeExe)) { return $null }
    try {
        $v = (& $NodeExe -v 2>$null) -replace '^v', ''
        if ($v) { return $v.Trim() }
    }
    catch { }
    return $null
}

# 使用指定目录下的 node（把该目录放到 PATH 最前面），并记住 npm / npx 路径
function Use-NodeFrom {
    param([string]$NodeExe)
    $dir = Split-Path -Parent $NodeExe
    if (-not (Test-Path -LiteralPath (Join-Path $dir 'npm.cmd'))) { return $false }
    if (($env:Path -split ';') -notcontains $dir) {
        $env:Path = "$dir;$env:Path"
    }
    $script:NodeDir = $dir
    $script:NpmCmd = Join-Path $dir 'npm.cmd'
    $script:NpxCmd = Join-Path $dir 'npx.cmd'
    return $true
}

# 本机可能存在的其它 Node 安装（含之前自动安装的版本）
function Get-NodeCandidates {
    $patterns = @()
    if ($STOCK_NODE_HOME) { $patterns += Join-Path $STOCK_NODE_HOME 'v*\node.exe' }
    if ($env:APPDATA) {
        $patterns += Join-Path $env:APPDATA 'nvm\v*\node.exe'
        $patterns += Join-Path $env:APPDATA 'fnm\node-versions\*\installation\node.exe'
    }
    if ($env:NVM_HOME) { $patterns += Join-Path $env:NVM_HOME 'v*\node.exe' }
    if ($env:ProgramFiles) { $patterns += Join-Path $env:ProgramFiles 'nodejs\node.exe' }
    if (${env:ProgramFiles(x86)}) { $patterns += Join-Path ${env:ProgramFiles(x86)} 'nodejs\node.exe' }
    if ($env:LOCALAPPDATA) {
        $patterns += Join-Path $env:LOCALAPPDATA 'Programs\nodejs\node.exe'
        $patterns += Join-Path $env:LOCALAPPDATA 'fnm\node-versions\*\installation\node.exe'
    }
    if ($env:USERPROFILE) {
        $patterns += Join-Path $env:USERPROFILE '.volta\tools\image\node\*\node.exe'
        $patterns += Join-Path $env:USERPROFILE '.asdf\installs\nodejs\*\bin\node.exe'
    }
    $seen = @{}
    foreach ($pattern in $patterns) {
        Get-Item -Path $pattern -ErrorAction SilentlyContinue | ForEach-Object {
            if (-not $seen.ContainsKey($_.FullName)) {
                $seen[$_.FullName] = $true
                $_.FullName
            }
        }
    }
}

# 从 nodejs.org 版本列表里挑一个满足要求的版本（优先 LTS）
function Get-CompatibleNodeVersion {
    param([string]$Requirement)
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $index = $null
    foreach ($url in @('https://nodejs.org/dist/index.json', 'https://npmmirror.com/mirrors/node/index.json')) {
        try {
            $index = Invoke-RestMethod -Uri $url -TimeoutSec 30
            if ($index) { break }
        }
        catch { }
    }
    if ($index) {
        foreach ($pass in @('lts', 'any')) {
            foreach ($entry in $index) {
                if ($entry.version -notmatch '^v(\d+\.\d+\.\d+)$') { continue }
                $ver = $Matches[1]
                if ($pass -eq 'lts' -and -not $entry.lts) { continue }
                if (Test-NodeCompatible -Version $ver -Requirement $Requirement) { return $ver }
            }
        }
    }
    # 离线兜底
    foreach ($ver in @('26.13.1', '24.15.0', '22.22.3')) {
        if (Test-NodeCompatible -Version $ver -Requirement $Requirement) { return $ver }
    }
    return $null
}

function Get-NodeArch {
    switch ($env:PROCESSOR_ARCHITECTURE) {
        'ARM64' { return 'arm64' }
        'AMD64' { return 'x64' }
        'x86' {
            if ($env:PROCESSOR_ARCHITEW6432 -eq 'AMD64') { return 'x64' }
            return 'x86'
        }
        default { return 'x64' }
    }
}

# 下载并解压官方 Node.js 发行包到 $STOCK_NODE_HOME\v<ver>
function Install-NodeVersion {
    param([string]$Version)
    $arch = Get-NodeArch
    $file = "node-v$Version-win-$arch.zip"
    $target = Join-Path $STOCK_NODE_HOME "v$Version"
    $targetExe = Join-Path $target 'node.exe'
    if (Test-Path -LiteralPath $targetExe) {
        if (Use-NodeFrom $targetExe) { return $true }
        # 目录残缺（例如上次解压中断）：删掉重新下载
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
    }

    $tmpZip = Join-Path $env:TEMP $file
    $urls = @(
        "https://nodejs.org/dist/v$Version/$file",
        "https://npmmirror.com/mirrors/node/v$Version/$file"
    )
    $downloaded = $false
    foreach ($url in $urls) {
        Write-Host "   ⬇️  下载 $file ..." -ForegroundColor DarkGray
        Remove-Item -LiteralPath $tmpZip -Force -ErrorAction SilentlyContinue
        if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
            & curl.exe -fL --connect-timeout 15 --max-time 900 -o $tmpZip $url | Out-Null
        }
        else {
            try { Invoke-WebRequest -Uri $url -OutFile $tmpZip -UseBasicParsing -TimeoutSec 900 } catch { }
        }
        if ((Test-Path -LiteralPath $tmpZip) -and (Get-Item -LiteralPath $tmpZip).Length -gt 10MB) {
            $downloaded = $true
            break
        }
    }
    if (-not $downloaded) {
        Remove-Item -LiteralPath $tmpZip -Force -ErrorAction SilentlyContinue
        Write-Host "❌ Node.js 下载失败，请手动安装：https://nodejs.org/" -ForegroundColor Red
        return $false
    }

    $tmpDir = Join-Path $env:TEMP "stockassistant-node-extract-$Version"
    Remove-Item -LiteralPath $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
    try {
        Expand-Archive -LiteralPath $tmpZip -DestinationPath $tmpDir -Force
    }
    catch {
        Write-Host "❌ Node.js 解压失败：$_" -ForegroundColor Red
        Remove-Item -LiteralPath $tmpZip, $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
        return $false
    }
    Remove-Item -LiteralPath $tmpZip -Force -ErrorAction SilentlyContinue

    $inner = Join-Path $tmpDir "node-v$Version-win-$arch"
    if (-not (Test-Path -LiteralPath (Join-Path $inner 'node.exe'))) {
        Write-Host "❌ 解压后未找到 node.exe" -ForegroundColor Red
        Remove-Item -LiteralPath $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
        return $false
    }
    New-Item -ItemType Directory -Path $STOCK_NODE_HOME -Force | Out-Null
    Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
    Move-Item -LiteralPath $inner -Destination $target -Force
    Remove-Item -LiteralPath $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    return (Use-NodeFrom (Join-Path $target 'node.exe'))
}

# 确保 node / npm 版本满足 Angular CLI 要求
function Ensure-NodeEnv {
    $requirement = Get-AngularNodeRequirement

    # 已经选好且仍满足要求（依赖安装后第二次调用时走这里）
    if ($script:NodeDir) {
        $readyVer = Get-NodeFileVersion (Join-Path $script:NodeDir 'node.exe')
        if ($readyVer -and (Test-NodeCompatible -Version $readyVer -Requirement $requirement)) { return $true }
    }

    $current = Get-Command node -ErrorAction SilentlyContinue
    if ($current) {
        $currentVer = Get-NodeFileVersion $current.Source
        if (Test-NodeCompatible -Version $currentVer -Requirement $requirement) {
            if (Use-NodeFrom $current.Source) { return $true }
        }
        elseif ($requirement) {
            Write-Host "⚠️  当前 Node.js 版本 v$currentVer 不满足 Angular CLI 要求（需要 $requirement）" -ForegroundColor Yellow
        }
    }

    # 尚未安装 @angular/cli 时读不到要求：先用当前 Node 装依赖，装完会重新校验
    if (-not $requirement) {
        if (-not $current) {
            Write-Host "❌ 未找到 Node.js，请先安装：https://nodejs.org/" -ForegroundColor Red
            return $false
        }
        Use-NodeFrom $current.Source | Out-Null
        return $true
    }

    foreach ($candidate in Get-NodeCandidates) {
        $ver = Get-NodeFileVersion $candidate
        if (-not $ver) { continue }
        if (Test-NodeCompatible -Version $ver -Requirement $requirement) {
            Write-Host "✅ 使用已安装的 Node.js v$ver（$(Split-Path -Parent $candidate)）" -ForegroundColor Green
            if (Use-NodeFrom $candidate) { return $true }
        }
    }

    Write-Host "📦 正在准备兼容版本的 Node.js ..." -ForegroundColor Yellow
    $ver = Get-CompatibleNodeVersion -Requirement $requirement
    if (-not $ver) {
        Write-Host "❌ 无法确定兼容的 Node.js 版本，请手动升级：https://nodejs.org/" -ForegroundColor Red
        return $false
    }
    Write-Host "   ℹ️  目标版本：v$ver" -ForegroundColor Gray
    if (-not (Install-NodeVersion -Version $ver)) { return $false }
    $installed = Get-NodeFileVersion (Join-Path $script:NodeDir 'node.exe')
    Write-Host "✅ Node.js v$installed 已就绪（$($script:NodeDir)）" -ForegroundColor Green
    return $true
}

if (-not (Ensure-NodeEnv)) {
    Write-Host "❌ Node.js 环境准备失败" -ForegroundColor Red
    exit 1
}

$npmCmd = if ($script:NpmCmd) { $script:NpmCmd } else { 'npm' }
if (-not (Get-Command $npmCmd -ErrorAction SilentlyContinue)) {
    Write-Host "❌ 未找到 npm，请先安装 Node.js（安装包自带 npm）" -ForegroundColor Red
    exit 1
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
        & $npmCmd ci --no-audit --no-fund
    }
    else {
        & $npmCmd install --no-audit --no-fund
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ 前端依赖安装失败，请检查网络或手动执行 npm install" -ForegroundColor Red
        exit 1
    }
}

# 3.5 依赖装好后重新校验 Node 版本（首次运行时 @angular/cli 才刚安装，
#     它的 engines.node 要求此时才能读到）
if (-not (Ensure-NodeEnv)) {
    Write-Host "❌ Node.js 环境准备失败" -ForegroundColor Red
    exit 1
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
$npxCmd = if ($script:NpxCmd) { $script:NpxCmd } else { 'npx' }
& $npxCmd ng serve --host 0.0.0.0 --port 4200
