# Sync bundled skills into existing Codex Development Lab project workspaces.
# Compatible with Windows PowerShell 5.1; ASCII-only source avoids legacy encoding problems.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Source = Join-Path $Root 'skills'
$Projects = Join-Path $Root 'projects'
$Names = @('general-development', 'code-quality', 'testing-evidence', 'fabric-minecraft-1.21.11', 'minecraft-mod-compatibility')

if (-not (Test-Path (Join-Path $Source 'general-development\SKILL.md'))) {
    throw 'Skills folder is missing. Extract the archive into the Codex Development Lab root.'
}
if (-not (Test-Path $Projects)) {
    Write-Host 'No existing projects folder. New projects will use the updated image after rebuilding.'
    exit 0
}

$Updated = 0
Get-ChildItem -LiteralPath $Projects -Directory | ForEach-Object {
    $Workspace = Join-Path $_.FullName 'workspace'
    if (-not (Test-Path -LiteralPath $Workspace -PathType Container)) { return }
    $Dest = Join-Path $Workspace '.agents\skills'
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    foreach ($Name in $Names) {
        $From = Join-Path $Source $Name
        $To = Join-Path $Dest $Name
        if (-not (Test-Path -LiteralPath $From -PathType Container)) { throw "Missing standard skill: $Name" }
        if (Test-Path -LiteralPath $To) { Remove-Item -LiteralPath $To -Recurse -Force }
        Copy-Item -LiteralPath $From -Destination $To -Recurse -Force
    }
    # Previous archive variants used a different folder name for this same standard Skill.
    $Legacy = Join-Path $Dest 'fabric-minecraft-12111'
    if (Test-Path -LiteralPath $Legacy) { Remove-Item -LiteralPath $Legacy -Recurse -Force }
    $script:Updated += 1
    Write-Host ("Updated workspace: " + $_.Name)
}
Write-Host ("Updated " + $Updated + " existing workspace(s). Rebuild worker and agent images next.")
