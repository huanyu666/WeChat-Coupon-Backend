param(
  [string]$TargetRoot = $PSScriptRoot
)

$resolvedRoot = (Resolve-Path -LiteralPath $TargetRoot).Path
$couponTarget = Join-Path $resolvedRoot 'web-coupon-query.js'
$cashbackTarget = Join-Path $resolvedRoot 'web-cashback-query.js'
$benefitsTarget = Join-Path $resolvedRoot 'web-benefits-query.js'
$couponBaseline = Join-Path $resolvedRoot 'web-coupon-query.rollback-base.js'
$cashbackBaseline = Join-Path $resolvedRoot 'web-cashback-query.rollback-base.js'

foreach ($path in @($couponBaseline, $cashbackBaseline)) {
  if (-not (Test-Path -LiteralPath $path)) { throw "Missing rollback baseline: $path" }
}

Copy-Item -LiteralPath $couponBaseline -Destination $couponTarget -Force
Copy-Item -LiteralPath $cashbackBaseline -Destination $cashbackTarget -Force
if (Test-Path -LiteralPath $benefitsTarget) {
  Remove-Item -LiteralPath $benefitsTarget -Force
}
Write-Output "Restored standalone query scripts under $resolvedRoot"
