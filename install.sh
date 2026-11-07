#!/bin/bash
# ==============================================================================
#  _                 ____        _
# | |    ___   __ _ | __ )  ___ | |_
# | |   / _ \ / _` ||  _ \ / _ \| __|
# | |__| (_) | (_| || |_) | (_) | |_
# |_____\___/ \__, ||____/ \___/ \__|
#             |___/
# ==============================================================================
# INSTALLATIONS-SCRIPT
# ==============================================================================
#
# Autor:        Phydran6
# Kontakt:      Phydran6
# Version:      2026.09.12.12.00.00
# Erstellt:     Januar 2026
#
# Beschreibung:
#   Installiert / aktualisiert / entfernt RIMPERIO via Docker Compose.
#   Funktioniert lokal aus dem Repo UND als One-Liner direkt von GitHub
#   (dann holt sich das Script die Quellen selbst per git clone).
#
# Voraussetzungen:
#   - Linux System (Debian/Ubuntu empfohlen)
#   - Root-Rechte
#   - Internetzugang (fuer Docker-/Repo-Installation)
#
# Verwendung:
#   sudo bash install.sh                # lokal aus dem Repo
#   curl -sSL <RAW-URL>/install.sh | sudo bash            # One-Liner
#   curl -sSL <RAW-URL>/install.sh | sudo bash -s -- -y   # ohne Rueckfragen
#   sudo bash install.sh update|uninstall|uninstall-purge
#   curl -sSL <RAW-URL>/install.sh | sudo bash -s -- update -y
#   curl -sSL <RAW-URL>/install.sh | sudo bash -s -- update -y --ref v2026.09.10
#
# ==============================================================================

set -e

# Farben für Output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ==============================================================================
# Konfiguration (Vorrang: Parameter > Umgebungsvariable > Default)
# ==============================================================================

RIMPERIO_VERSION="2026.10.18.22.00.00"

INSTALL_DIR="${RIMPERIO_DIR:-/opt/rimperio}"
REPO_URL="${RIMPERIO_REPO:-https://github.com/Phydran6/RIMPERIO-Server.git}"
REPO_BRANCH="${RIMPERIO_BRANCH:-main}"
# Welcher Stand geholt wird. Leer = der Kopf von REPO_BRANCH. Sonst ein Release,
# ein Tag oder ein Commit - damit laesst sich ein Server auf einer bestimmten
# Version halten (dieselbe Bedeutung wie --ref im Wartungsskript).
REF="${RIMPERIO_REF:-}"
PROMPT_TIMEOUT="${RIMPERIO_TIMEOUT:-5}"

ACTION="install"
ASSUME_YES="${RIMPERIO_YES:-false}"   # true => nie fragen
MANUAL="false"                      # true => Rueckfragen werden gestellt (kein Timeout)
GATE_DONE="false"
NO_BUILD="false"                    # true => Images nicht neu bauen
SKIP_PREFLIGHT="${RIMPERIO_SKIP_PREFLIGHT:-false}"

# Welche Zusatzdienste sollen mit? Komma-getrennt, z.B. "portainer,tugtainer".
# Leer = nur RIMPERIO. Im manuellen Modus wird gefragt.
ADDONS="${RIMPERIO_ADDONS:-}"
# HTTPS gleich bei der Installation (leer = erst mal HTTP, später im Web-UI)
DOMAIN="${RIMPERIO_DOMAIN:-}"
TLS_MODE="${RIMPERIO_TLS:-}"          # letsencrypt | internal
ACME_EMAIL="${RIMPERIO_ACME_EMAIL:-}"
HTTPS_ONLY="${RIMPERIO_HTTPS_ONLY:-false}"   # true = Port 80 leitet nur noch auf HTTPS weiter

# Bekannte Zusatzdienste (muessen zu server/deploy/optional.yml passen).
# Watchtower ist raus: es hat Container eigenmaechtig ausgetauscht. Tugtainer
# nimmt seinen Platz ein und macht nur eines - nachsehen und melden.
ALL_ADDONS="portainer tugtainer n8n openwebui postfix"

SRC_DIR=""        # Quelle der Dateien (lokales Repo oder frischer Clone)
TMP_CLONE=""      # temporaerer Clone, wird am Ende aufgeraeumt

# Wird von choose_addons/normalize_addons gefuellt.
SELECTED_ADDONS=()

# ==============================================================================
# Hilfsfunktionen
# ==============================================================================

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }
fail()        { log_error "$1"; exit 1; }

cleanup() {
    [[ -n "$TMP_CLONE" && -d "$TMP_CLONE" ]] && rm -rf "$TMP_CLONE"
    return 0
}
trap cleanup EXIT

# true nur, wenn /dev/tty wirklich geoeffnet werden kann (nicht nur -r).
# Sonst wuerde 'read </dev/tty' bei einer Pipe ohne Terminal eine Fehlermeldung werfen.
have_tty() { true 2>/dev/null </dev/tty; }

# Einmaliges "Gate" am Anfang: kurzer Countdown. Wird EINE Taste gedrueckt, schaltet
# der Installer auf MANUELL (alle folgenden Rueckfragen blockieren, kein Timeout).
# Keine Eingabe / kein Terminal / --yes => automatischer Ablauf mit Standardwerten.
interactive_gate() {
    [[ "$GATE_DONE" == "true" ]] && return 0
    GATE_DONE="true"
    [[ "$ASSUME_YES" == "true" ]] && return 0
    have_tty || return 0
    echo ""
    log_info "Automatischer Start in ${PROMPT_TIMEOUT}s. Für Rückfragen jetzt eine Taste drücken..."
    local _k=""
    if read -rsN1 -t "$PROMPT_TIMEOUT" _k </dev/tty; then
        MANUAL="true"
        # Restliche gepufferte Zeichen verwerfen, sonst nimmt die erste Abfrage sofort den Default.
        while read -rsN1 -t 0.05 _k </dev/tty 2>/dev/null; do :; done
        echo ""
        log_success "Manueller Modus aktiv - Rückfragen werden gestellt."
    else
        echo ""
        log_info "Keine Eingabe - automatischer Ablauf mit Standardwerten."
    fi
    return 0
}

