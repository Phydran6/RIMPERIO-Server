#!/bin/bash
# ==============================================================================
#  _                 ____        _
# | |    ___   __ _ | __ )  ___ | |_
# | |   / _ \ / _` ||  _ \ / _ \| __|
# | |__| (_) | (_| || |_) | (_) | |_
# |_____\___/ \__, ||____/ \___/ \__|
#             |___/
# ==============================================================================
# SETUP-ASSISTENT
# ==============================================================================
#
# Autor:        Phydran6
# Kontakt:      Phydran6
# Version:      2026.09.15.20.00.00
# Changelog:    docs/changelog/database.md
#
# Beschreibung:
#   Ein Einstieg fuer alles, was sich auf einem Linux-Rechner rund um RIMPERIO
#   tun laesst: Server installieren, aktualisieren, entfernen - Linux-Agent
#   einrichten, testen, entfernen - Systempruefung.
#
#   Der Assistent fragt jede Option ab, zeigt vor dem Start den passenden
#   direkten Einzeiler und ruft dann install.sh bzw. agent/logship/install-linux.sh
#   auf. Er macht selbst nichts, was diese Skripte nicht auch koennen - wer
#   schon weiss, was er will, nimmt gleich den Einzeiler.
#
# Verwendung:
#   curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/server/setup.sh | sudo bash
#   sudo bash setup.sh                      # aus dem geklonten Repository
#   sudo bash setup.sh --branch <name>      # Skripte aus einem anderen Branch
#
# ==============================================================================

set -o pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

REPO_SLUG="${RIMPERIO_REPO_SLUG:-Phydran6/RIMPERIO-Server}"
BRANCH="${RIMPERIO_BRANCH:-main}"
SERVER_DIR_DEFAULT="/opt/rimperio"
AGENT_DIR="/opt/rimperio-agent"
AGENT_SYSLOG_CONF="/etc/rsyslog.d/99-rimperio.conf"
# Watchtower ist raus - es hat Container eigenmaechtig ausgetauscht.
# Tugtainer prueft und meldet, eingespielt wird auf Ansage.
ALL_ADDONS="portainer tugtainer n8n openwebui postfix"

TMP_DIR=""

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }
fail()        { log_error "$1"; exit 1; }

cleanup() {
    [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]] && rm -rf "$TMP_DIR"
    return 0
}
trap cleanup EXIT

have_tty() { true 2>/dev/null </dev/tty; }

raw_url() { echo "https://raw.githubusercontent.com/${REPO_SLUG}/${BRANCH}/$1"; }

# ==============================================================================
# Eingabe
# ==============================================================================

# ask VAR "Frage" "Default"  - Enter nimmt den Default
ask() {
    local __name="$1" prompt="$2" def="${3:-}" ans=""
    if [[ -n "$def" ]]; then
        read -r -p "  $prompt [$def]: " ans </dev/tty || true
    else
        read -r -p "  $prompt: " ans </dev/tty || true
    fi
    [[ -z "$ans" ]] && ans="$def"
    printf -v "$__name" '%s' "$ans"
}

# ask_required VAR "Frage" "Default" - fragt so lange, bis etwas drinsteht
ask_required() {
    local __name="$1" prompt="$2" def="${3:-}" val=""
    while true; do
        ask val "$prompt" "$def"
        [[ -n "$val" ]] && break
        log_warn "Das wird gebraucht."
    done
    printf -v "$__name" '%s' "$val"
}

# ask_secret VAR "Frage" - verdeckte Eingabe, Pflicht
ask_secret() {
    local __name="$1" prompt="$2" val=""
    while true; do
        read -r -s -p "  $prompt (Eingabe unsichtbar): " val </dev/tty || true
        echo ""
        [[ -n "$val" ]] && break
        log_warn "Das wird gebraucht."
    done
    printf -v "$__name" '%s' "$val"
}

# yes_no "Frage" "j|n" - Rueckgabe 0 = ja
yes_no() {
    local prompt="$1" def="${2:-n}" ans=""
    local hint="j/N"; [[ "$def" == "j" ]] && hint="J/n"
    read -r -p "  $prompt [$hint]: " ans </dev/tty || true
    [[ -z "$ans" ]] && ans="$def"
    [[ "$ans" =~ ^[jJyY] ]]
}

