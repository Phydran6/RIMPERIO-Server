#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Datei:        install-agent.sh
# Projekt:      RIMPERIO — Agent
# Autor:        Phydran6
# Version:      2026.11.08.17.00.00
#
# Beschreibung: Richtet den RIMPERIO-Agent (Fernwartung) auf Linux ein — nicht
#               als root, sondern unter einem eigenen Dienstkonto:
#
#                 * Dienstkonto: Standard "rimperio", wählbar mit --user. Fehlt
#                   es, wird es angelegt (Systemkonto, ohne Anmeldung, ohne
#                   Passwort). root ist ausgeschlossen.
#                 * Alles unter /opt/RMM/rimperio:
#                     bin/rimperio-agent    Programm                 root,  0755
#                     lib/rimperio-helper   Hilfsskript (für sudo)   root,  0755
#                     etc/agent.json        Konfiguration mit Token  Konto, 0600
#                     var/                  Arbeitsverzeichnis       Konto, 0700
#                   Außerhalb liegt nur, was systemd und sudo verlangen:
#                     /etc/systemd/system/rimperio-agent.service
#                     /etc/sudoers.d/rimperio-agent
#                 * Rechte (--sudo): "limited" (Standard) erlaubt dem Konto per
#                   sudo genau das Hilfsskript: Paketlisten auffrischen, die
#                   freigegebenen Updates einspielen, Neustart, Deinstallation.
#                   Das Skript prüft jede Angabe selbst. "full" erlaubt
#                   zusätzlich sudo für Befehle aus der Oberfläche, "none" gar
#                   nichts (dann keine Updates und kein Neustart aus der Ferne).
#                 * Docker: Gibt es die Gruppe docker, bekommt der Dienst sie
#                   dazu (für Container-Updates). --no-docker lässt das weg.
#
#               Das Skript selbst braucht root (sudo) — nur, um Konto, Dienst
#               und Verzeichnisse anzulegen. Ein zweiter Lauf aktualisiert:
#               Programm, Hilfsskript und Dienst werden ersetzt, die Anmeldung
#               bleibt (dann geht es auch ohne --secret). Ältere Installationen
#               (root, /usr/local/bin, /etc/rimperio) werden dabei übernommen.
#
# Aufruf:
#   curl -sSL https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/agent/installer/linux/install-agent.sh \
#     | sudo bash -s -- --server https://rmm.example.de --secret SCHLÜSSEL [--site Buero] [--user rimperio]
#
#   Weitere Optionen:  --desc TEXT  --insecure  --sudo limited|full|none
#                      --no-docker  --binary DATEI (statt Download)
#   Entfernen:         … | sudo bash -s -- uninstall [--keep-record]
#   Per Umgebung:      SERVER, SECRET, SITE, DESC, INSECURE=1, RIMPERIO_USER,
#                      RIMPERIO_SUDO, RIMPERIO_DOCKER=0, RIMPERIO_BINARY,
#                      RIMPERIO_BASE_URL (Download-Quelle)
# ---------------------------------------------------------------------------
set -euo pipefail
umask 022

BASE="/opt/RMM/rimperio"
SERVICE="rimperio-agent"
UNIT="/etc/systemd/system/${SERVICE}.service"
LOG_UNIT="/etc/systemd/system/rimperio-log-agent.service"
SUDOERS="/etc/sudoers.d/rimperio-agent"
BIN="${BASE}/bin/rimperio-agent"
HELPER="${BASE}/lib/rimperio-helper"
CONF="${BASE}/etc/agent.json"
LEGACY_BIN="/usr/local/bin/rimperio-agent"
LEGACY_CONF_DIR="/etc/rimperio"
# Ab dieser Version kennt der Agent /opt/RMM/rimperio und das Dienstkonto.
MIN_VERSION="2026.11.08.17.00.00"
# Steht im Kommentarfeld eines Kontos, das ein RIMPERIO-Installer angelegt hat.
# Nur solche Konten entfernt die Deinstallation wieder.
ACCOUNT_MARK="RIMPERIO Agent"

ACTION="install"
SERVER="${SERVER:-}"
SECRET="${SECRET:-}"
SITE="${SITE:-Standard}"
DESC="${DESC:-}"
INSECURE="${INSECURE:-0}"
AGENT_USER="${RIMPERIO_USER:-}"
SUDO_MODE="${RIMPERIO_SUDO:-limited}"
DOCKER="${RIMPERIO_DOCKER:-1}"
LOCAL_BINARY="${RIMPERIO_BINARY:-}"
KEEP_RECORD=0
BASE_URL="${RIMPERIO_BASE_URL:-https://github.com/Phydran6/RIMPERIO-Server/releases/latest/download}"

AGENT_GROUP=""
AGENT_VERSION=""
REUSE_CONF=""
RUNS_AS=""
DOCKER_NOTE="nein"

