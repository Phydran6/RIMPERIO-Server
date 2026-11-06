#!/bin/bash
# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.07.19.16.00.00
# Beschreibung: PostgreSQL Major-Upgrade fuer RIMPERIO MIT Datenerhalt.
#               (z. B. 16-alpine -> 17-alpine)
#
#   Ablauf: 1) logische Sicherung der laufenden DB (pg_dump -Fc)
#           2) Stack stoppen + altes Postgres-Volume loeschen
#           3) neue Postgres-Version frisch starten
#           4) DB aus der Sicherung wiederherstellen
#           5) restlichen Stack starten
#
#   Die Sicherungsdatei bleibt danach als Backup liegen.
#
#   Verwendung (im Projektverzeichnis, wo docker-compose.yml liegt):
#       sudo bash db/migrate.sh            # Ziel = POSTGRES_VERSION aus .env (Default 17)
#       sudo bash db/migrate.sh 18         # Ziel-Major explizit angeben
#       sudo bash db/migrate.sh 17 -y      # ohne Rueckfrage
#
#   One-Liner direkt von GitHub (findet /opt/rimperio automatisch):
#       curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/db/migrate.sh | sudo bash
#       curl -sSL .../db/migrate.sh | sudo bash -s -- 18 -y   # Ziel + ohne Rueckfrage
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()   { echo -e "${RED}[ERROR]${NC} $1"; }

# --- Projektverzeichnis finden (auch fuer 'curl ... | sudo bash') ------------
# Reihenfolge: 1) aktuelles Verzeichnis  2) relativ zum Skript (db/..)
#              3) Standard-Installationsort /opt/rimperio
if [[ -f docker-compose.yml ]]; then
    :
elif [[ -f "$(dirname "$0")/../docker-compose.yml" ]]; then
    cd "$(dirname "$0")/.."
elif [[ -f /opt/rimperio/docker-compose.yml ]]; then
    cd /opt/rimperio
else
    err "docker-compose.yml nicht gefunden."
    err "Bitte ins Projektverzeichnis wechseln, z. B.: cd /opt/rimperio"
    exit 1
fi
info "Projektverzeichnis: $(pwd)"

# --- .env laden --------------------------------------------------------------
if [[ -f .env ]]; then
    set -a; . ./.env; set +a
fi
DB_USER="${DB_USER:-rimperio}"
DB_NAME="${DB_NAME:-rimperio}"

# --- Parameter ( [Ziel-Major] [-y] , Reihenfolge egal ) ----------------------
TARGET=""
AUTO="no"
for a in "$@"; do
    case "$a" in
        -y|--yes) AUTO="yes" ;;
        [0-9]*)   TARGET="$a" ;;
    esac
done
TARGET="${TARGET:-${POSTGRES_VERSION:-17}}"
[[ "${YES:-}" == "1" ]] && AUTO="yes"

STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP="rimperio-db-backup-${STAMP}.dump"

# --- Vorbedingungen ----------------------------------------------------------
command -v docker >/dev/null 2>&1 || { err "docker nicht gefunden."; exit 1; }
docker compose version >/dev/null 2>&1 || { err "docker compose nicht verfuegbar."; exit 1; }

# Der Container heisst seit 2026.09 'rimperio-ext-postgres' - das 'ext' sagt, dass
# PostgreSQL ein FREMDES Image ist und nicht zu RIMPERIO gehoert. Aeltere
# Installationen haben noch den alten Namen; beide werden akzeptiert.
PG_CONTAINER=""
for candidate in rimperio-ext-postgres rimperio-postgres; do
    if docker inspect "$candidate" >/dev/null 2>&1; then
        PG_CONTAINER="$candidate"
        break
    fi
done

if [[ -z "$PG_CONTAINER" ]]; then
    err "Kein Postgres-Container gefunden (gesucht: rimperio-ext-postgres, rimperio-postgres)."
    err "Bitte zuerst 'docker compose up -d postgres' ausfuehren."
    exit 1
fi
info "Postgres-Container: ${PG_CONTAINER}"

# Volume des laufenden Containers exakt ermitteln
PG_VOL="$(docker inspect "$PG_CONTAINER" \
    --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}')"
if [[ -z "$PG_VOL" ]]; then
    err "Konnte das Postgres-Daten-Volume nicht ermitteln. Abbruch."
    exit 1
