#!/bin/bash
# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.11.08.17.00.00
# Changelog:   docs/changelog/agents.md
# Beschreibung: RIMPERIO Linux Agent - Installer (teilautomatisch)
#
#   Standard = HTTPS: ein schlanker Python-systemd-Dienst sendet ALLE Logs
#   (journald) verschluesselt + Token an  https://<FQDN>/api/agents/ingest
#   Alternativ Syslog (rsyslog -> UDP/TCP). Keine externen Abhaengigkeiten.
#
#   NICHT ALS ROOT: Der Dienst "rimperio-log-agent" laeuft unter einem eigenen
#   Dienstkonto (Standard "rimperio", waehlbar mit --user; fehlt es, wird es
#   angelegt). Lesen darf er das Journal ueber die Gruppe systemd-journal,
#   schreiben nur in sein eigenes Verzeichnis. Alles liegt unter
#   /opt/RMM/rimperio - geteilt mit dem Fernwartungs-Agent (install-agent.sh):
#     lib/log-agent.py      Programm                  root,  0644
#     etc/log-agent.json    Konfiguration mit Token   Konto, 0600
#     var/log-agent.*       Leseposition, Geraetenummer
#   Aeltere Fassungen (root, /opt/rimperio-agent, Dienst rimperio-agent) werden
#   beim Installieren uebernommen - samt Leseposition, ohne neue Anmeldung.
#
#   TEILAUTOMATISCH: Am Anfang laeuft ein 5s-Countdown. Wird eine Taste gedrueckt,
#   schaltet der Installer auf MANUELL und fragt ab da alle Werte blockierend ab
#   (kein Timeout). Ohne Tastendruck / ohne Terminal (Pipe/cron) laeuft alles
#   automatisch mit Standardwerten durch.
#
#   One-Liner (GitHub Raw), Werte per Umgebungsvariable:
#     curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/agent/logship/install-linux.sh \
#       | RIMPERIO_FQDN=rimperio.example.com RIMPERIO_TOKEN=xxxx sudo -E bash
#
#   ...oder per Parameter:
#     curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/agent/logship/install-linux.sh \
#       | sudo bash -s -- --fqdn rimperio.example.com --token xxxx
#
#   Anderes Dienstkonto:  ... --user svc-rimperio
#   Deinstallieren:  sudo bash install-linux.sh uninstall
#   Testen:          sudo bash install-linux.sh test
# ==============================================================================

set -e

# ------------------------------------------------------------------------------
# PLATZHALTER (optional): Zum fest Eintragen einfach das fuehrende '#' entfernen
# und den Wert setzen. Dann laeuft der One-Liner voll automatisch - ohne dass
# man beim Start noch etwas eingeben muss. Parameter/Umgebungsvariablen haben
# Vorrang vor diesen Platzhaltern.
# ------------------------------------------------------------------------------
#PLACEHOLDER_FQDN="rimperio.example.com"
#PLACEHOLDER_TOKEN="hier-agent-token-eintragen"
#PLACEHOLDER_MODE="https"      # https (Standard) oder syslog
#PLACEHOLDER_PORT="443"
#PLACEHOLDER_MINLEVEL="info"   # info | warning | error

# Farben
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

AGENT_VERSION="2026.11.08.17.00.00"

# --- Pfade: Syslog-Modus (rsyslog) ---
CONFIG_FILE="/etc/rsyslog.d/99-rimperio.conf"
QUEUE_PREFIX="/var/spool/rsyslog/rimperio_queue"

# --- Pfade: HTTPS-Modus (alles unter /opt/RMM/rimperio) ---
BASE_DIR="/opt/RMM/rimperio"
AGENT_SCRIPT="${BASE_DIR}/lib/log-agent.py"
AGENT_CONFIG="${BASE_DIR}/etc/log-agent.json"
AGENT_CURSOR="${BASE_DIR}/var/log-agent.cursor"
AGENT_ID_FILE="${BASE_DIR}/var/log-agent.id"
SERVICE_NAME="rimperio-log-agent"
SYSTEMD_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
# Der Fernwartungs-Agent (install-agent.sh) wohnt im selben Verzeichnis und
# unter demselben Dienstkonto.
RMM_UNIT="/etc/systemd/system/rimperio-agent.service"
# Steht im Kommentarfeld eines Kontos, das ein RIMPERIO-Installer angelegt hat.
# Nur solche Konten entfernt die Deinstallation wieder.
ACCOUNT_MARK="RIMPERIO Agent"

# --- Aeltere Fassungen (bis 2026.11.07): root, eigenes Verzeichnis, und der
#     Dienst hiess wie der Fernwartungs-Agent "rimperio-agent". ---
LEGACY_DIR="/opt/rimperio-agent"
LEGACY_CONFIG="${LEGACY_DIR}/config.json"
LEGACY_UNIT="/etc/systemd/system/rimperio-agent.service"

# --- Defaults / Verhalten ---
PROMPT_TIMEOUT="${RIMPERIO_TIMEOUT:-5}"   # Sekunden Countdown bis Auto-Start
ASSUME_YES="false"                      # true => nie fragen, immer Default
MANUAL="false"                          # true => alle Werte werden abgefragt (kein Timeout)
GATE_DONE="false"                       # interactive_gate nur einmal ausfuehren
ACTION="install"

# --- Konfiguration (Vorrang: Parameter > Umgebungsvariable > Platzhalter) ---
MODE="${RIMPERIO_MODE:-${PLACEHOLDER_MODE:-https}}"
FQDN="${RIMPERIO_FQDN:-${PLACEHOLDER_FQDN:-}}"
IPFB="${RIMPERIO_IP:-}"
PORT="${RIMPERIO_PORT:-${PLACEHOLDER_PORT:-}}"
TOKEN="${RIMPERIO_TOKEN:-${PLACEHOLDER_TOKEN:-}}"
MINLEVEL="${RIMPERIO_MINLEVEL:-${PLACEHOLDER_MINLEVEL:-info}}"
MINLEVEL_EXPLICIT="false"                 # true => nicht aus vorhandener Installation uebernehmen
if [[ -n "${RIMPERIO_MINLEVEL:-${PLACEHOLDER_MINLEVEL:-}}" ]]; then MINLEVEL_EXPLICIT="true"; fi
INSECURE="${RIMPERIO_INSECURE:-false}"
PROTO="${RIMPERIO_PROTO:-udp}"            # nur Syslog-Modus: udp | tcp
# Dienstkonto des HTTPS-Agents. Leer = wie der Fernwartungs-Agent, sonst "rimperio".
AGENT_USER="${RIMPERIO_USER:-}"
AGENT_GROUP=""
REUSED_TOKEN="false"                      # true => Geraeteschluessel aus bestehender Installation

# ==============================================================================
# Hilfsfunktionen
# ==============================================================================

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
fail() { log_error "$1"; exit 1; }

require_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "Dieses Script muss als root ausgeführt werden!"
        log_info "Aufruf: sudo bash $0   (bzw. '| sudo -E bash' beim One-Liner)"
        exit 1
    fi
}

# true nur, wenn /dev/tty wirklich geoeffnet werden kann (nicht nur -r).
# Sonst wuerde 'read </dev/tty' bei Pipe ohne Terminal eine Fehlermeldung werfen.
have_tty() { true 2>/dev/null </dev/tty; }

# Fragt einen Wert ab. Nur im MANUELLEN Modus (nach Tastendruck im Gate) wird
# tatsächlich gefragt. Im automatischen Modus wird still der Default genommen.
#
# WICHTIG - Zeitlimit: früher wartete diese Funktion unbegrenzt. Lief das Skript
# über eine Pipe (curl | sudo bash), über SSH ohne echtes Terminal oder aus
# einem Automatismus heraus, blieb es an der ersten Frage einfach stehen. Genau
# das war der Grund, warum die Deinstallation "an vielen Stellen hängt".
# Jetzt gilt: keine Eingabe innerhalb von ASK_TIMEOUT Sekunden -> Default.
# Ein Terminal steht ohnehin nicht zur Verfügung -> sofort Default.
# ask VARNAME "Frage" "default"
ASK_TIMEOUT="${RIMPERIO_ASK_TIMEOUT:-60}"

ask() {
    local __name="$1" prompt="$2" def="$3" ans=""
    if [[ "$MANUAL" == "true" ]] && have_tty; then
        if ! read -r -t "$ASK_TIMEOUT" -p "$prompt [${def:-leer}] (Enter = Default): " ans </dev/tty; then
            ans=""
            echo ""
            log_warn "Keine Eingabe innerhalb von ${ASK_TIMEOUT}s - nehme Standardwert."
        fi
        echo ""
    fi
    [[ -z "$ans" ]] && ans="$def"
    printf -v "$__name" '%s' "$ans"
}

