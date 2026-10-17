[CmdletBinding()]
param(
    [switch]$SkipSync
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not $SkipSync) {
    & uv sync --extra build --frozen --project $projectRoot
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed with exit code $LASTEXITCODE" }
}
if (-not (Test-Path -LiteralPath $python)) {
    throw 'The maintained build environment is incomplete. Run uv sync --extra build first.'
}

& $python (Join-Path $projectRoot 'tools\build_icon.py')
if ($LASTEXITCODE -ne 0) { throw "icon build failed with exit code $LASTEXITCODE" }

& $python -m PyInstaller --noconfirm `
    --distpath (Join-Path $projectRoot 'dist') `
    --workpath (Join-Path $projectRoot 'build') `
    (Join-Path $projectRoot 'packaging\vram-radar.spec')
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$markerSource = Join-Path $projectRoot 'packaging\installed-marker.txt'
$markerDest = Join-Path $projectRoot 'dist\VRAMRadar\.vram-radar-installed'
if (-not (Test-Path -LiteralPath $markerSource)) {
    throw 'Missing packaging\installed-marker.txt'
}
Copy-Item -LiteralPath $markerSource -Destination $markerDest -Force

Write-Output (Join-Path $projectRoot 'dist\VRAMRadar\VRAMRadar.exe')