say()  { printf '%s\n' "$*"; }
warn() { printf 'Hinweis: %s\n' "$*" >&2; }
die()  { printf 'Fehler: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
RIMPERIO-Agent (Fernwartung) für Linux einrichten — läuft als eigenes Dienstkonto, nicht als root.

  sudo bash install-agent.sh --server URL --secret SCHLÜSSEL [Optionen]
  sudo bash install-agent.sh uninstall [--keep-record]

  --server URL       Adresse des RIMPERIO-Servers, z. B. https://rmm.example.de
  --secret S         Zugangsschlüssel aus der Oberfläche (am besten eine Einladung).
                     Bei einem zweiten Lauf nicht nötig: die Anmeldung bleibt.
  --site NAME        Standort/Gruppe (Standard: Standard)
  --desc TEXT        Beschreibung des Rechners
  --insecure         TLS-Prüfung aus (nur Labor mit Selbstzertifikat)
  --user NAME        Dienstkonto (Standard: rimperio; wird angelegt, wenn es fehlt)
  --sudo MODUS       limited (Standard): Updates, Neustart, Deinstallation über das
                     Hilfsskript · full: zusätzlich sudo für Befehle aus der
                     Oberfläche · none: keine Rechte
  --no-docker        dem Dienst die Gruppe docker nicht geben
  --binary DATEI     dieses Programm statt des Downloads (offline, Tests)
  --keep-record      (uninstall) Eintrag auf dem Server behalten

Alles liegt unter /opt/RMM/rimperio.
EOF
}

need_value() { [ $# -ge 2 ] && [ -n "$2" ] || die "$1 braucht einen Wert."; }

while [ $# -gt 0 ]; do
    case "$1" in
        install)       ACTION="install"; shift ;;
        uninstall)     ACTION="uninstall"; shift ;;
        --server)      need_value "$@"; SERVER="$2"; shift 2 ;;
        --secret)      need_value "$@"; SECRET="$2"; shift 2 ;;
        --site)        need_value "$@"; SITE="$2"; shift 2 ;;
        --desc)        need_value "$@"; DESC="$2"; shift 2 ;;
        --insecure)    INSECURE=1; shift ;;
        --user)        need_value "$@"; AGENT_USER="$2"; shift 2 ;;
        --sudo)        need_value "$@"; SUDO_MODE="$2"; shift 2 ;;
        --no-docker)   DOCKER=0; shift ;;
        --binary)      need_value "$@"; LOCAL_BINARY="$2"; shift 2 ;;
        --keep-record) KEEP_RECORD=1; shift ;;
        -h|--help)     usage; exit 0 ;;
        *)             die "Unbekannte Option: $1 (--help zeigt alle)" ;;
    esac
done

# ===========================================================================
# Prüfen
# ===========================================================================

require_root() {
    [ "$(id -u)" -eq 0 ] \
        || die "Bitte mit sudo ausführen. Nur die Einrichtung braucht root — der Agent läuft danach unter einem eigenen Konto."
}

require_systemd() {
    if ! command -v systemctl >/dev/null 2>&1 || [ ! -d /run/systemd/system ]; then
        die "systemd fehlt oder läuft nicht — der Agent braucht systemd als Dienstverwaltung."
    fi
    command -v runuser >/dev/null 2>&1 || die "runuser fehlt (util-linux) — bitte nachinstallieren."
}

# Konto, unter dem ein Dienst laut Unit-Datei läuft (leer = root/unbekannt).
unit_user() {
    if [ -f "$1" ]; then
        sed -n 's/^User=//p' "$1" | head -n1
    fi
}

# Ein Konto für alle RIMPERIO-Dienste auf dem Rechner: Fernwartungs- und
# Log-Agent teilen sich /opt/RMM/rimperio.
choose_account() {
    local other
    other="$(unit_user "$LOG_UNIT")"
    if [ "$other" = root ]; then other=""; fi
    if [ -z "$AGENT_USER" ]; then
        AGENT_USER="${other:-rimperio}"
    elif [ -n "$other" ] && [ "$other" != "$AGENT_USER" ]; then
        die "Der Log-Agent auf diesem Rechner läuft als \"$other\". Beide teilen sich $BASE — bitte dasselbe Konto nehmen: --user $other"
    fi
    if [ "$AGENT_USER" = root ]; then
        die "Der Agent läuft absichtlich nicht als root. Bitte ein eigenes Konto nennen (Standard: --user rimperio)."
    fi
    [[ "$AGENT_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] \
        || die "Ungültiger Kontoname \"$AGENT_USER\" — erlaubt: Kleinbuchstaben, Ziffern, _ und -, höchstens 32 Zeichen."
    case "$SUDO_MODE" in
        limited|full|none) ;;
        *) die "--sudo kennt limited, full oder none (nicht \"$SUDO_MODE\")." ;;
    esac
}

# ===========================================================================
# Dienstkonto und Verzeichnisse
# ===========================================================================

