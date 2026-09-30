#!/usr/bin/env python3
"""Taşınabilir kurulum paketi üretir — Docker gerekmez.

İnternete çıkabilen bir makinede çalıştırılır; DWHFlow tekerleğini ve tüm
bağımlılıklarını tek klasöre toplar, kurulum betikleriyle birlikte zipler.
Hedef makinede yalnızca Python gerekir, internet gerekmez.

    python scripts/paketle.py                      # bu makine için
    python scripts/paketle.py --platform linux     # Linux x86-64 hedefi
    python scripts/paketle.py --python 3.12        # farklı Python sürümü

ÖNEMLİ: Bağımlılıkların neredeyse hepsi saf Python ama ikisi değil —
``pydantic_core`` (Rust) ve ``pyyaml`` (C). Bu yüzden paket **hedef işletim sistemi ve Python
ana sürümüne** göre hazırlanır. Yanlış platform için hazırlanmış paket hedefte
"no matching distribution" hatası verir.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# pip'in --platform bayrağına karşılık gelen etiketler
PLATFORM_TAGS = {
    "windows": ["win_amd64"],
    "linux": ["manylinux2014_x86_64", "manylinux_2_17_x86_64"],
    "macos": ["macosx_11_0_arm64", "macosx_10_9_x86_64"],
}

KUR_BAT = r"""@echo off
REM DWHFlow cevrimdisi kurulum - Windows
setlocal

where python >nul 2>nul
if errorlevel 1 (
  echo HATA: Python bulunamadi. Python 3.11+ kurup tekrar deneyin.
  exit /b 1
)

echo DWHFlow kuruluyor...
python -m pip install --no-index --find-links "%~dp0wheels" dwhflow
if errorlevel 1 (
  echo.
  echo Kurulum basarisiz. Muhtemel sebep: bu paket farkli bir isletim sistemi
  echo ya da Python surumu icin hazirlanmis. Detay icin KUR.txt dosyasina bakin.
  exit /b 1
)

echo.
echo Kurulum denetleniyor...
python -m dwhflow.cli doctor
echo.
echo Baslatmak icin:  dwhflow serve
endlocal
"""

KUR_SH = r"""#!/usr/bin/env bash
# DWHFlow çevrimdışı kurulum - Linux / macOS
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "HATA: python3 bulunamadı. Python 3.11+ kurup tekrar deneyin."
  exit 1
fi

echo "DWHFlow kuruluyor..."
python3 -m pip install --no-index --find-links "$DIR/wheels" dwhflow

echo
echo "Kurulum denetleniyor..."
python3 -m dwhflow.cli doctor
echo
echo "Başlatmak için:  dwhflow serve"
"""


def kur_txt(target_platform: str, python_version: str, wheel_count: int) -> str:
    return f"""DWHFlow — çevrimdışı kurulum paketi
=====================================

Bu paket {target_platform} / Python {python_version} için hazırlandı.
İçinde {wheel_count} adet paket dosyası var. İnternet gerekmez, Docker gerekmez.

GEREKSİNİMLER
-------------
1. Python {python_version} — tam olarak bu ana sürüm. İki bağımlılık (pydantic_core,
   pyyaml) sürüme bağlı tekerlek olarak gelir; {python_version} için hazırlanmış paket
   başka bir Python sürümüne kurulmaz. Kontrol:  python --version
2. Node.js 18 ya da üstü — diyagram üretmek için. (Üretilmiş HTML'leri
   görüntülemek için gerekmez; tarayıcı yeter.) Kontrol:  node --version
   Kurulum: https://nodejs.org (LTS). Kapalı ağda Node.js kurulum dosyasını da
   bu paketle birlikte taşıyın.
3. İsteğe bağlı: Chrome ya da Chromium — diyagramların tarayıcı kontrolü için.
   Yoksa diyagramlar yine üretilir; kontrol "atlandı" olarak raporlanır.

KURULUM
-------
Windows :  kur.bat dosyasına çift tıklayın (ya da komut isteminden çalıştırın)
Linux/Mac:  bash kur.sh

Elle kurmak isterseniz:
    python -m pip install --no-index --find-links ./wheels dwhflow

