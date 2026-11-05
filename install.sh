#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Datei:        install.sh
# Projekt:      RIMPERIO
# Autor:        Phydran6
# Version:      2026.10.31.15.40.00
#
# Beschreibung: Ein Befehl, ein Server. Holt den Code, legt bei Bedarf Docker
#               nach, erzeugt starke Geheimnisse in der .env und startet den
#               ganzen Stack. Läuft auf einem Linux-Host mit Root.
#
# Aufruf:
#   curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/install.sh | sudo bash
# ---------------------------------------------------------------------------
set -euo pipefail

REPO="https://github.com/Phydran6/RIMPERIO-Server.git"
DIR="${RIMPERIO_DIR:-/opt/rimperio}"
RIMPERIO_VERSION="2026.10.31.15.40.00"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!  \033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mx  \033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Bitte als root ausführen (sudo)."

log "RIMPERIO-Installer $RIMPERIO_VERSION"

# --- Grundwerkzeuge --------------------------------------------------------
ensure_pkg() {
  command -v "$1" >/dev/null 2>&1 && return 0
  log "Installiere $1 …"
  if command -v apt-get >/dev/null 2>&1; then apt-get update -qq && apt-get install -y -qq "$2"
  elif command -v dnf >/dev/null 2>&1; then dnf install -y -q "$2"
  elif command -v yum >/dev/null 2>&1; then yum install -y -q "$2"
  else die "Kein bekannter Paketmanager. Bitte $1 von Hand installieren."; fi
}
ensure_pkg git git
ensure_pkg curl curl
ensure_pkg openssl openssl

# --- Docker ----------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  log "Installiere Docker …"
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker || true
fi
if ! docker compose version >/dev/null 2>&1; then
  die "Docker-Compose-Plugin fehlt. Bitte 'docker compose' bereitstellen."
fi

# --- Code holen ------------------------------------------------------------
if [ -d "$DIR/.git" ]; then
  log "Aktualisiere vorhandene Installation in $DIR …"
  git -C "$DIR" pull --ff-only || warn "git pull übersprungen (lokale Änderungen?)."
else
  log "Hole Code nach $DIR …"
  git clone --depth 1 "$REPO" "$DIR"
fi
cd "$DIR"

# --- .env erzeugen ---------------------------------------------------------
if [ ! -f .env ]; then
  log "Erzeuge .env mit frischen Geheimnissen …"
  cp .env.example .env
  DB_PW="$(openssl rand -hex 24)"
  SECRET="$(openssl rand -hex 48)"
  ENROLL="$(openssl rand -hex 16)"
  sed -i "s|CHANGE_ME_DB_PASSWORT|${DB_PW}|" .env
  sed -i "s|CHANGE_ME_64_ZEICHEN_ZUFALL|${SECRET}|" .env
  sed -i "s|CHANGE_ME_ENROLL_GEHEIMNIS|${ENROLL}|" .env
else
  warn ".env besteht bereits — bleibt unverändert."
fi

# --- Starten ---------------------------------------------------------------
log "Baue und starte den Stack (das kann beim ersten Mal dauern) …"
docker compose up -d --build

# --- Zusammenfassung -------------------------------------------------------
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
ENROLL_NOW="$(grep -E '^RIMPERIO_ENROLL_SECRET=' .env | cut -d= -f2-)"
echo
log "Fertig."
echo "  Oberfläche:   http://${IP:-SERVER-IP}/        (Anmeldung: admin / admin — sofort ändern!)"
echo "  API-Doku:     http://${IP:-SERVER-IP}/api/docs"
echo "  Gesundheit:   http://${IP:-SERVER-IP}/api/health"
echo "  Enrollment-Geheimnis für Agents:  ${ENROLL_NOW}"
echo
echo "  HTTPS: in $DIR/.env  RIMPERIO_SITE_ADDRESS=deine-domain.de  setzen, dann:  docker compose up -d"
echo "  Logs:  docker compose -f $DIR/docker-compose.yml logs -f backend"
