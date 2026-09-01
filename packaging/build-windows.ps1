param(
    [string]$Version = "dev",
    [string]$CodexVersion = "",
    [switch]$SkipCodex
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $projectRoot
$env:UV_PROJECT_ENVIRONMENT = Join-Path $projectRoot ".bundle-venv"

if ($Version -notmatch '^[0-9A-Za-z._-]+$') {
    throw "Version may contain only letters, numbers, dots, underscores, and hyphens."
}
if (-not $CodexVersion) {
    $CodexVersion = (Get-Content -LiteralPath (Join-Path $PSScriptRoot "codex-version.txt") -Raw).Trim()
}
if ($CodexVersion -notmatch '^\d+\.\d+\.\d+([.-][0-9A-Za-z.-]+)?$') {
    throw "CodexVersion is not a valid pinned package version: $CodexVersion"
}

function Invoke-Checked {
    param([scriptblock]$Command, [string]$Label)
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE."
    }
}

Invoke-Checked { pnpm install --frozen-lockfile } "pnpm install"
Invoke-Checked { pnpm --dir apps/web build } "web build"
Invoke-Checked { uv sync --extra dev --extra bundle } "Python bundle environment"
Invoke-Checked {
    uv run --extra bundle pyinstaller --noconfirm --clean packaging/satisfactory-helper.spec
} "PyInstaller"

$appRoot = (Resolve-Path (Join-Path $projectRoot "dist\Satisfactory Helper")).Path
$toolsRoot = Join-Path $appRoot "tools"
$licensesRoot = Join-Path $appRoot "licenses"
New-Item -ItemType Directory -Force -Path $toolsRoot, $licensesRoot | Out-Null

Copy-Item -LiteralPath (Join-Path $projectRoot "LICENSE") -Destination (Join-Path $appRoot "LICENSE.txt")
Copy-Item -LiteralPath (Join-Path $projectRoot "THIRD_PARTY_NOTICES.md") -Destination $appRoot
Copy-Item -LiteralPath (Join-Path $projectRoot "vendor\SatisfactoryMCP\LICENSE") -Destination (Join-Path $licensesRoot "SatisfactoryMCP-LICENSE.txt")

$temporaryCodexRoot = $null
try {
    if (-not $SkipCodex) {
        $temporaryBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        $temporaryCodexRoot = Join-Path $temporaryBase ("satisfactory-helper-codex-" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $temporaryCodexRoot | Out-Null
        Invoke-Checked {
            npm.cmd install --prefix $temporaryCodexRoot --ignore-scripts --no-audit --no-fund "@openai/codex@$CodexVersion"
        } "Codex CLI download"
        $codexCandidates = @(
            Get-ChildItem -LiteralPath (Join-Path $temporaryCodexRoot "node_modules") -Recurse -Filter "codex.exe" |
                Where-Object { $_.FullName -match 'codex-win32-x64' }
        )
        if ($codexCandidates.Count -ne 1) {
            throw "Expected one native Codex x64 executable, found $($codexCandidates.Count)."
        }
        Get-ChildItem -LiteralPath $codexCandidates[0].Directory.FullName -Filter "*.exe" |
            Copy-Item -Destination $toolsRoot
        Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/openai/codex/main/LICENSE" -OutFile (Join-Path $licensesRoot "OpenAI-Codex-LICENSE.txt")
    }
}
finally {
    if ($temporaryCodexRoot) {
        $resolvedTemporary = [IO.Path]::GetFullPath($temporaryCodexRoot)
        $temporaryBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if ($resolvedTemporary.StartsWith($temporaryBase, [StringComparison]::OrdinalIgnoreCase) -and
            (Split-Path -Leaf $resolvedTemporary).StartsWith("satisfactory-helper-codex-")) {
            Remove-Item -LiteralPath $resolvedTemporary -Recurse -Force
        }
    }
}

$signIn = @'
@echo off
title Satisfactory Helper - Codex sign in
"%~dp0tools\codex.exe" login
echo.
if errorlevel 1 echo Codex sign-in did not finish successfully.
if not errorlevel 1 echo Codex is ready. You can now start Satisfactory Helper.
pause
'@
Set-Content -LiteralPath (Join-Path $appRoot "Sign in to Codex.cmd") -Value $signIn -Encoding ascii

$quickStart = @"
Satisfactory Helper $Version

1. Double-click "Sign in to Codex.cmd" once and choose Sign in with ChatGPT.
2. Double-click "Satisfactory Helper.exe" whenever you want to use the app.
3. Your browser opens to the local workbench. Keep the console window open while using it.

No Node.js, pnpm, Python, uv, or API key is required. The app reads only private copies of
your Satisfactory saves. Claude is optional and appears automatically if Claude Code is
installed and signed in on this computer.
"@
Set-Content -LiteralPath (Join-Path $appRoot "QUICK START.txt") -Value $quickStart -Encoding utf8

$releaseRoot = Join-Path $projectRoot "dist\release"
New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
$archive = Join-Path $releaseRoot "Satisfactory-Helper-$Version-windows-x64.zip"
if (Test-Path -LiteralPath $archive) {
    Remove-Item -LiteralPath $archive -Force
}
Compress-Archive -Path (Join-Path $appRoot "*") -DestinationPath $archive -CompressionLevel Optimal
Write-Host "Created $archive"
