# Run from an administrator PowerShell. Restore the BARROT-only configuration.
$ErrorActionPreference = 'Stop'
$comparisonRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$comparisonPython = Join-Path $comparisonRoot '.venv\Scripts\python.exe'
$comparisonProbe = Join-Path $comparisonRoot 'test\test_11\test_11_connection_probe.py'
$comparisonDirectory = Join-Path $PSScriptRoot 'results\usb_diagnostics_20261011'
$comparisonTranscript = Join-Path $comparisonDirectory 'intel_comparison_console.log'
$comparisonResult = Join-Path $comparisonDirectory 'intel_comparison_state.json'
$intelBluetoothId = 'USB\VID_8087&PID_0AAA\5&8F40D4C&0&14'
$barrotBluetoothId = 'USB\VID_33FA&PID_0010\5&8F40D4C&0&1'
$comparisonState = @{ status = 'starting'; restored = $false; driver_updated = $false }
$comparisonChanged = $false
function Set-ComparisonDeviceState([string]$deviceId, [bool]$enabled) {
    $currentDevice = Get-PnpDevice -InstanceId $deviceId
    if ($enabled -and $currentDevice.Status -eq 'OK') { return }
    if (-not $enabled -and $currentDevice.Problem -eq 'CM_PROB_DISABLED') { return }
    if ($enabled) {
        & pnputil.exe /enable-device $deviceId
    } else {
        & pnputil.exe /disable-device $deviceId
    }
    if ($LASTEXITCODE -ne 0) { throw "PnPUtil failed for $deviceId (exit $LASTEXITCODE)." }
}
New-Item -ItemType Directory -Path $comparisonDirectory -Force | Out-Null
Start-Transcript -Path $comparisonTranscript -Force | Out-Null
try {
    $comparisonIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $comparisonPrincipal = New-Object Security.Principal.WindowsPrincipal($comparisonIdentity)
    $comparisonState.administrator = $comparisonPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $comparisonState.administrator) { throw 'Administrator privileges were not granted.' }
    $intelDevice = Get-PnpDevice -InstanceId $intelBluetoothId
    $barrotDevice = Get-PnpDevice -InstanceId $barrotBluetoothId
    if ($intelDevice.Class -ne 'Bluetooth' -or $barrotDevice.Class -ne 'Bluetooth') {
        throw 'Unexpected device class; no adapter changes made.'
    }
    if ($intelDevice.Problem -ne 'CM_PROB_DISABLED' -or $barrotDevice.Status -ne 'OK') {
        throw 'Expected Intel disabled and BARROT enabled; no adapter changes made.'
    }
    $comparisonChanged = $true
    Set-ComparisonDeviceState $barrotBluetoothId $false
    Set-ComparisonDeviceState $intelBluetoothId $true
    Start-Sleep -Seconds 3
    $intelDevice = Get-PnpDevice -InstanceId $intelBluetoothId
    if ($intelDevice.Status -ne 'OK') { throw 'Intel Bluetooth did not start.' }
    $comparisonState.status = 'testing_intel'
    $comparisonState | ConvertTo-Json | Set-Content -LiteralPath $comparisonResult
    Write-Output 'Intel-only comparison: installed driver unchanged, BARROT temporarily disabled.'
    & $comparisonPython -u $comparisonProbe --order 1 2 3 --connection-mode default --output-dir $comparisonDirectory
    if ($LASTEXITCODE -ne 0) { throw "Intel probe exited with code $LASTEXITCODE" }
    $comparisonState.status = 'finished'
} catch {
    $comparisonState.status = 'error'
    $comparisonState.error = $_.Exception.Message
    Write-Output $_.Exception.Message
} finally {
    if ($comparisonChanged) {
        try {
            Set-ComparisonDeviceState $intelBluetoothId $false
            Set-ComparisonDeviceState $barrotBluetoothId $true
            Start-Sleep -Seconds 3
            $intelAfter = Get-PnpDevice -InstanceId $intelBluetoothId
            $barrotAfter = Get-PnpDevice -InstanceId $barrotBluetoothId
            $comparisonState.restored = ($intelAfter.Problem -eq 'CM_PROB_DISABLED' -and $barrotAfter.Status -eq 'OK')
            if (-not $comparisonState.restored) { throw 'Restoration verification failed.' }
            Write-Output 'Restored: Intel disabled, BARROT enabled.'
        } catch {
            $comparisonState.restoration_error = $_.Exception.Message
            Write-Output "Restoration error: $($_.Exception.Message)"
        }
    }
    $comparisonState | ConvertTo-Json | Set-Content -LiteralPath $comparisonResult
    Stop-Transcript | Out-Null
}
