#!/usr/bin/env bash
# DWHFlow'u başlatır (Linux / macOS). Depo klasöründe:
#
#     bash calistir.sh                 # yalnız bu bilgisayar
#     bash calistir.sh --ekip          # ağdaki bilgisayarlar da bağlanır
#
# Seçenekler: --ekip, --ad AD (ekip modunda ek izinli ad, tekrarlanabilir), --port 8765,
# --veri KLASOR (varsayılan: depo/.dwhflow), --tarayici-acma, --komutu-goster.
# Durdurmak için Ctrl+C. İlk açılışta yönetici hesabı bu bilgisayardaki tarayıcıdan oluşturulur.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

port=8765
veri="$PWD/.dwhflow"
ekip=0
ac=1
goster=0
extra=()
while [ $# -gt 0 ]; do
  case "$1" in
    --ekip) ekip=1 ;;
    --ad) extra+=("$2"); shift ;;
    --port) port="$2"; shift ;;
    --veri) veri="$2"; shift ;;
    --tarayici-acma) ac=0 ;;
    --komutu-goster) goster=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "Bilinmeyen seçenek: $1 (yardım: bash calistir.sh --help)"; exit 2 ;;
  esac
  shift
done

if [ ! -x .venv/bin/dwhflow ]; then
  echo "DWHFlow kurulu değil. Önce kurun:  bash kur.sh"
  exit 1
fi

args=(serve --port "$port" --data-dir "$veri")
addresses=("http://127.0.0.1:$port")
if [ "$ekip" = 1 ]; then
  names=("$(hostname)")
  full="$(hostname -f 2>/dev/null || true)"
  [ -n "$full" ] && names+=("$full")
  if ips="$(hostname -I 2>/dev/null)"; then          # Linux
    for ip in $ips; do
      case "$ip" in *:*|127.*|169.254.*) ;; *) names+=("$ip") ;; esac   # IPv6 ve yerel adresler hariç
    done
  elif command -v ipconfig >/dev/null 2>&1; then     # macOS
    for iface in en0 en1; do
      ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
      [ -n "$ip" ] && names+=("$ip")
    done
  fi
  names+=(${extra[@]+"${extra[@]}"})
  args+=(--host 0.0.0.0)
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    args+=(--allow-host "$name")
    addresses+=("http://$name:$port")
  done < <(printf '%s\n' "${names[@]}" | tr '[:upper:]' '[:lower:]' | awk '!seen[$0]++')
fi

if [ "$goster" = 1 ]; then
  echo ".venv/bin/dwhflow ${args[*]}"
  exit 0
fi

if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
  echo "Port $port kullanımda. Başka port için:  bash calistir.sh --port 8766"
  exit 1
fi

echo "DWHFlow başlatılıyor"
echo "  veri klasörü: $veri"
echo "  adresler:"
for address in "${addresses[@]}"; do echo "    $address"; done
if [ "$ekip" = 1 ]; then
  echo
  echo "  Diğer bilgisayarlar bağlanamıyorsa güvenlik duvarında $port/tcp açılmalı"
  echo "  (ör. sudo ufw allow $port/tcp). Trafik şifresizdir; kurum dışına açmayın."
fi
echo

if [ "$ac" = 1 ] && command -v curl >/dev/null 2>&1; then
  (
    for _ in $(seq 1 120); do
      if curl -fsS "http://127.0.0.1:$port/api/health" >/dev/null 2>&1; then
        if command -v xdg-open >/dev/null 2>&1; then xdg-open "http://127.0.0.1:$port/" >/dev/null 2>&1 || true
        elif command -v open >/dev/null 2>&1; then open "http://127.0.0.1:$port/" || true
        fi
        exit 0
      fi
      sleep 0.5
    done
  ) &
fi

exec .venv/bin/dwhflow "${args[@]}"