# Einmaliges "Gate" am Anfang: kurzer Countdown. Wird EINE Taste gedrückt, schaltet
# der Installer auf MANUELL (alle folgenden Abfragen blockieren, kein Timeout mehr).
# Keine Eingabe / kein Terminal / --yes => automatischer Ablauf mit Standardwerten.
interactive_gate() {
    [[ "$GATE_DONE" == "true" ]] && return 0
    GATE_DONE="true"
    [[ "$ASSUME_YES" == "true" ]] && return 0
    have_tty || return 0
    echo ""
    log_info "Automatischer Start in ${PROMPT_TIMEOUT}s. Für MANUELLE Eingabe jetzt eine Taste drücken..."
    local _k=""
    if read -rsN1 -t "$PROMPT_TIMEOUT" _k </dev/tty; then
        MANUAL="true"
        # Restliche gepufferte Zeichen (z.B. Enter nach dem Aufweck-Tastendruck)
        # verwerfen, damit die erste echte Abfrage nicht sofort den Default nimmt.
        while read -rsN1 -t 0.05 _k </dev/tty 2>/dev/null; do :; done
        echo ""
        log_success "Manueller Modus aktiv - alle Werte werden abgefragt (kein Timeout)."
    else
        echo ""
        log_info "Keine Eingabe - automatischer Ablauf mit Standardwerten."
    fi
    return 0
}

show_help() {
    cat <<EOF
RIMPERIO Linux Agent Installer v${AGENT_VERSION}

Aufruf:   sudo bash install-linux.sh [aktion] [optionen]
Aktionen: install (Standard) | uninstall | uninstall-purge | test

Optionen (auch als Umgebungsvariable RIMPERIO_*):
  --fqdn <name>       Server-FQDN            (RIMPERIO_FQDN)   [Pflicht bei https]
  --token <token>     Agent-Token (Bearer)  (RIMPERIO_TOKEN)  [Pflicht bei https]
  --mode <https|syslog>  Modus (Standard https)  (RIMPERIO_MODE)
  --port <n>          Port (https=443, syslog=514)  (RIMPERIO_PORT)
  --proto <udp|tcp>   Protokoll im Syslog-Modus (Standard udp)  (RIMPERIO_PROTO)
  --ip <ip>           Optionale IP als Fallback     (RIMPERIO_IP)
  --min-level <info|warning|error>            (RIMPERIO_MINLEVEL)
  --insecure          Selbstsignierte TLS-Zerts akzeptieren  (RIMPERIO_INSECURE=true)
  --user <konto>      Dienstkonto des HTTPS-Agents (Standard rimperio, wird
                      angelegt; nie root)          (RIMPERIO_USER)
  --yes, --unattended Keine Rueckfragen, alles Default        (ASSUME_YES)
  --timeout <sek>     Countdown bis Auto-Start (Standard 5)  (RIMPERIO_TIMEOUT)
                      Taste druecken => manueller Modus (alle Werte, kein Timeout)

Beispiel (One-Liner):
  curl -sSL <URL> | sudo bash -s -- --fqdn rimperio.example.com --token xxxx
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            install)         ACTION="install" ;;
            uninstall|remove) ACTION="uninstall" ;;
            uninstall-purge|purge) ACTION="uninstall-purge" ;;
            test)            ACTION="test" ;;
            --fqdn)          FQDN="${2:-}"; shift ;;
            --fqdn=*)        FQDN="${1#*=}" ;;
            --token)         TOKEN="${2:-}"; shift ;;
            --token=*)       TOKEN="${1#*=}" ;;
            --ip)            IPFB="${2:-}"; shift ;;
            --ip=*)          IPFB="${1#*=}" ;;
            --port)          PORT="${2:-}"; shift ;;
            --port=*)        PORT="${1#*=}" ;;
            --proto)         PROTO="${2:-}"; shift ;;
            --proto=*)       PROTO="${1#*=}" ;;
            --mode)          MODE="${2:-}"; shift ;;
            --mode=*)        MODE="${1#*=}" ;;
            --min-level)     MINLEVEL="${2:-}"; MINLEVEL_EXPLICIT="true"; shift ;;
            --min-level=*)   MINLEVEL="${1#*=}"; MINLEVEL_EXPLICIT="true" ;;
            --insecure|-k)   INSECURE="true" ;;
            --user)          AGENT_USER="${2:-}"; shift ;;
            --user=*)        AGENT_USER="${1#*=}" ;;
            --yes|-y|--unattended) ASSUME_YES="true" ;;
            --timeout)       PROMPT_TIMEOUT="${2:-5}"; shift ;;
            --timeout=*)     PROMPT_TIMEOUT="${1#*=}" ;;
            --help|-h)       show_help; exit 0 ;;
            *)               log_warn "Unbekannter Parameter: $1" ;;
        esac
        shift || true
    done
}

# ==============================================================================
# Installationsstatus erkennen
# ==============================================================================

syslog_installed() { [[ -f "$CONFIG_FILE" ]] && grep -q "RIMPERIO Agent v" "$CONFIG_FILE"; }
# Aelterer Log-Agent: Dienst "rimperio-agent" mit dem Python-Skript (nicht der
# gleichnamige Fernwartungs-Agent!) oder sein Verzeichnis.
legacy_unit_is_log_agent() { [[ -f "$LEGACY_UNIT" ]] && grep -q 'rimperio_agent.py' "$LEGACY_UNIT"; }
legacy_installed() { legacy_unit_is_log_agent || [[ -d "$LEGACY_DIR" ]]; }
https_installed()  { [[ -f "$SYSTEMD_UNIT" ]] || [[ -f "$AGENT_SCRIPT" ]] || legacy_installed; }
is_installed()     { syslog_installed || https_installed; }

# Die Konfiguration der vorhandenen Installation: neu, sonst die aeltere.
existing_config() {
    if [[ -f "$AGENT_CONFIG" ]]; then echo "$AGENT_CONFIG"
    elif [[ -f "$LEGACY_CONFIG" ]]; then echo "$LEGACY_CONFIG"
    fi
}

# ==============================================================================
# Server-Konfig aus vorhandener Installation lesen (fuer Purge-Defaults)
# ==============================================================================

read_agent_config() {
    local key="$1" file
    file="$(existing_config)"
    [[ -n "$file" ]] || return 0
    command -v python3 >/dev/null 2>&1 || return 0
    python3 - "$file" "$key" <<'PYEOF' 2>/dev/null || true
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    v = d.get(sys.argv[2], "")
    if isinstance(v, bool):
        v = "true" if v else "false"
    print("" if v is None else v)
except Exception:
    pass
PYEOF
}

detect_server_from_config() {
    if [[ -n "$(existing_config)" ]]; then
        SERVER_HOST_DETECTED="$(read_agent_config server_fqdn)"
        [[ -z "$SERVER_HOST_DETECTED" ]] && SERVER_HOST_DETECTED="$(read_agent_config server_ip)"
        SERVER_PORT_DETECTED="$(read_agent_config server_port)"
        TOKEN_DETECTED="$(read_agent_config agent_token)"
        return
    fi
    if [[ -f "$CONFIG_FILE" ]]; then
        local line target host_port
        line=$(grep -E 'target=' "$CONFIG_FILE" | tail -1 || true)
        if [[ -n "$line" ]]; then
            SERVER_HOST_DETECTED=$(echo "$line" | sed -n 's/.*target="\([^"]*\)".*/\1/p')
            SERVER_PORT_DETECTED=$(grep -E 'port=' "$CONFIG_FILE" | sed -n 's/.*port="\([^"]*\)".*/\1/p' | tail -1)
        fi
    fi
}

# ==============================================================================
# DNS-Aufloesung + Erreichbarkeit (FQDN und IP gleichermassen)
# ==============================================================================

resolve_host() {
    local host="$1"
    if [[ "$host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        return 0
    fi
    if command -v getent >/dev/null 2>&1; then
        getent ahosts "$host" >/dev/null 2>&1 && return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import socket,sys; socket.getaddrinfo(sys.argv[1], None)' "$host" >/dev/null 2>&1 && return 0
    fi
    if command -v dig >/dev/null 2>&1; then
        [[ -n "$(dig +short "$host" 2>/dev/null)" ]] && return 0
    fi
    if command -v host >/dev/null 2>&1; then
        host "$host" >/dev/null 2>&1 && return 0
    fi
    return 1
}

check_tcp_reachable() {
    local host="$1" port="$2" tmo="${3:-3}"
    if timeout "$tmo" bash -c "exec 3<>/dev/tcp/${host}/${port}" 2>/dev/null; then
        return 0
    fi
    if command -v nc >/dev/null 2>&1; then
        nc -z -w "$tmo" "$host" "$port" >/dev/null 2>&1 && return 0
    fi
    return 1
}

# ==============================================================================
#  SYSLOG-MODUS  (rsyslog -> UDP/TCP)
# ==============================================================================

ensure_rsyslog() {
    if ! command -v rsyslogd &> /dev/null; then
        log_warn "rsyslog nicht gefunden, installiere..."
        if command -v apt-get &> /dev/null; then
            apt-get update && apt-get install -y rsyslog
        elif command -v dnf &> /dev/null; then
            dnf install -y rsyslog
        elif command -v yum &> /dev/null; then
            yum install -y rsyslog
        else
            fail "Paketmanager nicht erkannt. Bitte rsyslog manuell installieren."
        fi
    fi
    if ! systemctl is-active --quiet rsyslog 2>/dev/null; then
        systemctl enable rsyslog && systemctl start rsyslog
    fi
}

configure_syslog() {
    ensure_rsyslog
    log_info "Konfiguriere rsyslog (Syslog-Modus)..."

    ask FQDN "RIMPERIO Server Adresse (FQDN oder IP)" "$FQDN"
    [[ -z "$FQDN" ]] && fail "Server-Adresse fehlt. Per --fqdn / RIMPERIO_FQDN / Platzhalter setzen."
    [[ -z "$PORT" ]] && PORT="514"
    ask PORT "RIMPERIO Server Port" "$PORT"

    local proto="$PROTO"
    ask proto "Protokoll (udp/tcp)" "$PROTO"
    case "$proto" in tcp|TCP|2) proto="tcp" ;; *) proto="udp" ;; esac

    if resolve_host "$FQDN"; then
        log_success "Adresse auflösbar: $FQDN"
    else
        log_warn "\"$FQDN\" derzeit nicht per DNS auflösbar - rsyslog versucht es zur Laufzeit erneut."
    fi
    if [[ "$proto" == "tcp" ]]; then
        check_tcp_reachable "$FQDN" "$PORT" && log_success "TCP ${FQDN}:${PORT} erreichbar" \
            || log_warn "TCP ${FQDN}:${PORT} derzeit nicht erreichbar - Konfiguration wird dennoch geschrieben."
    fi

    [[ -f "$CONFIG_FILE" ]] && cp "$CONFIG_FILE" "${CONFIG_FILE}.backup.$(date +%Y%m%d_%H%M%S)"

    cat > "$CONFIG_FILE" << EOF
# ==============================================================================
# RIMPERIO Agent v${AGENT_VERSION} - rsyslog Konfiguration (Syslog-Modus)
# Erstellt: $(date)
# Server: ${FQDN}:${PORT} (${proto})
# ==============================================================================
action(
  type="omfwd"
  target="${FQDN}"
  port="${PORT}"
  protocol="${proto}"
  Template="RSYSLOG_TraditionalForwardFormat"
  action.resumeRetryCount="-1"
  queue.type="LinkedList"
  queue.filename="rimperio_queue"
  queue.maxDiskSpace="100m"
  queue.saveOnShutdown="on"
)
EOF
    log_success "Konfiguration erstellt: $CONFIG_FILE"
}

