# 构建离线可安装的wheel与源码包；制品默认在已忽略的dist/，不会部署或发布。
param([string]$OutDir = "dist")
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    python -m build --outdir $OutDir
    if ($LASTEXITCODE -ne 0) { throw "构建失败，请先安装构建依赖：python -m pip install -e '.[build]'" }
}
finally { Pop-Location }