# Ja/Nein-Rueckfrage. Im automatischen Modus gilt der uebergebene Default.
# confirm "Frage" "j|n"
confirm() {
    local prompt="$1" def="${2:-n}" ans=""
    if [[ "$MANUAL" == "true" ]]; then
        read -r -p "$prompt [$( [[ "$def" == "j" ]] && echo "J/n" || echo "j/N" )] " ans </dev/tty || true
        echo ""
    fi
    [[ -z "$ans" ]] && ans="$def"
    [[ "$ans" == "j" || "$ans" == "J" || "$ans" == "y" || "$ans" == "Y" ]]
}

generate_password() {
    if command -v openssl &> /dev/null; then
        openssl rand -base64 48 | tr -dc 'a-zA-Z0-9' | head -c 32
    else
        tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c 32
    fi
}

show_help() {
    cat <<EOF
RIMPERIO Installer v${RIMPERIO_VERSION}

Aufruf:   sudo bash install.sh [aktion] [optionen]
          curl -sSL ${REPO_URL%.git}/raw/${REPO_BRANCH}/install.sh | sudo bash

Aktionen: install (Standard) | update | uninstall | uninstall-purge

Optionen (auch als Umgebungsvariable RIMPERIO_*):
  --dir <pfad>       Installationsverzeichnis   (RIMPERIO_DIR)    [${INSTALL_DIR}]
  --repo <url>       Git-Repository             (RIMPERIO_REPO)   [${REPO_URL}]
  --branch <name>    Branch                     (RIMPERIO_BRANCH) [${REPO_BRANCH}]
  --ref <punkt>      Release, Tag oder Commit statt des Branch-Kopfes
                     (RIMPERIO_REF) - z.B. --ref v2026.09.10
  --with <liste>     Zusatzdienste, komma-getrennt (RIMPERIO_ADDONS)
                     Möglich: ${ALL_ADDONS// /, }
                     Beispiel: --with portainer,tugtainer
  --no-addons        Ausdrücklich ohne Zusatzdienste
  --domain <fqdn>    HTTPS gleich einrichten, z.B. rmm.example.de (RIMPERIO_DOMAIN)
  --tls <art>        letsencrypt (öffentlich erreichbar) | internal (eigenes Netz)
                     Standard: letsencrypt, wenn --email angegeben, sonst internal
  --email <adresse>  E-Mail für Let's Encrypt (RIMPERIO_ACME_EMAIL)
  --https-only       Port 80 nur noch als Weiterleitung auf HTTPS (mit --domain)
  --skip-preflight   Systemprüfung überspringen (nicht empfohlen)
  --no-build         Images nicht neu bauen (nur starten)
  --yes, -y          Keine Rückfragen (unattended)
  --timeout <sek>    Wartezeit des Start-Gates  (RIMPERIO_TIMEOUT) [${PROMPT_TIMEOUT}]
  --help, -h         Diese Hilfe

Zusatzdienste (alles fremde Images, laufen als rimperio-ext-*):
  portainer    Container-Oberfläche im Browser
  tugtainer    prüft, ob für die fremden Images Updates bereitliegen
               (löst Watchtower ab: es meldet nur, es tauscht nicht selbst)
  n8n          Automatisierung (u.a. für die KI-Auswertung)
  openwebui    KI-Oberfläche, auch für lokale Modelle (Ollama)
  postfix      Mailversand (konfiguriert wird er später im Web-UI)

  Sie laufen nur, wenn sie ausgewählt werden. Nachträglich lassen sie sich
  jederzeit unter System -> Zusatzdienste ein- und ausschalten.

Hinweise:
  * uninstall        stoppt und entfernt die Container, Daten (Volumes) bleiben.
  * uninstall-purge  löscht zusätzlich Volumes UND ${INSTALL_DIR} - Logs sind dann weg.
  * Eine vorhandene .env wird nie überschrieben (Datenbank-Passwort bleibt gültig).
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            install)                ACTION="install" ;;
            update|upgrade)         ACTION="update" ;;
            uninstall|remove)       ACTION="uninstall" ;;
            uninstall-purge|purge)  ACTION="uninstall-purge" ;;
            --dir)                  INSTALL_DIR="${2:-}"; shift ;;
            --dir=*)                INSTALL_DIR="${1#*=}" ;;
            --repo)                 REPO_URL="${2:-}"; shift ;;
            --repo=*)               REPO_URL="${1#*=}" ;;
            --branch)               REPO_BRANCH="${2:-}"; shift ;;
            --branch=*)             REPO_BRANCH="${1#*=}" ;;
            --ref)                  REF="${2:-}"; shift ;;
            --ref=*)                REF="${1#*=}" ;;
            --timeout)              PROMPT_TIMEOUT="${2:-5}"; shift ;;
            --timeout=*)            PROMPT_TIMEOUT="${1#*=}" ;;
            --with)                 ADDONS="${2:-}"; shift ;;
            --with=*)               ADDONS="${1#*=}" ;;
            --no-addons)            ADDONS="none" ;;
            --domain)               DOMAIN="${2:-}"; shift ;;
            --domain=*)             DOMAIN="${1#*=}" ;;
            --tls)                  TLS_MODE="${2:-}"; shift ;;
            --tls=*)                TLS_MODE="${1#*=}" ;;
            --email)                ACME_EMAIL="${2:-}"; shift ;;
            --email=*)              ACME_EMAIL="${1#*=}" ;;
            --https-only)           HTTPS_ONLY="true" ;;
            --skip-preflight)       SKIP_PREFLIGHT="true" ;;
            --no-build)             NO_BUILD="true" ;;
            --yes|-y|--unattended)  ASSUME_YES="true" ;;
            --help|-h)              show_help; exit 0 ;;
            *)                      log_warn "Unbekannter Parameter: $1" ;;
        esac
        shift || true
    done
}