# choose VAR "Frage" "Default" "1|2|3" - nur erlaubte Antworten
choose() {
    local __name="$1" prompt="$2" def="$3" allowed="$4" val=""
    while true; do
        ask val "$prompt" "$def"
        [[ "|$allowed|" == *"|$val|"* ]] && break
        log_warn "Bitte eine der Möglichkeiten wählen: ${allowed//|/, }"
    done
    printf -v "$__name" '%s' "$val"
}

heading() {
    echo ""
    echo -e "${BOLD}$1${NC}"
    echo -e "${DIM}$(printf '%.0s-' $(seq 1 ${#1}))${NC}"
}

# ==============================================================================
# Zustand erkennen
# ==============================================================================

server_installed() { [[ -f "${1:-$SERVER_DIR_DEFAULT}/docker-compose.yml" ]]; }
agent_installed()  { [[ -d "$AGENT_DIR" || -f "$AGENT_SYSLOG_CONF" ]]; }

show_status() {
    local s="nicht installiert" a="nicht installiert"
    if server_installed; then
        s="installiert in $SERVER_DIR_DEFAULT"
        [[ -f "$SERVER_DIR_DEFAULT/VERSION" ]] && s="$s (Version $(tr -d '[:space:]' < "$SERVER_DIR_DEFAULT/VERSION"))"
    fi
    if [[ -d "$AGENT_DIR" ]]; then
        a="installiert (HTTPS, $AGENT_DIR)"
    elif [[ -f "$AGENT_SYSLOG_CONF" ]]; then
        a="installiert (Syslog, $AGENT_SYSLOG_CONF)"
    fi
    echo -e "  Auf diesem Rechner:  RIMPERIO-Server ${BOLD}${s}${NC}"
    echo -e "                       Linux-Agent   ${BOLD}${a}${NC}"
}

# ==============================================================================
# Skripte holen: aus dem Repository daneben oder frisch von GitHub
# ==============================================================================

# fetch_script "install.sh" -> gibt den lokalen Pfad aus
fetch_script() {
    local rel="$1" here=""
    # setup.sh liegt in server/ -> Pfade gelten ab der Repo-Wurzel eine Ebene höher.
    here="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")/.." 2>/dev/null && pwd)"
    if [[ -n "$here" && -f "$here/$rel" && -f "$here/docker-compose.yml" ]]; then
        echo "$here/$rel"
        return 0
    fi
    [[ -n "$TMP_DIR" ]] || TMP_DIR="$(mktemp -d /tmp/rimperio-setup.XXXXXX)"
    local target="$TMP_DIR/$(basename "$rel")"
    if ! curl -fsSL --connect-timeout 10 "$(raw_url "$rel")" -o "$target"; then
        log_error "Konnte $(raw_url "$rel") nicht laden - Netz oder Branch prüfen."
        return 1
    fi
    echo "$target"
}

# ==============================================================================
# Ausfuehren: erst zeigen, dann auf Wunsch starten
# ==============================================================================

# run_step "skript-pfad-im-repo" "Anzeige-Argumente" arg1 arg2 ...
#   Die Anzeige-Argumente sind die Argumente fuer den Einzeiler; Geheimnisse
#   stehen dort als Platzhalter. Uebergeben werden die echten Argumente.
run_step() {
    local rel="$1" shown="$2"; shift 2

    heading "Das wird ausgeführt"
    echo ""
    echo "  Als direkter Einzeiler - zum Merken oder für den nächsten Rechner:"
    echo ""
    echo -e "  ${GREEN}curl -sSL $(raw_url "$rel") \\"
    echo -e "    | sudo bash -s -- ${shown}${NC}"
    echo ""
    if ! yes_no "Jetzt ausführen?" "j"; then
        log_info "Nichts geändert."
        exit 0
    fi

    local script
    script="$(fetch_script "$rel")" || exit 1
    echo ""
    # stdin ist bei 'curl | bash' die Pipe mit diesem Skript. Das Unterskript
    # soll daraus nichts lesen - seine Rueckfragen holt es sich von /dev/tty.
    bash "$script" "$@" </dev/null
    local rc=$?
    echo ""
    if [[ $rc -eq 0 ]]; then
        log_success "Fertig."
    else
        log_error "Beendet mit Fehlercode $rc - die Meldungen oben sagen, woran es lag."
    fi
    exit $rc
}