fi

echo ""
info "Aktuelle DB-Version:"
docker compose exec -T postgres postgres --version 2>/dev/null || true
info "Ziel-Version:      PostgreSQL ${TARGET}-alpine"
info "Daten-Volume:      ${PG_VOL}"
info "Sicherung nach:    ${BACKUP}"
echo ""

# --- 1) Sicherung ------------------------------------------------------------
info "Erstelle logische Sicherung (pg_dump -Fc)..."
docker compose exec -T postgres pg_dump -U "$DB_USER" -Fc "$DB_NAME" > "$BACKUP"
if [[ ! -s "$BACKUP" ]]; then
    err "Sicherung ist leer -> Abbruch. Es wurde nichts veraendert."
    rm -f "$BACKUP"
    exit 1
fi
ok "Sicherung erstellt ($(du -h "$BACKUP" | cut -f1))."

# --- Bestaetigung ------------------------------------------------------------
if [[ "$AUTO" != "yes" ]]; then
    warn "Das Volume '${PG_VOL}' wird GELOESCHT und mit PostgreSQL ${TARGET} neu aufgebaut."
    warn "Wiederherstellung erfolgt aus '${BACKUP}'."
    # /dev/tty, damit die Abfrage auch bei 'curl ... | sudo bash' funktioniert
    if [[ -r /dev/tty ]]; then
        read -r -p "Fortfahren? [j/N] " c < /dev/tty
    else
        err "Keine interaktive Eingabe moeglich (Pipe). Fuer automatischen Lauf '-y' anhaengen."
        warn "Sicherung bleibt: ${BACKUP}"
        exit 1
    fi
    [[ "$c" == "j" || "$c" == "J" ]] || { warn "Abgebrochen. Sicherung bleibt: ${BACKUP}"; exit 1; }
fi

# --- POSTGRES_VERSION in .env festschreiben ----------------------------------
if [[ -f .env ]] && grep -q '^POSTGRES_VERSION=' .env; then
    sed -i "s/^POSTGRES_VERSION=.*/POSTGRES_VERSION=${TARGET}/" .env
else
    echo "POSTGRES_VERSION=${TARGET}" >> .env
fi
export POSTGRES_VERSION="$TARGET"
ok "POSTGRES_VERSION=${TARGET} in .env gesetzt."

# --- 2) Stack stoppen, altes Volume entfernen --------------------------------
info "Stoppe Stack..."
docker compose down
info "Entferne altes Daten-Volume ${PG_VOL}..."
docker volume rm "$PG_VOL"

# --- 3) Neue Postgres-Version starten ----------------------------------------
info "Starte PostgreSQL ${TARGET} (frisch)..."
docker compose up -d postgres
info "Warte, bis die DB bereit ist..."
for i in $(seq 1 60); do
    if docker compose exec -T postgres pg_isready -U "$DB_USER" >/dev/null 2>&1; then break; fi
    sleep 2
    [[ "$i" == "60" ]] && { err "DB wurde nicht rechtzeitig bereit."; exit 1; }
done
ok "PostgreSQL ${TARGET} laeuft."

# --- 4) Wiederherstellung ----------------------------------------------------
# Das init.sql hat beim Erststart ein leeres Schema angelegt. Wir verwerfen es
# und stellen die Original-DB sauber (ohne Konflikte) aus der Sicherung wieder her.
info "Setze frisches Schema zurueck und stelle Sicherung wieder her..."
docker compose exec -T postgres dropdb   -U "$DB_USER" --if-exists --force "$DB_NAME"
docker compose exec -T postgres createdb -U "$DB_USER" "$DB_NAME"
docker compose exec -T postgres pg_restore -U "$DB_USER" -d "$DB_NAME" --no-owner < "$BACKUP"
ok "Daten wiederhergestellt."

# --- 5) Restlichen Stack starten ---------------------------------------------
info "Starte restlichen Stack..."
docker compose up -d

echo ""
ok "Upgrade auf PostgreSQL ${TARGET} abgeschlossen."
info "Neue DB-Version:"
docker compose exec -T postgres postgres --version 2>/dev/null || true
warn "Sicherung bleibt vorsichtshalber liegen: ${BACKUP}"
warn "Wenn RIMPERIO laeuft und die Logs vollstaendig sind, kannst du sie loeschen."