ensure_account() {
    if id -u "$AGENT_USER" >/dev/null 2>&1; then
        [ "$(id -u "$AGENT_USER")" -ne 0 ] || die "Das Konto \"$AGENT_USER\" hat die UID 0 und ist damit root."
        say "Dienstkonto \"$AGENT_USER\" ist vorhanden und wird benutzt."
    else
        local shell
        shell="$(command -v nologin 2>/dev/null || echo /bin/false)"
        if getent group "$AGENT_USER" >/dev/null 2>&1; then
            useradd -r -g "$AGENT_USER" -d "$BASE/var" -M -s "$shell" -c "$ACCOUNT_MARK" "$AGENT_USER" \
                || die "Das Dienstkonto \"$AGENT_USER\" ließ sich nicht anlegen (useradd)."
        else
            useradd -r -U -d "$BASE/var" -M -s "$shell" -c "$ACCOUNT_MARK" "$AGENT_USER" \
                || die "Das Dienstkonto \"$AGENT_USER\" ließ sich nicht anlegen (useradd)."
        fi
        say "Dienstkonto \"$AGENT_USER\" angelegt (Systemkonto, ohne Anmeldung und ohne Passwort)."
    fi
    AGENT_GROUP="$(id -gn "$AGENT_USER")"
}

# Wer in einen der Ordner über dem Hilfsskript schreiben darf, könnte es
# austauschen — und bekäme über die sudo-Regel root. Darum: nur root.
check_root_only() {
    local d="$1" owner mode
    owner="$(stat -c %u "$d")"
    mode="$(stat -c %a "$d")"
    if [ "$owner" != 0 ] || (( (8#$mode & 8#022) != 0 )); then
        die "$d gehört nicht root oder ist für andere beschreibbar (Rechte $mode). So ließe sich das Hilfsskript austauschen. Bitte zuerst richten: chown root:root $d && chmod go-w $d"
    fi
}

# Vor allem anderen (auch vor dem Anlegen des Kontos): Taugt der Ort?
check_layout() {
    [ -d /opt ] || install -d -m 0755 -o root -g root /opt
    check_root_only /opt
    local d
    for d in /opt/RMM "$BASE" "$BASE/bin" "$BASE/lib" "$BASE/etc" "$BASE/var"; do
        if [ -L "$d" ]; then die "$d ist ein symbolischer Link — so nicht. Bitte entfernen und neu starten."; fi
    done
}

prepare_dirs() {
    local d owner mode
    for d in /opt/RMM "$BASE" "$BASE/bin" "$BASE/lib"; do
        if [ -d "$d" ]; then
            owner="$(stat -c %u "$d")"; mode="$(stat -c %a "$d")"
            if [ "$owner" != 0 ] || (( (8#$mode & 8#022) != 0 )); then
                warn "$d gehörte nicht root bzw. war für andere beschreibbar (Rechte $mode) — jetzt root, 0755."
            fi
        fi
        install -d "$d"
        chown root:root "$d"
        chmod 0755 "$d"
    done
    # Nur die Ordner selbst - nie rekursiv: Was darin liegt, gehört dem Konto,
    # und root fasst es nicht an (ein Symlink darin führte sonst sonstwohin).
    for d in "$BASE/etc" "$BASE/var"; do
        install -d "$d"
        chown "$AGENT_USER:$AGENT_GROUP" "$d"
        chmod 0700 "$d"
    done
}

# Wechselt das Konto (zweiter Lauf mit anderem --user), gehört die Anmeldung
# noch dem bisherigen. Sie wird als dieses Konto gelesen - bevor prepare_dirs
# etc/ dem neuen gibt - und später als das neue geschrieben.
CARRY=""
trap 'if [ -n "$CARRY" ]; then rm -f "$CARRY"; fi' EXIT
carry_old_conf() {
    [ -f "$CONF" ] || return 0
    local owner
    owner="$(stat -c %U "$CONF" 2>/dev/null || true)"
    case "$owner" in ""|root|UNKNOWN|"$AGENT_USER") return 0 ;; esac
    CARRY="$(mktemp)"
    if runuser -u "$owner" -- cat "$CONF" > "$CARRY" 2>/dev/null; then
        say "Die Anmeldung von Konto \"$owner\" geht auf \"$AGENT_USER\" über."
    else
        rm -f "$CARRY"
        CARRY=""
    fi
}

# In etc/ und var/ schreibt nur das Dienstkonto selbst: root schriebe einem
# untergeschobenen Symlink hinterher. Inhalt kommt von stdin.
write_as_account() {
    runuser -u "$AGENT_USER" -- sh -c 'umask 077; cat > "$1.new" && mv -f "$1.new" "$1"' sh "$1"
}

# ===========================================================================
# Programm und Hilfsskript
# ===========================================================================

version_ge() { # $1 >= $2 im Format JJJJ.MM.TT.hh.mm.ss
    local a="${1//./}" b="${2//./}"
    [[ "$a" =~ ^[0-9]{14}$ && "$b" =~ ^[0-9]{14}$ ]] || return 1
    [ "$a" -ge "$b" ]
}

install_binary() {
    local new="$BASE/bin/.rimperio-agent.new" arch url
    rm -f "$new"
    if [ -n "$LOCAL_BINARY" ]; then
        [ -f "$LOCAL_BINARY" ] || die "--binary: $LOCAL_BINARY gibt es nicht."
        cp "$LOCAL_BINARY" "$new"
    else
        case "$(uname -m)" in
            x86_64|amd64)  arch="amd64" ;;
            aarch64|arm64) arch="arm64" ;;
            *) die "Nicht unterstützte Architektur: $(uname -m)" ;;
        esac
        url="${BASE_URL}/rimperio-agent-linux-${arch}"
        say "Lade den Agent von ${url} …"
        if command -v curl >/dev/null 2>&1; then
            curl -fsSL --retry 3 -o "$new" "$url" || die "Download fehlgeschlagen: $url"
        elif command -v wget >/dev/null 2>&1; then
            wget -qO "$new" "$url" || die "Download fehlgeschlagen: $url"
        else
            die "Weder curl noch wget vorhanden."
        fi
    fi
    chown root:root "$new"
    chmod 0755 "$new"
    AGENT_VERSION="$("$new" version 2>/dev/null | awk '{print $2}' || true)"
    if [ -z "$AGENT_VERSION" ]; then
        rm -f "$new"
        die "Das Programm lässt sich nicht starten (falsche Architektur oder beschädigter Download?)."
    fi
    if ! version_ge "$AGENT_VERSION" "$MIN_VERSION"; then
        rm -f "$new"
        die "Der Agent $AGENT_VERSION ist älter als $MIN_VERSION und kennt das eigene Dienstkonto noch nicht. Wird das Release gerade gebaut: in ein paar Minuten erneut versuchen."
    fi
    # mv statt cp: ein laufender Agent behält seine alte Datei, bis er neu startet.
    mv -f "$new" "$BIN"
    say "Agent $AGENT_VERSION liegt unter $BIN."
}

# Das Hilfsskript ist die einzige Stelle, an der der Agent root bekommt. Es
# nimmt nur feste Aufgaben an und prüft jede Angabe selbst.
write_helper() {
    local tmp="$HELPER.new"
    {
        printf '#!/bin/bash\n'
        printf '# RIMPERIO-Agent — Hilfsskript für Aufgaben, die root brauchen.\n'
        printf '# Angelegt von install-agent.sh (Agent %s). Nicht von Hand ändern —\n' "$AGENT_VERSION"
        printf '# der nächste Lauf des Installers schreibt es neu.\n'
        printf 'AGENT_USER=%q\n' "$AGENT_USER"
        printf 'HELPER_VERSION=%q\n' "$AGENT_VERSION"
        cat <<'HELPER'
#
# Aufgaben (die sudo-Regel in /etc/sudoers.d/rimperio-agent erlaubt nur dieses Skript):
#   pkg-refresh             Paketlisten auffrischen (apt-get update / dnf makecache)
#   pkg-upgrade [PAKET …]   genannte Pakete aktualisieren, ohne Angabe alle
#   reboot [MINUTEN]        Neustart planen (Standard: in 1 Minute)
#   uninstall               Agent vollständig entfernen
#   version
# Paketnamen werden geprüft: nur Buchstaben, Ziffern und . _ : + ~ -, keine
# Pfade, keine Paketdateien. Aus der Umgebung des Aufrufers zählt nichts.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C
umask 022
cd /

BASE=/opt/RMM/rimperio
SERVICE=rimperio-agent
UNIT=/etc/systemd/system/rimperio-agent.service
LOG_UNIT=/etc/systemd/system/rimperio-log-agent.service
SUDOERS=/etc/sudoers.d/rimperio-agent
ACCOUNT_MARK="RIMPERIO Agent"
PKG_RE='^[A-Za-z0-9][A-Za-z0-9._:+~-]{0,200}$'

die() { echo "rimperio-helper: $*" >&2; exit 2; }

[ "$(id -u)" -eq 0 ] || die "muss als root laufen (sudo)"

pkg_manager() {
    if command -v apt-get >/dev/null 2>&1; then echo apt
    elif command -v dnf >/dev/null 2>&1; then echo dnf
    elif command -v yum >/dev/null 2>&1; then echo yum
    else die "kein unterstützter Paketmanager (apt, dnf, yum)"
    fi
}

check_packages() {
    local p
    for p in "$@"; do
        [[ "$p" =~ $PKG_RE ]] || die "kein gültiger Paketname: $p"
        case "$p" in
            *.deb|*.udeb|*.ddeb|*.rpm|*.dsc|*.changes) die "nur Paketnamen, keine Paketdateien: $p" ;;
        esac
    done
}

cmd_pkg_refresh() {
    [ $# -eq 0 ] || die "pkg-refresh erwartet keine Angaben"
    local pm
    pm="$(pkg_manager)"
    case "$pm" in
        apt) DEBIAN_FRONTEND=noninteractive apt-get update -qq ;;
        dnf) dnf -q -y makecache ;;
        yum) yum -q -y makecache ;;
    esac
}

cmd_pkg_upgrade() {
    check_packages "$@"
    local pm
    pm="$(pkg_manager)"
    case "$pm" in
        apt)
            export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
            if [ $# -eq 0 ]; then
                apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade
            else
                # --only-upgrade: nur schon installierte Pakete, nichts Neues.
                apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
                    install --only-upgrade "$@"
            fi ;;
        dnf) dnf -y upgrade "$@" ;;
        yum) yum -y upgrade "$@" ;;
    esac
}

