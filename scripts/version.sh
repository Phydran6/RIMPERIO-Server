#!/bin/bash
# ---------------------------------------------------------------------------
# Datei:        scripts/version.sh
# Projekt:      RIMPERIO
# Autor:        Phydran6
# Version:      2026.11.27.14.00.00
# Beschreibung: Projektversion setzen, hochzaehlen und pruefen - nach
#               Semantic Versioning 2.0.0 (https://semver.org/lang/de/).
#
#   scripts/version.sh                  zeigt die aktuelle Version
#   scripts/version.sh bump patch       0.0.1 -> 0.0.2   Fehlerbehebung
#   scripts/version.sh bump minor       0.0.2 -> 0.1.0   neue Funktion, abwaertskompatibel
#   scripts/version.sh bump major       0.1.0 -> 1.0.0   inkompatible Aenderung
#   scripts/version.sh set 0.2.0-rc.1   genau diese Version (Vorab-Stand -> Prerelease)
#   scripts/version.sh check            stimmen alle Stellen mit VERSION ueberein? (CI)
#
# Massstab ist die Datei VERSION. Das Skript zieht die Nummer an die anderen
# Stellen nach, damit nichts mehr auseinanderlaeuft:
#
#   VERSION                              Update-Pruefung, Release-Tag v<VERSION>
#   server/backend/app/config.py         app_version (API, Sicherungen)
#   server/frontend/package.json         Version der Oberflaeche
#   server/frontend/package-lock.json    dito
#   install.sh                           RIMPERIO_VERSION (Installer-Ausgabe)
#
# Agent (exe, MSI, Linux-Binary) liest VERSION beim Bauen selbst.
# Danach: Eintrag "## v<VERSION> (Datum) – Titel" in docs/changelog/releases.md,
# committen, mergen - das Release legt .github/workflows/auto-release.yml an.
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SEMVER='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-(0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(\.(0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?$'

die() { echo "Fehler: $*" >&2; exit 1; }

is_semver() {
    # Vierstellige Jahreszahl vorne = altes Zeitstempel-Schema, nie SemVer.
    [[ "$1" =~ $SEMVER ]] && ! [[ "$1" =~ ^(19|20|21)[0-9]{2}\. ]]
}

current() { tr -d '[:space:]' < VERSION; }

write_all() {
    local v="$1"
    printf '%s\n' "$v" > VERSION
    sed -i -E "s/^(    app_version: str = )\"[^\"]*\"/\1\"$v\"/" server/backend/app/config.py
    # Nur der Kopf (Projekt selbst), nicht die Abhaengigkeiten weiter unten.
    sed -i -E "1,12s/^(  \"version\": )\"[^\"]*\"/\1\"$v\"/" server/frontend/package.json
    sed -i -E "1,12s/^(  (    )?\"version\": )\"[^\"]*\"/\1\"$v\"/" server/frontend/package-lock.json
    sed -i -E "s/^RIMPERIO_VERSION=\"[^\"]*\"/RIMPERIO_VERSION=\"$v\"/" install.sh
}

check() {
    local v bad=0 got
    v="$(current)"
    if ! is_semver "$v"; then
        echo "VERSION '$v' ist noch im alten Zeitstempel-Schema - Pruefung entfaellt."
        echo "Umstellen mit: scripts/version.sh bump patch"
        return 0
    fi
    probe() { # Ort, gefundener Wert
        if [ "$2" = "$v" ]; then echo "ok    $1 ($2)"; else echo "FALSCH $1: '$2' statt '$v'"; bad=1; fi
    }
    got="$(sed -n -E 's/^    app_version: str = "([^"]*)".*/\1/p' server/backend/app/config.py)"
    probe server/backend/app/config.py "$got"
    got="$(sed -n -E '1,12s/^  "version": "([^"]*)".*/\1/p' server/frontend/package.json)"
    probe server/frontend/package.json "$got"
    while read -r got; do probe server/frontend/package-lock.json "$got"; done \
        < <(sed -n -E '1,12s/^  (    )?"version": "([^"]*)".*/\2/p' server/frontend/package-lock.json)
    got="$(sed -n -E 's/^RIMPERIO_VERSION="([^"]*)".*/\1/p' install.sh)"
    probe install.sh "$got"
    if ! grep -q -E "^## v${v//./\\.}( |$)" docs/changelog/releases.md; then
        echo "FEHLT docs/changelog/releases.md: Abschnitt '## v$v (Datum) – Titel'"
        bad=1
    else
        echo "ok    docs/changelog/releases.md (## v$v)"
    fi
    [ "$bad" = 0 ] || die "Version nicht ueberall gleich. Reparieren mit: scripts/version.sh set $v"
}

bump() {
    local part="${1:-}" v major minor patch
    v="$(current)"
    if ! is_semver "$v"; then
        # Erster Schritt weg vom Zeitstempel-Schema: es beginnt bei 0.0.1.
        echo "0.0.1"; return
    fi
    local pre=""
    [[ "$v" == *-* ]] && pre="${v#*-}"
    v="${v%%-*}"
    IFS=. read -r major minor patch <<< "$v"
    # Von einem Vorab-Stand aus (0.2.0-rc.1) ist der naechste Schritt die
    # fertige Version selbst (0.2.0) - so wie "npm version".
    case "$part" in
        patch) [ -n "$pre" ] || patch=$((patch + 1)) ;;
        minor) if [ -z "$pre" ] || [ "$patch" != 0 ]; then minor=$((minor + 1)); patch=0; fi ;;
        major) if [ -z "$pre" ] || [ "$minor.$patch" != 0.0 ]; then major=$((major + 1)); minor=0; patch=0; fi ;;
        *) die "bump braucht patch, minor oder major." ;;
    esac
    echo "$major.$minor.$patch"
}

case "${1:-show}" in
    show)  current ;;
    check) check ;;
    set)
        [ -n "${2:-}" ] || die "set braucht eine Version, z. B. 0.1.0"
        is_semver "$2" || die "'$2' ist keine SemVer-Version (MAJOR.MINOR.PATCH[-vorab], ohne fuehrendes v)."
        write_all "$2"; echo "Version: $2" ;;
    bump)
        old="$(current)"; new="$(bump "${2:-}")"
        write_all "$new"; echo "Version: $old -> $new" ;;
    -h|--help|help) sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//' ;;
    *) die "Unbekannt: $1 (show|bump|set|check)" ;;
esac
