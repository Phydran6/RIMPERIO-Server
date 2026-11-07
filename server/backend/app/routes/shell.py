# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Endpunkte fuer das Terminal im Browser
# ==============================================================================
"""
Das Terminal haengt an einem WebSocket - anders geht es nicht: eine Shell
schickt jederzeit von sich aus Ausgabe, und der Browser jederzeit Tasten.

Zur Anmeldung: ein WebSocket kann keinen `Authorization`-Kopf mitschicken. Der
Token kommt deshalb als Abfrageparameter, und er wird hier von Hand geprueft -
dieselbe Pruefung wie bei jedem anderen Endpunkt, nur eben nicht ueber die
uebliche Abhaengigkeit.

Das Protokoll auf der Leitung ist bewusst einfach:

    Browser -> Server:  {"type": "input",  "data": "ls -l\\r"}
                        {"type": "resize", "rows": 40, "cols": 120}
                        {"type": "ping"}
    Server -> Browser:  {"type": "output", "data": "..."}
                        {"type": "ready",  "session": "..."}
                        {"type": "error",  "message": "..."}
                        {"type": "closed", "reason": "..."}
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect

from .. import shell
from ..auth import admin_from_token, get_current_admin
from ..limiter import client_ip as real_client_ip

logger = logging.getLogger("rimperio.routes.shell")

router = APIRouter(prefix="/api/shell", tags=["Terminal"])


@router.get("/status")
async def status(_=Depends(get_current_admin)):
    """Gibt es das Terminal auf diesem Server — und was ist gerade offen?"""
    info = shell.availability()
    info["sessions"] = shell.sessions()
    return info


@router.delete("/sessions/{session_id}")
async def close(session_id: str, admin=Depends(get_current_admin)):
    """Beendet eine offene Sitzung (auch die von jemand anderem)."""
    if not shell.close_session(session_id, reason=f"von {admin.username} geschlossen"):
        raise HTTPException(status_code=404, detail="Diese Sitzung gibt es nicht (mehr).")
    return {"closed": True, "session": session_id}


@router.websocket("/ws")
async def terminal(websocket: WebSocket,
                   token: str = Query(..., description="Ticket aus /api/auth/ticket (Zweck: shell)"),
                   rows: int = Query(24, ge=4, le=300),
                   cols: int = Query(80, ge=20, le=500)):
    """Öffnet eine Root-Shell auf dem Server und verbindet sie mit dem Browser."""
    user = await admin_from_token(token, purpose="shell")
    if not user:
        # 1008 = "policy violation". Die Verbindung wird angenommen und sofort
        # wieder geschlossen, damit der Browser einen sauberen Grund bekommt.
        await websocket.accept()
        await websocket.send_json({"type": "error",
                                   "message": "Nicht angemeldet oder keine Administratorrechte."})
        await websocket.close(code=1008)
        return

    availability = shell.availability()
    if not availability["available"]:
        await websocket.accept()
        await websocket.send_json({"type": "error", "message": availability["reason"]})
        await websocket.close(code=1008)
        return

    await websocket.accept()

    try:
        session = shell.open_session(user.username, rows=rows, cols=cols,
                                     source_ip=real_client_ip(websocket))
    except (PermissionError, RuntimeError) as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close(code=1011)
        return

    await websocket.send_json({
        "type": "ready",
        "session": session.id,
        "user": user.username,
        "warning": ("Diese Shell läuft als root auf dem Server. "
                    "Die Sitzung wird protokolliert."),
    })

    async def pump_output() -> None:
        """Alles, was die Shell ausgibt, an den Browser weiterreichen."""
        try:
            async for chunk in shell.read_stream(session.id):
                await websocket.send_json({"type": "output", "data": chunk})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            # Die Shell ist weg - der Browser soll das erfahren und nicht
            # stumm vor einem toten Terminal sitzen.
            try:
                await websocket.send_json({"type": "closed", "reason": "Die Shell wurde beendet."})
                await websocket.close()
            except (WebSocketDisconnect, RuntimeError):
                pass

    output_task = asyncio.create_task(pump_output())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                # Nachsichtig: reiner Text gilt als Eingabe.
                shell.write(session.id, raw)
                continue

            kind = message.get("type")
            if kind == "input":
                if not shell.write(session.id, message.get("data", "")):
                    break
            elif kind == "resize":
                shell.resize(session.id, message.get("rows", 24), message.get("cols", 80))
            elif kind == "ping":
                await websocket.send_json({"type": "pong"})
            elif kind == "close":
                break
    except WebSocketDisconnect:
        logger.info("Terminal-Verbindung von %s getrennt", user.username)
    except Exception as exc:                                        # defensiv
        logger.warning("Terminal-Sitzung %s abgebrochen: %s", session.id, exc)
    finally:
        output_task.cancel()
        shell.close_session(session.id, reason="Verbindung beendet")
