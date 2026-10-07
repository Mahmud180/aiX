<#
  Download / verify the aiX diffusion models.
  Resumable, retried, size-verified. Writes models_manifest.json.
  Root defaults to $env:AIX_AI_ROOT, else D:\AI.
#>
$ErrorActionPreference = "Continue"
$root = $env:AIX_AI_ROOT
if (-not $root) { $root = "D:\AI" }
$log = "$root\logs\download.log"
$manifestPath = "$root\models_manifest.json"
New-Item -ItemType Directory -Force -Path "$root\logs", "$root\models\unet", "$root\models\vae" | Out-Null
function Log($m) { "$(Get-Date -Format s)  $m" | Tee-Object -FilePath $log -Append }

$models = @(
    @{ out = "$root\models\unet\flux1-dev-Q6_K.gguf";          url = "https://huggingface.co/city96/FLUX.1-dev-gguf/resolve/main/flux1-dev-Q6_K.gguf" },
    @{ out = "$root\models\text_encoders\t5xxl_fp8_e4m3fn.safetensors"; url = "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors" },
    @{ out = "$root\models\text_encoders\clip_l.safetensors"; url = "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors" },
    @{ out = "$root\models\vae\ae.safetensors";               url = "https://huggingface.co/ffxvs/vae-flux/resolve/main/ae.safetensors" },
    @{ out = "$root\models\unet\wan2.2_ti2v_5B_fp16.safetensors"; url = "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors" },
    @{ out = "$root\models\text_encoders\umt5_xxl_fp8_e4m3fn_scaled.safetensors"; url = "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" },
    @{ out = "$root\models\vae\wan2.2_vae.safetensors";       url = "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan2.2_vae.safetensors" },
    @{ out = "$root\models\unet\flux1-kontext-dev-Q6_K.gguf"; url = "https://huggingface.co/QuantStack/FLUX.1-Kontext-dev-GGUF/resolve/main/flux1-kontext-dev-Q6_K.gguf" }
)

$manifest = @()
foreach ($m in $models) {
    $name = Split-Path $m.out -Leaf
    $head = & curl.exe -sIL $m.url 2>&1
    $len = ($head | Select-String -Pattern "content-length" | Select-Object -Last 1) -replace '[^0-9]', ''
    $sha = ($head | Select-String -Pattern "x-linked-etag" | Select-Object -Last 1) -replace '.*"', '' -replace '"', '' -replace '.*:', ''
    $want = [int64]$len
    $have = 0
    if (Test-Path $m.out) { $have = (Get-Item $m.out).Length }

    if ($want -gt 0 -and $have -eq $want) {
        Log "OK    $name ($([math]::Round($have / 1GB, 2)) GB)"
    } else {
        Log "GET   $name"
        & curl.exe -L --fail --retry 5 --retry-delay 5 -C - -o $m.out $m.url 2>> $log
        $have = (Get-Item $m.out -ErrorAction SilentlyContinue).Length
        Log "  -> exit=$LASTEXITCODE size=$have"
    }
    $manifest += [pscustomobject]@{ file = $name; path = $m.out; url = $m.url; size = $want; sha256 = $sha }
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $manifestPath -Encoding UTF8
Log "FETCH DONE"