restart_rsyslog() {
    log_info "Starte rsyslog neu..."
    if rsyslogd -N1 2>/dev/null; then
        systemctl restart rsyslog
        sleep 2
        systemctl is-active --quiet rsyslog && log_success "rsyslog läuft" \
            || fail "rsyslog konnte nicht gestartet werden. Prüfe: journalctl -u rsyslog -n 50"
    else
        fail "rsyslog Konfiguration fehlerhaft! Prüfe: rsyslogd -N1"
    fi
}

# ==============================================================================
#  DIENSTKONTO UND VERZEICHNISSE  (HTTPS-Modus - der Agent laeuft nicht als root)
# ==============================================================================

# Konto, unter dem ein Dienst laut Unit-Datei laeuft (leer = root/unbekannt).
unit_user() {
    if [[ -f "$1" ]]; then sed -n 's/^User=//p' "$1" | head -n1; fi
}

# Laeuft der Fernwartungs-Agent (install-agent.sh) ebenfalls unter /opt/RMM/rimperio?
rmm_agent_here() { [[ -f "$RMM_UNIT" ]] && grep -q "${BASE_DIR}/" "$RMM_UNIT"; }

# Ein Konto fuer alle RIMPERIO-Dienste auf dem Rechner - Fernwartungs- und
# Log-Agent teilen sich /opt/RMM/rimperio. Ohne Angabe gilt das Konto des
# Fernwartungs-Agents, sonst "rimperio".
choose_account() {
    local other=""
    if rmm_agent_here; then other="$(unit_user "$RMM_UNIT")"; fi
    if [[ "$other" == "root" ]]; then other=""; fi
    ask AGENT_USER "Dienstkonto, unter dem der Agent läuft (wird angelegt, falls es fehlt)" "${AGENT_USER:-${other:-rimperio}}"
    if [[ -n "$other" && "$other" != "$AGENT_USER" ]]; then
        fail "Der Fernwartungs-Agent auf diesem Rechner läuft als \"$other\". Beide teilen sich $BASE_DIR - bitte dasselbe Konto nehmen: --user $other"
    fi
    if [[ "$AGENT_USER" == "root" ]]; then
        fail "Der Agent läuft absichtlich nicht als root. Bitte ein eigenes Konto nennen (Standard: --user rimperio)."
    fi
    if [[ ! "$AGENT_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
        fail "Ungültiger Kontoname \"$AGENT_USER\" - erlaubt: Kleinbuchstaben, Ziffern, _ und -, höchstens 32 Zeichen."
    fi
}

ensure_account() {
    if id -u "$AGENT_USER" >/dev/null 2>&1; then
        [[ "$(id -u "$AGENT_USER")" -ne 0 ]] || fail "Das Konto \"$AGENT_USER\" hat die UID 0 und ist damit root."
        log_info "Dienstkonto \"$AGENT_USER\" ist vorhanden und wird benutzt."
    else
        command -v useradd >/dev/null 2>&1 || fail "useradd fehlt - bitte das Konto \"$AGENT_USER\" selbst anlegen."
        local shell
        shell="$(command -v nologin 2>/dev/null || echo /bin/false)"
        if getent group "$AGENT_USER" >/dev/null 2>&1; then
            useradd -r -g "$AGENT_USER" -d "${BASE_DIR}/var" -M -s "$shell" -c "$ACCOUNT_MARK" "$AGENT_USER" \
                || fail "Das Dienstkonto \"$AGENT_USER\" ließ sich nicht anlegen (useradd)."
        else
            useradd -r -U -d "${BASE_DIR}/var" -M -s "$shell" -c "$ACCOUNT_MARK" "$AGENT_USER" \
                || fail "Das Dienstkonto \"$AGENT_USER\" ließ sich nicht anlegen (useradd)."
        fi
        log_success "Dienstkonto \"$AGENT_USER\" angelegt (Systemkonto, ohne Anmeldung und ohne Passwort)."
    fi
    AGENT_GROUP="$(id -gn "$AGENT_USER")"
}

# Programm und Konfiguration liegen in Ordnern, die nur root beschreiben darf;
# das Dienstkonto bekommt etc/ (lesen) und var/ (Leseposition) fuer sich.
prepare_dirs() {
    local d owner mode
    [[ -d /opt ]] || install -d -m 0755 -o root -g root /opt
    owner="$(stat -c %u /opt)"; mode="$(stat -c %a /opt)"
    if [[ "$owner" != "0" ]] || (( (8#$mode & 8#022) != 0 )); then
        fail "/opt gehört nicht root oder ist für andere beschreibbar (Rechte $mode). Bitte zuerst richten: chown root:root /opt && chmod go-w /opt"
    fi
    for d in /opt/RMM "$BASE_DIR" "${BASE_DIR}/lib" "${BASE_DIR}/etc" "${BASE_DIR}/var"; do
        if [[ -L "$d" ]]; then fail "$d ist ein symbolischer Link - so nicht. Bitte entfernen und neu starten."; fi
    done
    for d in /opt/RMM "$BASE_DIR" "${BASE_DIR}/lib"; do
        install -d "$d"
        chown root:root "$d"
        chmod 0755 "$d"
    done
    # Nur die Ordner selbst - nie rekursiv: Was darin liegt, gehoert dem Konto,
    # und root fasst es nicht an (ein Symlink darin fuehrte sonst sonstwohin).
    for d in "${BASE_DIR}/etc" "${BASE_DIR}/var"; do
        install -d "$d"
        chown "${AGENT_USER}:${AGENT_GROUP}" "$d"
        chmod 0700 "$d"
    done
}

# In etc/ und var/ schreibt nur das Dienstkonto selbst: root schriebe einem
# untergeschobenen Symlink hinterher. Inhalt kommt von stdin.
write_as_account() {
    runuser -u "$AGENT_USER" -- sh -c 'umask 077; cat > "$1.new" && mv -f "$1.new" "$1"' sh "$1"
}

# Werte, die aus einer vorhandenen Konfiguration kommen, landen spaeter in
# Befehlen - nur, was nach Adresse, Port und Schluessel aussieht, kommt durch.
valid_host()  { [[ "$1" =~ ^[A-Za-z0-9._:-]{1,253}$ ]]; }
valid_port()  { [[ "$1" =~ ^[0-9]{1,5}$ ]]; }
valid_token() { [[ "$1" =~ ^[A-Za-z0-9._~+/=-]{8,250}$ ]]; }

# Gruppe, die das ganze Journal lesen darf.
journal_group() {
    if getent group systemd-journal >/dev/null 2>&1; then echo "systemd-journal"
    elif getent group adm >/dev/null 2>&1; then echo "adm"
    fi
}

remove_account() {
    local acct="$1" entry
    entry="$(getent passwd "$acct" || true)"
    [[ -n "$entry" ]] || return 0
    if [[ "$(printf '%s' "$entry" | cut -d: -f5)" != "$ACCOUNT_MARK" ]]; then
        log_info "Konto \"$acct\" bleibt - es war schon vor RIMPERIO da."
        return 0
    fi
    pkill -KILL -u "$acct" 2>/dev/null || true
    sleep 1
    if userdel "$acct" 2>/dev/null; then
        log_info "Dienstkonto \"$acct\" entfernt."
    else
        log_warn "Dienstkonto \"$acct\" ließ sich nicht entfernen - bitte selbst: userdel $acct"
    fi
}

# Konto und /opt/RMM/rimperio gehen erst, wenn nicht auch noch der
# Fernwartungs-Agent darunter laeuft.
cleanup_shared() {
    local acct="$1"
    [[ -d "$BASE_DIR" ]] || return 0
    if rmm_agent_here; then
        log_info "Der Fernwartungs-Agent bleibt eingerichtet - Konto und $BASE_DIR bleiben."
        return 0
    fi
    if [[ -n "$acct" && "$acct" != "root" ]]; then remove_account "$acct"; fi
    rm -rf "$BASE_DIR"
    rmdir /opt/RMM 2>/dev/null || true
    log_info "Verzeichnis entfernt: $BASE_DIR"
}

stop_and_remove_unit() {
    local name="$1" file="$2"
    # `timeout` davor: reagiert der Dienst nicht mehr, wartet systemctl sonst
    # bis zu 90 Sekunden pro Aufruf. Nach 20 s wird nachgeholfen.
    if ! timeout 20 systemctl stop "$name" 2>/dev/null; then
        timeout 10 systemctl kill -s KILL "$name" 2>/dev/null || true
    fi
    timeout 15 systemctl disable "$name" 2>/dev/null || true
    rm -f "$file"
    timeout 15 systemctl daemon-reload 2>/dev/null || true
    timeout 15 systemctl reset-failed "$name" 2>/dev/null || true
}

# Aeltere Fassung (root, /opt/rimperio-agent, Dienst rimperio-agent): anhalten,
# Leseposition und Geraetenummer uebernehmen - der Agent macht genau dort
# weiter -, danach Dienst und Verzeichnis entfernen.
stop_legacy() {
    local name
    for name in rimperio-agent "$SERVICE_NAME"; do
        # Nur den aelteren Log-Agent (Python-Skript unter /opt/rimperio-agent) -
        # install-agent.sh hat ihn womoeglich schon in rimperio-log-agent umbenannt.
        if grep -qs 'rimperio_agent.py' "/etc/systemd/system/${name}.service"; then
            timeout 20 systemctl stop "$name" 2>/dev/null || timeout 10 systemctl kill -s KILL "$name" 2>/dev/null || true
        fi
    done
}

migrate_legacy_state() {
    [[ -d "$LEGACY_DIR" ]] || return 0
    local moved=""
    # Die alten Dateien gehoeren root (lesen als root), geschrieben wird als Konto.
    if [[ ! -e "$AGENT_CURSOR" && -f "${LEGACY_DIR}/cursor" ]]; then
        write_as_account "$AGENT_CURSOR" < "${LEGACY_DIR}/cursor"
        moved="Leseposition"
    fi
    if [[ ! -e "$AGENT_ID_FILE" && -f "${LEGACY_DIR}/agent_id" ]]; then
        tr -dc '0-9' < "${LEGACY_DIR}/agent_id" | write_as_account "$AGENT_ID_FILE"
        moved="${moved:+$moved und }Gerätenummer"
    fi
    if [[ -n "$moved" ]]; then log_info "Aus der älteren Installation übernommen: $moved."; fi
}

remove_legacy() {
    if legacy_unit_is_log_agent; then
        stop_and_remove_unit rimperio-agent "$LEGACY_UNIT"
        log_info "Älterer Dienst rimperio-agent (Log-Agent als root) entfernt."
    fi
    if [[ -d "$LEGACY_DIR" ]]; then
        rm -rf "$LEGACY_DIR"
        log_info "Älteres Verzeichnis entfernt: $LEGACY_DIR"
    fi
    rm -rf "/var/lib/rimperio-agent" 2>/dev/null || true   # Altstand vor v2026.07.18.16
}

# Ohne --fqdn/--token: die Werte der vorhandenen Installation nehmen (Update
# ohne neue Anmeldung). Der gespeicherte Schluessel ist schon der eigene des
# Geraets - er wird nicht noch einmal getauscht.
EXISTING_TOKEN=""
load_existing_settings() {
    [[ -n "$(existing_config)" ]] || return 0
    [[ -z "$FQDN" && -z "$TOKEN" ]] || return 0
    local token fqdn ip port level
    token="$(read_agent_config agent_token)"
    fqdn="$(read_agent_config server_fqdn)"
    ip="$(read_agent_config server_ip)"
    port="$(read_agent_config server_port)"
    level="$(read_agent_config min_level)"
    [[ -n "$token" ]] || return 0
    # Die Datei gehoert dem Dienstkonto - was darin steht, wird geprueft, bevor
    # es in Befehle wandert, die als root laufen.
    if ! valid_token "$token" || { [[ -n "$fqdn" ]] && ! valid_host "$fqdn"; } \
        || { [[ -n "$ip" ]] && ! valid_host "$ip"; } || { [[ -n "$port" ]] && ! valid_port "$port"; } \
        || [[ -z "$fqdn$ip" ]]; then
        log_warn "Die vorhandene Konfiguration sieht nicht richtig aus - bitte --fqdn und --token angeben."
        return 0
    fi
    FQDN="${fqdn:-$ip}"
    if [[ -z "$IPFB" ]]; then IPFB="$ip"; fi
    if [[ -z "$PORT" ]]; then PORT="$port"; fi
    if [[ "$(read_agent_config skip_tls_verify)" == "true" ]]; then INSECURE="true"; fi
    case "$level" in
        info|warning|error) if [[ "$MINLEVEL_EXPLICIT" != "true" ]]; then MINLEVEL="$level"; fi ;;
    esac
    TOKEN="$token"
    EXISTING_TOKEN="$token"
    log_info "Einstellungen der vorhandenen Installation übernommen (Server ${FQDN:-$IPFB}) - keine neue Anmeldung nötig."
}

# ==============================================================================
#  HTTPS-MODUS  (Python systemd-Dienst -> /api/agents/ingest)
# ==============================================================================

PYTHON_BIN=""

ensure_https_deps() {
    PYTHON_BIN="$(command -v python3 || true)"
    if [[ -z "$PYTHON_BIN" ]]; then
        log_warn "python3 nicht gefunden, installiere..."
        if command -v apt-get &> /dev/null; then
            apt-get update && apt-get install -y python3
        elif command -v dnf &> /dev/null; then
            dnf install -y python3
        elif command -v yum &> /dev/null; then
            yum install -y python3
        else
            fail "Paketmanager nicht erkannt. Bitte python3 manuell installieren."
        fi
        PYTHON_BIN="$(command -v python3 || true)"
    fi
    [[ -z "$PYTHON_BIN" ]] && fail "python3 weiterhin nicht verfügbar."
    command -v systemctl >/dev/null 2>&1 || fail "systemd (systemctl) nicht gefunden - HTTPS-Modus benötigt systemd."
    command -v journalctl >/dev/null 2>&1 || fail "journalctl nicht gefunden - HTTPS-Agent liest Logs aus journald."
    command -v runuser >/dev/null 2>&1 || fail "runuser fehlt (util-linux) - der Agent läuft unter einem eigenen Konto, dafür wird es gebraucht."
}

write_agent_script() {
    cat > "$AGENT_SCRIPT" << 'PYEOF'
#!/usr/bin/env python3
# ==============================================================================
# RIMPERIO Linux Agent - HTTPS Log Forwarder
# Liest ALLE Logs aus journald und sendet sie verschlüsselt + Token-authentifiziert
# an  https://<FQDN>/api/agents/ingest  (Pakete von max. 500 Events, Cursor
# rueckt nach jedem angekommenen Paket vor, Rueckstand wird gedrosselt geholt).
# Nur Python-Standardbibliothek - keine externen Abhängigkeiten.
# ==============================================================================
import json
import os
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# Der Dienst laeuft als eigenes Konto (nicht root): Konfiguration lesen,
# Leseposition und Geraetenummer schreiben - mehr darf er nicht.
CONFIG_FILE = os.environ.get("RIMPERIO_CONFIG", "/opt/RMM/rimperio/etc/log-agent.json")
CURSOR_FILE = os.environ.get("RIMPERIO_CURSOR", "/opt/RMM/rimperio/var/log-agent.cursor")
# Hier merkt sich der Agent, welcher Geraeteeintrag auf dem Server ihm gehoert.
# Beim Deinstallieren wird genau dieser abgeraeumt - und nicht der eines
# Rechners, der zufaellig genauso heisst.
AGENT_ID_FILE = os.environ.get("RIMPERIO_AGENT_ID", "/opt/RMM/rimperio/var/log-agent.id")
# Pro Anfrage bis zu 500 Eintraege (der Server nimmt bis 5000). Weniger
# Anfragen = weit weg vom Limit des Servers (600 Anfragen/Minute).
BATCH_SIZE = 500
MSG_MAX = 2048
SOURCE_MAX = 100          # Feldgrenze des Servers
MAX_LINES = 5000          # Journal-Zeilen pro Durchlauf (begrenzt Speicher + Paketzahl)
CATCHUP_PAUSE = 2         # Sekunden zwischen Durchlaeufen, solange Rueckstand anliegt
MAX_BACKOFF = 300         # laengste Pause nach Sendefehlern (Sekunden)

# journald PRIORITY (syslog severity 0-7) -> Level-String
PRIO_TO_LEVEL = {0: "critical", 1: "critical", 2: "critical", 3: "error",
                 4: "warning", 5: "notice", 6: "info", 7: "debug"}
# Numerische Schwelle je min_level (kleiner = schwerwiegender)
MIN_PRIO = {"debug": 7, "info": 6, "notice": 5, "warning": 4, "error": 3, "critical": 2}


def log(msg):
    print("[rimperio-agent] " + msg, flush=True)


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


CFG = load_config()
SERVER_FQDN = (CFG.get("server_fqdn") or "").strip()
SERVER_IP = (CFG.get("server_ip") or "").strip()
SERVER_PORT = int(CFG.get("server_port") or 443)
TOKEN = (CFG.get("agent_token") or "").strip()
SKIP_TLS = bool(CFG.get("skip_tls_verify"))
MIN_LEVEL = (CFG.get("min_level") or "info").lower()
HOSTNAME = (CFG.get("hostname") or "").strip() or socket.gethostname()
POLL = int(CFG.get("poll_interval") or 5)
MIN_PRIO_NUM = MIN_PRIO.get(MIN_LEVEL, 6)


def build_ctx():
    ctx = ssl.create_default_context()
    if SKIP_TLS:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def resolvable(host):
    try:
        socket.getaddrinfo(host, None)
        return True
    except Exception:
        return False


def target_host():
    # FQDN zuerst (DNS-basiert), IP als Fallback
    if SERVER_FQDN and resolvable(SERVER_FQDN):
        return SERVER_FQDN
    if SERVER_IP:
        return SERVER_IP
    return SERVER_FQDN


def ingest_url():
    host = target_host()
    if not host:
        return None
    if SERVER_PORT == 443:
        return "https://%s/api/agents/ingest" % host
    return "https://%s:%d/api/agents/ingest" % (host, SERVER_PORT)


def local_ip():
    # Eigene IP Richtung Server melden: steht ein Reverse Proxy (NPM, Caddy)
    # dazwischen, sieht der Server sonst nur dessen IP. UDP-connect verschickt
    # nichts, es waehlt nur die ausgehende Schnittstelle.
    if (CFG.get("ip_address") or "").strip():
        return CFG["ip_address"].strip()
    host = target_host()
    if not host:
        return ""
    try:
        family, _, _, _, addr = socket.getaddrinfo(host, SERVER_PORT, 0, socket.SOCK_DGRAM)[0]
        s = socket.socket(family, socket.SOCK_DGRAM)
        try:
            s.connect(addr)
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return ""


def payload(events):
    body = {"hostname": HOSTNAME, "device_type": "linux_agent", "events": events}
    ip = local_ip()
    if ip:
        body["ip_address"] = ip
    return json.dumps(body).encode("utf-8")
def remember_agent_id(payload):
    """Merkt sich die Geraetenummer, die der Server zurueckmeldet."""
    try:
        agent_id = (payload or {}).get("agent_id")
        if not agent_id:
            return
        if read_agent_id() == str(agent_id):
            return
        os.makedirs(os.path.dirname(AGENT_ID_FILE), exist_ok=True)
        tmp = AGENT_ID_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(str(agent_id))
        os.replace(tmp, AGENT_ID_FILE)
        log("Geraetenummer auf dem Server: %s" % agent_id)
    except Exception as e:
        # Kein Grund abzubrechen - ohne die Nummer greift beim Abmelden nur der
        # ungenauere Weg ueber den Hostnamen.
        log("Geraetenummer nicht gespeichert: %s" % e)


def read_agent_id():
    try:
        with open(AGENT_ID_FILE, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except Exception:
        return None


def _retry_after(e):
    try:
        return max(1, min(int(e.headers.get("Retry-After") or 0), MAX_BACKOFF))
    except Exception:
        return 0


def send(events):
    """Ein Paket senden. Rueckgabe: (ergebnis, wartezeit).

    ergebnis "ok"    - angekommen (auch: vom Server als Duplikat erkannt)
             "retry" - spaeter GENAU DIESES Paket nochmal (Netz, Server weg,
                       429/5xx, Schluessel abgelehnt)
             "drop"  - der Server lehnt den Inhalt dauerhaft ab (400/413/422).
                       Erneut schicken wuerde ewig scheitern und alles
                       dahinter blockieren - also protokollieren und weiter.
    """
    if not events:
        return "ok", 0
    url = ingest_url()
    if not url:
        log("Kein Ziel (FQDN/IP) konfiguriert")
        return "retry", 0
    body = payload(events)
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": "Bearer " + TOKEN,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30, context=build_ctx()) as r:
            if not 200 <= r.status < 300:
                log("HTTP %s von %s" % (r.status, url))
                return "retry", 0
            try:
                remember_agent_id(json.loads(r.read().decode("utf-8", "replace")))
            except Exception:
                pass
            return "ok", 0
    except urllib.error.HTTPError as e:
        if e.code in (400, 413, 422):
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            log("HTTP %s - Paket mit %d Eintraegen verworfen: %s" % (e.code, len(events), detail))
            return "drop", 0
        log("HTTP %s von %s" % (e.code, url))
        return "retry", _retry_after(e)
    except Exception as e:
        log("Sendefehler (%s): %s" % (url, e))
        return "retry", 0


def read_cursor():
    try:
        with open(CURSOR_FILE, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def write_cursor(cursor):
    if not cursor:
        return
    try:
        os.makedirs(os.path.dirname(CURSOR_FILE), exist_ok=True)
        tmp = CURSOR_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(cursor)
        os.replace(tmp, CURSOR_FILE)
    except Exception as e:
        log("Cursor konnte nicht gespeichert werden: %s" % e)


def latest_cursor():
    # Cursor des letzten Journal-Eintrags -> Start "ab jetzt" (keine History-Flut)
    try:
        out = subprocess.run(["journalctl", "-o", "json", "-n", "1", "--no-pager"],
                             capture_output=True, text=True, timeout=30)
        for line in out.stdout.splitlines():
            try:
                j = json.loads(line)
            except Exception:
                continue
            if "__CURSOR" in j:
                return j["__CURSOR"]
    except Exception as e:
        log("latest_cursor Fehler: %s" % e)
    return None


def decode_message(m):
    if isinstance(m, list):
        try:
            m = bytes(m).decode("utf-8", "replace")
        except Exception:
            m = str(m)
    if m is None:
        return ""
    m = " ".join(str(m).split())
    if len(m) > MSG_MAX:
        m = m[:MSG_MAX]
    return m


def event_time(j):
    # Zeitpunkt des Ereignisses (nicht der Zustellung). Damit erkennt der
    # Server doppelt gelieferte Eintraege und verwirft sie, und nachgeholte
    # Logs landen zur richtigen Zeit statt alle "jetzt".
    try:
        us = int(j.get("__REALTIME_TIMESTAMP"))
        return datetime.fromtimestamp(us / 1000000.0, timezone.utc).isoformat()
    except Exception:
        return None


def to_event(j):
    try:
        prio = int(j.get("PRIORITY", 6))
    except Exception:
        prio = 6
    if prio > MIN_PRIO_NUM:
        return None
    level = PRIO_TO_LEVEL.get(prio, "info")
    ident = j.get("SYSLOG_IDENTIFIER") or j.get("_COMM") or "journal"
    unit = j.get("_SYSTEMD_UNIT")
    source = ("%s/%s" % (unit, ident)) if unit else ident
    message = decode_message(j.get("MESSAGE"))
    if not message:
        return None
    # Der Server nimmt hoechstens 100 Zeichen - ein laengerer Unit-Name liess
    # frueher das ganze Paket abweisen.
    ev = {"level": level, "source": str(source)[:SOURCE_MAX], "message": message}
    ts = event_time(j)
    if ts:
        ev["timestamp"] = ts
    try:
        facility = int(j.get("SYSLOG_FACILITY"))
        if 0 <= facility <= 23:
            ev["facility"] = facility
    except Exception:
        pass
    return ev


def collect_since(cursor):
    """Hoechstens MAX_LINES Journal-Zeilen nach `cursor` lesen.

    Rueckgabe: (batches, ok, more)
      batches - Liste von (events, cursor_danach). Jedes Paket traegt die
                Position, bis zu der es reicht - so rueckt der Cursor nach
                JEDEM angekommenen Paket vor und ein Fehler wiederholt nur
                dieses eine Paket, nie alles davor.
      ok      - False: journalctl hat den Cursor abgelehnt -> neu aufsetzen
      more    - True: es liegt noch Rueckstand an (MAX_LINES erreicht)

    Frueher las der Agent den GESAMTEN Rueckstand in einem Rutsch und schickte
    bei einem einzigen Fehler alles noch einmal. Nach einer laengeren Pause
    wurde daraus eine Endlosschleife, die den Server geflutet hat.
    """
    args = ["journalctl", "-o", "json", "--no-pager", "--after-cursor", cursor]
    try:
        # stderr verwerfen: gelesen wird nur stdout, ein volles stderr-Rohr
        # wuerde journalctl sonst blockieren.
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, errors="replace")
    except Exception as e:
        log("journalctl Fehler: %s" % e)
        return [], True, False
    batches = []
    events = []
    last = cursor
    lines = 0
    more = False
    try:
        for line in proc.stdout:
            try:
                j = json.loads(line)
            except Exception:
                continue
            if "__CURSOR" not in j:
                continue
            last = j["__CURSOR"]
            lines += 1
            ev = to_event(j)
            if ev:
                events.append(ev)
                if len(events) >= BATCH_SIZE:
                    batches.append((events, last))
                    events = []
            if lines >= MAX_LINES:
                more = True
                break
    finally:
        if more:
            proc.kill()
        try:
            proc.communicate(timeout=30)
        except Exception:
            proc.kill()
    if not more and proc.returncode not in (0, None) and lines == 0:
        log("journalctl rc=%s (Cursor ungueltig/rotiert?) - setze neu auf" % proc.returncode)
        return [], False, False
    # Rest - auch ganz ohne Events (alles unter MinLevel), damit der Cursor
    # trotzdem ueber die gefilterten Zeilen hinweg vorrueckt.
    if events or last != cursor:
        batches.append((events, last))
    return batches, True, more


def main():
    if not TOKEN:
        log("FEHLER: agent_token fehlt in config.json")
        sys.exit(1)
    log("gestartet - Ziel: %s Port %s Hostname %s MinLevel %s"
        % (SERVER_FQDN or SERVER_IP, SERVER_PORT, HOSTNAME, MIN_LEVEL))

    cursor = read_cursor()
    failures = 0
    while True:
        if not cursor:
            cursor = latest_cursor()
            if cursor:
                write_cursor(cursor)
            else:
                time.sleep(POLL)
                continue

        batches, ok, more = collect_since(cursor)
        if not ok:
            # journalctl-Fehler (evtl. ungültiger/rotierter Cursor) -> neu setzen
            cursor = None
            time.sleep(POLL)
            continue

        wait = 0
        for events, upto in batches:
            result, wait = send(events)
            if result == "retry":
                break
            # "ok" oder "drop": dieses Paket ist erledigt -> Position merken.
            cursor = upto
            write_cursor(cursor)
            failures = 0
        else:
            if more:
                # Rueckstand gedrosselt abarbeiten, nicht im Dauerfeuer.
                time.sleep(CATCHUP_PAUSE)
                continue
            time.sleep(POLL)
            continue

        # Senden gescheitert: nur ab dem gescheiterten Paket neu versuchen,
        # mit wachsender Pause (5 s, 10 s, 20 s ... bis 5 min), damit ein
        # angeschlagener Server nicht zusaetzlich unter Beschuss geraet.
        failures += 1
        backoff = min(POLL * (2 ** min(failures - 1, 10)), MAX_BACKOFF)
        time.sleep(max(wait, backoff))


if __name__ == "__main__":
    main()
PYEOF
    # Gehoert root: das Dienstkonto darf sein Programm lesen, nicht aendern.
    chown root:root "$AGENT_SCRIPT"
    chmod 0644 "$AGENT_SCRIPT"
}

write_agent_config() {
    local skip="false"
    [[ "$INSECURE" == "true" ]] && skip="true"
    # Das Dienstkonto schreibt selbst (0600) - das Token darf nur es lesen.
    write_as_account "$AGENT_CONFIG" << EOF
{
  "mode": "https",
  "server_fqdn": "${FQDN}",
  "server_ip": "${IPFB}",
  "server_port": ${PORT},
  "agent_token": "${TOKEN}",
  "skip_tls_verify": ${skip},
  "min_level": "${MINLEVEL}",
  "hostname": "$(hostname)",
  "poll_interval": 5
}
EOF
}

write_systemd_unit() {
    local jgroup groups_line=""
    jgroup="$(journal_group)"
    if [[ -n "$jgroup" ]]; then
        groups_line="SupplementaryGroups=${jgroup}"
    else
        log_warn "Weder die Gruppe systemd-journal noch adm gefunden - das Konto sieht womöglich nicht alle Logs."
    fi
    cat > "$SYSTEMD_UNIT" << EOF
# RIMPERIO Log-Agent v${AGENT_VERSION} - angelegt von install-linux.sh.
# Laeuft als Dienstkonto "${AGENT_USER}", nicht als root: liest das Journal ueber
# die Gruppe ${jgroup:-systemd-journal}, schreibt nur nach ${BASE_DIR}/var.
[Unit]
Description=RIMPERIO Log-Agent v${AGENT_VERSION} (HTTPS)
After=network-online.target
Wants=network-online.target
RequiresMountsFor=${BASE_DIR}

[Service]
Type=simple
User=${AGENT_USER}
Group=${AGENT_GROUP}
${groups_line}
Environment=RIMPERIO_CONFIG=${AGENT_CONFIG}
Environment=RIMPERIO_CURSOR=${AGENT_CURSOR}
Environment=RIMPERIO_AGENT_ID=${AGENT_ID_FILE}
ExecStart=${PYTHON_BIN} ${AGENT_SCRIPT}
WorkingDirectory=${BASE_DIR}/var
Restart=always
RestartSec=10
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=${BASE_DIR}/var

[Install]
WantedBy=multi-user.target
EOF
    chmod 0644 "$SYSTEMD_UNIT"
    systemctl daemon-reload
}

configure_https() {
    ensure_https_deps
    log_info "Konfiguriere HTTPS-Agent (Python systemd-Dienst, eigenes Dienstkonto)..."

    # Update: ohne --fqdn/--token gelten die Werte der vorhandenen Installation.
    load_existing_settings

    # FQDN (Pflicht, DNS-basiert) - wird auch teilautomatisch immer noch abgefragt
    ask FQDN "RIMPERIO Server FQDN (DNS, z.B. rimperio.example.com)" "$FQDN"
    FQDN="${FQDN#http://}"; FQDN="${FQDN#https://}"; FQDN="${FQDN%%/*}"
    [[ -z "$FQDN" ]] && fail "FQDN fehlt. Per --fqdn / RIMPERIO_FQDN / Platzhalter setzen (HTTPS ist DNS-basiert)."

    [[ -z "$PORT" ]] && PORT="443"
    ask PORT "Server Port" "$PORT"
    ask IPFB "Optionale IP als Fallback (leer = keine)" "$IPFB"

    # Der hier eingegebene Schluessel ist nur die Eintrittskarte: er wird gleich
    # gegen einen eigenen getauscht (siehe enroll_own_token). Am besten eine
    # Einladung aus dem Web-UI unter "Zugangsschlüssel" - die laeuft ab und
    # gilt nur einmal.
    ask TOKEN "Zugangsschlüssel (Einladung oder Generalschlüssel, im Web-UI erstellt)" "$TOKEN"
    [[ -z "$TOKEN" ]] && fail "Zugangsschlüssel fehlt. Per --token / RIMPERIO_TOKEN / Platzhalter setzen."

    local ins="$INSECURE"
    ask ins "Selbstsignierte TLS-Zertifikate akzeptieren? (true/false)" "$INSECURE"
    case "$ins" in true|TRUE|j|J|y|Y|1) INSECURE="true" ;; *) INSECURE="false" ;; esac

    ask MINLEVEL "Log-Level (info/warning/error)" "$MINLEVEL"
    case "$MINLEVEL" in warning|error) : ;; *) MINLEVEL="info" ;; esac

    # Die Werte landen in Befehlen und in der Konfiguration - nur Gueltiges.
    valid_host "$FQDN" || fail "Ungültige Server-Adresse: \"$FQDN\""
    valid_port "$PORT" || fail "Ungültiger Port: \"$PORT\""
    [[ -z "$IPFB" ]] || valid_host "$IPFB" || fail "Ungültige Fallback-IP: \"$IPFB\""
    valid_token "$TOKEN" || fail "Der Zugangsschlüssel enthält unerwartete Zeichen."

    # Dienstkonto (nie root) - ohne Angabe wie der Fernwartungs-Agent, sonst "rimperio".
    choose_account

    # DNS/Erreichbarkeit informativ pruefen (kein harter Abbruch)
    if resolve_host "$FQDN"; then
        log_success "FQDN auflösbar: $FQDN"
    else
        log_warn "\"$FQDN\" derzeit nicht per DNS auflösbar - der Dienst versucht es zur Laufzeit erneut."
    fi
    check_tcp_reachable "$FQDN" "$PORT" && log_success "HTTPS ${FQDN}:${PORT} erreichbar" \
        || log_warn "HTTPS ${FQDN}:${PORT} derzeit nicht erreichbar - Dienst wird dennoch eingerichtet."

    # Eigenen Schluessel holen, bevor die Konfiguration geschrieben wird -
    # ausser er stammt schon aus der vorhandenen Installation.
    if [[ -n "$EXISTING_TOKEN" && "$TOKEN" == "$EXISTING_TOKEN" ]]; then
        REUSED_TOKEN="true"
        log_info "Der Geräteschlüssel der vorhandenen Installation bleibt."
    else
        enroll_own_token
    fi

    ensure_account
    prepare_dirs
    stop_legacy
    migrate_legacy_state
    write_agent_script
    write_agent_config
    write_systemd_unit
    remove_legacy
    log_success "HTTPS-Agent eingerichtet: $BASE_DIR (Dienstkonto \"$AGENT_USER\")"
}

# ==============================================================================
# Eigenen Geraeteschluessel holen
# ==============================================================================
# Frueher lag auf jedem Rechner derselbe Schluessel. Wer einen davon aufmachte,
# hatte den Schluessel fuer alle Geraete - und konnte im Namen jedes beliebigen
# Rechners Logzeilen erfinden oder Geraete samt Logs loeschen.
#
# Jetzt tauscht der Agent den mitgegebenen Schluessel beim Installieren gegen
# einen EIGENEN, der nur fuer diesen Rechner gilt. Der mitgegebene darf eine
# kurzlebige Einladung sein (empfohlen) - dann liegt der Generalschluessel
# nirgends mehr herum.
#
# Wichtig: Faellt das Anmelden aus - weil der Server noch eine aeltere Fassung
# faehrt und den Endpunkt nicht kennt -, laeuft die Installation mit dem
# mitgegebenen Schluessel weiter. Ein Agent, der sich wegen einer Neuerung
# gar nicht erst installieren laesst, waere die schlechtere Loesung.
# Umgesetzt mit python3 statt curl: python3 ist fuer den Agenten ohnehin
# Voraussetzung (ensure_https_deps stellt es sicher), curl ist auf manchen
# Minimal-Installationen nicht dabei. Ein Abhaengigkeitsproblem weniger.
enroll_own_token() {
    local host; host="$(hostname)"
    log_info "Melde diesen Rechner am Server an (eigener Schlüssel)..."

    local result
    result="$("$PYTHON_BIN" - "$FQDN" "$PORT" "$TOKEN" "$host" "$INSECURE" <<'PYENROLL'
import json
import ssl
import sys
import urllib.error
import urllib.request

fqdn, port, token, hostname, insecure = sys.argv[1:6]

context = None
if insecure == "true":
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

payload = json.dumps({"hostname": hostname, "device_type": "linux_agent"}).encode()
request = urllib.request.Request(
    f"https://{fqdn}:{port}/api/agents/enroll",
    data=payload,
    method="POST",
    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
)

try:
    with urllib.request.urlopen(request, timeout=20, context=context) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    own = (data.get("token") or "").strip()
    print("OK " + own if own else "EMPTY")
except urllib.error.HTTPError as exc:
    print(f"HTTP {exc.code}")
except Exception as exc:                                    # Netz, TLS, DNS
    print(f"UNREACHABLE {exc}")
PYENROLL
)" || result="UNREACHABLE"

    case "$result" in
        OK\ *)
            TOKEN="${result#OK }"
            log_success "Eigener Geräteschlüssel erhalten - der mitgegebene wird nicht gespeichert."
            ;;
        "HTTP 404")
            log_warn "Der Server kennt die Anmeldung noch nicht (ältere Fassung). Nehme den mitgegebenen Schlüssel."
            ;;
        "HTTP 401"|"HTTP 403")
            fail "Der Server hat den Schlüssel abgelehnt. Stimmt er noch, oder ist die Einladung abgelaufen?"
            ;;
        UNREACHABLE*)
            log_warn "Server nicht erreichbar - es wird der mitgegebene Schlüssel eingetragen."
            ;;
        *)
            log_warn "Anmeldung nicht möglich (${result}). Nehme den mitgegebenen Schlüssel."
            ;;
    esac
}