Sanal ortama kurmak (önerilir, sistem Python'unu kirletmez):
    python -m venv dwhflow-venv
    dwhflow-venv\\Scripts\\activate        (Windows)
    source dwhflow-venv/bin/activate      (Linux/Mac)
    python -m pip install --no-index --find-links ./wheels dwhflow

KULLANIM
--------
    dwhflow doctor                      kurulumu denetler
    dwhflow convert --ornek --open      örnek dokümanlarla dener
    dwhflow convert ./dokumanlarim -o ./cikti
    dwhflow serve                       ekip arayüzü (http://127.0.0.1:8765)
    dwhflow kullanici ekle ADI --rol yonetici   ilk yönetici (ya da tarayıcıdan ilk kurulum)
    dwhflow serve --host 0.0.0.0 --allow-host SUNUCU   ekibe açmak için
    dwhflow --help                      tüm komutlar

SORUN GİDERME
-------------
"No matching distribution found for pydantic-core" benzeri bir hata alırsanız:
bu paket başka bir işletim sistemi ya da Python sürümü için hazırlanmıştır.
Bağımlılıkların çoğu saf Python ama pydantic_core ve pyyaml platforma
bağlıdır. Doğru paketi üretmek için, internete çıkabilen bir makinede:

    python scripts/paketle.py --platform linux --python 3.12

"dwhflow komutu bulunamadı": Python'un Scripts/bin klasörü PATH'te değildir.
Bunun yerine şunu kullanın:  python -m dwhflow.cli serve

"Node.js bulunamadı": diyagram üretimi için Node.js 18+ kurun ya da PATH'e ekleyin.

VERİ VE GİZLİLİK
----------------
Araç çalışırken hiçbir dış servise bağlanmaz: telemetri yok, CDN yok, diyagram
motorunun güncelleme denetimi kapalı. Sunucu yalnızca 127.0.0.1 adresine bağlanır.
Yapay zekâ isteğe bağlıdır ve varsayılan olarak yalnız yerel adreslere bağlanır.
"""


def run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"komut başarısız: {' '.join(command[:4])}…")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform",
        choices=sorted(PLATFORM_TAGS),
        default=_current_platform(),
        help="hedef işletim sistemi (varsayılan: bu makine)",
    )
    parser.add_argument(
        "--python",
        default=f"{sys.version_info.major}.{sys.version_info.minor}",
        help="hedef Python ana sürümü, ör. 3.12 (varsayılan: bu makine)",
    )
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="çıktı dizini")
    parser.add_argument("--no-zip", action="store_true", help="zip üretme, klasör bırak")
    args = parser.parse_args()

    bundle = args.out / f"dwhflow-{args.platform}-py{args.python}"
    wheels = bundle / "wheels"
    if bundle.exists():
        shutil.rmtree(bundle)
    wheels.mkdir(parents=True)

    print(f"Hedef: {args.platform} / Python {args.python}")
    print("1/3  DWHFlow tekerleği üretiliyor…")
    run([sys.executable, "-m", "pip", "wheel", str(ROOT), "-w", str(wheels), "--no-deps", "-q"])

    print("2/3  Bağımlılıklar indiriliyor…")
    # Liste elle kopyalanmaz: pyproject.toml tek doğruluk kaynağıdır (kopya sapar).
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    download = [sys.executable, "-m", "pip", "download", *deps, "-d", str(wheels), "-q"]
    if args.platform != _current_platform() or args.python != _current_python():
        # Çapraz platform: yalnız hazır tekerlek indirilebilir.
        download += ["--only-binary=:all:", "--python-version", args.python]
        for tag in PLATFORM_TAGS[args.platform]:
            download += ["--platform", tag]
    run(download)

    count = len(list(wheels.glob("*.whl")))
    print(f"      {count} paket toplandı")

    print("3/3  Kurulum betikleri yazılıyor…")
    (bundle / "kur.bat").write_text(KUR_BAT, encoding="utf-8")
    script = bundle / "kur.sh"
    script.write_text(KUR_SH, encoding="utf-8")
    script.chmod(0o755)
    (bundle / "KUR.txt").write_text(kur_txt(args.platform, args.python, count), encoding="utf-8")

    if args.no_zip:
        print(f"\nHazır: {bundle}")
        return 0

    # with_suffix kullanılmaz: "py3.11" içindeki ".11" uzantı sanılır ve yutulur.
    archive = bundle.parent / f"{bundle.name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in bundle.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(bundle.parent))
    shutil.rmtree(bundle)
    size = archive.stat().st_size / 1e6
    print(f"\nHazır: {archive}  ({size:.1f} MB)")
    print("Bu dosyayı hedef makineye kopyalayıp açın, kur.bat / kur.sh çalıştırın.")
    return 0


def _current_platform() -> str:
    system = platform.system().lower()
    return {"windows": "windows", "linux": "linux", "darwin": "macos"}.get(system, "linux")


def _current_python() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


if __name__ == "__main__":
    raise SystemExit(main())
