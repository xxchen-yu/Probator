# 一键启动 AgentTestHub 与校园智能助手夹具
#
#   .\start.ps1                 # 默认：干净基线 + 真实大模型链路
#   .\start.ps1 -Fault weak_guard   # 缺陷注入：校准测试工具的检出率
#   .\start.ps1 -Mode template      # 离线模板链路（不烧 token，用于调工具本身）
#
# 平台：http://127.0.0.1:8500
# 夹具：http://127.0.0.1:8000

param(
    [string]$Fault = "clean",
    [string]$Mode = "auto",
    [int]$AgentPort = 8000,
    [int]$PlatformPort = 8500
)

$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }

function Stop-Existing($pattern) {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$pattern*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

Stop-Existing "agent_server"
Stop-Existing "server.py"
Start-Sleep -Milliseconds 800

$env:FAULT_PROFILE = $Fault
$env:ANSWER_MODE = $Mode
$env:AGENT_PORT = "$AgentPort"
$env:PLATFORM_PORT = "$PlatformPort"

Start-Process -FilePath "python" -ArgumentList (Join-Path $root "待测智能体\agent_server.py") -WindowStyle Hidden | Out-Null
Start-Sleep -Seconds 2
Start-Process -FilePath "python" -ArgumentList (Join-Path $root "平台\server.py") -WindowStyle Hidden | Out-Null
Start-Sleep -Seconds 3

$health = $null
try { $health = Invoke-RestMethod "http://127.0.0.1:$AgentPort/health" -TimeoutSec 5 } catch { }
if ($health) {
    Write-Host "夹具在线：http://127.0.0.1:$AgentPort  engine=$($health.engine)  fault=$($health.fault_profile)"
} else {
    Write-Host "夹具未能启动，请检查 $root\待测智能体\.env" -ForegroundColor Red
}
Write-Host "平台地址：http://127.0.0.1:$PlatformPort" -ForegroundColor Green
