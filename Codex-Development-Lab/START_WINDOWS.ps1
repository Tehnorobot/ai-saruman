$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker CLI not found. Install and start Docker Desktop with WSL2 enabled.'
}

docker info --format '{{.ServerVersion}}' | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Docker daemon is not available. Start Docker Desktop and try again.'
}

& docker compose version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Docker Compose v2 not available. Update Docker Desktop.'
}

# Old build containers used direct host binds and are not Compose services.
# Never silently leave one running while switching to the restricted launcher.
$legacyRunners = @(& docker ps -aq --filter 'label=codex-lab.role=project-test' --filter 'name=^/codex-lab-test-')
if ($LASTEXITCODE -ne 0) {
    throw 'Cannot check legacy build containers. Inspect Docker status before upgrading.'
}
if ($legacyRunners.Count -gt 0) {
    throw 'Legacy build containers remain. Pause the old worker and follow ISOLATION_RU.md before upgrading.'
}
if (Test-Path -LiteralPath 'projects') {
    foreach ($projectDirectory in Get-ChildItem -LiteralPath 'projects' -Directory) {
        $runnerMarker = Join-Path $projectDirectory.FullName '.lab-runner.json'
        if (Test-Path -LiteralPath $runnerMarker) {
            $runnerRecord = Get-Content -LiteralPath $runnerMarker -Raw | ConvertFrom-Json
            if ($runnerRecord.container -cnotmatch '^[0-9a-f]{32}$') {
                throw "Legacy runner marker remains in $($projectDirectory.Name). Follow ISOLATION_RU.md; do not discard an active run."
            }
        }
    }
}

if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host 'Created .env from .env.example. Set S3 credentials there when needed.' -ForegroundColor Yellow
}

. (Join-Path $PSScriptRoot 'Test-LabIsolation.ps1')
$effectiveConfig = & docker compose config --format json
if ($LASTEXITCODE -ne 0) { throw 'Cannot validate the effective Compose configuration.' }
$checkedConfig = $effectiveConfig | ConvertFrom-Json
Assert-LabIsolation -Configuration $checkedConfig -LabRoot $PSScriptRoot
Assert-LabVolumeMetadata -Configuration $checkedConfig

# Build each distinct local image BEFORE starting image-only services.
# Otherwise Compose may try Docker Hub for codex-lab-service:local during first boot.
Write-Host 'Building local images: dashboard, worker, restricted runner, filtered egress...' -ForegroundColor Cyan
& docker compose build dashboard worker runner egress provider-gateway
if ($LASTEXITCODE -ne 0) {
    throw 'Local image build failed. Inspect the build error above. Do not start services yet.'
}

Write-Host 'Starting containers using the locally built images...' -ForegroundColor Cyan
& docker compose up -d --no-build --force-recreate
if ($LASTEXITCODE -ne 0) {
    throw 'Container start failed. Inspect Docker output or run: docker compose logs'
}

Write-Host 'Dashboard: http://localhost:8080' -ForegroundColor Green
Write-Host 'One-time Codex authentication:'
Write-Host 'docker compose run --rm agent codex login --device-auth' -ForegroundColor Cyan
Write-Host 'Authentication status:'
Write-Host 'docker compose run --rm agent codex login status'
Write-Host 'Worker logs:'
Write-Host 'docker compose logs -f worker'
