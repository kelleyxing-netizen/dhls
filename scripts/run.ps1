# 本机启动包装；生产发布及回滚需执行docs/prelaunch-checklist.md，脚本不自动部署。
param([ValidateRange(1,65535)][int]$Port = 8000)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    python -m dhls --port $Port
    if ($LASTEXITCODE -ne 0) { throw "启动失败，请检查已安装包、环境变量与目录权限。" }
}
finally { Pop-Location }
