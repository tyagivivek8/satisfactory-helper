$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
. "$PSScriptRoot\load-env.ps1" -Path (Join-Path $projectRoot ".env")

uv run satisfactory-helper --dev
