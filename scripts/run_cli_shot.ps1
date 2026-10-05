# Launch a CLI command in an isolated cmd window and capture that exact window.
# Example:
#   powershell -File run_cli_shot.ps1 -Command "py app.py" -Out screenshots\result.png
# Command and Out may instead come from LAB_CLI_COMMAND and LAB_CLI_OUT.
param(
    [string]$Command,
    [string]$Out,
    [string]$Title = "Lab CLI evidence",
    [int]$WaitMs = 1200,
    [switch]$KeepOpen
)

$ErrorActionPreference = 'Stop'
if (-not $Command) { $Command = $env:LAB_CLI_COMMAND }
if (-not $Out) { $Out = $env:LAB_CLI_OUT }
if (-not $Command) { throw 'Specify Command or LAB_CLI_COMMAND' }
if (-not $Out) { throw 'Specify Out or LAB_CLI_OUT' }
if ($WaitMs -lt 250) { throw 'WaitMs must be at least 250 ms' }

$scriptDir = Split-Path -Parent $PSCommandPath
$uniqueTitle = "$Title [$([guid]::NewGuid().ToString('N').Substring(0, 8))]"
$escapedTitle = $uniqueTitle.Replace('&', '^&')
$cmdLine = "chcp 65001>nul & title $escapedTitle & $Command"
if ($KeepOpen) {
    $cmdLine += ' & echo. & echo Press any key to close... & pause>nul'
}

$process = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/k', $cmdLine) -WorkingDirectory (Get-Location) -PassThru
Start-Sleep -Milliseconds $WaitMs

$shot = Join-Path $scriptDir 'shot.ps1'
try {
    & $shot -Title ([regex]::Escape($uniqueTitle)) -Out $Out -Delay 200
    if (-not $?) { throw 'shot.ps1 failed' }
} finally {
    if (-not $KeepOpen -and -not $process.HasExited) {
        $process.CloseMainWindow() | Out-Null
        Start-Sleep -Milliseconds 200
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
    }
}
