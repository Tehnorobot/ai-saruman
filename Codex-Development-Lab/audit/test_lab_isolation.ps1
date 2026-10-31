$ErrorActionPreference = 'Stop'
$labRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $labRoot 'Test-LabIsolation.ps1')
$env:DOCKER_CONFIG = Join-Path $env:TEMP 'codex-lab-history-cli'
$compose = 'C:\Users\aimihail\AppData\Local\Programs\DockerDesktop\resources\cli-plugins\docker-compose.exe'
$json = & $compose --project-directory $labRoot --env-file NUL config --format json
if ($LASTEXITCODE -ne 0) { throw 'Cannot obtain fixture configuration.' }
$baseline = $json | ConvertFrom-Json
Assert-LabIsolation $baseline $labRoot
$results = @()
function Reject-Override([string]$Label, [scriptblock]$Mutate) {
    $candidate = $json | ConvertFrom-Json
    & $Mutate $candidate
    try { Assert-LabIsolation $candidate $labRoot | Out-Null }
    catch { return [pscustomobject]@{case=$Label; rejected=$true} }
    throw "Unsafe fixture was admitted: $Label"
}
$results += Reject-Override 'host-directory-outside-lab' {
    param($c) $c.services.agent.volumes[0].source = Split-Path -Parent $labRoot
}
$results += Reject-Override 'whole-lab-exposes-secrets' {
    param($c) $c.services.agent.volumes[0].source = $labRoot
}
$results += Reject-Override 'lab-env-file-exposes-secrets' {
    param($c) $c.services.agent.volumes[0].source = Join-Path $labRoot '.env'
}
$results += Reject-Override 'privileged-agent' {
    param($c) $c.services.agent | Add-Member -Force privileged $true
}
$results += Reject-Override 'agent-host-network' {
    param($c) $c.services.agent | Add-Member -Force network_mode 'host'
}
$results += Reject-Override 'agent-external-network' {
    param($c) $c.services.agent.networks = [pscustomobject]@{default=@{}}
}
$results += Reject-Override 'internal-network-with-host-gateway' {
    param($c) $c.networks.test_internal.driver_opts.'com.docker.network.bridge.gateway_mode_ipv4'='nat'
}
$results += Reject-Override 'internal-network-with-unexpected-driver' {
    param($c) $c.networks.agent_internal.driver='macvlan'
}
$results += Reject-Override 'unexpected-agent-capability' {
    param($c) $c.services.agent | Add-Member -Force cap_add @('SYS_ADMIN')
}
$results += Reject-Override 'runner-command-engine-bypass' {
    param($c) $c.services.runner | Add-Member -Force command @('python3','-c','arbitrary Engine requests')
}
$results += Reject-Override 'runner-entrypoint-engine-bypass' {
    param($c) $c.services.runner | Add-Member -Force entrypoint @('sh')
}
$results += Reject-Override 'runner-image-engine-bypass' {
    param($c) $c.services.runner.image='alpine:3.21'
}
$results += Reject-Override 'runner-build-engine-bypass' {
    param($c) $c.services.runner.build.dockerfile='Dockerfile.worker'
}
$results += Reject-Override 'runner-env-engine-bypass' {
    param($c) $c.services.runner.environment | Add-Member -Force PYTHONPATH '/state'
}
$results += Reject-Override 'runner-healthcheck-engine-bypass' {
    param($c) $c.services.runner.healthcheck.test=@('CMD','python3','-c','arbitrary Engine requests')
}
$results += Reject-Override 'runner-hook-engine-bypass' {
    param($c) $c.services.runner | Add-Member -Force post_start @(@{command='arbitrary Engine requests'})
}
$results += Reject-Override 'proxy-command-LAN-bypass' {
    param($c) $c.services.egress.command=@('python3','-m','http.server')
}
$results += Reject-Override 'proxy-healthcheck-LAN-bypass' {
    param($c) $c.services.egress.healthcheck.test=@('CMD','sh','-c','unrestricted proxy')
}
$results += Reject-Override 'proxy-entrypoint-LAN-bypass' {
    param($c) $c.services.egress | Add-Member -Force entrypoint @('sh')
}
$results += Reject-Override 'proxy-image-LAN-bypass' {
    param($c) $c.services.egress.image='alpine:3.21'
}
$results += Reject-Override 'proxy-env-LAN-bypass' {
    param($c) $c.services.egress | Add-Member -Force environment @{PYTHONPATH='/tmp'}
}
$results += Reject-Override 'writable-control-volume-to-worker' {
    param($c) ($c.services.worker.volumes | Where-Object target -eq '/run/lab-runner').read_only=$false
}
$results += Reject-Override 'engine-socket-to-agent' {
    param($c) $c.services.agent.volumes[0].source='/var/run/docker.sock'; $c.services.agent.volumes[0].target='/var/run/docker.sock'
}
$results += Reject-Override 'named-volume-bind-bypass' {
    param($c) $c.volumes.gradle_cache | Add-Member -Force driver_opts @{type='none';o='bind';device='C:\'}
}
$results += Reject-Override 'unknown-service-from-override' {
    param($c) $c.services | Add-Member -Force unexpected $c.services.agent
}
$results += Reject-Override 'dashboard-exposed-to-LAN' {
    param($c) $c.services.dashboard.ports[0].host_ip='0.0.0.0'
}
$results += Reject-Override 'writable-artifacts-to-dashboard' {
    param($c) ($c.services.dashboard.volumes | Where-Object target -eq '/artifacts').read_only=$false
}
$link = Join-Path $labRoot 'audit\isolation-fixture-link'
if (Test-Path -LiteralPath $link) { throw 'Junction fixture already exists.' }
try {
    New-Item -ItemType Junction -Path $link -Target (Join-Path $labRoot 'projects') | Out-Null
    $results += Reject-Override 'junction-inside-lab' {
        param($c) $c.services.agent.volumes[0].source=$link
    }
} finally {
    if ([IO.Path]::GetFullPath($link).StartsWith($labRoot + '\audit\') -and
            (Get-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue).LinkType -eq 'Junction') {
        [IO.Directory]::Delete($link)
    }
}
$results += Reject-Override 'paid-api-key-in-agent' {
    param($c) $c.services.'agent-dslab'.environment | Add-Member -Force DSLAB_API_KEY 'synthetic-key'
}
$results += Reject-Override 'control-key-in-agent' {
    param($c) $c.services.'agent-dslab'.environment | Add-Member -Force DSLAB_CONTROL_TOKEN 'synthetic-control'
}
$results += Reject-Override 'provider-gateway-command-override' {
    param($c) $c.services.'provider-gateway' | Add-Member -Force command @('sh','-c','echo unsafe')
}
$results += Reject-Override 'provider-gateway-endpoint-override' {
    param($c) $c.services.'provider-gateway'.environment.DSLAB_BASE_URL='https://example.com'
}
$results += Reject-Override 'writable-provider-socket-in-agent' {
    param($c) ($c.services.'agent-dslab'.volumes | Where-Object source -eq 'provider_socket').read_only=$false
}
$result = [pscustomobject]@{status='PASS'; canonical_config_accepted=$true; unsafe_cases=$results}
$result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'isolation-admission-result.json') -Encoding UTF8
Write-Host "Admission fixtures: $($results.Count) unsafe configurations rejected."
