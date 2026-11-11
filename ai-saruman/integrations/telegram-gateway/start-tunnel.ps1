<#
.SYNOPSIS
Open a loopback-only SSH tunnel to the Telegram gateway.
.DESCRIPTION
Default mode permits interactive SSH authentication. -Reconnect requires an explicit
private key file and working unattended key authentication (ssh-agent if encrypted).
First verify/store the VPS host key using a normal SSH login. This script refuses
unknown/changed host keys. It does not create keys, change SSH settings, store passwords,
register scheduled tasks, publish posts, or read the gateway API key.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.:-]*$')]
    [string]$Server,
    [ValidatePattern('^[a-z_][a-z0-9_-]*[$]?$')]
    [string]$User = 'root',
    [ValidateRange(1, 65535)]
    [int]$Port = 22,
    [ValidateRange(1, 65535)]
    [int]$LocalPort = 8787,
    [string]$IdentityFile,
    [switch]$Reconnect,
    [ValidateRange(1, 3600)]
    [int]$RetrySeconds = 10
)

$ErrorActionPreference = 'Stop'
$sshCommand = (Get-Command ssh.exe -ErrorAction Stop).Source
if ($Reconnect -and -not $IdentityFile) {
    throw '-Reconnect requires -IdentityFile and configured SSH key authentication.'
}
if ($IdentityFile) {
    if (-not (Test-Path -LiteralPath $IdentityFile -PathType Leaf)) {
        throw 'The specified SSH private key file does not exist.'
    }
    $IdentityFile = (Resolve-Path -LiteralPath $IdentityFile).Path
}

$sshArguments = @(
    '-N', '-T', '-p', "$Port",
    '-o', 'StrictHostKeyChecking=yes',
    '-o', 'ExitOnForwardFailure=yes',
    '-o', 'ServerAliveInterval=30',
    '-o', 'ServerAliveCountMax=3',
    '-o', 'ConnectTimeout=15',
    '-L', "127.0.0.1:${LocalPort}:127.0.0.1:8787"
)
if ($IdentityFile) {
    $sshArguments += @('-i', $IdentityFile, '-o', 'IdentitiesOnly=yes')
}
if ($Reconnect) {
    $sshArguments += @('-o', 'BatchMode=yes')
}
$sshArguments += "${User}@${Server}"

do {
    # Fail on a busy local port instead of repeatedly opening another tunnel.
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $LocalPort)
    try {
        $listener.Start()
    }
    catch {
        throw "Local port $LocalPort is unavailable. Stop the existing tunnel or choose another -LocalPort."
    }
    finally {
        $listener.Stop()
    }
    Write-Host "Tunnel: http://127.0.0.1:$LocalPort -> VPS gateway. Keep this window open; Ctrl+C to stop."
    & $sshCommand @sshArguments
    $sshExitCode = $LASTEXITCODE
    if (-not $Reconnect) {
        exit $sshExitCode
    }
    Write-Warning "SSH exited ($sshExitCode). Reconnecting in $RetrySeconds seconds. Ctrl+C to stop."
    Start-Sleep -Seconds $RetrySeconds
} while ($Reconnect)
