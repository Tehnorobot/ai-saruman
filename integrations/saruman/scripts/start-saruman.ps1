param(
    [switch]$Doctor,
    [switch]$AutoStart,
    [switch]$NoStt,
    [switch]$NoFilter,
    [string]$EnvFile = ".env.saruman"
)
$ErrorActionPreference = "Stop"
$SarumanRoot = Split-Path -Parent $PSScriptRoot
Push-Location $SarumanRoot
try {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "Install uv for native Windows first (see docs/SARUMAN_WINDOWS.md)."
    }
    if (-not (Test-Path $EnvFile -PathType Leaf)) {
        throw "Copy .env.saruman.example to .env.saruman and configure it first."
    }
    $env:PYTHONPATH = Join-Path $SarumanRoot "src"
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONUTF8 = "1"
    $SarumanCommand = if ($Doctor) { "doctor" } else { "run" }
    $SarumanArgs = @("run", "--locked", "--python", "3.10", "--no-default-groups", "--group", "collectors",
                     "saruman-minecraft", $SarumanCommand, "--env-file", $EnvFile)
    if ($Doctor) { $SarumanArgs += "--live" }
    if ($AutoStart) { $SarumanArgs += "--auto-start" }
    if ($NoStt) { $SarumanArgs += "--no-stt" }
    if ($NoFilter) { $SarumanArgs += "--no-filter" }
    & uv @SarumanArgs
    $SarumanExit = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $SarumanExit