start_https_service() {
    log_info "Starte RIMPERIO Log-Agent-Dienst..."
    if command -v restorecon >/dev/null 2>&1; then
        restorecon -R "$BASE_DIR" "$SYSTEMD_UNIT" 2>/dev/null || true   # SELinux
    fi
    systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true
    systemctl restart "$SERVICE_NAME"
    sleep 2
    systemctl is-active --quiet "$SERVICE_NAME" \
        || fail "Dienst konnte nicht gestartet werden. Prüfe: journalctl -u ${SERVICE_NAME} -n 50"
    local pid uid
    pid="$(systemctl show -p MainPID "$SERVICE_NAME" 2>/dev/null | cut -d= -f2)"
    uid="$(awk '/^Uid:/{print $2}' "/proc/${pid:-0}/status" 2>/dev/null || true)"
    if [[ "$uid" == "0" ]]; then
        systemctl stop "$SERVICE_NAME" || true
        fail "Der Dienst lief als root - das darf nicht sein. Bitte $SYSTEMD_UNIT prüfen."
    fi
    log_success "Dienst läuft: $SERVICE_NAME (als \"$AGENT_USER\", nicht als root)"
}

# ==============================================================================
# Test
# ==============================================================================

send_test_message() {
    local cfg
    cfg="$(existing_config)"
    if [[ -n "$cfg" ]] && command -v python3 >/dev/null 2>&1; then
        log_info "Sende HTTPS-Test-Nachrichten..."
        if python3 - "$cfg" "$(hostname)" "$AGENT_VERSION" <<'PYEOF'
import json, ssl, socket, sys, urllib.request, urllib.error
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
hostname, version = sys.argv[2], sys.argv[3]
fqdn = (cfg.get("server_fqdn") or "").strip()
ip = (cfg.get("server_ip") or "").strip()
port = int(cfg.get("server_port") or 443)
token = (cfg.get("agent_token") or "").strip()
skip = bool(cfg.get("skip_tls_verify"))

def resolvable(h):
    try:
        socket.getaddrinfo(h, None); return True
    except Exception:
        return False

host = fqdn if (fqdn and resolvable(fqdn)) else (ip or fqdn)
url = "https://%s/api/agents/ingest" % host if port == 443 else "https://%s:%d/api/agents/ingest" % (host, port)
ctx = ssl.create_default_context()
if skip:
    ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
def local_ip():
    try:
        family, _, _, _, addr = socket.getaddrinfo(host, port, 0, socket.SOCK_DGRAM)[0]
        s = socket.socket(family, socket.SOCK_DGRAM)
        try:
            s.connect(addr); return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return ""

data = {"hostname": hostname, "device_type": "linux_agent", "events": [
    {"level": "info", "source": "rimperio-test", "message": "RIMPERIO Agent v%s - Installation erfolgreich" % version},
    {"level": "warning", "source": "rimperio-test", "message": "RIMPERIO Agent v%s - Test Warning" % version},
    {"level": "error", "source": "rimperio-test", "message": "RIMPERIO Agent v%s - Test Error" % version},
]}
ip_addr = (cfg.get("ip_address") or "").strip() or local_ip()
if ip_addr:
    data["ip_address"] = ip_addr
body = json.dumps(data).encode("utf-8")
req = urllib.request.Request(url, data=body, method="POST", headers={
    "Authorization": "Bearer " + token, "Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        print("accepted=%s" % r.read().decode("utf-8", "replace"))
    sys.exit(0)
except urllib.error.HTTPError as e:
    print("HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:200])); sys.exit(1)
except Exception as e:
    print("Fehler: %s" % e); sys.exit(1)
PYEOF
        then
            log_success "HTTPS-Test-Nachrichten gesendet"
        else
            log_warn "HTTPS-Test fehlgeschlagen - Server/Token/TLS prüfen."
        fi
        log_info "Prüfe im RIMPERIO Web-Interface ob die Nachrichten angekommen sind"
        return
    fi

    log_info "Sende Test-Nachricht (Syslog)..."
    logger -p user.info -t "rimperio-test" "RIMPERIO Agent v${AGENT_VERSION} - Installation erfolgreich auf $(hostname)"
    logger -p user.warning -t "rimperio-test" "RIMPERIO Agent v${AGENT_VERSION} - Test Warning"
    logger -p user.err -t "rimperio-test" "RIMPERIO Agent v${AGENT_VERSION} - Test Error"
    log_success "Test-Nachrichten gesendet"
    log_info "Prüfe im RIMPERIO Web-Interface ob die Nachrichten angekommen sind"
}

# ==============================================================================
# Vorhandene Installation
# ==============================================================================

handle_existing_installation() {
    is_installed || return 0
    log_warn "Es scheint bereits eine RIMPERIO-Installation vorhanden zu sein."
    syslog_installed && echo "  - Syslog-Konfiguration: $CONFIG_FILE"
    [[ -f "$SYSTEMD_UNIT" ]] && echo "  - HTTPS-Dienst: $SERVICE_NAME ($BASE_DIR, Konto $(unit_user "$SYSTEMD_UNIT"))"
    legacy_installed && echo "  - Ältere Fassung (läuft als root): $LEGACY_DIR - wird übernommen"
    local choice="1"
    ask choice "1) Neu konfigurieren  2) Deinstallieren  3) Abbrechen" "1"
    case "$choice" in
        2) uninstall ""; exit 0 ;;
        3) log_info "Abgebrochen."; exit 0 ;;
        *) log_info "Neuinstallation - vorhandene Konfiguration wird ersetzt." ;;
    esac
}

