# 使用项目虚拟环境启动，可从任意工作目录调用。
$ErrorActionPreference = 'Stop'

$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    Write-Error '未找到项目虚拟环境。请先按 README 的运行说明创建 .venv 并安装依赖。'
    exit 1
}

# 固定工作目录，保证相对路径与在项目根目录启动时一致。
Push-Location -LiteralPath $PSScriptRoot
try {
    & $pythonPath -m app.main
    $appExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $appExitCode