# ==============================================================================
# Abfragen: Server
# ==============================================================================

ask_server_dir() {
    ask SERVER_DIR "Installationsverzeichnis" "$SERVER_DIR_DEFAULT"
    SERVER_DIR="${SERVER_DIR%/}"
}

# Setzt ADDON_LIST (komma-getrennt, leer = keine)
ask_addons() {
    echo ""
    echo "  Zusatzdienste (keiner läuft, ohne ausgewählt zu sein):"
    echo "    1) Portainer    Container-Oberfläche im Browser"
    echo "    2) Tugtainer    prüft, ob für die fremden Images Updates bereitliegen"
    echo "    3) n8n          Automatisierung, u.a. für die KI-Auswertung"
    echo "    4) Open WebUI   KI-Oberfläche, auch für lokale Modelle (Ollama)"
    echo "    5) Postfix      Mailversand (Einstellungen später im Web-UI)"
    echo "  Mehrere gehen: z.B. '1 3'. Leer = keine."
    local answer="" picked=()
    ask answer "Auswahl" ""
    [[ "$answer" == *1* ]] && picked+=("portainer")
    [[ "$answer" == *2* ]] && picked+=("tugtainer")
    [[ "$answer" == *3* ]] && picked+=("n8n")
    [[ "$answer" == *4* ]] && picked+=("openwebui")
    [[ "$answer" == *5* ]] && picked+=("postfix")
    ADDON_LIST="$(IFS=,; echo "${picked[*]}")"
}

# Setzt REF (leer = neuester Stand des Branches)
ask_ref() {
    echo ""
    echo "  Welcher Stand?"
    echo "    1) Der neueste (Branch $BRANCH)"
    echo "    2) Ein bestimmtes Release, Tag oder Commit"
    local c=""
    choose c "Auswahl" "1" "1|2"
    REF=""
    [[ "$c" == "2" ]] && ask_required REF "Release, Tag oder Commit (z.B. v2026.09.10)" ""
}

server_install() {
    heading "RIMPERIO-Server installieren"
    ask_server_dir
    if server_installed "$SERVER_DIR"; then
        log_warn "In $SERVER_DIR ist RIMPERIO schon installiert."
        if yes_no "Stattdessen aktualisieren?" "j"; then
            server_update "$SERVER_DIR"
        fi
        log_info "Für eine Neuinstallation vorher 'Deinstallieren' oder 'Komplett entfernen' wählen."
        exit 0
    fi
    ask_addons
    ask_ref

    local skip=""
    echo ""
    if ! yes_no "Systemprüfung vorher laufen lassen? (empfohlen)" "j"; then
        skip="--skip-preflight"
    fi

    local args=(install --dir "$SERVER_DIR")
    [[ -n "$ADDON_LIST" ]] && args+=(--with "$ADDON_LIST") || args+=(--no-addons)
    [[ -n "$REF" ]] && args+=(--ref "$REF")
    [[ "$BRANCH" != "main" ]] && args+=(--branch "$BRANCH")
    [[ -n "$skip" ]] && args+=("$skip")
    args+=(--yes)

    run_step "install.sh" "${args[*]}" "${args[@]}"
}

server_update() {
    heading "RIMPERIO-Server aktualisieren"
    if [[ -n "${1:-}" ]]; then
        SERVER_DIR="$1"
    else
        ask_server_dir
    fi
    server_installed "$SERVER_DIR" || fail "In $SERVER_DIR ist kein RIMPERIO installiert."
    ask_ref

    local args=(update --dir "$SERVER_DIR")
    echo ""
    if yes_no "Zusatzdienste neu festlegen? (Nein = bleiben, wie sie sind)" "n"; then
        ask_addons
        [[ -n "$ADDON_LIST" ]] && args+=(--with "$ADDON_LIST") || args+=(--no-addons)
    fi
    [[ -n "$REF" ]] && args+=(--ref "$REF")
    [[ "$BRANCH" != "main" ]] && args+=(--branch "$BRANCH")
    if ! yes_no "Images neu bauen? (Nein = nur neu starten)" "j"; then
        args+=(--no-build)
    fi
    args+=(--yes)

    run_step "install.sh" "${args[*]}" "${args[@]}"
}