# ==============================================================================
# Installations-Ablauf
# ==============================================================================

do_install() {
    require_root
    interactive_gate
    handle_existing_installation

    # Modus (Standard: https)
    ask MODE "Modus (https/syslog)" "$MODE"
    case "$MODE" in syslog|SYSLOG|2) MODE="syslog" ;; *) MODE="https" ;; esac

    if [[ "$MODE" == "https" ]]; then
        configure_https
        start_https_service
        send_test_message
        print_summary "https"
    else
        configure_syslog
        restart_rsyslog
        send_test_message
        print_summary "syslog"
    fi
}

# ==============================================================================
# Deinstallation
# ==============================================================================

uninstall() {
    local mode="${1:-}"
    local do_purge_server=false
    interactive_gate
    log_info "Deinstalliere RIMPERIO Agent..."

    if [[ "$mode" == "purge" ]]; then
        do_purge_server=true
    elif [[ -z "$mode" ]]; then
        local rm_mode="1"
        ask rm_mode "Was entfernen? 1) Nur lokal  2) Vollständig (inkl. Server-Eintrag + Logs)" "1"
        [[ "$rm_mode" == "2" ]] && do_purge_server=true
    fi

    # Server-Purge zuerst, solange Token/Config noch vorhanden sind
    [[ "$do_purge_server" == true ]] && purge_server_entry

    # HTTPS-Dienst + Dateien (inkl. Leseposition) entfernen. Das Dienstkonto
    # merken, bevor die Unit weg ist. Den Fernwartungs-Agent (Dienst
    # rimperio-agent) nie anfassen - nur den aelteren Log-Agent gleichen Namens.
    local acct=""
    acct="$(unit_user "$SYSTEMD_UNIT")"
    if [[ -z "$acct" && -d "${BASE_DIR}/var" ]]; then acct="$(stat -c %U "${BASE_DIR}/var" 2>/dev/null || true)"; fi
    if [[ -f "$SYSTEMD_UNIT" ]] || systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}.service"; then
        stop_and_remove_unit "$SERVICE_NAME" "$SYSTEMD_UNIT"
        log_info "HTTPS-Dienst entfernt: $SERVICE_NAME"
    fi
    rm -f "$AGENT_SCRIPT" "$AGENT_CONFIG" "$AGENT_CURSOR" "${AGENT_CURSOR}.tmp" "$AGENT_ID_FILE" "${AGENT_ID_FILE}.tmp"
    remove_legacy
    cleanup_shared "$acct"

    # Syslog-Konfiguration entfernen
    if [[ -f "$CONFIG_FILE" ]]; then
        rm -f "$CONFIG_FILE"
        log_info "rsyslog-Konfiguration entfernt: $CONFIG_FILE"
    fi
    rm -f "${CONFIG_FILE}.backup."* 2>/dev/null || true
    rm -f "${QUEUE_PREFIX}"* 2>/dev/null || true
    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet rsyslog 2>/dev/null; then
        timeout 30 systemctl restart rsyslog || log_warn "rsyslog liess sich nicht neu starten - bitte selbst nachsehen."
    fi

    log_success "RIMPERIO Agent wurde entfernt (lokal)."
}

