# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Sofort-Zustellung von Aufträgen an wartende Agents
# ==============================================================================
"""
Ein Agent ab 2026.11.21 hält neben dem Check-in eine zweite Verbindung offen
(``POST /api/rmm/agent/wait``, Long-Poll). Wird für ihn ein Auftrag
eingereiht, weckt ``notify`` die wartende Anfrage, und der Agent hat den
Befehl nach Bruchteilen einer Sekunde statt erst beim nächsten Check-in.

Alles hier lebt im Speicher des einen Backend-Prozesses (uvicorn ohne
``--workers``). Geht ein Weckruf verloren, schaut die wartende Anfrage ohnehin
alle paar Sekunden selbst in der Datenbank nach.
"""

import asyncio
import time
from typing import Dict

_events: Dict[int, asyncio.Event] = {}
_last_wait: Dict[int, float] = {}

# So lange gilt ein Agent nach seiner letzten Warte-Anfrage als „sofort
# erreichbar“. Etwas mehr als eine Warterunde plus Wiederverbindung.
LIVE_WINDOW = 75.0


def event(agent_id: int) -> asyncio.Event:
    ev = _events.get(agent_id)
    if ev is None:
        ev = _events[agent_id] = asyncio.Event()
    return ev


def mark_waiting(agent_id: int) -> None:
    _last_wait[agent_id] = time.monotonic()


def is_live(agent_id: int) -> bool:
    seen = _last_wait.get(agent_id)
    return seen is not None and time.monotonic() - seen < LIVE_WINDOW


def notify(agent_id: int, delay: float = 0.0) -> None:
    """Weckt die wartende Anfrage des Agents. ``delay`` für Aufrufer, die erst
    später committen (der Auftrag soll beim Nachsehen schon sichtbar sein)."""
    ev = _events.get(agent_id)
    if ev is None:
        return
    if delay <= 0:
        ev.set()
        return
    try:
        asyncio.get_running_loop().call_later(delay, ev.set)
    except RuntimeError:
        ev.set()


def forget(agent_id: int) -> None:
    _events.pop(agent_id, None)
    _last_wait.pop(agent_id, None)
