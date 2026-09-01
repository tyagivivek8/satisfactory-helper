param(
    [string]$SaveRoot = (Join-Path $env:LOCALAPPDATA "FactoryGame\Saved\SaveGames"),
    [string]$OutputPath,
    [string]$CompareTo
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$resolvedSaveRoot = (Resolve-Path -LiteralPath $SaveRoot).Path

if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputPath = Join-Path $projectRoot ".local-data\safety\save-manifest-$timestamp.json"
}

$outputDirectory = Split-Path -Parent $OutputPath
if (-not (Test-Path -LiteralPath $outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
}
$resolvedOutputDirectory = (Resolve-Path -LiteralPath $outputDirectory).Path
if ($resolvedOutputDirectory.StartsWith($resolvedSaveRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to write a manifest inside the Satisfactory save directory."
}

$rows = Get-ChildItem -LiteralPath $resolvedSaveRoot -Recurse -File -Filter "*.sav" |
    Sort-Object FullName |
    ForEach-Object {
        [ordered]@{
            path = $_.FullName
            size = $_.Length
            mtime_utc_ticks = $_.LastWriteTimeUtc.Ticks
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        }
    }

$manifest = [ordered]@{
    save_root = $resolvedSaveRoot
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    files = @($rows)
}

if ($CompareTo) {
    $baseline = Get-Content -LiteralPath $CompareTo -Raw | ConvertFrom-Json
    $before = @($baseline.files | ForEach-Object { "$($_.path)|$($_.size)|$($_.mtime_utc_ticks)|$($_.sha256)" })
    $after = @($manifest.files | ForEach-Object { "$($_.path)|$($_.size)|$($_.mtime_utc_ticks)|$($_.sha256)" })
    $difference = Compare-Object -ReferenceObject $before -DifferenceObject $after
    if ($difference) {
        $difference | Format-Table | Out-String | Write-Error
        throw "Original Satisfactory saves differ from the baseline manifest."
    }
    Write-Output "SAFE: all $($after.Count) original save files match $CompareTo"
    exit 0
}

$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
Write-Output (Resolve-Path -LiteralPath $OutputPath).Path