# ==============================================================================
# Zusatzdienste auswählen
# ==============================================================================
#
# Die Auswahl bestimmt zweierlei: was der Installer nachher startet und wogegen
# die Systemprüfung rechnet. Deshalb steht sie VOR der Prüfung - sonst prüfte
# man gegen eine Auswahl, die noch niemand getroffen hat.

# Liste normalisieren: Kommas/Leerzeichen egal, Unbekanntes fliegt raus.
normalize_addons() {
    local input="${1:-}" cleaned=() candidate
    [[ "$input" == "none" ]] && { SELECTED_ADDONS=(); return 0; }
    input="${input//,/ }"
    for candidate in $input; do
        candidate="$(echo "$candidate" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
        [[ -z "$candidate" ]] && continue
        if [[ " $ALL_ADDONS " == *" $candidate "* ]]; then
            # Doppelte Nennungen ignorieren.
            [[ " ${cleaned[*]:-} " == *" $candidate "* ]] || cleaned+=("$candidate")
        else
            log_warn "Unbekannter Zusatzdienst '$candidate' - übersprungen."
        fi
    done
    SELECTED_ADDONS=("${cleaned[@]:-}")
    # Bei leerem Array haengt Bash sonst einen leeren Eintrag an.
    [[ ${#SELECTED_ADDONS[@]} -eq 1 && -z "${SELECTED_ADDONS[0]}" ]] && SELECTED_ADDONS=()
    return 0
}

# Fragt im manuellen Modus nach. Automatisch: nur RIMPERIO - das ist die Auswahl,
# die auf jeder Maschine laeuft und niemanden ueberrascht.
choose_addons() {
    SELECTED_ADDONS=()

    if [[ -n "$ADDONS" ]]; then
        normalize_addons "$ADDONS"
        return 0
    fi

    if [[ "$MANUAL" != "true" ]]; then
        log_info "Ohne Rückfrage: nur RIMPERIO (Zusatzdienste später unter System -> Zusatzdienste)."
        return 0
    fi

    echo ""
    echo "=============================================="
    echo "  Was soll installiert werden?"
    echo "=============================================="
    echo ""
    echo "  RIMPERIO selbst ist gesetzt. Zusätzlich möglich:"
    echo ""
    echo "    1) Portainer    Container-Oberfläche im Browser"
    echo "    2) Tugtainer    prüft, ob für die fremden Images Updates bereitliegen"
    echo "    3) n8n          Automatisierung, u.a. für die KI-Auswertung"
    echo "    4) Open WebUI   KI-Oberfläche, auch für lokale Modelle (Ollama)"
    echo "    5) Postfix      Mailversand (Einstellungen später im Web-UI)"
    echo ""
    echo "  Mehrere Ziffern gehen: z.B. '1 2' oder '12'. Leer = nur RIMPERIO."
    echo ""

    local answer=""
    read -r -p "  Auswahl [leer = nur RIMPERIO]: " answer </dev/tty || true
    echo ""

    local picked=()
    [[ "$answer" == *1* ]] && picked+=("portainer")
    [[ "$answer" == *2* ]] && picked+=("tugtainer")
    [[ "$answer" == *3* ]] && picked+=("n8n")
    [[ "$answer" == *4* ]] && picked+=("openwebui")
    [[ "$answer" == *5* ]] && picked+=("postfix")

    # Auch Namen statt Ziffern zulassen - manche tippen lieber "portainer".
    if [[ ${#picked[@]} -eq 0 && -n "$answer" ]]; then
        normalize_addons "$answer"
        return 0
    fi

    SELECTED_ADDONS=("${picked[@]:-}")
    [[ ${#SELECTED_ADDONS[@]} -eq 1 && -z "${SELECTED_ADDONS[0]}" ]] && SELECTED_ADDONS=()

    if [[ ${#SELECTED_ADDONS[@]} -gt 0 ]]; then
        log_info "Ausgewählt: RIMPERIO + ${SELECTED_ADDONS[*]}"
    else
        log_info "Ausgewählt: nur RIMPERIO"
    fi
    return 0
}

# ==============================================================================
# Systemprüfung: trägt diese Maschine die Auswahl?
# ==============================================================================
# ==============================================================================
# HTTPS: Domain und Zertifikat
# ==============================================================================

# Fragt im manuellen Modus nach der Domain. Automatisch (ohne Tastendruck/--yes)
# gilt nur, was per --domain/--tls/--email kam - sonst erst mal HTTP.
ask_https() {
    if [[ -z "$DOMAIN" && "$MANUAL" == "true" ]]; then
        echo ""
        echo "=============================================="
        echo "  HTTPS"
        echo "=============================================="
        echo ""
        echo "  Unter welcher Domain soll RIMPERIO erreichbar sein? (z.B. rmm.example.de)"
        echo "  Leer = erst mal nur HTTP per IP; HTTPS geht später im Browser unter"
        echo "  Einstellungen -> Netzwerk -> Reverse Proxy."
        echo ""
        read -r -p "  Domain [leer = nur HTTP]: " DOMAIN </dev/tty || true
        if [[ -n "$DOMAIN" && -z "$TLS_MODE" ]]; then
            echo ""
            echo "    1) Let's Encrypt - die Domain zeigt öffentlich auf diesen Server,"
            echo "       Port 80 und 443 sind von außen erreichbar"
            echo "    2) Eigenes Zertifikat (Caddy) - nur im eigenen Netz, der Browser"
            echo "       fragt einmal nach"
            local c=""
            read -r -p "  Zertifikat [1]: " c </dev/tty || true
            [[ "$c" == "2" ]] && TLS_MODE="internal" || TLS_MODE="letsencrypt"
        fi
        if [[ "$TLS_MODE" == "letsencrypt" && -z "$ACME_EMAIL" ]]; then
            read -r -p "  E-Mail für Let's Encrypt (Hinweise zum Zertifikat): " ACME_EMAIL </dev/tty || true
        fi
    fi

    [[ -z "$DOMAIN" ]] && return 0
    DOMAIN="${DOMAIN,,}"
    DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN%%/*}"
    if [[ ! "$DOMAIN" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?([.][a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]]; then
        fail "Ungültige Domain: $DOMAIN (erwartet z.B. rmm.example.de)"
    fi
    [[ -z "$TLS_MODE" ]] && { [[ -n "$ACME_EMAIL" ]] && TLS_MODE="letsencrypt" || TLS_MODE="internal"; }
    case "$TLS_MODE" in
        letsencrypt|internal) ;;
        *) fail "Unbekannte Zertifikatsart: $TLS_MODE (letsencrypt oder internal)" ;;
    esac
    if [[ "$TLS_MODE" == "letsencrypt" && -z "$ACME_EMAIL" ]]; then
        log_warn "Let's Encrypt braucht eine E-Mail (--email) - nehme das eigene Zertifikat (internal)."
        TLS_MODE="internal"
    fi
    log_info "HTTPS: https://$DOMAIN ($TLS_MODE)"
}

# Schreibt die HTTPS-Wahl in die .env. Ohne Domain bleibt alles, wie es ist.
apply_https_config() {
    [[ -z "$DOMAIN" ]] && return 0
    local env_file="$INSTALL_DIR/.env"
    set_env_value "$env_file" "RIMPERIO_DOMAIN" "$DOMAIN"
    set_env_value "$env_file" "RIMPERIO_TLS" "$TLS_MODE"
    set_env_value "$env_file" "RIMPERIO_ACME_EMAIL" "$ACME_EMAIL"
    set_env_value "$env_file" "SITE_URL" "https://$DOMAIN"
    set_env_value "$env_file" "RIMPERIO_HTTPS_ONLY" "$HTTPS_ONLY"
}

run_preflight() {
    [[ "$SKIP_PREFLIGHT" == "true" ]] && { log_warn "Systemprüfung übersprungen (--skip-preflight)."; return 0; }

    local script=""
    if [[ -n "$SRC_DIR" && -f "$SRC_DIR/server/install/preflight.sh" ]]; then
        script="$SRC_DIR/server/install/preflight.sh"
    elif [[ -f "$(dirname "${BASH_SOURCE[0]:-.}")/server/install/preflight.sh" ]]; then
        script="$(dirname "${BASH_SOURCE[0]:-.}")/server/install/preflight.sh"
    fi

    if [[ -z "$script" ]]; then
        log_warn "Die Systemprüfung (server/install/preflight.sh) wurde nicht gefunden - wird übersprungen."
        return 0
    fi

    local rc=0
    bash "$script" "${SELECTED_ADDONS[@]:-}" || rc=$?

    case "$rc" in
        0)  return 0 ;;
        2)
            # "Wird knapp" ist kein Abbruchgrund - der Betreiber entscheidet.
            log_warn "Das System ist für diese Auswahl knapp bemessen."
            if [[ "$ASSUME_YES" == "true" ]]; then
                log_info "Automatischer Modus - es wird trotzdem installiert."
                return 0
            fi
            if confirm "Trotzdem weitermachen?" "j"; then
                return 0
            fi
            log_info "Abgebrochen. Mit weniger Zusatzdiensten erneut versuchen."
            exit 0
            ;;
        *)
            log_error "Die Systemprüfung sagt: so geht es nicht."
            if [[ "$ASSUME_YES" != "true" ]] && confirm "Die Prüfung ignorieren und es trotzdem versuchen?" "n"; then
                log_warn "Auf eigene Verantwortung - los geht's."
                return 0
            fi
            log_info "Abgebrochen. Die genannten Punkte beheben und erneut starten."
            exit 1
            ;;
    esac
}

# ==============================================================================
# Voraussetzungen prüfen
# ==============================================================================

check_requirements() {
    log_info "Prüfe Voraussetzungen..."

    if [[ $EUID -ne 0 ]]; then
        log_error "Dieses Script muss als root ausgeführt werden!"
        log_info "Aufruf: sudo bash install.sh   (bzw. '| sudo bash' beim One-Liner)"
        exit 1
    fi

    if ! command -v git &> /dev/null; then
        log_warn "git nicht gefunden - installiere..."
        apt-get update && apt-get install -y git
    fi

    if ! command -v curl &> /dev/null; then
        log_warn "curl nicht gefunden - installiere..."
        apt-get update && apt-get install -y curl
    fi

    if ! command -v docker &> /dev/null; then
        log_warn "Docker nicht gefunden - installiere automatisch..."
        curl -fsSL https://get.docker.com | sh
        systemctl enable docker
        systemctl start docker
        log_success "Docker installiert"
    fi

    if ! docker compose version &> /dev/null; then
        fail "Docker Compose (Plugin) nicht verfügbar!"
    fi

    log_success "Alle Voraussetzungen erfüllt"
}

# ==============================================================================
# Quelle bestimmen: lokales Repo oder frischer Clone von GitHub
# ==============================================================================

resolve_source() {
    # Liegt das Script neben einer docker-compose.yml, ist das Repo schon lokal da.
    local script_path script_dir
    script_path="${BASH_SOURCE[0]:-}"
    if [[ -n "$script_path" && -f "$script_path" ]]; then
        script_dir="$(cd "$(dirname "$script_path")" && pwd)"
        if [[ -f "$script_dir/docker-compose.yml" ]]; then
            SRC_DIR="$script_dir"
            log_info "Quelle: lokales Verzeichnis $SRC_DIR"
            return 0
        fi
    fi

    # One-Liner-Fall (curl | bash): Quellen selbst holen.
    local target="${REF:-$REPO_BRANCH}"
    TMP_CLONE="$(mktemp -d /tmp/rimperio-src.XXXXXX)"
    log_info "Quelle: $REPO_URL ($target) wird geklont..."
    # --branch nimmt auch ein Tag entgegen; ein Commit dagegen nicht - dafuer
    # der zweite Versuch mit vollem Clone und anschliessendem Auschecken.
    if ! git clone --depth 1 --branch "$target" "$REPO_URL" "$TMP_CLONE/repo" 2>/dev/null; then
        log_warn "Flacher Clone von '$target' fehlgeschlagen - versuche vollen Clone."
        rm -rf "$TMP_CLONE/repo"
        git clone "$REPO_URL" "$TMP_CLONE/repo" \
            || fail "git clone fehlgeschlagen - Repo/Branch/Netzwerk prüfen."
        git -C "$TMP_CLONE/repo" checkout --force "$target" \
            || fail "'$target' gibt es im Repository nicht (Release, Tag oder Commit prüfen)."
    fi
    [[ -f "$TMP_CLONE/repo/docker-compose.yml" ]] \
        || fail "Im geklonten Repo fehlt docker-compose.yml."
    SRC_DIR="$TMP_CLONE/repo"
    log_success "Quellen geladen"
}

# ==============================================================================
# .env sicherstellen (bestehende NIE überschreiben)
# ==============================================================================

# Setzt einen Wert in der .env: vorhandene Zeile ersetzen, sonst anhaengen.
# Bewusst zeilenweise - in der .env stehen Kommentare und eigene Anpassungen,
# die eine neu geschriebene Datei alle verlieren wuerde.
set_env_value() {
    local env_file="$1" key="$2" value="$3"
    if grep -qE "^${key}=" "$env_file" 2>/dev/null; then
        # Trennzeichen '|', weil Pfade und URLs Schraegstriche enthalten.
        sed -i "s|^${key}=.*|${key}=${value}|" "$env_file"
    else
        printf '%s=%s\n' "$key" "$value" >> "$env_file"
    fi
}

# Schreibt COMPOSE_FILE, COMPOSE_PROFILES und die Passwoerter der ausgewaehlten
# Zusatzdienste. Laeuft auch beim Update - dann kommt nur dazu, was fehlt.
apply_addon_config() {
    local env_file="$INSTALL_DIR/.env"
    [[ -f "$env_file" ]] || return 0

    if [[ ${#SELECTED_ADDONS[@]} -eq 0 ]]; then
        log_info "Keine Zusatzdienste - der Stack bleibt bei RIMPERIO allein."
        return 0
    fi

    log_info "Richte Zusatzdienste ein: ${SELECTED_ADDONS[*]}"

    # server/deploy/optional.yml muss mitgelesen werden, sonst kennt Compose die
    # Dienste gar nicht und ein Profil bliebe wirkungslos.
    set_env_value "$env_file" "COMPOSE_FILE" "docker-compose.yml:server/deploy/optional.yml"
    set_env_value "$env_file" "COMPOSE_PROFILES" "$(IFS=,; echo "${SELECTED_ADDONS[*]}")"

    local addon password
    for addon in "${SELECTED_ADDONS[@]}"; do
        case "$addon" in
            portainer)
                # Portainer liest sein Startpasswort aus einer Datei, nicht aus
                # der Umgebung - beides anlegen, damit es zusammenpasst.
                if ! grep -qE '^PORTAINER_ADMIN_PASSWORD=.+' "$env_file" 2>/dev/null; then
                    password="$(generate_password)"
                    set_env_value "$env_file" "PORTAINER_ADMIN_PASSWORD" "$password"
                    mkdir -p "$INSTALL_DIR/data"
                    printf '%s' "$password" > "$INSTALL_DIR/data/portainer_password"
                    chmod 600 "$INSTALL_DIR/data/portainer_password"
                    log_success "Portainer-Passwort erzeugt (steht im Web-UI unter System -> Zusatzdienste)"
                fi
                ;;
            n8n)
                if ! grep -qE '^N8N_PASSWORD=.+' "$env_file" 2>/dev/null; then
                    set_env_value "$env_file" "N8N_USER" "admin"
                    set_env_value "$env_file" "N8N_PASSWORD" "$(generate_password)"
                    # Ohne Schluessel legt n8n bei jedem Start einen neuen an -
                    # gespeicherte Zugangsdaten waeren danach unlesbar.
                    set_env_value "$env_file" "N8N_ENCRYPTION_KEY" "$(generate_password)"
                    log_success "n8n-Zugang erzeugt (steht im Web-UI unter System -> Zusatzdienste)"
                fi
                ;;
            tugtainer)
                # Ohne gesetztes Geheimnis startet der Container nicht - und die
                # Meldung von Compose hilft dabei niemandem weiter.
                if ! grep -qE '^TUGTAINER_AGENT_SECRET=.+' "$env_file" 2>/dev/null; then
                    set_env_value "$env_file" "TUGTAINER_AGENT_SECRET" "$(generate_password)"
                fi
                ;;
            openwebui)
                if ! grep -qE '^OPENWEBUI_SECRET_KEY=.+' "$env_file" 2>/dev/null; then
                    set_env_value "$env_file" "OPENWEBUI_SECRET_KEY" "$(generate_password)"
                    log_success "Open WebUI eingerichtet (erster Aufruf legt das Konto an)"
                fi
                ;;
            postfix)
                mkdir -p "$INSTALL_DIR/data/postfix"
                # Eine leere Datei genuegt: env_file in der Compose-Datei
                # verlangt sie, gefuellt wird sie spaeter aus dem Web-UI.
                [[ -f "$INSTALL_DIR/data/postfix/settings.env" ]] || {
                    printf '# Wird von RIMPERIO geschrieben (System -> Mail).\n' \
                        > "$INSTALL_DIR/data/postfix/settings.env"
                    chmod 600 "$INSTALL_DIR/data/postfix/settings.env"
                }
                set_env_value "$env_file" "POSTFIX_HOSTNAME" "$(hostname -f 2>/dev/null || hostname)"
                ;;
        esac
    done
    return 0
}

ensure_env() {
    local env_file="$INSTALL_DIR/.env"

    if [[ -f "$env_file" ]]; then
        log_info "Bestehende .env bleibt unverändert (Datenbank-Passwort bleibt gültig)"
        apply_addon_config
        apply_https_config
        return 0
    fi

    log_info "Erstelle .env..."
    local db_password jwt_secret webhook_secret admin_password
    db_password="$(generate_password)"
    # 64 Zeichen: mit diesem Wert unterschreibt der Server jede Sitzung.
    jwt_secret="$(generate_password)$(generate_password)"
    webhook_secret="$(generate_password)"
    admin_password="$(generate_password | head -c 20)"

    cat > "$env_file" << EOF
# RIMPERIO v${RIMPERIO_VERSION} Konfiguration
# Automatisch generiert am $(date)

# Datenbank
DB_USER=rimperio
DB_PASSWORD=${db_password}
DB_NAME=rimperio

# PostgreSQL-Major-Version (Docker-Tag). Standard: 17 (neu + stabil).
POSTGRES_VERSION=17

# JWT Secret für API Authentifizierung
JWT_SECRET=${jwt_secret}

# Startpasswort für das Konto 'admin'. Ersetzt beim ersten Start das bekannte
# Standardpasswort 'admin'. Nach der ersten Anmeldung bitte ändern - und diese
# Zeile darf dann gerne raus.
RIMPERIO_ADMIN_INITIAL_PASSWORD=${admin_password}

# --- Patchmanagement ---------------------------------------------------------
# Der Server sieht selbst nach, ob auf GitHub etwas Neues liegt, und meldet es
# sofort in jedes offene Fenster. Takt in Sekunden.
RIMPERIO_UPDATE_WATCH=true
RIMPERIO_UPDATE_WATCH_INTERVAL=120
# Noch schneller geht es mit einem GitHub-Webhook. Diesen Wert im Repository
# unter Settings -> Webhooks als Secret eintragen, Ereignis "push", Ziel:
#   https://<dein-rimperio>/api/updates/webhook
RIMPERIO_WEBHOOK_SECRET=${webhook_secret}

# --- Sicherungen -------------------------------------------------------------
# Wie viele Sicherungen aufgehoben werden (System -> Sicherung).
RIMPERIO_KEEP_BACKUPS=10

# --- Terminal im Browser -----------------------------------------------------
# Bewusst AUS: es öffnet eine Root-Shell auf diesem Server. Wer es braucht,
# setzt hier true und startet das Backend neu.
RIMPERIO_WEBSHELL=false

# --- Sprache -----------------------------------------------------------------
# Vorgabe für die Oberfläche, wenn der Browser nichts anderes verlangt (de|en).
RIMPERIO_DEFAULT_LANGUAGE=de
EOF
    chmod 600 "$env_file"
    log_success ".env erstellt"

    apply_addon_config
    apply_https_config
}

# ==============================================================================
# Installation / Update
# ==============================================================================

copy_files() {
    mkdir -p "$INSTALL_DIR"

    # Wird das Script direkt aus dem Installationsverzeichnis gestartet, waere
    # das Kopieren ein "same file"-Fehler - dann ist ohnehin nichts zu tun.
    if [[ "$(cd "$SRC_DIR" && pwd)" == "$(cd "$INSTALL_DIR" && pwd)" ]]; then
        log_info "Quelle ist bereits das Installationsverzeichnis - kein Kopieren nötig."
        return 0
    fi

    log_info "Kopiere Dateien nach $INSTALL_DIR..."
    # Punkt am Ende: Inhalt kopieren, nicht das Verzeichnis selbst.
    # .env liegt nicht in der Quelle und bleibt dadurch erhalten.
    cp -a "$SRC_DIR/." "$INSTALL_DIR/"
    log_success "Dateien aktualisiert"
}

install_rimperio() {
    if [[ -d "$INSTALL_DIR" && -f "$INSTALL_DIR/docker-compose.yml" ]]; then
        log_warn "In $INSTALL_DIR ist RIMPERIO bereits installiert."
        if confirm "Vorhandene Installation aktualisieren (Daten und .env bleiben)?" "j"; then
            log_info "Wechsle auf Update..."
            update_rimperio
            return 0
        fi

        local backup_dir="${INSTALL_DIR}.backup.$(date +%Y%m%d_%H%M%S)"
        log_info "Erstelle Backup: $backup_dir"
        mv "$INSTALL_DIR" "$backup_dir"
        mkdir -p "$INSTALL_DIR"
        # Lag die Quelle im verschobenen Verzeichnis (Script aus /opt/rimperio
        # gestartet), zeigt SRC_DIR jetzt ins Backup.
        if [[ "$SRC_DIR" == "$INSTALL_DIR" || "$SRC_DIR" == "$INSTALL_DIR"/* ]]; then
            SRC_DIR="${backup_dir}${SRC_DIR#"$INSTALL_DIR"}"
        fi
        # .env mitnehmen: sonst passt das Passwort nicht mehr zum bestehenden
        # Postgres-Volume und das Backend kommt nicht mehr an die Datenbank.
        if [[ -f "$backup_dir/.env" ]]; then
            cp -a "$backup_dir/.env" "$INSTALL_DIR/.env"
            log_info ".env aus dem Backup übernommen"
        fi
    fi

    copy_files
    ensure_env
    log_success "RIMPERIO v${RIMPERIO_VERSION} installiert nach $INSTALL_DIR"
}

# Setzt ein vorhandenes Git-Verzeichnis auf einen bestimmten Punkt (Release, Tag
# oder Commit). Reihenfolge beim Aufloesen wie im Wartungsskript: Tag, Zweig auf
# dem Server, roher Commit - so gewinnt ein Tag, der wie ein Zweig heisst, nicht
# zufaellig.
checkout_ref() {
    local resolved="" candidate
    log_info "Hole Stand '$REF' per git..."
    git -C "$INSTALL_DIR" fetch --tags --prune --force origin \
        "+refs/heads/*:refs/remotes/origin/*" || return 1
    for candidate in "refs/tags/$REF" "origin/$REF" "$REF"; do
        if git -C "$INSTALL_DIR" rev-parse --verify --quiet "${candidate}^{commit}" >/dev/null 2>&1; then
            resolved="$candidate"
            break
        fi
    done
    if [[ -z "$resolved" ]]; then
        log_warn "'$REF' ist im Repository nicht auffindbar."
        return 1
    fi
    git -C "$INSTALL_DIR" reset --hard "$resolved" || return 1
    log_success "Stand gesetzt auf $resolved ($(git -C "$INSTALL_DIR" rev-parse --short HEAD))"
    return 0
}

update_rimperio() {
    [[ -d "$INSTALL_DIR" ]] || fail "$INSTALL_DIR existiert nicht - erst installieren."

    local updated="false"
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        # Git meckert sonst ueber "dubious ownership", wenn root ein fremdes Repo anfasst.
        git config --global --add safe.directory "$INSTALL_DIR" >/dev/null 2>&1 || true
        if [[ -n "$REF" ]]; then
            if checkout_ref; then
                updated="true"
            else
                log_warn "'$REF' konnte nicht gesetzt werden - kopiere stattdessen die Quellen."
            fi
        else
            log_info "Aktualisiere per git pull..."
            if git -C "$INSTALL_DIR" pull --ff-only; then
                updated="true"
            else
                log_warn "git pull fehlgeschlagen - kopiere stattdessen die Quellen."
            fi
        fi
    fi

    # Kein Git-Repo (oder pull fehlgeschlagen): Dateien aus der Quelle druebersetzen.
    if [[ "$updated" != "true" ]]; then
        [[ -n "$SRC_DIR" ]] || resolve_source
        copy_files
    fi

    ensure_env
    log_success "RIMPERIO aktualisiert"
}

# ==============================================================================
# Docker Build und Start
# ==============================================================================

build_and_start() {
    cd "$INSTALL_DIR"

    if [[ "$NO_BUILD" != "true" ]]; then
        log_info "Baue Docker Images..."
        docker compose build
        log_success "Docker Images gebaut"
    fi

    log_info "Starte Container..."
    # --remove-orphans raeumt Container weg, die nicht mehr in der Compose-Datei
    # stehen (z.B. den frueheren portainer-agent).
    docker compose up -d --remove-orphans

    log_info "Warte auf Services..."
    sleep 10

    if docker compose ps | grep -q "Up"; then
        log_success "Alle Container laufen"
    else
        log_error "Einige Container sind nicht gestartet"
        docker compose ps
        exit 1
    fi
}

# ==============================================================================
# Deinstallation
# ==============================================================================

uninstall_rimperio() {
    local purge="$1"

    [[ -d "$INSTALL_DIR" ]] || fail "$INSTALL_DIR existiert nicht - nichts zu entfernen."

    if [[ "$purge" == "true" ]]; then
        log_warn "PURGE: Container, Volumes (= ALLE Logs!) und $INSTALL_DIR werden gelöscht."
        # Ohne --yes ist der Default "nein"; wer unattended purged, meint es ernst.
        if [[ "$ASSUME_YES" != "true" ]] && ! confirm "Wirklich alles löschen?" "n"; then
            log_info "Abgebrochen."
            exit 0
        fi
        (cd "$INSTALL_DIR" && docker compose down -v --remove-orphans) || log_warn "docker compose down meldete einen Fehler"
        rm -rf "$INSTALL_DIR"
        log_success "RIMPERIO vollständig entfernt"
    else
        (cd "$INSTALL_DIR" && docker compose down --remove-orphans) || log_warn "docker compose down meldete einen Fehler"
        log_success "Container gestoppt und entfernt. Daten und $INSTALL_DIR bleiben erhalten."
        log_info "Wieder starten: cd $INSTALL_DIR && sudo docker compose up -d"
    fi
}

# ==============================================================================
# Abschluss
# ==============================================================================

print_summary() {
    local local_ip
    local_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    [[ -z "$local_ip" ]] && local_ip="<server-ip>"

    echo ""
    echo "=============================================="
    echo -e "${GREEN}RIMPERIO v${RIMPERIO_VERSION} ist bereit!${NC}"
    echo "=============================================="
    echo ""
    echo "Web-Interface:"
    if [[ -n "$DOMAIN" ]]; then
        echo "  https://${DOMAIN}   (dazu weiterhin http://${local_ip})"
        [[ "$TLS_MODE" == "letsencrypt" ]] && echo "  Let's Encrypt klappt nur, wenn ${DOMAIN} auf diesen Server zeigt und Port 80/443 offen sind."
        [[ "$TLS_MODE" == "internal" ]] && echo "  Eigenes Zertifikat: der Browser fragt beim ersten Aufruf einmal nach."
    else
        echo "  http://${local_ip}"
        echo "  HTTPS: im Browser unter Einstellungen -> Netzwerk -> Reverse Proxy,"
        echo "  oder gleich beim Einzeiler: ... | sudo bash -s -- --domain rmm.example.de --email du@example.de"
    fi
    echo ""
    local initial_admin_password=""
    initial_admin_password="$(grep -E '^RIMPERIO_ADMIN_INITIAL_PASSWORD=' "$INSTALL_DIR/.env" 2>/dev/null | head -n1 | cut -d= -f2-)"
    echo "Erste Anmeldung:"
    echo "  Benutzer: admin"
    if [[ -n "$initial_admin_password" ]]; then
        echo "  Passwort: ${initial_admin_password}"
        echo "            (steht auch in $INSTALL_DIR/.env - nur root kann sie lesen)"
    else
        echo "  Passwort: admin  (bestehende Installation ohne Startpasswort)"
    fi
    echo ""
    echo -e "${YELLOW}WICHTIG: Passwort nach dem ersten Login ändern!${NC}"
    echo ""
    echo "Syslog-Empfang:  Port 514 (UDP/TCP)"
    echo "API-Doku:        in der Oberfläche unter Über RIMPERIO (nur Administratoren)"
    echo "PostgreSQL:      127.0.0.1:5432, nur vom Server selbst (Zugangsdaten in $INSTALL_DIR/.env)"
    echo ""

    if [[ ${#SELECTED_ADDONS[@]} -gt 0 ]]; then
        echo "Zusatzdienste:"
        local addon
        for addon in "${SELECTED_ADDONS[@]}"; do
            case "$addon" in
                portainer)  echo "  Portainer    http://${local_ip}:9000  (Benutzer: admin)" ;;
                tugtainer)  echo "  Tugtainer    http://${local_ip}:9412  (prüft nur, tauscht nicht)" ;;
                openwebui)  echo "  Open WebUI   http://${local_ip}:3000  (erster Aufruf legt das Konto an)" ;;
                n8n)        echo "  n8n          http://${local_ip}:5678  (Benutzer: admin)" ;;
                watchtower) echo "  Watchtower   abgelöst durch Tugtainer - bitte entfernen" ;;
                postfix)    echo "  Postfix      einzustellen im Web-UI unter System -> Mail" ;;
            esac
        done
        echo ""
        echo -e "  ${YELLOW}Die Passwörter stehen im Web-UI unter System -> Zusatzdienste${NC}"
        echo "  (und in $INSTALL_DIR/.env)."
        echo ""
    else
        echo "Zusatzdienste:   keine – jederzeit nachrüstbar unter System -> Zusatzdienste"
        echo ""
    fi
    echo "Updates:"
    echo "  In der Oberfläche unter System -> Updates (mit Rückfall-Option)"
    echo "  oder als Einzeiler:"
    echo "  curl -sSL ${REPO_URL%.git}/raw/${REPO_BRANCH}/install.sh | sudo bash -s -- update -y"
    echo "  Mit Sicherung und selbsttätigem Rückfall:"
    echo "  sudo bash $INSTALL_DIR/server/backend/scripts/rimperio-update.sh apply"
    echo "  Alles dazu: docs/updates/README.md"
    echo ""
    echo "Nützliche Befehle:"
    echo "  cd $INSTALL_DIR"
    echo "  sudo docker compose logs -f   # Logs anzeigen"
    echo "  sudo docker compose restart   # Neustart"
    echo "  sudo docker compose down      # Stoppen"
    echo "  sudo bash install.sh update   # Aktualisieren"
    echo ""
}

# ==============================================================================
# Hauptprogramm
# ==============================================================================

main() {
    parse_args "$@"

    echo ""
    echo "=============================================="
    echo "  RIMPERIO v${RIMPERIO_VERSION} Installer"
    echo "  Aktion: ${ACTION}"
    echo "=============================================="

    case "$ACTION" in
        uninstall)
            interactive_gate
            [[ $EUID -eq 0 ]] || fail "Bitte als root ausführen (sudo)."
            uninstall_rimperio "false"
            ;;
        uninstall-purge)
            interactive_gate
            [[ $EUID -eq 0 ]] || fail "Bitte als root ausführen (sudo)."
            uninstall_rimperio "true"
            ;;
        update)
            interactive_gate
            check_requirements
            # Quellen nur holen, wenn kein Git-Repo im Zielverzeichnis liegt.
            [[ -d "$INSTALL_DIR/.git" ]] || resolve_source
            # Beim Update nur uebernehmen, was ausdruecklich verlangt wurde -
            # sonst schaltete ein Update stillschweigend Dienste zu oder ab.
            [[ -n "$ADDONS" ]] && normalize_addons "$ADDONS"
            update_rimperio
            build_and_start
            print_summary
            ;;
        install)
            interactive_gate
            # Erst die Auswahl, dann dagegen pruefen: die Anforderungen haengen
            # davon ab, was mitinstalliert werden soll.
            choose_addons
            ask_https
            resolve_source
            run_preflight
            check_requirements
            install_rimperio
            build_and_start
            print_summary
            ;;
        *)
            fail "Unbekannte Aktion: $ACTION"
            ;;
    esac
}

# Script starten
main "$@"