server_uninstall() {
    heading "RIMPERIO-Server deinstallieren - Daten bleiben"
    ask_server_dir
    server_installed "$SERVER_DIR" || fail "In $SERVER_DIR ist kein RIMPERIO installiert."
    echo ""
    echo "  Entfernt werden:  alle RIMPERIO-Container (auch die Zusatzdienste)"
    echo "  Es bleiben:       Logs und Einstellungen (Docker-Volumes), Sicherungen,"
    echo "                    $SERVER_DIR samt .env, die gebauten Images"
    echo ""
    echo "  Wieder starten:   cd $SERVER_DIR && sudo docker compose up -d"

    local args=(uninstall --dir "$SERVER_DIR" --yes)
    run_step "install.sh" "${args[*]}" "${args[@]}"
}

server_purge() {
    heading "RIMPERIO-Server komplett entfernen - inkl. aller Logs"
    ask_server_dir
    server_installed "$SERVER_DIR" || fail "In $SERVER_DIR ist kein RIMPERIO installiert."
    echo ""
    echo -e "  ${RED}${BOLD}Unwiderruflich gelöscht werden:${NC}"
    echo "    - alle Container, auch die Zusatzdienste"
    echo "    - alle Docker-Volumes: sämtliche Logs, Einstellungen, Benutzer,"
    echo -e "      TLS-Zertifikate, Daten von n8n/Portainer ${BOLD}und die Sicherungs-ZIPs${NC}"
    echo "    - $SERVER_DIR samt .env (Passwörter)"
    echo ""
    echo "  Bleiben liegen: Update-Sicherungen in ${SERVER_DIR}-backups/,"
    echo "  die gebauten Images und Docker selbst."
    echo ""
    echo -e "  ${YELLOW}Tipp:${NC} Vorher im Web-UI unter System -> Sicherung eine Sicherung"
    echo "  herunterladen, wenn die Daten noch gebraucht werden."
    echo ""
    local word=""
    ask word "Zum Bestätigen LÖSCHEN eintippen" ""
    if [[ "$word" != "LÖSCHEN" && "$word" != "LOESCHEN" ]]; then
        log_info "Abgebrochen - nichts gelöscht."
        exit 0
    fi

    local args=(uninstall-purge --dir "$SERVER_DIR" --yes)
    run_step "install.sh" "${args[*]}" "${args[@]}"
}

# ==============================================================================
# Abfragen: Linux-Agent
# ==============================================================================

# Bereinigt eine eingefuegte Adresse: https://host:8443/pfad -> host (Port separat)
clean_host() {
    local h="$1"
    h="${h#http://}"; h="${h#https://}"; h="${h%%/*}"
    echo "$h"
}

