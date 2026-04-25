$serviceName = "WXAPP"

$params = @(
    "Application", "AppDirectory", "AppParameters",
    "AppStdout", "AppStderr",
    "AppStdoutCreationDisposition", "AppStderrCreationDisposition",
    "AppRotateFiles", "AppRotateBytes", "AppRotateOnline", "AppRotateSeconds",
    "AppEnvironmentExtra",
    "AppStopMethodSkip", "AppStopMethodConsole", "AppStopMethodWindow", "AppStopMethodThreads",
    "AppExit", "AppRestartDelay", "AppThrottle",
    "Start", "DisplayName", "Description",
    "ObjectName", "Password", "DependOnService", "DependOnGroup",
    "Type", "ErrorControl", "LoadOrderGroup"
)

Write-Host "NSSM Configuration for service: $serviceName" -ForegroundColor Green
Write-Host ("=" * 70)

foreach ($param in $params) {
    $rawOutput = & cmd /c "nssm get `"$serviceName`" `"$param`" 2>nul"
    if ($LASTEXITCODE -eq 0 -and $rawOutput) {
        $value = ($rawOutput -join "").Trim()
        if ($value -eq "") {
            $value = "<empty>"
        }
        Write-Host ("{0,-30} : {1}" -f $param, $value)
    } else {
        Write-Host ("{0,-30} : <not set or unsupported>" -f $param)
    }
}