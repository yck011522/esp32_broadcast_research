# Start the authorized two-hour experiment in a hidden, independent process.
$ErrorActionPreference = 'Stop'
$experimentRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$experimentPython = Join-Path $experimentRoot '.venv\Scripts\python.exe'
$experimentScript = Join-Path $PSScriptRoot 'test_13_ble_modem.py'
if (-not (Test-Path -LiteralPath $experimentPython)) { throw 'Repository .venv Python is missing.' }
$experimentStamp = [DateTime]::UtcNow.ToString('yyyyMMdd_HHmmss_ffffff')
$experimentDirectory = Join-Path $PSScriptRoot "results\runs\endurance_$experimentStamp"
New-Item -ItemType Directory -Path $experimentDirectory | Out-Null
$experimentConsole = Join-Path $experimentDirectory 'console.log'
$experimentErrors = Join-Path $experimentDirectory 'stderr.log'
$experimentArguments = @('-u', "`"$experimentScript`"", '--hours', '2', '--server-ports',
                         'COM4', 'COM5', '--output-dir', "`"$experimentDirectory`"")
$experimentStarted = [DateTime]::UtcNow
$experimentProcess = Start-Process -FilePath $experimentPython -ArgumentList $experimentArguments `
    -WorkingDirectory $experimentRoot -WindowStyle Hidden -RedirectStandardOutput $experimentConsole `
    -RedirectStandardError $experimentErrors -PassThru
$experimentMetadata = [ordered]@{
    process_id = $experimentProcess.Id
    started_utc = $experimentStarted.ToString('o')
    planned_traffic_seconds = 7200
    estimated_finish_utc = $experimentStarted.AddSeconds(7220).ToString('o')
    python = $experimentPython
    script = $experimentScript
    arguments = $experimentArguments
    run_directory = $experimentDirectory
    console_log = $experimentConsole
    stderr_log = $experimentErrors
    status_note = 'Started in background; the final *.summary.json determines the result.'
}
$experimentMetadata | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $experimentDirectory 'launch.json') -Encoding UTF8
Write-Output "Started PID $($experimentProcess.Id): $experimentDirectory"
Write-Output "Console: $experimentConsole"
