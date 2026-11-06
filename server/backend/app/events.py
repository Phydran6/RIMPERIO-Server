# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Ereignisstrom an offene Oberflaechen (Server-Sent Events)
# ==============================================================================
"""
Ein kleiner Verteiler, damit die Oberflaeche nicht dauernd nachfragen muss.

Wozu: Sobald auf GitHub etwas Neues liegt, soll jeder laufende Server das
*sofort* mitbekommen und den Hinweis anzeigen - nicht erst, wenn jemand
zufaellig die Update-Seite oeffnet. Zwei Wege fuettern denselben Verteiler:

* der GitHub-Webhook (`POST /api/updates/webhook`) - wirklich sofort, sobald
  der Server von aussen erreichbar ist;
* der eigene Beobachter, der GitHub im Takt abfragt - fuer alle Server, die
  nicht aus dem Internet erreichbar sind.

Von hier geht es per Server-Sent Events an die offenen Browser-Fenster.
Bewusst kein WebSocket: es fliesst nur in eine Richtung, und SSE laeuft ohne
Sonderbehandlung durch Caddy und jeden Reverse Proxy.

Der letzte Stand jedes Ereignistyps wird gemerkt und einem neu verbundenen
Fenster sofort geschickt. Wer die Seite oeffnet, nachdem der Hinweis kam,
sieht ihn also trotzdem.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator, Dict, Optional, Set

logger = logging.getLogger("rimperio.events")

# Wie viele Ereignisse eine langsame Verbindung aufstauen darf, bevor sie
# verworfen wird. Ein haengender Browser darf den Server nicht volllaufen lassen.
QUEUE_SIZE = 32

# Takt des Herzschlags. Ohne regelmaessiges Lebenszeichen kappen Proxys eine
# stille Verbindung nach ein bis zwei Minuten.
HEARTBEAT_SECONDS = 20


class EventBus:
    """Verteilt Ereignisse an alle offenen Verbindungen."""

    def __init__(self) -> None:
        self._subscribers: Set[asyncio.Queue] = set()
        self._last: Dict[str, dict] = {}
        self._lock = asyncio.Lock()

    # -- senden ---------------------------------------------------------------
    def publish(self, event_type: str, payload: Optional[dict] = None,
                sticky: bool = True) -> None:
        """Verteilt ein Ereignis. `sticky` merkt es fuer neue Verbindungen."""
        message = {
            "type": event_type,
            "at": time.time(),
            "data": payload or {},
        }
        if sticky:
            self._last[event_type] = message

        dead = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                dead.append(queue)
        for queue in dead:
            self._subscribers.discard(queue)
        if dead:
            logger.debug("%s überlastete Verbindung(en) getrennt", len(dead))

    def clear(self, event_type: str) -> None:
        """Gemerkten Stand verwerfen (z.B. nachdem das Update eingespielt wurde)."""
        self._last.pop(event_type, None)

    def last(self, event_type: str) -> Optional[dict]:
        return self._last.get(event_type)

    @property
    def listener_count(self) -> int:
        return len(self._subscribers)

    # -- empfangen ------------------------------------------------------------
    async def stream(self) -> AsyncIterator[str]:
        """Fertig formatierter SSE-Strom fuer eine einzelne Verbindung."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        async with self._lock:
            self._subscribers.add(queue)

        try:
            # Erst der gemerkte Stand, dann alles Neue.
            yield _format({"type": "hello", "at": time.time(),
                           "data": {"listeners": self.listener_count}})
            for message in list(self._last.values()):
                yield _format(message)

            while True:
                # Wegen Ueberlastung abgehaengt (siehe publish): Rest ausliefern,
                # dann die Verbindung beenden. Sonst kamen nur noch Herzschlaege,
                # der Browser hielt sich fuer verbunden und bekam nie wieder ein
                # Ereignis. So baut EventSource die Verbindung selbst neu auf.
                if queue not in self._subscribers and queue.empty():
                    return
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    # Kommentarzeile: haelt die Verbindung offen, erzeugt im
                    # Browser aber kein Ereignis.
                    yield ": heartbeat\n\n"
                    continue
                yield _format(message)
        finally:
            async with self._lock:
                self._subscribers.discard(queue)


def _format(message: dict) -> str:
    body = json.dumps(message.get("data", {}), ensure_ascii=False, default=str)
    return f"event: {message['type']}\ndata: {body}\n\n"


# Ein Verteiler pro Prozess. Laeuft das Backend mit mehreren Arbeitsprozessen,
# sieht jeder nur seine eigenen Verbindungen - fuer Hinweise ist das in Ordnung,
# weil der Beobachter in jedem Prozess laeuft.
bus = EventBus()


# =============================================================================
# Bekannte Ereignistypen
# =============================================================================
UPDATE_AVAILABLE = "update.available"     # Auf GitHub liegt etwas Neues
UPDATE_STATE = "update.state"             # Ein Wartungslauf aendert seinen Zustand
UPDATE_OUTPUT = "update.output"           # Neue Zeilen aus dem laufenden Wartungslauf
BACKUP_DONE = "backup.done"               # Eine Sicherung wurde fertig
STACK_CHANGED = "stack.changed"           # Zusatz-Container gestartet/gestoppt
SYSTEM_EVENT = "system.event"             # Neue Zeile im Systemtagebuch
DISK_PRESSURE = "disk.pressure"           # Die Platte laeuft voll, es wird aufgeraeumt
