# Pull the chat base model and the vision model from Ollama.
$ErrorActionPreference = "Continue"
$env:OLLAMA_HOST = if ($env:OLLAMA_HOST) { $env:OLLAMA_HOST } else { "127.0.0.1:11434" }
$ollama = if ($env:OLLAMA_EXE) { $env:OLLAMA_EXE } else { "ollama" }
$root = $env:AIX_AI_ROOT
if (-not $root) { $root = "D:\AI" }
$log = "$root\logs\ollama_pull.log"
New-Item -ItemType Directory -Force -Path "$root\logs" | Out-Null
function Log($m) { "$(Get-Date -Format s)  $m" | Tee-Object -FilePath $log -Append }

foreach ($m in @("huihui_ai/qwen2.5-abliterate:14b", "qwen2.5vl:7b")) {
    Log "pull $m"
    & $ollama pull $m 2>&1 | Tee-Object -FilePath $log -Append
    Log "done $m exit=$LASTEXITCODE"
}
Log "PULLS DONE"
