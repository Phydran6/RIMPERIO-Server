#!/bin/bash
# ==============================================================================
#  RIMPERIO - Systempruefung vor der Installation
# ==============================================================================
#
# Autor:        Phydran6
# Kontakt:      Phydran6
# Changelog:    docs/changelog/database.md
#
# Beschreibung:
#   Prueft, ob dieser Rechner RIMPERIO ueberhaupt tragen kann - und ob er die
#   ausgewaehlten Zusatzdienste noch dazu vertraegt.
#
#   Gedacht als Teil von install.sh, laeuft aber auch allein:
#
#     sudo bash server/install/preflight.sh                       # nur RIMPERIO
#     sudo bash server/install/preflight.sh portainer watchtower  # mit Zusatzdiensten
#     curl -sSL <RAW-URL>/server/install/preflight.sh | sudo bash
#
#   Rueckgabewerte:
#     0  passt
#     1  geht nicht (harter Ausschluss - z.B. falsche Architektur)
#     2  wird knapp (laeuft, kann aber langsam werden)
#
#   Bewusst zweistufig: "geht nicht" und "wird knapp" sind verschiedene Dinge.
#   Ein Raspberry Pi mit 2 GB laeuft mit RIMPERIO allein prima - nur eben nicht
#   mit Portainer, n8n und Postfix obendrauf. Das gehoert gesagt, nicht
#   verboten.
# ==============================================================================

set -uo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

# ------------------------------------------------------------------------------
# Bedarf je Baustein (Anhaltswerte aus dem Betrieb, keine harten Grenzen)
#   RAM in MB, Platte in MB
# ------------------------------------------------------------------------------
BASE_RAM=1024          # RIMPERIO selbst: Postgres + Backend + Frontend + Caddy + Syslog
BASE_DISK=4096         # Images, Volumes und etwas Luft fuer Logs

# Untergrenze, unterhalb derer es keinen Sinn mehr ergibt.
MIN_RAM=768
MIN_DISK=2048

declare -A ADDON_RAM=(
    [portainer]=256
    [watchtower]=128
    [n8n]=768
    [postfix]=128
)
declare -A ADDON_DISK=(
    [portainer]=400
    [watchtower]=150
    [n8n]=1200
    [postfix]=200
)
declare -A ADDON_LABEL=(
    [portainer]="Portainer"
    [watchtower]="Watchtower"
    [n8n]="n8n"
    [postfix]="Postfix (Mailversand)"
)

# Ports, die RIMPERIO selbst belegt.
BASE_PORTS=(80 443 514 5432)
declare -A ADDON_PORTS=(
    [portainer]="9000 9443"
    [n8n]="5678"
    [postfix]="1587"
)

FAILURES=()
WARNINGS=()
NOTES=()

log_head()  { echo -e "\n${BOLD}$1${NC}"; }
log_ok()    { echo -e "  ${GREEN}[ok]${NC}    $1"; }
log_warn()  { echo -e "  ${YELLOW}[knapp]${NC} $1"; WARNINGS+=("$1"); }
log_fail()  { echo -e "  ${RED}[nein]${NC}  $1"; FAILURES+=("$1"); }
log_note()  { echo -e "  ${BLUE}[info]${NC}  $1"; NOTES+=("$1"); }

# ==============================================================================
# Was soll geprueft werden?
# ==============================================================================
WANTED=()
for arg in "$@"; do
    case "$arg" in
        portainer|watchtower|n8n|postfix) WANTED+=("$arg") ;;
        all) WANTED=(portainer watchtower n8n postfix) ;;
        --quiet|-q) QUIET=1 ;;
        *) echo "Unbekannter Zusatzdienst: $arg (erlaubt: portainer watchtower n8n postfix all)" ;;
    esac
done

