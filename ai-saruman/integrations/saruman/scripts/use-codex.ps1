# Make the installed CLI available in this PowerShell session and child processes.
$SarumanCodexCommand = Get-Command codex -CommandType Application -ErrorAction SilentlyContinue
if (-not $SarumanCodexCommand) {
    $SarumanExtensions = Join-Path $env:USERPROFILE '.vscode\extensions'
    $SarumanCodexBinary = Get-ChildItem -LiteralPath $SarumanExtensions -Directory `
        -Filter 'openai.chatgpt-*' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        ForEach-Object { Join-Path $_.FullName 'bin\windows-x86_64\codex.exe' } |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
    if (-not $SarumanCodexBinary) {
        throw 'Codex CLI not found. Install the Codex CLI or the OpenAI Codex extension for VS Code.'
    }
    $SarumanCodexDirectory = Split-Path -Parent $SarumanCodexBinary
    $env:Path = "$SarumanCodexDirectory;$env:Path"
}