agent_install() {
    heading "Linux-Agent installieren"
    if agent_installed; then
        log_warn "Auf diesem Rechner ist schon ein Agent eingerichtet - er wird neu konfiguriert."
    fi
    echo ""
    echo "  Wie sollen die Logs zum Server?"
    echo "    1) HTTPS mit Token  - verschlüsselt, auch übers Internet (empfohlen)"
    echo "    2) Syslog           - unverschlüsselt, nur im eigenen Netz"
    local m=""
    choose m "Auswahl" "1" "1|2"

    local fqdn="" port="" args=() shown=() token=""
    if [[ "$m" == "1" ]]; then
        echo ""
        ask_required fqdn "Adresse des RIMPERIO-Servers (FQDN, z.B. rimperio.example.com)" ""
        fqdn="$(clean_host "$fqdn")"
        if [[ "$fqdn" == *:* ]]; then port="${fqdn##*:}"; fqdn="${fqdn%%:*}"; fi
        ask port "Port" "${port:-443}"
        ask_secret token "Zugangsschluessel (Web-UI: Zugang & Sicherheit -> Zugangsschluessel)"
        local ip=""
        ask ip "IP des Servers als Rückfallebene, falls DNS ausfällt (leer = keine)" ""
        echo ""
        echo "  Ab welchem Schweregrad senden?"
        echo "    1) info     - alles (Standard)"
        echo "    2) warning  - Warnungen und schlimmer"
        echo "    3) error    - nur Fehler"
        local lvl=""
        choose lvl "Auswahl" "1" "1|2|3"
        case "$lvl" in 2) lvl="warning" ;; 3) lvl="error" ;; *) lvl="info" ;; esac

        args=(install --mode https --fqdn "$fqdn" --port "$port" --min-level "$lvl")
        [[ -n "$ip" ]] && args+=(--ip "$ip")
        echo ""
        if yes_no "Selbstsigniertes Zertifikat auf dem Server? (dann wird es akzeptiert)" "n"; then
            args+=(--insecure)
        fi
        args+=(--yes)
        # Token nicht auf die Kommandozeile (ps, Verlauf) - per Umgebung weiterreichen.
        # args: install --mode https --fqdn <fqdn> | --port ... -> ab Index 5 anhaengen
        shown=(install --mode https --fqdn "$fqdn" --token DEIN-AGENT-TOKEN "${args[@]:5}")
        export RIMPERIO_TOKEN="$token"
    else
        echo ""
        ask_required fqdn "Adresse des RIMPERIO-Servers (FQDN oder IP)" ""
        fqdn="$(clean_host "$fqdn")"
        if [[ "$fqdn" == *:* ]]; then port="${fqdn##*:}"; fqdn="${fqdn%%:*}"; fi
        ask port "Port" "${port:-514}"
        echo ""
        echo "  Protokoll:"
        echo "    1) UDP  - Standard, ohne Zustellbestätigung"
        echo "    2) TCP  - zuverlässiger, geht auch durch einen Stream-Proxy"
        local p=""
        choose p "Auswahl" "1" "1|2"
        [[ "$p" == "2" ]] && p="tcp" || p="udp"
        args=(install --mode syslog --fqdn "$fqdn" --port "$port" --proto "$p" --yes)
        shown=("${args[@]}")
    fi

    run_step "agent/logship/install-linux.sh" "${shown[*]}" "${args[@]}"
}

agent_test() {
    heading "Linux-Agent: Testnachrichten senden"
    agent_installed || fail "Auf diesem Rechner ist kein Agent eingerichtet."
    local args=(test)
    run_step "agent/logship/install-linux.sh" "${args[*]}" "${args[@]}"
}

agent_uninstall() {
    heading "Linux-Agent deinstallieren - nur auf diesem Rechner"
    agent_installed || fail "Auf diesem Rechner ist kein Agent eingerichtet."
    echo ""
    echo "  Entfernt werden:  Dienst rimperio-agent, $AGENT_DIR, rsyslog-Weiterleitung"
    echo "  Es bleiben:       das Gerät und seine Logs auf dem RIMPERIO-Server"
    local args=(uninstall --yes)
    run_step "agent/logship/install-linux.sh" "${args[*]}" "${args[@]}"
}

agent_purge() {
    heading "Linux-Agent komplett entfernen - inkl. Gerät und Logs auf dem Server"
    agent_installed || fail "Auf diesem Rechner ist kein Agent eingerichtet."
    echo ""
    echo "  Entfernt werden:  alles auf diesem Rechner (wie 'Deinstallieren')"
    echo "                    und auf dem Server: das Gerät, alle weiteren Einträge"
    echo "                    mit diesem Hostnamen und deren Logs"
    echo ""

    local args=(uninstall-purge --yes) shown=(uninstall-purge --yes)
    if [[ ! -f "$AGENT_DIR/config.json" ]]; then
        # Syslog-Agent: kennt weder Server-Adresse fuer HTTPS noch Token.
        log_info "Für das Abmelden am Server werden Adresse und Token gebraucht."
        # Ohne --port naehme install-linux.sh den Syslog-Port (514) aus der
        # rsyslog-Konfiguration - das Abmelden laeuft aber ueber HTTPS.
        local fqdn="" port="" token=""
        ask_required fqdn "Adresse des RIMPERIO-Servers (FQDN)" ""
        fqdn="$(clean_host "$fqdn")"
        if [[ "$fqdn" == *:* ]]; then port="${fqdn##*:}"; fqdn="${fqdn%%:*}"; fi
        ask port "HTTPS-Port des Servers" "${port:-443}"
        ask_secret token "Zugangsschluessel"
        export RIMPERIO_TOKEN="$token"
        args=(uninstall-purge --fqdn "$fqdn" --port "$port" --yes)
        shown=(uninstall-purge --fqdn "$fqdn" --port "$port" --token DEIN-AGENT-TOKEN --yes)
    fi
    local word=""
    ask word "Zum Bestätigen LÖSCHEN eintippen" ""
    if [[ "$word" != "LÖSCHEN" && "$word" != "LOESCHEN" ]]; then
        log_info "Abgebrochen - nichts gelöscht."
        exit 0
    fi
    run_step "agent/logship/install-linux.sh" "${shown[*]}" "${args[@]}"
}

