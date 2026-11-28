#!/bin/bash
# ---------------------------------------------------------------------------
# Datei:        build-spk.sh
# Projekt:      RIMPERIO — Agent als Synology-Paket
# Autor:        Phydran6
# Version:      2026.11.27.21.00.00
#
# Beschreibung: Baut rimperio-agent-synology.spk für Synology DSM 7 – ein Paket
#               für alle unterstützten CPUs (x86_64, aarch64, armv7); welches
#               Programm läuft, entscheidet das Startskript auf dem NAS.
#
#   Verwendung (im Repo):
#       bash agent/installer/synology/build-spk.sh            # baut die Programme selbst
#       BIN_DIR=agent/bin bash agent/installer/synology/build-spk.sh
#                         # nimmt fertige rimperio-agent-linux-{amd64,arm64,armv7}
#       SPK_BUILD=17 …    # Build-Nummer (Standard: Anzahl Commits)
#
#   Ergebnis: agent/bin/rimperio-agent-synology.spk (fester Name, damit
#   …/releases/latest/download/rimperio-agent-synology.spk immer stimmt;
#   die Version steht im Paket und im Paket-Zentrum)
# ---------------------------------------------------------------------------
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
AGENT="$REPO/agent"
OUT_DIR="${OUT_DIR:-$AGENT/bin}"
VERSION="$(tr -d '[:space:]' < "$REPO/VERSION")"
BUILD="${SPK_BUILD:-$(git -C "$REPO" rev-list --count HEAD 2>/dev/null || echo 1)}"
SPK_VERSION="${VERSION%%-*}-$(printf '%04d' "$((10#$BUILD % 10000))")"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/pkg/bin" "$WORK/spk" "$OUT_DIR"

# --- Programme -------------------------------------------------------------
declare -A ARCHS=([amd64]="amd64:" [arm64]="arm64:" [armv7]="arm:7")
for name in amd64 arm64 armv7; do
    dst="$WORK/pkg/bin/rimperio-agent-$name"
    if [[ -n "${BIN_DIR:-}" ]]; then
        cp "$BIN_DIR/rimperio-agent-linux-$name" "$dst"
    else
        IFS=: read -r goarch goarm <<< "${ARCHS[$name]}"
        ldflags="-s -w -X github.com/Phydran6/RIMPERIO-Server/agent/internal/core.Version=$VERSION"
        (cd "$AGENT" && CGO_ENABLED=0 GOOS=linux GOARCH="$goarch" GOARM="$goarm" \
            go build -trimpath -ldflags "$ldflags" -o "$dst" ./cmd/rimperio-agent)
    fi
    chmod 0755 "$dst"
done
cp "$HERE/common.sh" "$WORK/pkg/bin/common.sh"
cp "$REPO/LICENSE" "$WORK/pkg/LICENSE"

# --- package.tgz (wird nach /var/packages/rimperio-agent/target entpackt) ---
tar -C "$WORK/pkg" --owner=0 --group=0 -czf "$WORK/spk/package.tgz" .
CHECKSUM="$(md5sum "$WORK/spk/package.tgz" | cut -d' ' -f1)"

# --- INFO ------------------------------------------------------------------
cat > "$WORK/spk/INFO" <<EOF
package="rimperio-agent"
version="$SPK_VERSION"
os_min_ver="7.0-40000"
displayname="RIMPERIO Agent"
description="Connects this NAS to RIMPERIO using outbound HTTPS only – no open port, no VPN. RIMPERIO monitors and updates DSM through it, even when the NAS is at another site."
description_ger="Verbindet dieses NAS mit RIMPERIO – nur ausgehend per HTTPS, ohne offenen Port und ohne VPN. RIMPERIO überwacht und aktualisiert DSM darüber, auch wenn das NAS an einem anderen Standort steht."
maintainer="Phydran6"
maintainer_url="https://github.com/Phydran6/RIMPERIO-Server"
support_url="https://github.com/Phydran6/RIMPERIO-Server/blob/main/docs/rmm/nas.md"
arch="noarch"
ctl_stop="yes"
startable="yes"
thirdparty="yes"
silent_install="no"
silent_upgrade="yes"
checksum="$CHECKSUM"
EOF

cp -r "$HERE/scripts" "$HERE/conf" "$HERE/WIZARD_UIFILES" "$WORK/spk/"
cp "$HERE/PACKAGE_ICON.PNG" "$HERE/PACKAGE_ICON_256.PNG" "$WORK/spk/"
cp "$REPO/LICENSE" "$WORK/spk/LICENSE"
chmod 0755 "$WORK/spk/scripts/"*

# --- .spk = unkomprimiertes tar, INFO zuerst --------------------------------
SPK="$OUT_DIR/rimperio-agent-synology.spk"
(cd "$WORK/spk" && tar --owner=0 --group=0 -cf "$SPK" INFO package.tgz scripts conf WIZARD_UIFILES \
    PACKAGE_ICON.PNG PACKAGE_ICON_256.PNG LICENSE)
echo "Gebaut: $SPK (DSM-Version $SPK_VERSION, $(du -h "$SPK" | cut -f1))"
