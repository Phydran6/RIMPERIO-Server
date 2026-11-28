#!/bin/sh
# ---------------------------------------------------------------------------
# Datei:        common.sh  (im Paket: target/bin/common.sh)
# Projekt:      RIMPERIO — Agent als Synology-Paket
# Autor:        Phydran6
# Version:      2026.11.27.21.00.00
#
# Beschreibung: Gemeinsames für die Paket-Skripte: das Programm passend zur CPU.
# ---------------------------------------------------------------------------

agent_bin() {
    case "$(uname -m)" in
        x86_64 | amd64) echo "$1/bin/rimperio-agent-amd64" ;;
        aarch64 | arm64 | armv8*) echo "$1/bin/rimperio-agent-arm64" ;;
        armv7*) echo "$1/bin/rimperio-agent-armv7" ;;
        *) echo "" ;;
    esac
}
