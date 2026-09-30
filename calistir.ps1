<#
  DWHFlow'u başlatır (Windows). Depo klasöründe:

      powershell -ExecutionPolicy Bypass -File calistir.ps1          # yalnız bu bilgisayar
      powershell -ExecutionPolicy Bypass -File calistir.ps1 -Ekip    # ağdaki bilgisayarlar da bağlanır

  Ya da çift tıkla: calistir.bat  (ekip için: calistir.bat -Ekip)

  Seçenekler:
    -Ekip            Sunucuyu ağa açar; bu bilgisayarın adı, tam adı ve IPv4 adresleri izinli olur
    -Ad ad1,ad2      Ekip modunda ek izinli adlar (ör. DNS takma adı: dwhflow.kurum.local)
    -Port 8765       Port
    -Veri KLASOR     Projeler, kullanıcılar ve ayarların klasörü (varsayılan: depo\.dwhflow)
    -TarayiciAcma    Tarayıcıyı otomatik açma
    -KomutuGoster    Çalıştırmadan yalnız komutu yaz

  Durdurmak için bu pencerede Ctrl+C. İlk açılışta yönetici hesabı bu bilgisayardaki
  tarayıcıdan oluşturulur.
#>
param(
    [switch]$Ekip,
    [string[]]$Ad = @(),
    [int]$Port = 8765,
    [string]$Veri = '',
    [switch]$TarayiciAcma,
    [switch]$KomutuGoster
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$dwhflow = Join-Path $PSScriptRoot '.venv\Scripts\dwhflow.exe'
if (-not (Test-Path -LiteralPath $dwhflow)) {
    Write-Host "DWHFlow kurulu değil. Önce kurun:  kur.bat  (ya da powershell -ExecutionPolicy Bypass -File kur.ps1)" -ForegroundColor Red
    exit 1
}
if (-not $Veri) { $Veri = Join-Path $PSScriptRoot '.dwhflow' }

$serveArgs = @('serve', '--port', "$Port", '--data-dir', $Veri)
$addresses = @("http://127.0.0.1:$Port")

if ($Ekip) {
    $names = New-Object System.Collections.Generic.List[string]
    $names.Add($env:COMPUTERNAME)
    try {
        $fqdn = [System.Net.Dns]::GetHostEntry($env:COMPUTERNAME).HostName
        if ($fqdn) { $names.Add($fqdn) }
    } catch { }
    try {
        Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
            ForEach-Object { $names.Add($_.IPAddress) }
    } catch { }
    foreach ($extra in $Ad) { if ($extra) { $names.Add($extra.Trim()) } }
    $unique = $names | ForEach-Object { $_.ToLowerInvariant() } | Select-Object -Unique
    $serveArgs += @('--host', '0.0.0.0')
    foreach ($name in $unique) {
        $serveArgs += @('--allow-host', $name)
        $addresses += "http://${name}:$Port"
    }
}

if ($KomutuGoster) {
    Write-Host "$dwhflow $($serveArgs -join ' ')"
    exit 0
}

# Port boş mu? (başka bir DWHFlow ya da program kullanıyorsa açık bir mesaj ver)
try {
    $busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
} catch { $busy = $null }
if ($busy) {
    $owner = (Get-Process -Id $busy.OwningProcess -ErrorAction SilentlyContinue).ProcessName
    Write-Host "Port $Port kullanımda ($owner, PID $($busy.OwningProcess)). Başka port için:  calistir.bat -Port 8766" -ForegroundColor Red
    exit 1
}

Write-Host "DWHFlow başlatılıyor" -ForegroundColor Cyan
Write-Host "  veri klasörü: $Veri"
Write-Host "  adresler:"
foreach ($address in $addresses) { Write-Host "    $address" }

if ($Ekip) {
    $rule = $null
    try {
        $rule = Get-NetFirewallRule -DisplayName "DWHFlow*" -ErrorAction SilentlyContinue | Select-Object -First 1
    } catch { }
    if (-not $rule) {
        Write-Host ""
        Write-Host "  Diğer bilgisayarlar bağlanamıyorsa Windows Güvenlik Duvarı'nda port açılmalı." -ForegroundColor Yellow
        Write-Host "  Yönetici olarak açılmış bir PowerShell'de bir kez çalıştırın:" -ForegroundColor Yellow
        Write-Host "    New-NetFirewallRule -DisplayName 'DWHFlow $Port' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Domain,Private"
    }
    Write-Host ""
    Write-Host "  Trafik şifresizdir; kurum dışına açmayın. HTTPS için IIS/nginx ters vekil önerilir." -ForegroundColor Yellow
}
Write-Host ""

if (-not $TarayiciAcma) {
    # Sunucu hazır olunca tarayıcıyı aç (arka planda; sunucu bu pencerede çalışır).
    $null = Start-Job -ArgumentList "http://127.0.0.1:$Port/" -ScriptBlock {
        param($url)
        for ($i = 0; $i -lt 120; $i++) {
            try {
                $null = Invoke-WebRequest -UseBasicParsing -Uri ($url + 'api/health') -TimeoutSec 2
                Start-Process $url
                return
            } catch { Start-Sleep -Milliseconds 500 }
        }
    }
}

& $dwhflow @serveArgs
exit $LASTEXITCODE
