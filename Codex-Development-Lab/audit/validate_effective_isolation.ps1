$ErrorActionPreference = 'Stop'
$labRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $labRoot
$env:DOCKER_CONFIG = Join-Path $env:TEMP 'codex-lab-history-cli'
$docker = 'C:\Users\aimihail\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe'
$compose = 'C:\Users\aimihail\AppData\Local\Programs\DockerDesktop\resources\cli-plugins\docker-compose.exe'
. (Join-Path $labRoot 'Test-LabIsolation.ps1')
$raw = & $compose --env-file NUL config --format json
if ($LASTEXITCODE -ne 0) { throw 'Effective Compose configuration could not be obtained.' }
$config = $raw | ConvertFrom-Json
Assert-LabIsolation $config $labRoot
Assert-LabVolumeMetadata -Configuration $config -DockerPath $docker
# Keep only structural evidence, never service environments or credentials.
$services = @{}
foreach ($service in $config.services.PSObject.Properties) {
    $services[$service.Name] = @{
        volumes=$service.Value.volumes; networks=$service.Value.networks;
        user=$service.Value.user; network_mode=$service.Value.network_mode;
        privileged=$service.Value.privileged; pid=$service.Value.pid
    }
}
@{name=$config.name;services=$services;volumes=$config.volumes;networks=$config.networks} |
    ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'performance-security\compose-structure.json') -Encoding UTF8
Write-Host 'Existing private volume metadata checked; no credentials were read.'