cmd_reboot() {
    [ $# -le 1 ] || die "reboot erwartet höchstens eine Angabe (Minuten)"
    local minutes="${1:-1}"
    [[ "$minutes" =~ ^[0-9]{1,5}$ ]] || die "Minuten bitte als Zahl"
    minutes=$((10#$minutes))
    [ "$minutes" -ge 1 ] || minutes=1
    shutdown -r "+$minutes" "RIMPERIO: Neustart nach Updates" 2>&1 || systemctl reboot
}

# Läuft dieser Aufruf im Dienst des Agents selbst (Auftrag vom Server)?
in_agent_service() { grep -qs "/${SERVICE}\.service" /proc/self/cgroup; }

unit_user() {
    if [ -f "$1" ]; then sed -n 's/^User=//p' "$1" | head -n1; fi
}

# Wohnt der Log-Agent ebenfalls unter /opt/RMM/rimperio? Dann bleibt das Verzeichnis.
log_agent_here() { [ -f "$LOG_UNIT" ] && grep -q "$BASE/" "$LOG_UNIT"; }

remove_account() {
    local entry
    entry="$(getent passwd "$AGENT_USER" || true)"
    [ -n "$entry" ] || return 0
    if [ "$(printf '%s' "$entry" | cut -d: -f5)" != "$ACCOUNT_MARK" ]; then
        echo "Konto \"$AGENT_USER\" bleibt — es war schon vor RIMPERIO da."
        return 0
    fi
    if [ "$(unit_user "$LOG_UNIT")" = "$AGENT_USER" ]; then
        echo "Konto \"$AGENT_USER\" bleibt — der Log-Agent läuft noch darunter."
        return 0
    fi
    pkill -KILL -u "$AGENT_USER" 2>/dev/null || true
    sleep 1
    if userdel "$AGENT_USER" 2>/dev/null; then
        echo "Dienstkonto \"$AGENT_USER\" entfernt."
    else
        echo "Dienstkonto \"$AGENT_USER\" ließ sich nicht entfernen — bitte selbst: userdel $AGENT_USER"
    fi
}

cmd_uninstall() {
    [ $# -eq 0 ] || die "uninstall erwartet keine Angaben"
    if in_agent_service; then
        # Auftrag vom Server: Das Stoppen des Dienstes beendete auch dieses
        # Skript. Darum als eigene, kurzlebige Unit weiter.
        if command -v systemd-run >/dev/null 2>&1 &&
           systemd-run --unit="${SERVICE}-remove-$$" --description="RIMPERIO-Agent entfernen" \
               /bin/bash "$0" uninstall >/dev/null 2>&1; then
            echo "Deinstallation läuft — der Dienst wird gleich beendet."
            return 0
        fi
        # Ohne systemd-run: erst alles entfernen, den Dienst zuletzt anhalten.
        systemctl disable "$SERVICE" >/dev/null 2>&1 || true
        rm -f "$UNIT" "$SUDOERS" "$BASE/bin/rimperio-agent" "$BASE/etc/agent.json"
        if ! log_agent_here; then rm -rf "$BASE"; rmdir /opt/RMM 2>/dev/null || true; fi
        systemctl daemon-reload 2>/dev/null || true
        echo "Agent entfernt. Das Dienstkonto \"$AGENT_USER\" bleibt — bitte selbst: userdel $AGENT_USER"
        systemctl stop --no-block "$SERVICE" 2>/dev/null || true
        return 0
    fi

    echo "Entferne den RIMPERIO-Agent …"
    if [ -f "$UNIT" ]; then
        timeout 45 systemctl stop "$SERVICE" 2>/dev/null || systemctl kill -s KILL "$SERVICE" 2>/dev/null || true
        systemctl disable "$SERVICE" >/dev/null 2>&1 || true
        rm -f "$UNIT"
        systemctl daemon-reload 2>/dev/null || true
        systemctl reset-failed "$SERVICE" 2>/dev/null || true
        echo "Dienst $SERVICE entfernt."
    fi
    rm -f "$SUDOERS"
    rm -f "$BASE/bin/rimperio-agent" "$BASE/bin/.rimperio-agent.new" "$BASE/etc/agent.json"
    remove_account
    if log_agent_here; then
        rm -f "$0"
        echo "Der Log-Agent bleibt eingerichtet — $BASE bleibt bestehen."
    else
        rm -rf "$BASE"
        rmdir /opt/RMM 2>/dev/null || true
        echo "$BASE entfernt."
    fi
    echo "RIMPERIO-Agent entfernt."
}

case "${1:-}" in
    pkg-refresh) shift; cmd_pkg_refresh "$@" ;;
    pkg-upgrade) shift; cmd_pkg_upgrade "$@" ;;
    reboot)      shift; cmd_reboot "$@" ;;
    uninstall)   shift; cmd_uninstall "$@" ;;
    version)     echo "rimperio-helper $HELPER_VERSION (Konto $AGENT_USER)" ;;
    *) die "unbekannte Aufgabe \"${1:-}\" — erlaubt: pkg-refresh, pkg-upgrade [PAKET …], reboot [MINUTEN], uninstall, version" ;;
