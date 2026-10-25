# aider-local: Aider (the hands) driven by a local Ollama model (the brain).
# A fallback for when Claude usage runs out. Run it inside any git repo.
#
#   aider-local                          default brain in architect mode, interactive chat
#   aider-local --brain 7b src\app.py    pick a brain, start with a file in the chat
#   aider-local --normal                 skip the planning pass (faster, less careful)
#   aider-local --list                   show the brains and whether they are downloaded
#
# Own options: --brain <7b|14b|16b|ollama-tag>, --hand <...>, --normal, --list.
# Everything else goes to Aider unchanged (e.g. --message "...", file names).
# Default brain: $env:AIDER_LOCAL_BRAIN if set, else $DefaultBrain below.
#
# Defaults come from the swarm benchmark (4 demo tasks, 2026-10-21; see README.md):
#   14b + architect 3/4 | 14b 2/4 | 7b + architect 2/4 | 7b 1/4 | 16b 1/4 | 16b + architect 0/4

$ErrorActionPreference = 'Stop'

$Brains = [ordered]@{
    '7b'  = 'qwen2.5-coder:7b'       # 4.7 GB, 100% GPU, fastest
    '14b' = 'qwen2.5-coder:14b'      # 9.0 GB, tight on 11 GB
    '16b' = 'deepseek-coder-v2:16b'  # 8.9 GB mixture-of-experts, fast for its size
    '30b' = 'qwen3-coder:30b'        # 18 GB mixture-of-experts, about half on the CPU at 12 GB
}
$DefaultBrain = '14b'
$DefaultArchitect = $true   # plan, then edit: +1 task for both Qwen models in the benchmark
$OllamaBase = 'http://127.0.0.1:11434'
$Settings = Join-Path $PSScriptRoot 'model.settings.yml'

function Resolve-Tag([string]$name) {
    if ($Brains.Contains($name)) { return $Brains[$name] }
    if ($name -match ':') { return $name }
    throw "Unknown brain '$name'. Use one of: $($Brains.Keys -join ', '), or a full Ollama tag like qwen2.5-coder:7b"
}

function Test-Ollama {
    try { Invoke-RestMethod "$OllamaBase/api/version" -TimeoutSec 2 | Out-Null; return $true } catch { return $false }
}

function Get-InstalledTags {
    try { return @((Invoke-RestMethod "$OllamaBase/api/tags" -TimeoutSec 5).models | ForEach-Object { $_.name }) } catch { return @() }
}

function Start-Ollama {
    if (Test-Ollama) { return }
    $exe = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    if (-not (Test-Path $exe)) {
        $cmd = Get-Command ollama -ErrorAction SilentlyContinue
        if (-not $cmd) { throw 'Ollama is not installed (winget install Ollama.Ollama).' }
        $exe = $cmd.Source
    }
    # Models live on E: (see OLLAMA_MODELS); make sure a fresh server sees the same store.
    $store = [Environment]::GetEnvironmentVariable('OLLAMA_MODELS', 'User')
    if ($store) { $env:OLLAMA_MODELS = $store }
    Write-Host 'Starting Ollama server...' -ForegroundColor DarkGray
    Start-Process -WindowStyle Hidden -FilePath $exe -ArgumentList 'serve'
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Ollama) { return }
    }
    throw "Ollama did not start within 30 s. Try running 'ollama serve' in another terminal."
}

# --- parse our own options; pass the rest through to Aider ---------------------
$brainName = if ($env:AIDER_LOCAL_BRAIN) { $env:AIDER_LOCAL_BRAIN } else { $DefaultBrain }
$handName = $null
$list = $false
$architect = $DefaultArchitect
$passThrough = New-Object System.Collections.Generic.List[string]
for ($i = 0; $i -lt $args.Count; $i++) {
    $a = [string]$args[$i]
    if ($a -eq '--brain' -and $i + 1 -lt $args.Count) { $brainName = [string]$args[++$i] }
    elseif ($a -eq '--hand' -and $i + 1 -lt $args.Count) { $handName = [string]$args[++$i] }
    elseif ($a -eq '--list') { $list = $true }
    elseif ($a -eq '--normal') { $architect = $false }
    elseif ($a -eq '--architect') { $architect = $true }
    else { $passThrough.Add($a) }
}

Start-Ollama
$installed = Get-InstalledTags

if ($list) {
    foreach ($k in $Brains.Keys) {
        $tag = $Brains[$k]
        $mark = if ($installed -contains $tag) { 'downloaded' } else { "missing (ollama pull $tag)" }
        $def = if ($k -eq $brainName) { '  <- default' } else { '' }
        '{0,-4} {1,-24} {2}{3}' -f $k, $tag, $mark, $def
    }
    exit 0
}

$brain = Resolve-Tag $brainName
foreach ($tag in @($brain) + $(if ($handName) { @(Resolve-Tag $handName) } else { @() })) {
    if ($installed -notcontains $tag) { throw "Model '$tag' is not downloaded. Run: ollama pull $tag" }
}

$aider = Get-Command aider -ErrorAction SilentlyContinue
if (-not $aider) { throw 'Aider is not installed. Run: uv tool install --python 3.12 aider-chat' }

$aiderArgs = @(
    '--model', "ollama_chat/$brain",
    '--model-settings-file', $Settings,
    '--chat-language', 'English',   # otherwise Aider follows the Windows locale
    '--no-show-model-warnings',
    '--no-analytics',
    '--no-check-update'
)
$mode = 'normal (edits directly)'
if ($handName) {
    $hand = Resolve-Tag $handName
    $aiderArgs += @('--editor-model', "ollama_chat/$hand")
    $architect = $true
    $mode = "architect, hand = $hand"
    if ($hand -ne $brain) {
        Write-Host 'Note: two different models cannot both stay on an 11 GB GPU; Ollama will swap them every turn (slow).' -ForegroundColor Yellow
    }
} elseif ($architect) {
    $mode = 'architect (plans, then edits; --normal to skip planning)'
}
if ($architect) { $aiderArgs += '--architect' }

$env:OLLAMA_API_BASE = $OllamaBase
Write-Host "Brain: $brain via Ollama | Hands: Aider ($mode)" -ForegroundColor Cyan
& $aider.Source @aiderArgs @passThrough
exit $LASTEXITCODE
