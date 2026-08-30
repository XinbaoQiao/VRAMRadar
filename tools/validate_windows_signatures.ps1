[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string[]]$Path,

    [string]$ExpectedSigner = 'SignPath Foundation'
)

$ErrorActionPreference = 'Stop'
$supportedExtensions = @('.exe', '.dll', '.pyd')
$candidates = [System.Collections.Generic.List[System.IO.FileInfo]]::new()

foreach ($inputPath in $Path) {
    $resolved = Resolve-Path -LiteralPath $inputPath -ErrorAction Stop
    $item = Get-Item -LiteralPath $resolved.Path -Force
    if ($item.PSIsContainer) {
        Get-ChildItem -LiteralPath $item.FullName -Recurse -File -Force |
            Where-Object { $supportedExtensions -contains $_.Extension.ToLowerInvariant() } |
            ForEach-Object { $candidates.Add($_) }
    } elseif ($supportedExtensions -contains $item.Extension.ToLowerInvariant()) {
        $candidates.Add($item)
    } else {
        throw "Unsupported signature target: $($item.FullName)"
    }
}

$uniqueCandidates = @($candidates | Sort-Object FullName -Unique)
if ($uniqueCandidates.Count -eq 0) {
    throw 'No Windows PE signature targets were found.'
}

$failures = [System.Collections.Generic.List[string]]::new()
foreach ($candidate in $uniqueCandidates) {
    $signature = Get-AuthenticodeSignature -LiteralPath $candidate.FullName
    $subject = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { '' }
    $timestamped = $null -ne $signature.TimeStamperCertificate
    if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
        $failures.Add("$($candidate.FullName): signature status is $($signature.Status)")
        continue
    }
    if ($ExpectedSigner -and $subject -notlike "*$ExpectedSigner*") {
        $failures.Add("$($candidate.FullName): unexpected signer '$subject'")
    }
    if (-not $timestamped) {
        $failures.Add("$($candidate.FullName): trusted timestamp is missing")
    }
}

if ($failures.Count -gt 0) {
    throw "Windows signature validation failed:`n$($failures -join "`n")"
}

Write-Output "Validated $($uniqueCandidates.Count) Authenticode signature(s) from '$ExpectedSigner' with trusted timestamps."
