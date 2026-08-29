[CmdletBinding()]
param(
    [switch]$SkipBundle,
    [string]$Version
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$bundle = Join-Path $projectRoot 'dist\VRAMRadar\VRAMRadar.exe'
$manifest = Join-Path $projectRoot 'packaging\vram-radar.iss'

if (-not $SkipBundle) {
    & (Join-Path $projectRoot 'Build-VramRadar.ps1') -SkipSync
    if ($LASTEXITCODE -ne 0) { throw "bundle build failed with exit code $LASTEXITCODE" }
}
if (-not (Test-Path -LiteralPath $bundle)) {
    throw 'The maintained Windows bundle is missing. Run Build-VramRadar.ps1 first.'
}

if (-not $Version) {
    $versionSource = Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'src\vram_radar\__init__.py')
    $versionMatch = [regex]::Match($versionSource, '__version__\s*=\s*"(?<version>[0-9]+(?:\.[0-9]+){2}(?:[-+][0-9A-Za-z.-]+)?)"')
    if (-not $versionMatch.Success) {
        throw 'Could not determine the application version from src\vram_radar\__init__.py.'
    }
    $Version = $versionMatch.Groups['version'].Value
}
if ($Version -notmatch '^[0-9]+(?:\.[0-9]+){2}(?:[-+][0-9A-Za-z.-]+)?$') {
    throw "Invalid installer version: $Version"
}

$compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
if (-not $compiler) {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    )
    $compiler = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
}
if (-not $compiler) {
    throw 'Inno Setup 6 was not found. Install it, then rerun this command.'
}

& $compiler "/DMyAppVersion=$Version" $manifest
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }

$installer = Join-Path $projectRoot "dist-installer\VRAMRadar-Setup-$Version.exe"
if (-not (Test-Path -LiteralPath $installer)) {
    throw "Inno Setup completed but the expected installer is missing: $installer"
}
Write-Output $installer
