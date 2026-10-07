<#
  aiX media backend installer.
  Installs ComfyUI portable and downloads the diffusion models.
  Root defaults to $env:AIX_AI_ROOT, else D:\AI. Re-runnable.
#>
$ErrorActionPreference = "Continue"
$root = $env:AIX_AI_ROOT
if (-not $root) { $root = "D:\AI" }
$comfyDir = "$root\ComfyUI_windows_portable"
$setupDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = "$root\logs\setup.log"
New-Item -ItemType Directory -Force -Path "$root\logs", "$root\models" | Out-Null
function Log($m) { "$(Get-Date -Format s)  $m" | Tee-Object -FilePath $log -Append }

$yamlRoot = $root.Replace("\", "/")
foreach ($d in @("unet", "clip", "vae", "text_encoders", "upscale_models", "checkpoints", "loras", "controlnet")) {
    New-Item -ItemType Directory -Force -Path "$root\models\$d" | Out-Null
}

$archive = "$setupDir\ComfyUI_portable.7z"
if (-not (Test-Path "$comfyDir\python_embeded\python.exe")) {
    if (-not (Test-Path $archive)) {
        Log "downloading ComfyUI portable"
        & curl.exe -L --fail --retry 3 -o $archive "https://github.com/Comfy-Org/ComfyUI/releases/download/v0.39.0/ComfyUI_windows_portable_nvidia.7z"
    }
    Log "extracting ComfyUI"
    & tar.exe -xf $archive -C $root
} else { Log "ComfyUI already present" }

$yaml = @"
aix:
    base_path: $yamlRoot/models
    checkpoints: checkpoints
    diffusion_models: unet
    unet: unet
    clip: clip
    text_encoders: text_encoders
    vae: vae
    loras: loras
    upscale_models: upscale_models
    controlnet: controlnet
"@
Set-Content -Path "$comfyDir\ComfyUI\extra_model_paths.yaml" -Value $yaml -Encoding UTF8

$gguf = "$comfyDir\ComfyUI\custom_nodes\ComfyUI-GGUF"
if (-not (Test-Path $gguf)) {
    Log "installing ComfyUI-GGUF node"
    $zip = "$setupDir\ComfyUI-GGUF.zip"
    & curl.exe -L --fail --retry 3 -o $zip "https://codeload.github.com/city96/ComfyUI-GGUF/zip/refs/heads/main"
    & tar.exe -xf $zip -C "$comfyDir\ComfyUI\custom_nodes"
    Rename-Item -Force "$comfyDir\ComfyUI\custom_nodes\ComfyUI-GGUF-main" $gguf -ErrorAction SilentlyContinue
}
& "$comfyDir\python_embeded\python.exe" -m pip install --no-warn-script-location "gguf>=0.13.0" sentencepiece protobuf | Out-Null

& powershell -NoProfile -ExecutionPolicy Bypass -File "$setupDir\fetch_models.ps1"
& powershell -NoProfile -ExecutionPolicy Bypass -File "$setupDir\pull_models.ps1"

Log "setup complete"
Write-Host "Done. Set AIX_AI_ROOT=$root then run aix.bat"
