#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Datei:        install-agent.sh
# Projekt:      RIMPERIO — Agent
# Autor:        Phydran6
# Version:      2026.10.31.15.40.00
#
# Beschreibung: Richtet den RIMPERIO-Agent auf Linux ein: lädt das passende Binary,
#               legt es nach /usr/local/bin und registriert den systemd-Dienst.
#               Muss als root laufen.
#
# Aufruf:
#   sudo bash install-agent.sh --server https://rmm.example.de --secret GEHEIMNIS [--site Buero]
#   # oder per Umgebung: SERVER=… SECRET=… sudo -E bash install-agent.sh
# ---------------------------------------------------------------------------
set -euo pipefail

SERVER="${SERVER:-}"
SECRET="${SECRET:-}"
SITE="${SITE:-Standard}"
DESC="${DESC:-}"
INSECURE="${INSECURE:-0}"
BASE_URL="${RIMPERIO_BASE_URL:-https://github.com/Phydran6/RIMPERIO-Server/releases/latest/download}"

while [ $# -gt 0 ]; do
  case "$1" in
    --server) SERVER="$2"; shift 2 ;;
    --secret) SECRET="$2"; shift 2 ;;
    --site)   SITE="$2"; shift 2 ;;
    --desc)   DESC="$2"; shift 2 ;;
    --insecure) INSECURE=1; shift ;;
    *) echo "Unbekannte Option: $1" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "Bitte als root ausführen (sudo)." >&2; exit 1; }
[ -n "$SERVER" ] && [ -n "$SECRET" ] || { echo "SERVER und SECRET sind nötig." >&2; exit 2; }

# Architektur bestimmen.
case "$(uname -m)" in
  x86_64|amd64) ARCH="amd64" ;;
  aarch64|arm64) ARCH="arm64" ;;
  *) echo "Nicht unterstützte Architektur: $(uname -m)" >&2; exit 1 ;;
esac

URL="${BASE_URL}/rimperio-agent-linux-${ARCH}"
DEST="/usr/local/bin/rimperio-agent"

echo "Lade Agent von ${URL} …"
if command -v curl >/dev/null 2>&1; then
  curl -fSL "$URL" -o "$DEST"
else
  wget -O "$DEST" "$URL"
fi
chmod 0755 "$DEST"

ARGS=(setup -server "$SERVER" -secret "$SECRET" -site "$SITE")
[ -n "$DESC" ] && ARGS+=(-desc "$DESC")
[ "$INSECURE" = "1" ] && ARGS+=(-insecure)

echo "Richte systemd-Dienst ein …"
"$DEST" "${ARGS[@]}"

echo "Fertig. Status:  systemctl status rimperio-agent"
