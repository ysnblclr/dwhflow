<#
  DWHFlow kurulumu (Windows). Depo klasöründe çalıştırın:

      powershell -ExecutionPolicy Bypass -File kur.ps1

  Yaptıkları: Python 3.11+ ve Node.js 18+ denetimi, proje klasöründe .venv
  sanal ortamı, bağımlılıkların kurulumu (internet gerekir), `dwhflow doctor`.
  Sistem Python'una hiçbir şey kurulmaz. Yeniden çalıştırmak güvenlidir.
#>
param(
    [switch]$Dev,    # geliştirme araçlarını da kur (pytest, ruff, mypy)
    [switch]$Serve   # kurulumdan sonra arayüzü başlat
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Find-Python {
    foreach ($candidate in @('py -3', 'python', 'python3')) {
        $words = $candidate -split ' '
        $exe = $words[0]
        $prefix = @($words | Select-Object -Skip 1)
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $version = & $exe @prefix -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        } catch { continue }
        if (-not $version) { continue }
        $parts = "$version".Trim().Split('.')
        if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11) {
            return @{ Exe = $exe; Prefix = $prefix; Version = "$version".Trim() }
        }
    }
    return $null
}

Write-Host "DWHFlow kurulumu" -ForegroundColor Cyan

$python = Find-Python
if (-not $python) {
    Write-Host "HATA: Python 3.11 ya da üstü bulunamadı." -ForegroundColor Red
    Write-Host "Kurmak için:  winget install -e --id Python.Python.3.12"
    Write-Host "Kurduktan sonra yeni bir PowerShell penceresinde tekrar çalıştırın."
    exit 1
}
Write-Host "  Python $($python.Version) bulundu ($($python.Exe))"

$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
    $nodeVersion = (& node --version).Trim()
    $major = [int]($nodeVersion.TrimStart('v').Split('.')[0])
    if ($major -ge 18) {
        Write-Host "  Node.js $nodeVersion bulundu"
    } else {
        Write-Host "  UYARI: Node.js $nodeVersion eski; diyagram üretimi için 18+ gerekli." -ForegroundColor Yellow
        Write-Host "  Güncellemek için:  winget install -e --id OpenJS.NodeJS.LTS"
    }
} else {
    Write-Host "  UYARI: Node.js bulunamadı. Kurulum sürer ama diyagram üretilemez." -ForegroundColor Yellow
    Write-Host "  Kurmak için:  winget install -e --id OpenJS.NodeJS.LTS"
}

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host "Sanal ortam oluşturuluyor (.venv)..."
    $pyExe = $python.Exe
    $pyPrefix = $python.Prefix
    & $pyExe @pyPrefix -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Host "HATA: sanal ortam oluşturulamadı." -ForegroundColor Red; exit 1 }
}
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

Write-Host "Bağımlılıklar kuruluyor (birkaç dakika sürebilir)..."
& $venvPython -m pip install --quiet --upgrade pip
$target = '.'
if ($Dev) { $target = '.[dev]' }
& $venvPython -m pip install --quiet $target
if ($LASTEXITCODE -ne 0) { Write-Host "HATA: pip kurulumu başarısız." -ForegroundColor Red; exit 1 }

Write-Host ""
& $venvPython -m dwhflow.cli doctor
$dwhflow = Join-Path $PSScriptRoot '.venv\Scripts\dwhflow.exe'

Write-Host ""
Write-Host "Kurulum tamam." -ForegroundColor Green
Write-Host "Arayüzü başlatmak için:   $dwhflow serve   (ilk açılışta bu makinedeki tarayıcıdan yönetici oluşturulur)"
Write-Host "Yöneticiyi komutla oluşturmak için:  $dwhflow kullanici ekle ADINIZ --rol yonetici"
Write-Host "Ekibe açmak için:         $dwhflow serve --host 0.0.0.0 --allow-host SUNUCU_ADI"
Write-Host "Örneklerle denemek için:  $dwhflow convert --ornek --open"

if ($Serve) { & $dwhflow serve }
