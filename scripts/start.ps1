$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
. "$PSScriptRoot\load-env.ps1" -Path (Join-Path $projectRoot ".env")

if (-not (Test-Path -LiteralPath "vendor\SatisfactoryMCP\pyproject.toml")) {
    throw "The vendored SatisfactoryMCP source is missing. Download a complete Satisfactory Helper release."
}

$codexReady = $false
$claudeReady = $false
$codexCompatibilityError = $null
$requiredCodexVersion = (Get-Content -LiteralPath (Join-Path $projectRoot "packaging\codex-version.txt") -Raw).Trim()
$configuredCodexExecutable = $env:SATISFACTORY_HELPER_CODEX_EXECUTABLE
$codexCommand = if ($configuredCodexExecutable) {
    $configuredCodexExecutable
}
else {
    (Get-Command codex -ErrorAction SilentlyContinue).Source
}
$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    if ($codexCommand) {
        $codexVersion = (& $codexCommand --version 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $codexVersion -notin @($requiredCodexVersion, "codex-cli $requiredCodexVersion")) {
            $codexCompatibilityError = "Codex $codexVersion is incompatible with this release. Run 'npm install --global @openai/codex@$requiredCodexVersion'."
        }
        else {
            $codexLogin = & $codexCommand login status 2>&1
            $codexReady = $LASTEXITCODE -eq 0 -and $codexLogin -match "Logged in"
        }
    }
    if (Get-Command claude -ErrorAction SilentlyContinue) {
        $claudeLogin = claude auth status --json 2>&1
        $claudeReady = $LASTEXITCODE -eq 0 -and $claudeLogin -match '"loggedIn"\s*:\s*true'
    }
}
finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
if (-not $codexReady -and -not $claudeReady) {
    if ($codexCompatibilityError) {
        throw $codexCompatibilityError
    }
    throw "No planning agent is signed in. Run 'codex login' or launch 'claude' and follow its browser login prompts, then start Satisfactory Helper again."
}
if (-not $codexReady) {
    if ($codexCompatibilityError) {
        Write-Warning "$codexCompatibilityError Claude remains available in the agent selector."
    }
    else {
        Write-Warning "Codex is offline; Claude remains available in the agent selector."
    }
}
if (-not $claudeReady) {
    Write-Warning "Claude is offline; Codex remains available in the agent selector."
}

uv sync --extra dev

$mapOut = Join-Path $projectRoot ".local-data\engine\data\local"
$mapSidecar = Join-Path $mapOut "map.json"
$mapProbe = Join-Path $mapOut "tiles\0\0_0.png"
if (-not (Test-Path -LiteralPath $mapSidecar) -or -not (Test-Path -LiteralPath $mapProbe)) {
    New-Item -ItemType Directory -Force -Path $mapOut | Out-Null
    Write-Host "Preparing the local in-game map (first start only)..."
    $mapGameRoot = @(
        $env:SATISFACTORY_GAME_ROOT,
        "C:\Program Files (x86)\Steam\steamapps\common\Satisfactory",
        "C:\Program Files\Epic Games\SatisfactoryEarlyAccess",
        "D:\SteamLibrary\steamapps\common\Satisfactory",
        "E:\SteamLibrary\steamapps\common\Satisfactory",
        "G:\SteamLibrary\steamapps\common\Satisfactory"
    ) | Where-Object {
        $_ -and (Test-Path -LiteralPath (Join-Path $_ "FactoryGame\Content\Paks"))
    } | Select-Object -First 1
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($mapGameRoot) {
            uv run --no-sync python vendor\SatisfactoryMCP\tools\gen_map_image.py --game $mapGameRoot --size 8192 --out-dir $mapOut
            $mapExitCode = $LASTEXITCODE
        }
        else {
            $mapExitCode = 1
            Write-Warning "No Satisfactory install was found for local map extraction."
        }
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($mapExitCode -ne 0) {
        Write-Warning "The in-game map could not be generated. Factory geometry will use the measured-grid fallback."
    }
}

pnpm install --frozen-lockfile
pnpm --dir apps/web build
uv run satisfactory-helper
