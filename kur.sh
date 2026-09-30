#!/usr/bin/env bash
# DWHFlow kurulumu (Linux / macOS). Depo klasöründe çalıştırın:
#
#     bash kur.sh            # kurulum
#     bash kur.sh --dev      # + geliştirme araçları
#
# Python 3.11+ ve Node.js 18+ denetlenir, proje klasöründe .venv sanal ortamı
# kurulur (internet gerekir), sonunda `dwhflow doctor` çalışır.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

target="."
[ "${1:-}" = "--dev" ] && target=".[dev]"

python=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    python="$candidate"
    break
  fi
done
if [ -z "$python" ]; then
  echo "HATA: Python 3.11 ya da üstü bulunamadı (ör. sudo apt install python3.12 python3.12-venv)."
  exit 1
fi
echo "Python: $("$python" --version)"

if command -v node >/dev/null 2>&1; then
  major="$(node --version | sed 's/^v//; s/\..*//')"
  if [ "$major" -ge 18 ]; then
    echo "Node.js: $(node --version)"
  else
    echo "UYARI: Node.js $(node --version) eski; diyagram üretimi için 18+ gerekli."
  fi
else
  echo "UYARI: Node.js bulunamadı. Kurulum sürer ama diyagram üretilemez (https://nodejs.org)."
fi

[ -x .venv/bin/python ] || "$python" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet "$target"

echo
.venv/bin/python -m dwhflow.cli doctor || true
echo
echo "Kurulum tamam."
echo "Arayüzü başlatmak için:   .venv/bin/dwhflow serve   (ilk açılışta bu makinedeki tarayıcıdan yönetici oluşturulur)"
echo "Yöneticiyi komutla oluşturmak için:  .venv/bin/dwhflow kullanici ekle ADINIZ --rol yonetici"
echo "Ekibe açmak için:         .venv/bin/dwhflow serve --host 0.0.0.0 --allow-host SUNUCU_ADI"
echo "Örneklerle denemek için:  .venv/bin/dwhflow convert --ornek --open"