esac
HELPER
    } > "$tmp"
    chown root:root "$tmp"
    chmod 0755 "$tmp"
    bash -n "$tmp" || die "Das Hilfsskript ist fehlerhaft — Abbruch."
    mv -f "$tmp" "$HELPER"
}

# ===========================================================================
# Anmeldung
# ===========================================================================

# Wert aus der flachen agent.json des Agents (eine Angabe je Zeile).
json_value() {
    sed -n "s/^[[:space:]]*\"$1\":[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$2" 2>/dev/null | head -n1
}

# Gibt es schon eine Anmeldung bei diesem Server? Dann bleibt sie — eine
# zweite Anmeldung desselben Rechners lehnt der Server ohnehin ab.
find_enrollment() {
    local f url token
    for f in "$CONF" "$LEGACY_CONF_DIR/agent.json"; do
        [ -f "$f" ] || continue
        url="$(json_value server_url "$f")"
        token="$(json_value agent_token "$f")"
        if [ -z "$token" ] || [ -z "$url" ]; then continue; fi
        if [ -z "$SERVER" ] || [ "${url%/}" = "${SERVER%/}" ]; then
            REUSE_CONF="$f"
            SERVER="$url"
            return 0
        fi
        warn "Bisher angemeldet bei $url, jetzt bei $SERVER. Den alten Eintrag dort bitte in der Oberfläche entfernen."
        return 1
    done
    return 1
}

