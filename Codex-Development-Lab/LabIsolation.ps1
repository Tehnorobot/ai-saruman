# Admission check for the effective, merged Compose configuration. No credentials are printed.
function Assert-LabIsolation {
    param([Parameter(Mandatory=$true)]$Configuration,
          [Parameter(Mandatory=$true)][string]$LabRoot)
    $labBase = [IO.Path]::GetFullPath($LabRoot).TrimEnd('\', '/')

    function Assert-LocalPath([string]$Candidate) {
        if (-not [IO.Path]::IsPathRooted($Candidate)) {
            $Candidate = Join-Path $labBase $Candidate
        }
        $absolute = [IO.Path]::GetFullPath($Candidate).TrimEnd('\', '/')
        if ($absolute -ne $labBase -and -not $absolute.StartsWith($labBase + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Isolation check: a host path escapes Codex-Development-Lab.'
        }
        # Reject junctions/symlinks at every existing ancestor, including the Lab itself.
        $current = $absolute
        while ($current) {
            if (Test-Path -LiteralPath $current) {
                $item = Get-Item -LiteralPath $current -Force
                if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw 'Isolation check: a host mount/build path contains a reparse point.'
                }
            }
            $parent = [IO.Directory]::GetParent($current)
            if ($null -eq $parent) { break }
            $current = $parent.FullName
        }
    }

    Assert-LocalPath $labBase
    if ($Configuration.name -ne 'codex-development-lab') { throw 'Isolation check: unexpected Compose project.' }
    $known = @('init-permissions','dashboard','worker','agent','runner','backup','storage','egress','provider-gateway','agent-dslab')
    foreach ($requiredService in $known) {
        if (-not $Configuration.services.$requiredService) { throw 'Isolation check: required service is missing.' }
    }
    $bindAccess = @{
        'init-permissions' = @{'data'=$false;'projects'=$false;'artifacts'=$false;'backups'=$false;'backup-requests'=$false;'agent-tasks'=$false};
        'dashboard' = @{'data'=$false;'projects'=$false;'artifacts'=$true;'agent-tasks'=$true;'backups'=$true};
        'worker' = @{'data'=$false;'projects'=$false;'artifacts'=$false;'backup-requests'=$false;'agent-tasks'=$false};
        'agent' = @{'projects'=$false;'agent-tasks'=$false};
        'agent-dslab' = @{'projects'=$false;'agent-tasks'=$false};
        'backup' = @{'data'=$false;'projects'=$true;'artifacts'=$true;'backups'=$false;'backup-requests'=$false};
        'storage' = @{'artifacts'=$true}
    }
    foreach ($property in $Configuration.services.PSObject.Properties) {
        $name = $property.Name
        $service = $property.Value
        if ($known -notcontains $name) { throw 'Isolation check: unexpected service from Compose override.' }
        if ($service.privileged -or $service.pid -eq 'host' -or $service.ipc -eq 'host' -or
                $service.network_mode -eq 'host' -or $service.use_api_socket -or
                $service.devices -or $service.device_cgroup_rules -or $service.gpus -or
                $service.volumes_from -or $service.secrets -or $service.configs) {
            throw 'Isolation check: host privileges/devices or external configuration are forbidden.'
        }
        if ($service.post_start -or $service.pre_stop) {
            throw 'Isolation check: service execution hooks are forbidden.'
        }
        if (@($service.cap_drop) -notcontains 'ALL' -or
                @($service.security_opt) -notcontains 'no-new-privileges:true') {
            throw 'Isolation check: capabilities must be dropped and privilege escalation disabled.'
        }
        $allowedCaps = if ($name -in @('runner','init-permissions')) { @('CHOWN','DAC_OVERRIDE','FOWNER') } else { @() }
        foreach ($capability in @($service.cap_add)) {
            if ($capability -and $allowedCaps -notcontains $capability) { throw 'Isolation check: unexpected capability.' }
        }
        if ($name -notin @('runner','init-permissions') -and $service.user -ne '1000:1000') {
            throw 'Isolation check: application services must use uid/gid 1000.'
        }
        if (($name -eq 'runner' -and $service.user -ne '0:1000') -or
                ($name -eq 'init-permissions' -and $service.user -ne '0:0')) {
            throw 'Isolation check: unexpected trusted service identity.'
        }
        if ($name -in @('runner','egress','init-permissions','provider-gateway') -and -not $service.read_only) {
            throw 'Isolation check: trusted service root filesystem must be read-only.'
        }
        if ($name -eq 'runner') {
            # This is the sole Engine client: its execution must not be overridden.
            if ($service.image -ne 'codex-lab-runner:local' -or $service.entrypoint -or $service.command -or
                    $service.build.dockerfile -ne 'Dockerfile.runner' -or
                    [IO.Path]::GetFullPath($service.build.context).TrimEnd('\','/') -ne $labBase -or
                    @($service.environment.PSObject.Properties.Name).Count -ne 1 -or
                    $service.environment.LAB_RUNNER_IMAGE -ne 'codex-lab-worker:local') {
                throw 'Isolation check: trusted runner execution cannot be overridden.'
            }
            $runnerHealth = @('CMD','python3','-c',"import socket;s=socket.socket(socket.AF_UNIX);s.settimeout(2);s.connect('/run/lab-runner/runner.sock');s.close()")
            if (($service.healthcheck.test -join "`n") -cne ($runnerHealth -join "`n")) {
                throw 'Isolation check: trusted runner healthcheck cannot be overridden.'
            }
        }
        if ($name -eq 'egress') {
            $egressCommand = @('python3','-I','/opt/lab/egress.py')
            $egressHealth = @('CMD','python3','-c',"import socket;s=socket.create_connection(('127.0.0.1',3128),2);s.close()")
            if ($service.image -ne 'codex-lab-egress:local' -or $service.entrypoint -or
                    ($service.command -join "`n") -cne ($egressCommand -join "`n") -or
                    ($service.healthcheck.test -join "`n") -cne ($egressHealth -join "`n") -or
                    $service.build.dockerfile -ne 'Dockerfile.egress' -or
                    [IO.Path]::GetFullPath($service.build.context).TrimEnd('\','/') -ne $labBase -or
                    $service.environment) {
                throw 'Isolation check: filtered proxy execution cannot be overridden.'
            }
        }
        if ($name -eq 'provider-gateway') {
            $gatewayEnvironment = @('DSLAB_API_KEY','DSLAB_CONTROL_TOKEN','DSLAB_BASE_URL','DSLAB_USAGE_DB',
                'HTTP_PROXY','HTTPS_PROXY','NO_PROXY')
            $gatewayHealth = @('CMD','python','-c',"import socket;s=socket.socket(socket.AF_UNIX);s.settimeout(2);s.connect('/run/lab-provider/gateway.sock');s.close()")
            if ($service.image -ne 'codex-lab-provider:local' -or $service.entrypoint -or $service.command -or
                    ($service.healthcheck.test -join "`n") -cne ($gatewayHealth -join "`n") -or
                    $service.build.dockerfile -ne 'Dockerfile.gateway' -or
                    [IO.Path]::GetFullPath($service.build.context).TrimEnd('\','/') -ne $labBase -or
                    @($service.environment.PSObject.Properties.Name).Count -ne $gatewayEnvironment.Count -or
                    @($service.environment.PSObject.Properties.Name | Where-Object { $gatewayEnvironment -notcontains $_ }).Count -or
                    $service.environment.DSLAB_BASE_URL -notin @('https://api.dslab.tech','https://api.dslab.tech/v1') -or
                    $service.environment.DSLAB_USAGE_DB -ne '/usage/usage.sqlite3' -or
                    $service.environment.HTTP_PROXY -ne 'http://lab-egress:3128' -or
                    $service.environment.HTTPS_PROXY -ne 'http://lab-egress:3128' -or
                    $service.environment.NO_PROXY -ne 'localhost,127.0.0.1,::1') {
                throw 'Isolation check: trusted provider gateway execution cannot be overridden.'
            }
        }
        if ($name -in @('runner','worker','backup','init-permissions') -and $service.network_mode -ne 'none') {
            throw 'Isolation check: controller services must have no TCP network.'
        }
        if ($name -in @('agent','agent-dslab','provider-gateway')) {
            if ($service.network_mode -or @($service.networks.PSObject.Properties.Name).Count -ne 1 -or
                    @($service.networks.PSObject.Properties.Name) -notcontains 'agent_internal') {
                throw 'Isolation check: agent must use only its isolated network.'
            }
        }
        if ($name -eq 'egress') {
            $egressNetworks = @($service.networks.PSObject.Properties.Name)
            if ($service.network_mode -or $egressNetworks.Count -ne 3 -or
                    @('agent_internal','test_internal','egress_public' | Where-Object { $egressNetworks -notcontains $_ }).Count) {
                throw 'Isolation check: unexpected proxy networks.'
            }
        }
        if ($name -ne 'provider-gateway' -and $service.environment.DSLAB_API_KEY) {
            throw 'Isolation check: only the provider gateway may receive the paid API key.'
        }
        if ($name -notin @('provider-gateway','dashboard','worker') -and $service.environment.DSLAB_CONTROL_TOKEN) {
            throw 'Isolation check: controller credential is forbidden in project execution containers.'
        }
        foreach ($port in @($service.ports)) {
            if ($port -and ($name -ne 'dashboard' -or $port.host_ip -ne '127.0.0.1' -or $port.target -ne 8080)) {
                throw 'Isolation check: only the loopback dashboard port may be published.'
            }
        }
        if ($service.build) {
            $context = if ($service.build -is [string]) { $service.build } else { $service.build.context }
            Assert-LocalPath $context
            if ($service.build.additional_contexts -or $service.build.ssh -or $service.build.secrets) {
                throw 'Isolation check: external build inputs are forbidden.'
            }
            if ($service.build.dockerfile) {
                Assert-LocalPath (Join-Path $context $service.build.dockerfile)
            }
        }
        foreach ($mount in @($service.volumes)) {
            if (-not $mount) { continue }
            if ($mount.type -eq 'bind') {
                if ($mount.source -eq '/var/run/docker.sock') {
                    if ($name -ne 'runner' -or $mount.target -ne '/var/run/docker.sock') {
                        throw 'Isolation check: only the restricted runner may access the Engine socket.'
                    }
                } else {
                    Assert-LocalPath $mount.source
                    $leaf = $mount.target.TrimStart('/')
                    if (-not $bindAccess.ContainsKey($name) -or -not $bindAccess[$name].ContainsKey($leaf) -or
                            [IO.Path]::GetFullPath($mount.source).TrimEnd('\','/') -ne (Join-Path $labBase $leaf) -or
                            [bool]$mount.read_only -ne $bindAccess[$name][$leaf]) {
                        throw 'Isolation check: host mounts must match the service directory allowlist.'
                    }
                }
            } elseif ($mount.type -eq 'volume') {
                $allowedTargets = @{
                    'runner_control' = @{'runner'='/run/lab-runner';'worker'='/run/lab-runner'};
                    'runner_state' = @{'runner'='/state'};
                    'codex_auth' = @{'agent'='/home/lab/.codex'};
                    'gradle_cache' = @{'agent'='/home/lab/.gradle'};
                    'dslab_gradle_cache' = @{'agent-dslab'='/home/lab/.gradle'};
                    'provider_socket' = @{'init-permissions'='/run/lab-provider';'provider-gateway'='/run/lab-provider';'worker'='/run/lab-provider';'dashboard'='/run/lab-provider';'agent-dslab'='/run/lab-provider'};
                    'provider_usage' = @{'init-permissions'='/usage';'provider-gateway'='/usage'};
                    's3_state' = @{'dashboard'='/s3-state';'storage'='/s3-state'}
                }
                if (-not $allowedTargets.ContainsKey($mount.source) -or
                        -not $allowedTargets[$mount.source].ContainsKey($name) -or
                        $allowedTargets[$mount.source][$name] -ne $mount.target) {
                    throw 'Isolation check: unexpected private-volume access.'
                }
                $mustReadOnly = ($name -eq 'worker' -and $mount.source -eq 'runner_control') -or
                                ($name -eq 'dashboard' -and $mount.source -eq 's3_state') -or
                                ($mount.source -eq 'provider_socket' -and $name -notin @('provider-gateway','init-permissions'))
                if ([bool]$mount.read_only -ne $mustReadOnly) {
                    throw 'Isolation check: unexpected private-volume write access.'
                }
            } else { throw 'Isolation check: unsupported mount type.' }
        }
    }
    foreach ($volume in $Configuration.volumes.PSObject.Properties) {
        if ($volume.Value.external -or $volume.Value.driver_opts -or
                ($volume.Value.driver -and $volume.Value.driver -ne 'local') -or
                $volume.Value.name -ne ('codex-development-lab_' + $volume.Name)) {
            throw 'Isolation check: volumes must be private local Docker volumes without host bind options.'
        }
    }
    foreach ($networkName in @('agent_internal','test_internal')) {
        $network = $Configuration.networks.$networkName
        if (-not $network -or -not $network.internal -or $network.external -or $network.driver -ne 'bridge' -or
                $network.driver_opts.'com.docker.network.bridge.gateway_mode_ipv4' -ne 'isolated' -or
                $network.driver_opts.'com.docker.network.bridge.gateway_mode_ipv6' -ne 'isolated' -or
                $network.name -ne ('codex-development-lab_' + $networkName)) {
            throw 'Isolation check: isolated internal networks without a host gateway are required.'
        }
    }
    Write-Host 'Isolation check passed: host paths confined to this Lab; agent networking isolated.' -ForegroundColor Green
}

function Assert-LabVolumeMetadata {
    param([Parameter(Mandatory=$true)]$Configuration, [string]$DockerPath = 'docker')
    $existing = @(& $DockerPath volume ls --format '{{.Name}}')
    if ($LASTEXITCODE -ne 0) { throw 'Isolation check: cannot inspect existing volume names.' }
    foreach ($volume in $Configuration.volumes.PSObject.Properties) {
        $volumeName = $volume.Value.name
        if ($existing -notcontains $volumeName) { continue }
        $raw = & $DockerPath volume inspect $volumeName --format '{{json .}}'
        if ($LASTEXITCODE -ne 0) { throw 'Isolation check: cannot inspect existing private volume metadata.' }
        $metadata = $raw | ConvertFrom-Json
        if ($metadata.Driver -ne 'local' -or
                ($null -ne $metadata.Options -and @($metadata.Options.PSObject.Properties).Count -gt 0)) {
            throw 'Isolation check: an existing volume uses a custom driver or host bind options.'
        }
    }
}