# Meldet den Agent auf dem Server ab und laesst dort Eintrag und Logs loeschen.
#
# Zwei Dinge, die hier frueher schiefgingen:
#   1. Drei blockierende Rueckfragen. Ohne Terminal blieb die Deinstallation
#      hier stehen - die Werte stehen aber ohnehin in der Konfiguration.
#   2. `curl` ohne Zeitlimit. War der Server nicht erreichbar (abgebaut, Firewall,
#      falscher FQDN), haing der Aufruf minutenlang. Jetzt: hoechstens 20 s.
#
# Neu ist ausserdem die Zuordnung ueber die gespeicherte Geraetenummer: der
# Server loescht damit genau den Eintrag dieses Rechners. Fehlt sie, greift der
# Hostname - dann aber gleich fuer alle Eintraege dieses Namens, damit keine
# Karteileichen aus frueheren IP-Wechseln liegen bleiben.
purge_server_entry() {
    detect_server_from_config
    local api_host="${FQDN:-$SERVER_HOST_DETECTED}"
    local api_port="${PORT:-${SERVER_PORT_DETECTED:-443}}"
    local api_token="${TOKEN:-$TOKEN_DETECTED}"

    # Nur nachfragen, was tatsaechlich fehlt - und auch das nur mit Zeitlimit.
    [[ -z "$api_host" ]]  && ask api_host  "Server Host/FQDN"      "$api_host"
    [[ -z "$api_port" ]]  && ask api_port  "Server Port"           "443"
    [[ -z "$api_token" ]] && ask api_token "Agent Token (Bearer)"  "$api_token"

    if [[ -z "$api_token" || -z "$api_host" ]]; then
        log_warn "Kein Token/Host bekannt - der Server-Eintrag bleibt stehen."
        log_info "Nachholen im Web-UI unter 'Geräte': dort das Gerät löschen (mit Logs)."
        return 0
    fi

    if ! command -v curl >/dev/null 2>&1; then
        log_warn "curl nicht gefunden - der Server-Eintrag bleibt stehen."
        return 0
    fi

    local host_ip mac_addr agent_id
    host_ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    mac_addr=$(ip link show 2>/dev/null | awk '$1=="link/ether"{print $2; exit}')
    [[ -z "$mac_addr" ]] && mac_addr=$(cat /sys/class/net/*/address 2>/dev/null | grep -v '^00:00:00:00:00:00$' | head -1)
    # Die Datei gehoert dem Dienstkonto: keinem Symlink folgen, nur Ziffern nehmen.
    agent_id=""
    if [[ -f "$AGENT_ID_FILE" && ! -L "$AGENT_ID_FILE" ]]; then
        agent_id="$(head -c 32 "$AGENT_ID_FILE" 2>/dev/null || true)"
    elif [[ -f "${LEGACY_DIR}/agent_id" ]]; then
        agent_id="$(head -c 32 "${LEGACY_DIR}/agent_id" 2>/dev/null || true)"
    fi
    agent_id="$(printf '%s' "$agent_id" | tr -dc '0-9')"

    local url_base
    [[ "$api_port" == "443" ]] && url_base="https://${api_host}" || url_base="https://${api_host}:${api_port}"

    # agent_id nur mitschicken, wenn es eine gibt - sonst waere es JSON-null.
    local id_field=""
    [[ -n "$agent_id" ]] && id_field="\"agent_id\":${agent_id},"

    local payload
    payload="{${id_field}\"hostname\":\"$(hostname)\",\"ip_address\":\"${host_ip:-}\",\"mac_address\":\"${mac_addr:-}\",\"purge\":true,\"all_for_hostname\":true}"

    local curl_opts=(-sS --connect-timeout 8 --max-time 20)
    [[ "$INSECURE" == "true" ]] && curl_opts+=(-k)

    log_info "Melde den Agent auf dem Server ab (${url_base})..."

    local response http_code
    response="$(curl "${curl_opts[@]}" -w '\n%{http_code}' -X POST \
        "${url_base}/api/agents/decommission" \
        -H "Authorization: Bearer $api_token" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>&1)" || true

    http_code="$(printf '%s' "$response" | tail -n1)"
    local body
    body="$(printf '%s' "$response" | sed '$d')"

    case "$http_code" in
        2*)
            log_success "Auf dem Server abgeräumt: ${body}"
            ;;
        404)
            log_info "Auf dem Server gab es dazu keinen Eintrag (mehr) - nichts zu tun."
            ;;
        401|403)
            log_warn "Der Server hat den Token abgelehnt - der Eintrag bleibt stehen."
            log_info "Im Web-UI unter 'Geräte' von Hand löschen."
            ;;
        *)
            log_warn "Der Server war nicht erreichbar (${http_code:-keine Antwort}) - der Eintrag bleibt stehen."
            log_info "Im Web-UI unter 'Geräte' von Hand löschen."
            ;;
    esac
    return 0
}

# ==============================================================================
# Zusammenfassung
# ==============================================================================

print_summary() {
    local mode="${1:-https}"
    echo ""
    echo "=============================================="
    echo -e "${GREEN}RIMPERIO Agent v${AGENT_VERSION} installiert!${NC}"
    echo "=============================================="
    if [[ "$mode" == "https" ]]; then
        local url_host="$FQDN"
        [[ "${PORT:-443}" != "443" ]] && url_host="${FQDN}:${PORT}"
        echo "Modus:         HTTPS (verschlüsselt + Token)"
        echo "Ziel:          https://${url_host}/api/agents/ingest"
        echo "Installation:  $BASE_DIR (Programm lib/, Konfiguration etc/, Zustand var/)"
        echo "Dienst:        $SERVICE_NAME - läuft als \"$AGENT_USER\", nicht als root"
        echo ""
        echo "  systemctl status ${SERVICE_NAME}     # Status"
        echo "  journalctl -u ${SERVICE_NAME} -f     # Dienst-Logs"
    else
        echo "Modus:         Syslog (rsyslog)"
        echo "Konfiguration: $CONFIG_FILE"
        echo ""
        echo "  systemctl status rsyslog     # Status"
    fi
    echo ""
    echo "Deinstallation:  sudo bash $0 uninstall"
    echo ""
}

# ==============================================================================
# Hauptprogramm
# ==============================================================================

main() {
    parse_args "$@"

    echo ""
    echo "=============================================="
    echo "  RIMPERIO Agent v${AGENT_VERSION} - Linux"
    echo "  Modus: ${MODE}  |  Auto-Start in ${PROMPT_TIMEOUT}s (Taste = manuell)"
    echo "=============================================="

    case "$ACTION" in
        uninstall)        require_root; uninstall "" ;;
        uninstall-purge)  require_root; uninstall "purge" ;;
        test)             send_test_message ;;
        *)                do_install ;;
    esac
}

main "$@"