adopt_enrollment() {
    local owner
    if [ "$REUSE_CONF" != "$CONF" ]; then
        # Ältere Installation: /etc/rimperio gehört root - root liest, das Konto schreibt.
        write_as_account "$CONF" < "$REUSE_CONF"
        say "Bestehende Anmeldung übernommen: $REUSE_CONF → $CONF"
    else
        if [ -n "$CARRY" ]; then
            write_as_account "$CONF" < "$CARRY"
            rm -f "$CARRY"
            CARRY=""
        fi
        owner="$(stat -c %U "$CONF")"
        if [ "$owner" != "$AGENT_USER" ]; then
            die "$CONF gehört \"$owner\" statt dem Dienstkonto — bitte die Datei entfernen und mit --secret neu anmelden."
        fi
        say "Die Anmeldung bei $SERVER bleibt bestehen."
    fi
    if [ -n "$SECRET" ]; then say "Der Zugangsschlüssel wird dafür nicht gebraucht."; fi
}

enroll() {
    [ -n "$SERVER" ] || die "Fehlt: --server (Adresse des RIMPERIO-Servers)."
    [ -n "$SECRET" ] || die "Fehlt: --secret (Zugangsschlüssel aus der Oberfläche, am besten eine Einladung)."
    local args=(enroll -server "$SERVER" -secret "$SECRET" -site "$SITE")
    if [ -n "$DESC" ]; then args+=(-desc "$DESC"); fi
    if [ "$INSECURE" = 1 ]; then args+=(-insecure); fi
    rm -f "$CONF"   # entfernt nur den Eintrag, folgt keinem Symlink
    say "Melde den Agent am Server an …"
    # Als Dienstkonto: Die Konfiguration gehört von Anfang an ihm, nicht root.
    (cd "$BASE/var" && runuser -u "$AGENT_USER" -- "$BIN" "${args[@]}") \
        || die "Anmeldung am Server fehlgeschlagen (siehe oben). Aufräumen: sudo $HELPER uninstall"
}

# ===========================================================================
# Rechte (sudo)
# ===========================================================================