# ==============================================================================
# Systempruefung
# ==============================================================================

preflight_only() {
    heading "Systemprüfung - reicht dieser Rechner?"
    ask_addons
    local args=()
    [[ -n "$ADDON_LIST" ]] && args=(${ADDON_LIST//,/ })
    run_step "server/install/preflight.sh" "${args[*]}" "${args[@]}"
}

# ==============================================================================
# Hauptprogramm
# ==============================================================================

show_help() {
    cat <<EOF
RIMPERIO Setup-Assistent

Aufruf:   curl -sSL $(raw_url server/setup.sh) | sudo bash
          sudo bash setup.sh [--branch <name>]

Fragt alle Optionen ab, zeigt den passenden Einzeiler und führt ihn aus.
Ohne Terminal (cron, Automatisierung) gibt es nichts zu fragen - dann die
Einzeiler direkt benutzen, siehe README.
EOF
}

main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --branch)   BRANCH="${2:-main}"; shift ;;
            --branch=*) BRANCH="${1#*=}" ;;
            --help|-h)  show_help; exit 0 ;;
            *)          log_warn "Unbekannter Parameter: $1" ;;
        esac
        shift || true
    done

    have_tty || fail "Der Assistent braucht ein Terminal. Ohne Terminal die Einzeiler direkt nutzen (siehe README)."
    [[ $EUID -eq 0 ]] || fail "Bitte als root starten:  curl -sSL $(raw_url server/setup.sh) | sudo bash"
    command -v curl >/dev/null 2>&1 || fail "curl fehlt - bitte zuerst installieren (apt-get install -y curl)."

    echo ""
    echo "=============================================="
    echo "  RIMPERIO Setup-Assistent"
    echo "=============================================="
    echo ""
    show_status
    echo ""
    echo -e "  ${BOLD}RIMPERIO-Server${NC} ${DIM}- dieser Rechner sammelt die Logs${NC}"
    echo "    1) Installieren"
    echo "    2) Aktualisieren"
    echo "    3) Deinstallieren             Daten bleiben erhalten"
    echo "    4) Komplett entfernen         inkl. aller Logs und Sicherungen"
    echo ""
    echo -e "  ${BOLD}Linux-Agent${NC} ${DIM}- dieser Rechner schickt Logs an einen RIMPERIO-Server${NC}"
    echo "    5) Installieren / neu einrichten"
    echo "    6) Testnachrichten senden"
    echo "    7) Deinstallieren             Gerät bleibt auf dem Server"
    echo "    8) Komplett entfernen         inkl. Gerät und Logs auf dem Server"
    echo ""
    echo -e "  ${BOLD}Sonstiges${NC}"
    echo "    9) Systemprüfung              reicht dieser Rechner für den Server?"
    echo "    0) Beenden"
    echo ""
    echo -e "  ${DIM}Windows-Agent: siehe README (PowerShell-Einzeiler mit Menü).${NC}"
    echo ""

    local c=""
    choose c "Was soll passieren?" "" "0|1|2|3|4|5|6|7|8|9"
    case "$c" in
        1) server_install ;;
        2) server_update ;;
        3) server_uninstall ;;
        4) server_purge ;;
        5) agent_install ;;
        6) agent_test ;;
        7) agent_uninstall ;;
        8) agent_purge ;;
        9) preflight_only ;;
        0) log_info "Beendet."; exit 0 ;;
    esac
}

main "$@"
