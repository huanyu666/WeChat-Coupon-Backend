param(
    [string]$Archive = ""
)

$ErrorActionPreference = "Stop"
$ExpectedSha256 = "1E911D30E5C7EE3596196024A51B25A22C0D86CAC5A33AC187885958B89F02C7"
$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ParentDir = Split-Path $ProjectDir -Parent

if (-not $Archive) {
    $Archive = Join-Path $ParentDir "backups\meituan-expand-before-cleanup-20260814.zip"
}
$Archive = (Resolve-Path -LiteralPath $Archive).Path

$ActualSha256 = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash
if ($ActualSha256 -ne $ExpectedSha256) {
    throw "Backup SHA256 mismatch: $ActualSha256"
}

$SavedDir = Join-Path $ParentDir ("meituan-expand.cleaned-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
Move-Item -LiteralPath $ProjectDir -Destination $SavedDir
Expand-Archive -LiteralPath $Archive -DestinationPath $ParentDir

Write-Host "Restored: $(Join-Path $ParentDir 'meituan-expand')"
Write-Host "Cleaned copy kept at: $SavedDir"