ensure_sudo() {
    command -v sudo >/dev/null 2>&1 && return 0
    say "sudo fehlt — wird installiert (für Updates und Neustart aus der Ferne) …"
    if command -v apt-get >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq sudo >/dev/null 2>&1 \
            || { apt-get update -qq >/dev/null 2>&1 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq sudo >/dev/null 2>&1; } || true
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y -q sudo >/dev/null 2>&1 || true
    elif command -v yum >/dev/null 2>&1; then
        yum install -y -q sudo >/dev/null 2>&1 || true
    fi
    command -v sudo >/dev/null 2>&1
}

write_sudoers() {
    if [ "$SUDO_MODE" = none ]; then
        rm -f "$SUDOERS"
        say "Keine sudo-Regel (--sudo none): Updates und Neustart aus der Ferne gehen nicht."
        return 0
    fi
    if ! ensure_sudo; then
        warn "sudo ließ sich nicht installieren — der Agent läuft ohne Rechte für Updates und Neustart."
        SUDO_MODE=none
        rm -f "$SUDOERS"
        return 0
    fi
    local tmp
    tmp="$(mktemp)"
    {
        echo "# RIMPERIO-Agent: Rechte des Dienstkontos \"$AGENT_USER\" — angelegt von install-agent.sh."
        echo "# Updates, Neustart und Deinstallation gehen nur über das Hilfsskript; es prüft"
        echo "# jede Angabe selbst. Die Datei verschwindet mit dem Agent."
        echo "Defaults:$AGENT_USER !requiretty"
        echo "$AGENT_USER ALL=(root) NOPASSWD: $HELPER"
        if [ "$SUDO_MODE" = full ]; then
            echo "# --sudo full: Befehle aus der Oberfläche dürfen sudo benutzen (wie root)."
            echo "$AGENT_USER ALL=(ALL:ALL) NOPASSWD: ALL"
        fi
    } > "$tmp"
    if ! visudo -cf "$tmp" >/dev/null 2>&1; then
        rm -f "$tmp"
        die "Die sudo-Regel wäre ungültig (visudo) — nichts geändert."
    fi
    install -m 0440 -o root -g root "$tmp" "$SUDOERS"
    rm -f "$tmp"
    if ! grep -Eqs '^[[:space:]]*[#@]includedir[[:space:]]+/etc/sudoers\.d' /etc/sudoers; then
        warn "/etc/sudoers bindet /etc/sudoers.d nicht ein — die sudo-Regel greift so nicht."
    fi
}

verify_sudo() {
    [ "$SUDO_MODE" != none ] || return 0
    if runuser -u "$AGENT_USER" -- sudo -n "$HELPER" version >/dev/null 2>&1; then
        say "sudo-Regel greift: Updates und Neustart laufen über $HELPER."
    else
        warn "Das Konto \"$AGENT_USER\" kommt per sudo nicht an das Hilfsskript — Updates aus der Ferne gehen so nicht. Prüfen: sudo -l -U $AGENT_USER"
    fi
}

# ===========================================================================
# Dienst
# ===========================================================================

# Ältere Log-Agents hießen als Dienst ebenfalls rimperio-agent. Nicht
# überschreiben, sondern umbenennen: Er läuft weiter, bis install-linux.sh ihn
# neu einrichtet (dann ebenfalls ohne root).
rescue_old_log_agent() {
    if [ ! -f "$UNIT" ] || ! grep -q 'rimperio_agent.py' "$UNIT"; then
        return 0
    fi
    timeout 30 systemctl stop "$SERVICE" 2>/dev/null || true
    systemctl disable "$SERVICE" >/dev/null 2>&1 || true
    if [ -f "$LOG_UNIT" ]; then
        rm -f "$UNIT"   # der Log-Agent ist schon neu eingerichtet — nur ein Rest
        systemctl daemon-reload
        return 0
    fi
    warn "Unter dem Dienstnamen $SERVICE lief noch der ältere Log-Agent. Er heißt jetzt rimperio-log-agent und läuft weiter. Bitte bei Gelegenheit mit install-linux.sh neu einrichten — dann ebenfalls ohne root."
    sed 's/^Description=.*/Description=RIMPERIO Log-Agent (ältere Fassung)/' "$UNIT" > "$LOG_UNIT"
    rm -f "$UNIT"
    systemctl daemon-reload
    systemctl enable rimperio-log-agent >/dev/null 2>&1 || true
    systemctl start rimperio-log-agent 2>/dev/null \
        || warn "Der ältere Log-Agent ließ sich nicht wieder starten: journalctl -u rimperio-log-agent"
}