# ==============================================================================
# 1. Betriebssystem und Architektur
# ==============================================================================
check_platform() {
    log_head "System"

    local kernel arch os_name os_version
    kernel="$(uname -s)"
    arch="$(uname -m)"

    if [[ "$kernel" != "Linux" ]]; then
        log_fail "RIMPERIO laeuft nur auf Linux (gefunden: $kernel)."
        return
    fi

    case "$arch" in
        x86_64|amd64)  log_ok "Architektur: $arch" ;;
        aarch64|arm64) log_ok "Architektur: $arch (ARM64 - alle Images sind dafuer verfuegbar)" ;;
        armv7l|armhf)
            log_fail "Architektur $arch (32-Bit ARM): fuer PostgreSQL und Caddy gibt es hier keine passenden Images."
            ;;
        *)
            log_warn "Architektur $arch ist ungetestet. Es kann sein, dass einzelne Images fehlen."
            ;;
    esac

    if [[ -r /etc/os-release ]]; then
        # shellcheck disable=SC1091
        os_name="$(. /etc/os-release && echo "${PRETTY_NAME:-$NAME}")"
        os_version="$(. /etc/os-release && echo "${VERSION_ID:-}")"
        log_ok "Betriebssystem: ${os_name}"

        # Nicht als Fehler: Docker laeuft praktisch ueberall. Aber der Installer
        # holt Pakete per apt/dnf/yum - wer nichts davon hat, muss selbst ran.
        if ! command -v apt-get >/dev/null 2>&1 \
           && ! command -v dnf >/dev/null 2>&1 \
           && ! command -v yum >/dev/null 2>&1; then
            log_note "Kein apt/dnf/yum gefunden - fehlende Pakete (git, curl) muessen von Hand nachinstalliert werden."
        fi
    else
        log_note "Keine /etc/os-release - Betriebssystem nicht bestimmbar."
    fi

    # Kernel-Version: aeltere Kernel koennen mit neueren Docker-Versionen zicken.
    local kmajor kminor
    kmajor="$(uname -r | cut -d. -f1)"
    kminor="$(uname -r | cut -d. -f2 | tr -dc '0-9')"
    if [[ -n "$kmajor" && "$kmajor" -lt 4 ]] || \
       { [[ "$kmajor" == "4" ]] && [[ -n "$kminor" ]] && [[ "$kminor" -lt 4 ]]; }; then
        log_warn "Kernel $(uname -r) ist sehr alt. Docker verlangt mindestens 4.4."
    else
        log_ok "Kernel: $(uname -r)"
    fi

    # In einem Container zu installieren geht - aber der Neustart-Knopf, das
    # Update ueber die Oberflaeche und das Terminal brauchen den echten Host.
    if [[ -f /.dockerenv ]] || grep -qa 'docker\|lxc' /proc/1/cgroup 2>/dev/null; then
        log_note "Dieses System sieht selbst nach einem Container aus. RIMPERIO laeuft dort, aber Update ueber die Oberflaeche, Neustart und Terminal brauchen Zugriff auf den echten Host."
    fi
}

# ==============================================================================
# 2. Rechte
# ==============================================================================
check_privileges() {
    log_head "Rechte"
    if [[ $EUID -ne 0 ]]; then
        log_fail "Die Installation braucht Root-Rechte (sudo)."
    else
        log_ok "Root-Rechte vorhanden."
    fi
}

# ==============================================================================
# 3. Arbeitsspeicher, Platte, Prozessor
# ==============================================================================
compute_need() {
    NEED_RAM=$BASE_RAM
    NEED_DISK=$BASE_DISK
    for addon in "${WANTED[@]}"; do
        NEED_RAM=$(( NEED_RAM + ${ADDON_RAM[$addon]:-0} ))
        NEED_DISK=$(( NEED_DISK + ${ADDON_DISK[$addon]:-0} ))
    done
}

