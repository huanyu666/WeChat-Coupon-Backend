param(
  [string]$Target = (Join-Path $PSScriptRoot 'web-h5sign.js')
)
$baseline = Join-Path $PSScriptRoot 'web-h5sign.rollback-base.js'
if (-not (Test-Path -LiteralPath $baseline)) { throw "Missing rollback baseline: $baseline" }
Copy-Item -LiteralPath $baseline -Destination $Target -Force
Write-Output "Restored $Target from $baseline"