write_unit() {
    local groups_line="" nnp_line=""
    if [ "$DOCKER" != 0 ] && getent group docker >/dev/null 2>&1; then
        groups_line="SupplementaryGroups=docker"
        DOCKER_NOTE="ja (Gruppe docker, für Container-Updates)"
    fi
    if [ "$SUDO_MODE" = none ]; then
        nnp_line="NoNewPrivileges=true"
    fi
    cat > "$UNIT.new" <<EOF
# RIMPERIO-Agent (Fernwartung) — angelegt von install-agent.sh, Agent ${AGENT_VERSION}.
# Läuft als Dienstkonto "${AGENT_USER}", nicht als root. Rechte für Updates und
# Neustart: /etc/sudoers.d/rimperio-agent (nur das Hilfsskript).
[Unit]
Description=RIMPERIO Agent (Fernwartung)
Documentation=https://github.com/Phydran6/RIMPERIO-Server/tree/main/agent
After=network-online.target
Wants=network-online.target
RequiresMountsFor=${BASE}
ConditionFileIsExecutable=${BIN}

[Service]
Type=simple
User=${AGENT_USER}
Group=${AGENT_GROUP}
${groups_line}
ExecStart=${BIN} run
WorkingDirectory=${BASE}/var
Environment=HOME=${BASE}/var
Restart=always
RestartSec=30
TimeoutStopSec=30
${nnp_line}

[Install]
WantedBy=multi-user.target
EOF
    chmod 0644 "$UNIT.new"
    mv -f "$UNIT.new" "$UNIT"
}

start_service() {
    # SELinux (RHEL & Co.): Kennzeichnung der neuen Dateien richten.
    if command -v restorecon >/dev/null 2>&1; then
        restorecon -R "$BASE" "$UNIT" "$SUDOERS" 2>/dev/null || true
    fi
    systemctl daemon-reload
    systemctl enable "$SERVICE" >/dev/null 2>&1
    systemctl restart "$SERVICE"
    sleep 3
    systemctl is-active --quiet "$SERVICE" \
        || die "Der Dienst startet nicht. Ursache: journalctl -u $SERVICE -n 50"
    local pid uid
    pid="$(systemctl show -p MainPID "$SERVICE" 2>/dev/null | cut -d= -f2)"
    uid="$(awk '/^Uid:/{print $2}' "/proc/${pid:-0}/status" 2>/dev/null || true)"
    if [ "$uid" = 0 ]; then
        systemctl stop "$SERVICE" || true
        die "Der Dienst lief als root — das darf nicht sein. Bitte $UNIT prüfen."
    fi
    RUNS_AS="$(id -nu "${uid:-$AGENT_USER}" 2>/dev/null || echo "$AGENT_USER")"
}

cleanup_legacy() {
    if [ -e "$LEGACY_BIN" ]; then
        rm -f "$LEGACY_BIN"
        say "Ältere Programmdatei $LEGACY_BIN entfernt."
    fi
    if [ -d "$LEGACY_CONF_DIR" ]; then
        rm -rf "$LEGACY_CONF_DIR"
        say "Ältere Konfiguration $LEGACY_CONF_DIR entfernt."
    fi
}

summary() {
    local rights
    case "$SUDO_MODE" in
        limited) rights="eingeschränkt — Updates, Neustart und Deinstallation nur über das Hilfsskript" ;;
        full)    rights="volles sudo — Befehle aus der Oberfläche dürfen sudo benutzen" ;;
        *)       rights="keine — keine Updates und kein Neustart aus der Ferne" ;;
    esac
    cat <<EOF

==============================================
 RIMPERIO-Agent eingerichtet
==============================================
 Dienst:         $SERVICE — läuft als "$RUNS_AS", nicht als root
 Server:         $SERVER
 Programm:       $BIN ($AGENT_VERSION)
 Konfiguration:  $CONF
 Rechte (sudo):  $rights
 Docker:         $DOCKER_NOTE

   systemctl status $SERVICE
   journalctl -u $SERVICE -f
   sudo $BIN status
   sudo $BIN uninstall            # alles weg, auch der Eintrag auf dem Server

EOF
}

# ===========================================================================
# Ablauf
# ===========================================================================

do_install() {
    require_root
    require_systemd
    choose_account
    check_layout
    ensure_account
    carry_old_conf
    prepare_dirs
    install_binary
    write_helper
    rescue_old_log_agent
    if find_enrollment; then
        adopt_enrollment
    else
        enroll
    fi
    write_sudoers
    write_unit
    start_service
    verify_sudo
    cleanup_legacy
    summary
}

do_uninstall() {
    require_root
    if [ -x "$BIN" ]; then
        if [ "$KEEP_RECORD" = 1 ]; then "$BIN" uninstall -keep-record; else "$BIN" uninstall; fi
    elif [ -x "$HELPER" ]; then
        "$HELPER" uninstall
    elif [ -x "$LEGACY_BIN" ]; then
        if [ "$KEEP_RECORD" = 1 ]; then "$LEGACY_BIN" uninstall -keep-record; else "$LEGACY_BIN" uninstall; fi
    else
        say "Kein RIMPERIO-Agent gefunden — nichts zu tun."
    fi
}

case "$ACTION" in
    uninstall) do_uninstall ;;
    *)         do_install ;;
esac
