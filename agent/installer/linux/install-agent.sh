#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Datei:        install-agent.sh
# Projekt:      RIMPERIO — Agent
# Autor:        Phydran6
# Version:      2026.11.18.21.00.00
#
# Beschreibung: Richtet den RIMPERIO-Agent (RMM: Patchen, Checks, Inventur) auf
#               Linux ein: lädt das passende Binary, legt es nach
#               /usr/local/bin und registriert den systemd-Dienst.
#               Muss als root laufen.
#
#               Darf beliebig oft laufen: Ist der Agent schon angemeldet, wird
#               nur das Programm erneuert und der Dienst neu gestartet — ohne
#               zweite Anmeldung und ohne doppelten Eintrag auf dem Server.
#
#               Läuft auf dem Rechner noch der alte Log-Agent unter dem
#               Dienstnamen "rimperio-agent" (vor 2026.11.18), zieht er nach
#               "rimperio-logship" um. Vorher hat er den Dienst dieses Agents
#               überschrieben: dann kamen nur noch Logs an, gepatcht wurde nichts.
#
# Aufruf:
#   sudo bash install-agent.sh --server https://rmm.example.de --secret GEHEIMNIS [--site Buero]
#   # oder per Umgebung: SERVER=… SECRET=… sudo -E bash install-agent.sh
#   # Schon angemeldet? Dann reicht:  sudo bash install-agent.sh
# ---------------------------------------------------------------------------
set -euo pipefail

SERVER="${SERVER:-}"
SECRET="${SECRET:-}"
SITE="${SITE:-Standard}"
DESC="${DESC:-}"
INSECURE="${INSECURE:-0}"
BASE_URL="${RIMPERIO_BASE_URL:-https://github.com/Phydran6/RIMPERIO-Server/releases/latest/download}"

DEST="/usr/local/bin/rimperio-agent"
CONFIG="/etc/rimperio/agent.json"
UNIT="/etc/systemd/system/rimperio-agent.service"

# Log-Agent: neuer und alter Ort.
LOG_DIR="/opt/rimperio-logship"
LOG_UNIT="/etc/systemd/system/rimperio-logship.service"
LOG_LEGACY_DIR="/opt/rimperio-agent"

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

enrolled() { [ -f "$CONFIG" ] && grep -q '"agent_token": *"[^"]' "$CONFIG"; }

if ! enrolled; then
  [ -n "$SERVER" ] && [ -n "$SECRET" ] || { echo "SERVER und SECRET sind nötig." >&2; exit 2; }
fi

# --- Alten Log-Agent umziehen ----------------------------------------------
# Erkennbar daran, dass sein Dienst ein Python-Skript startet. Der Log-Agent
# läuft danach weiter, nur unter eigenem Namen.
if [ -f "$UNIT" ] && grep -q 'rimperio_agent.py' "$UNIT"; then
  echo "Hinweis: Der Dienst rimperio-agent gehört hier dem Log-Agent."
  echo "         Er zieht nach rimperio-logship um, damit beide nebeneinander laufen."
  PY="$(sed -n 's/^ExecStart=\([^ ]*\) .*/\1/p' "$UNIT" | head -1)"
  [ -n "$PY" ] || PY="$(command -v python3 || echo /usr/bin/python3)"
  systemctl stop rimperio-agent 2>/dev/null || true
  systemctl disable rimperio-agent >/dev/null 2>&1 || true
  rm -f "$UNIT"

  if [ -f "$LOG_LEGACY_DIR/rimperio_agent.py" ]; then
    if [ -e "$LOG_DIR" ]; then rm -rf "$LOG_LEGACY_DIR"; else mv "$LOG_LEGACY_DIR" "$LOG_DIR"; fi
  fi
  if [ -f "$LOG_DIR/rimperio_agent.py" ]; then
    cat > "$LOG_UNIT" <<EOF
[Unit]
Description=RIMPERIO Log-Agent (HTTPS Log Forwarder)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${PY} ${LOG_DIR}/rimperio_agent.py
Environment=RIMPERIO_CONFIG=${LOG_DIR}/config.json
Environment=RIMPERIO_CURSOR=${LOG_DIR}/cursor
Environment=RIMPERIO_AGENT_ID=${LOG_DIR}/agent_id
Restart=always
RestartSec=10
User=root
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now rimperio-logship >/dev/null 2>&1 \
      && echo "Log-Agent läuft jetzt als rimperio-logship." \
      || echo "Warnung: Log-Agent ließ sich nicht starten (journalctl -u rimperio-logship)." >&2
  else
    systemctl daemon-reload
    echo "Warnung: Skript des Log-Agents nicht gefunden — Log-Agent bei Bedarf neu installieren." >&2
  fi
fi

# --- Programm laden ----------------------------------------------------------
case "$(uname -m)" in
  x86_64|amd64) ARCH="amd64" ;;
  aarch64|arm64) ARCH="arm64" ;;
  *) echo "Nicht unterstützte Architektur: $(uname -m)" >&2; exit 1 ;;
esac

URL="${BASE_URL}/rimperio-agent-linux-${ARCH}"
TMP="$(mktemp "${DEST}.XXXXXX")"
trap 'rm -f "$TMP"' EXIT

echo "Lade Agent von ${URL} …"
if command -v curl >/dev/null 2>&1; then
  curl -fSL "$URL" -o "$TMP"
else
  wget -O "$TMP" "$URL"
fi
chmod 0755 "$TMP"
# Umbenennen statt überschreiben: klappt auch, während der alte Agent läuft.
mv -f "$TMP" "$DEST"
trap - EXIT

# --- Anmelden und Dienst -----------------------------------------------------
ARGS=(-server "$SERVER" -secret "$SECRET" -site "$SITE")
[ -n "$DESC" ] && ARGS+=(-desc "$DESC")
[ "$INSECURE" = "1" ] && ARGS+=(-insecure)

if enrolled; then
  echo "Agent ist schon angemeldet ($CONFIG) — erneuere nur Programm und Dienst."
  if [ -f "$UNIT" ]; then
    systemctl restart rimperio-agent
  else
    "$DEST" install
  fi
elif [ -f "$UNIT" ]; then
  echo "Melde Agent an …"
  "$DEST" enroll "${ARGS[@]}"
  systemctl restart rimperio-agent
else
  echo "Richte systemd-Dienst ein …"
  "$DEST" setup "${ARGS[@]}"
fi

sleep 2
if systemctl is-active --quiet rimperio-agent; then
  echo "Fertig. Der Agent läuft und erscheint unter Geräte → Rechner."
  echo "Die erste Update-Suche startet in den nächsten Minuten."
else
  echo "Der Dienst läuft nicht. Prüfen mit:  journalctl -u rimperio-agent -n 50" >&2
  exit 1
fi