check_resources() {
    log_head "Ausstattung"
    compute_need

    local ram_mb swap_mb disk_mb cpus
    ram_mb="$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)"
    swap_mb="$(awk '/SwapTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)"
    disk_mb="$(df -Pm "${RIMPERIO_DIR:-/opt}" 2>/dev/null | awk 'NR==2 {print $4}' || echo 0)"
    [[ -z "$disk_mb" || "$disk_mb" == "0" ]] && disk_mb="$(df -Pm / | awk 'NR==2 {print $4}')"
    cpus="$(nproc 2>/dev/null || echo 1)"

    if [[ ${#WANTED[@]} -gt 0 ]]; then
        local labels=()
        for addon in "${WANTED[@]}"; do labels+=("${ADDON_LABEL[$addon]}"); done
        echo -e "  Auswahl: RIMPERIO + ${labels[*]}"
    else
        echo -e "  Auswahl: nur RIMPERIO"
    fi
    echo -e "  Bedarf:  ~${NEED_RAM} MB RAM, ~${NEED_DISK} MB Platte"

    # --- RAM ---
    if [[ "$ram_mb" -lt "$MIN_RAM" ]]; then
        log_fail "Arbeitsspeicher: ${ram_mb} MB. Unter ${MIN_RAM} MB startet PostgreSQL nicht zuverlaessig."
    elif [[ "$ram_mb" -lt "$NEED_RAM" ]]; then
        if [[ "$swap_mb" -gt 0 ]]; then
            log_warn "Arbeitsspeicher: ${ram_mb} MB (+${swap_mb} MB Swap), empfohlen ~${NEED_RAM} MB. Laeuft, wird unter Last aber langsam."
        else
            log_warn "Arbeitsspeicher: ${ram_mb} MB, empfohlen ~${NEED_RAM} MB - und kein Swap eingerichtet. Bei Lastspitzen beendet der Kernel Prozesse."
        fi
    else
        log_ok "Arbeitsspeicher: ${ram_mb} MB"
    fi

    # --- Platte ---
    if [[ "$disk_mb" -lt "$MIN_DISK" ]]; then
        log_fail "Freier Plattenplatz: ${disk_mb} MB. Fuer die Images allein sind mindestens ${MIN_DISK} MB noetig."
    elif [[ "$disk_mb" -lt "$NEED_DISK" ]]; then
        log_warn "Freier Plattenplatz: ${disk_mb} MB, empfohlen ~${NEED_DISK} MB. Logs wachsen - RIMPERIO raeumt ab 80 % Belegung selbst auf."
    else
        log_ok "Freier Plattenplatz: ${disk_mb} MB"
    fi

    # --- Prozessor ---
    local need_cpus=1
    [[ ${#WANTED[@]} -ge 2 ]] && need_cpus=2
    if [[ "$cpus" -lt "$need_cpus" ]]; then
        log_warn "Prozessorkerne: ${cpus}, empfohlen ${need_cpus}. Der erste Bau der Images dauert dann sehr lange."
    else
        log_ok "Prozessorkerne: ${cpus}"
    fi
}

# ==============================================================================
# 4. Docker
# ==============================================================================
check_docker() {
    log_head "Docker"

    if ! command -v docker >/dev/null 2>&1; then
        log_note "Docker ist nicht installiert - der Installer holt es nach (get.docker.com)."
        return
    fi

    local version
    version="$(docker --version 2>/dev/null | sed -n 's/.*version \([0-9.]*\).*/\1/p')"
    log_ok "Docker ${version:-vorhanden}"

    if ! docker info >/dev/null 2>&1; then
        log_fail "Der Docker-Dienst antwortet nicht (laeuft er? 'systemctl start docker')."
        return
    fi

    if docker compose version >/dev/null 2>&1; then
        log_ok "Docker Compose: $(docker compose version --short 2>/dev/null || echo vorhanden)"
    elif command -v docker-compose >/dev/null 2>&1; then
        log_fail "Nur das alte 'docker-compose' gefunden. RIMPERIO braucht das Plugin ('docker compose')."
    else
        log_fail "Docker Compose (Plugin) fehlt."
    fi
}

# ==============================================================================
# 5. Ports
# ==============================================================================
port_in_use() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -lntu 2>/dev/null | awk '{print $5}' | grep -qE "[:.]${port}\$" && return 0
    elif command -v netstat >/dev/null 2>&1; then
        netstat -lntu 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}\$" && return 0
    fi
    return 1
}

check_ports() {
    log_head "Ports"

    local ports=("${BASE_PORTS[@]}")
    for addon in "${WANTED[@]}"; do
        # shellcheck disable=SC2206
        local extra=(${ADDON_PORTS[$addon]:-})
        [[ ${#extra[@]} -gt 0 ]] && ports+=("${extra[@]}")
    done

    local busy=()
    for port in "${ports[@]}"; do
        if port_in_use "$port"; then
            busy+=("$port")
        fi
    done

    if [[ ${#busy[@]} -eq 0 ]]; then
        log_ok "Alle benoetigten Ports sind frei (${ports[*]})."
        return
    fi

    for port in "${busy[@]}"; do
        # Laeuft dort schon RIMPERIO selbst, ist das kein Problem, sondern der
        # Normalfall beim Aktualisieren.
        if docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -q "rimperio-.*:${port}->"; then
            log_note "Port ${port} ist von RIMPERIO selbst belegt (bestehende Installation)."
        else
            log_warn "Port ${port} ist bereits belegt. Der betreffende Container startet sonst nicht."
        fi
    done
}

# ==============================================================================
# 6. Netz
# ==============================================================================
check_network() {
    log_head "Netz"

    if command -v getent >/dev/null 2>&1 && getent hosts github.com >/dev/null 2>&1; then
        log_ok "DNS loest auf (github.com)."
    else
        log_warn "github.com ist nicht aufloesbar. Ohne DNS gibt es weder Quellen noch Images."
    fi

    local probe=""
    if command -v curl >/dev/null 2>&1; then
        probe="$(curl -sS -m 8 -o /dev/null -w '%{http_code}' https://github.com 2>/dev/null)"
    elif command -v wget >/dev/null 2>&1; then
        wget -q --spider --timeout=8 https://github.com 2>/dev/null && probe="200"
    fi

    if [[ "$probe" =~ ^(200|30[0-9])$ ]]; then
        log_ok "GitHub ist erreichbar."
    else
        log_warn "GitHub ist gerade nicht erreichbar. Installation und Updates brauchen den Zugang."
    fi
}

# ==============================================================================
# 7. Zeit
# ==============================================================================
check_time() {
    log_head "Uhrzeit"
    # Eine falsch gehende Uhr laesst TLS-Verbindungen scheitern und macht die
    # Zeitstempel der Logs wertlos. Beides faellt sonst erst spaeter auf.
    if command -v timedatectl >/dev/null 2>&1; then
        if timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -q yes; then
            log_ok "Die Uhr wird per NTP gestellt."
        else
            log_warn "Die Uhr wird nicht per NTP gestellt. Falsche Zeitstempel und TLS-Fehler sind die Folge."
        fi
    else
        log_note "timedatectl nicht vorhanden - Uhrzeit nicht pruefbar."
    fi
}

# ==============================================================================
# Ergebnis
# ==============================================================================
summary() {
    echo ""
    echo "=============================================="
    if [[ ${#FAILURES[@]} -gt 0 ]]; then
        echo -e "${RED}${BOLD}So geht es nicht.${NC}"
        echo "=============================================="
        for item in "${FAILURES[@]}"; do echo "  - $item"; done
        [[ ${#WARNINGS[@]} -gt 0 ]] && { echo ""; echo "  Ausserdem knapp:"; for item in "${WARNINGS[@]}"; do echo "  - $item"; done; }
        echo ""
        return 1
    fi

    if [[ ${#WARNINGS[@]} -gt 0 ]]; then
        echo -e "${YELLOW}${BOLD}Es laeuft - aber es wird knapp.${NC}"
        echo "=============================================="
        for item in "${WARNINGS[@]}"; do echo "  - $item"; done
        echo ""
        echo "  Weniger Zusatzdienste auswaehlen macht es entspannter."
        echo ""
        return 2
    fi

    echo -e "${GREEN}${BOLD}Passt. Dieses System traegt die Auswahl.${NC}"
    echo "=============================================="
    echo ""
    return 0
}

main() {
    echo ""
    echo "=============================================="
    echo "  RIMPERIO - Systempruefung"
    echo "=============================================="

    check_platform
    check_privileges
    check_resources
    check_docker
    check_ports
    check_network
    check_time
    summary
}

main
exit $?
