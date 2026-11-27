<#
  Internet Download Assistant - kurulum paketi derleme betigi (Windows PowerShell)

  Kullanim (proje kokunde, PowerShell):
      .\build.ps1                 # her seyi yapar: araclari indirir, exe uretir, Setup.exe olusturur
      .\build.ps1 -RefreshTools   # bin\ icindeki araclari (yt-dlp, Deno, ffmpeg) yeniden indirir
      .\build.ps1 -SkipInstaller  # yalnizca dist\IDA\ klasorunu uretir (Inno Setup adimini atlar)

  Sonuc: dist_installer\IDA-Setup-<surum>.exe
  Sureyi uzatan tek sey ilk calistirmadaki indirmelerdir (~400 MB). Sonraki calistirmalar hizlidir.
#>
param([switch]$RefreshTools, [switch]$SkipInstaller)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Get-File($url, $dest) {
    Write-Host "    indiriliyor: $url"
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
}

# ---------------------------------------------------------------- 1) sanal ortam
Step "Python ortami hazirlaniyor"
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Python bulunamadi. python.org'dan Python 3.11+ (64 bit) kur ve 'Add to PATH' kutusunu isaretle." }
}
& $Py -m pip install --quiet --upgrade pip
& $Py -m pip install --quiet -r requirements.txt -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "Gerekli Python paketleri kurulamadi." }

# ---------------------------------------------------------------- 2) araclar (bin\)
Step "Araclar kontrol ediliyor (bin\)"
$Bin = Join-Path $Root "bin"
$Cache = Join-Path $Root "build_cache"
New-Item -ItemType Directory -Force -Path $Bin, $Cache | Out-Null

if ($RefreshTools -or -not (Test-Path (Join-Path $Bin "yt-dlp.exe"))) {
    Get-File "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" (Join-Path $Bin "yt-dlp.exe")
} else { Write-Host "    yt-dlp.exe hazir" }

if ($RefreshTools -or -not (Test-Path (Join-Path $Bin "deno.exe"))) {
    $zip = Join-Path $Cache "deno.zip"
    Get-File "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip" $zip
    Expand-Archive -Path $zip -DestinationPath $Bin -Force
} else { Write-Host "    deno.exe hazir" }

if ($RefreshTools -or -not (Test-Path (Join-Path $Bin "ffmpeg.exe"))) {
    $zip = Join-Path $Cache "ffmpeg.zip"
    Get-File "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" $zip
    $extract = Join-Path $Cache "ffmpeg"
    if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
    Expand-Archive -Path $zip -DestinationPath $extract -Force
    Get-ChildItem -Path $Bin -Filter "*.dll" -ErrorAction SilentlyContinue | Remove-Item -Force
    $inner = Get-ChildItem -Path $extract -Directory | Select-Object -First 1
    Get-ChildItem -Path (Join-Path $inner.FullName "bin") -File |
        Where-Object { $_.Name -ne "ffplay.exe" } |
        ForEach-Object { Copy-Item $_.FullName -Destination $Bin -Force }
} else { Write-Host "    ffmpeg.exe hazir" }

foreach ($name in @("yt-dlp.exe", "deno.exe", "ffmpeg.exe", "ffprobe.exe")) {
    if (-not (Test-Path (Join-Path $Bin $name))) { throw "bin\$name bulunamadi." }
}

# ---------------------------------------------------------------- 3) Visual C++ calisma zamani
Step "Visual C++ calisma zamani paketi kontrol ediliyor"
$Redist = Join-Path $Root "installer\redist"
New-Item -ItemType Directory -Force -Path $Redist | Out-Null
if (-not (Test-Path (Join-Path $Redist "vc_redist.x64.exe"))) {
    Get-File "https://aka.ms/vs/17/release/vc_redist.x64.exe" (Join-Path $Redist "vc_redist.x64.exe")
} else { Write-Host "    vc_redist.x64.exe hazir" }

# ---------------------------------------------------------------- 4) simge ve exe
Step "Simge uretiliyor"
& $Py installer\make_icon.py
if ($LASTEXITCODE -ne 0) { throw "Simge uretilemedi." }

Step "IDA.exe derleniyor (PyInstaller)"
if (Test-Path "dist\IDA") { Remove-Item "dist\IDA" -Recurse -Force }
& $Py -m PyInstaller --noconfirm --clean IDA.spec
if ($LASTEXITCODE -ne 0 -or -not (Test-Path "dist\IDA\IDA.exe")) { throw "PyInstaller basarisiz oldu." }
Write-Host "    dist\IDA\IDA.exe hazir"

Step "Dosyalar dogrulaniyor"
foreach ($rel in @("extension\manifest.json", "extension\background.js", "extension\popup.html",
                   "extension\icons\icon128.png", "bin\yt-dlp.exe", "bin\ffmpeg.exe", "bin\deno.exe")) {
    if (-not (Test-Path (Join-Path $Root $rel))) { throw "Eksik dosya: $rel" }
}
$env:QT_QPA_PLATFORM = "offscreen"
& $Py -c "import core.tools, core.handoff, core.ytauth, core.speed, core.hashcheck, core.rules, core.stats, core.tuner, core.netwatch, core.thumbgen, gui.components.toggle_switch, gui.main_window"
$importCode = $LASTEXITCODE
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
if ($importCode -ne 0) { throw "Modul ice aktarma denetimi basarisiz (eksik bagimlilik olabilir)." }
Write-Host "    dosyalar ve moduller tamam"

if ($SkipInstaller) { Write-Host ""; Write-Host "Kurulum paketi adimi atlandi (-SkipInstaller)." -ForegroundColor Yellow; exit 0 }

# ---------------------------------------------------------------- 5) Inno Setup
Step "Kurulum paketi olusturuluyor (Inno Setup)"
$Iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) {
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { $Iscc = $cmd.Source }
}
if (-not $Iscc) { throw "Inno Setup 6 bulunamadi. https://jrsoftware.org/isdl.php adresinden kur." }

$OutDir = Join-Path $Root "dist_installer"
if (Test-Path $OutDir) { Get-ChildItem $OutDir -Filter "IDA-*Setup-*" | Remove-Item -Force }
$Version = (& $Py -c "from utils.version import __version__; print(__version__)").Trim()
& $Iscc "/DMyAppVersion=$Version" "installer\IDA.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup derlemesi basarisiz oldu." }

Write-Host ""
Step "Cevrimici (kucuk) kurulum paketi olusturuluyor"
& $Iscc "/DMyAppVersion=$Version" "/DLITE=1" "installer\IDA.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup (cevrimici paket) derlemesi basarisiz oldu." }
$SetupFile = Join-Path $OutDir "IDA-Setup-$Version.exe"
$Hash = (Get-FileHash -Algorithm SHA256 -Path $SetupFile).Hash.ToLower()
Set-Content -Path "$SetupFile.sha256" -Value "$Hash  IDA-Setup-$Version.exe" -Encoding ascii
Write-Host "TAMAM: dist_installer\IDA-Setup-$Version.exe" -ForegroundColor Green
Write-Host "GitHub release'e su UC dosyayi yukle (etiket: v$Version): IDA-Setup-$Version.exe, IDA-Setup-$Version.exe.sha256, IDA-Online-Setup-$Version.exe" -ForegroundColor Cyan
