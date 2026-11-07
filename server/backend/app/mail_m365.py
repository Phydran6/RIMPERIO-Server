# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Mailversand ueber Microsoft 365: Einrichtung per
#               Admin-Anmeldung, Versand ueber Microsoft Graph
# ==============================================================================
"""
Microsoft 365 ohne Klicken im Entra-Portal.

**Ablauf der Einrichtung**

1. Der Administrator meldet sich mit dem Geraetecode-Verfahren bei Microsoft an
   (https://microsoft.com/devicelogin). Das braucht keine Redirect-Adresse -
   wichtig, weil RIMPERIO oft nur im LAN oder hinter wechselnden Namen laeuft.
   Angemeldet wird ueber die oeffentliche Microsoft-App "Microsoft Graph
   Command Line Tools" - dieselbe, die ``Connect-MgGraph`` benutzt.
   Erbeten werden nur ``Application.ReadWrite.All`` (App anlegen) und
   ``AppRoleAssignment.ReadWrite.All`` (Zustimmung erteilen).
2. Mit diesem Token legt RIMPERIO im Mandanten an:
   * eine App-Registrierung "RIMPERIO Mailversand" mit dem Anwendungsrecht
     ``Mail.Send`` auf Microsoft Graph,
   * den dazugehoerigen Dienstprinzipal,
   * die Admin-Zustimmung fuer ``Mail.Send``,
   * ein Client-Geheimnis (24 Monate, kuerzer, falls eine Richtlinie das
     verlangt).
3. Das Token des Administrators wird danach verworfen - gespeichert wird nur,
   was die App selbst braucht (Mandant, App-ID, Geheimnis).
4. Eine fruehere, von RIMPERIO angelegte App im selben Mandanten wird
   geloescht, damit keine verwaisten Registrierungen mit Senderecht bleiben.

**Versand**: Client-Credentials-Token, dann ``POST /users/{absender}/sendMail``.
SMTP AUTH mit Passwort stellt Microsoft fuer Exchange Online ab - Graph ist der
Weg, der bleibt.

**Einschraenkung**: ``Mail.Send`` als Anwendungsrecht darf von *jedem* Postfach
des Mandanten senden. Begrenzen laesst sich das nur in Exchange Online
(RBAC fuer Anwendungen) - per PowerShell, nicht per Graph. Die Oberflaeche
zeigt den passenden Befehl an.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

import httpx
from jose import jwt

from .config import settings

logger = logging.getLogger("rimperio.mail.m365")

LOGIN = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"

# Oeffentliche Microsoft-App "Microsoft Graph Command Line Tools".
CLI_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
SETUP_SCOPES = ("https://graph.microsoft.com/Application.ReadWrite.All "
                "https://graph.microsoft.com/AppRoleAssignment.ReadWrite.All "
                "https://graph.microsoft.com/User.Read")

GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"
MAIL_SEND_ROLE_FALLBACK = "b633e1c5-b582-4048-a93e-9f11b44c7e96"

FLOW_TTL_SECONDS = 1200
APP_NAME = "RIMPERIO Mailversand"

STEPS = [
    ("login", "Bei Microsoft angemeldet"),
    ("app", "App-Registrierung anlegen"),
    ("consent", "Recht „Mail.Send“ erteilen (Admin-Zustimmung)"),
    ("secret", "Client-Geheimnis erzeugen"),
    ("save", "Einstellung speichern"),
    ("cleanup", "Alte RIMPERIO-App entfernen"),
    ("ready", "Warten, bis Microsoft die Berechtigung verteilt hat"),
]


class GraphError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        super().__init__(f"{code}: {message}" if code else message)


# =============================================================================
# Laufende Einrichtungen (nur im Speicher - ein Worker, kurze Lebensdauer)
# =============================================================================
_flows: Dict[str, Dict[str, Any]] = {}


def _expire() -> None:
    now = time.time()
    for key in [k for k, v in _flows.items() if v["created"] + FLOW_TTL_SECONDS < now]:
        task = _flows[key].get("task")
        if task and not task.done():
            continue
        _flows.pop(key, None)


def _public(flow: dict) -> dict:
    return {
        "flow": flow["id"],
        "status": flow["status"],
        "user_code": flow.get("user_code"),
        "verification_uri": flow.get("verification_uri"),
        "expires_at": flow.get("expires_at"),
        "steps": flow["steps"],
        "error": flow.get("error"),
        "result": flow.get("result"),
    }


def _step(flow: dict, step_id: str, status: str, detail: str = "") -> None:
    for step in flow["steps"]:
        if step["id"] == step_id:
            step["status"] = status
            if detail:
                step["detail"] = detail


async def start(from_address: str, from_name: str, admin_username: str) -> dict:
    """Fordert bei Microsoft einen Geraetecode an."""
    _expire()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{LOGIN}/organizations/oauth2/v2.0/devicecode",
            data={"client_id": CLI_CLIENT_ID, "scope": SETUP_SCOPES})
    data = _json(response)
    if response.status_code != 200 or "device_code" not in data:
        raise RuntimeError("Microsoft hat keinen Anmeldecode ausgegeben: "
                           f"{data.get('error_description') or data.get('error') or response.status_code}")

    flow_id = secrets.token_urlsafe(16)
    now = time.time()
    _flows[flow_id] = {
        "id": flow_id,
        "created": now,
        "status": "waiting",
        "device_code": data["device_code"],
        "user_code": data["user_code"],
        "verification_uri": data.get("verification_uri") or "https://microsoft.com/devicelogin",
        "expires_at": now + int(data.get("expires_in", 900)),
        "interval": max(int(data.get("interval", 5)), 3),
        "next_poll": now + 3,
        "from_address": (from_address or "").strip(),
        "from_name": (from_name or "").strip() or "RIMPERIO",
        "admin": admin_username,
        "steps": [{"id": sid, "label": label, "status": "pending", "detail": ""}
                  for sid, label in STEPS],
        "error": None,
        "result": None,
        "task": None,
    }
    return _public(_flows[flow_id])


async def status(flow_id: str) -> dict:
    """Fragt bei Microsoft nach, ob die Anmeldung durch ist (hoechstens im
    vorgegebenen Takt) und liefert den Fortschritt."""
    flow = _flows.get(flow_id)
    if not flow:
        raise KeyError("Diese Einrichtung ist abgelaufen. Bitte neu starten.")

    now = time.time()
    if flow["status"] == "waiting":
        if now > flow["expires_at"]:
            flow["status"] = "error"
            flow["error"] = "Der Anmeldecode ist abgelaufen. Bitte neu starten."
        elif now >= flow["next_poll"]:
            flow["next_poll"] = now + flow["interval"]
            await _poll_token(flow)
    return _public(flow)


def cancel(flow_id: str) -> None:
    flow = _flows.pop(flow_id, None)
    if flow and flow.get("task") and not flow["task"].done():
        flow["task"].cancel()


async def _poll_token(flow: dict) -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{LOGIN}/organizations/oauth2/v2.0/token", data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": CLI_CLIENT_ID,
            "device_code": flow["device_code"],
        })
    data = _json(response)
    if response.status_code == 200 and data.get("access_token"):
        flow["status"] = "working"
        flow["task"] = asyncio.create_task(_provision(flow, data["access_token"]))
        return

    error = data.get("error", "")
    if error == "authorization_pending":
        return
    if error == "slow_down":
        flow["interval"] += 5
        return
    flow["status"] = "error"
    if error == "authorization_declined":
        flow["error"] = "Die Anmeldung wurde bei Microsoft abgebrochen."
    elif error == "expired_token":
        flow["error"] = "Der Anmeldecode ist abgelaufen. Bitte neu starten."
    else:
        flow["error"] = _explain_login(data.get("error_description") or error
                                       or f"HTTP {response.status_code}")


def _explain_login(text: str) -> str:
    if "AADSTS65001" in text or "consent" in text.lower():
        return ("Microsoft verlangt eine Zustimmung, die dieses Konto nicht geben darf. "
                "Bitte mit einem Globalen Administrator anmelden und bei der Abfrage "
                "„Im Namen Ihrer Organisation zustimmen“ anhaken. " + text)
    if "AADSTS53003" in text or "AADSTS50105" in text:
        return ("Eine Richtlinie für bedingten Zugriff blockiert die Anmeldung per "
                "Gerätecode. Entweder die Richtlinie kurz ausnehmen oder die "
                "App-Registrierung von Hand anlegen (unten: „Vorhandene "
                "App-Registrierung eintragen“). " + text)
    return f"Anmeldung bei Microsoft fehlgeschlagen: {text}"


# =============================================================================
# Einrichtung im Mandanten
# =============================================================================
async def _provision(flow: dict, admin_token: str) -> None:
    from . import alerts, mail
    from .database import async_session

    current_step = "login"
    created_app: Optional[dict] = None
    try:
        claims = jwt.get_unverified_claims(admin_token)
        tenant_id = claims.get("tid", "")
        headers = {"Authorization": f"Bearer {admin_token}"}
        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            me = await _graph(client, "GET", "/me?$select=displayName,userPrincipalName,mail")
            admin_upn = me.get("userPrincipalName") or me.get("mail") or "?"
            _step(flow, "login", "ok", f"{me.get('displayName') or admin_upn} ({admin_upn})")
            from_address = flow["from_address"] or me.get("mail") or admin_upn
            if not tenant_id:
                org = await _graph(client, "GET", "/organization?$select=id")
                tenant_id = (org.get("value") or [{}])[0].get("id", "")

            async with async_session() as db:
                old = await mail.load_config(db)

            # --- App-Registrierung --------------------------------------------
            current_step = "app"
            _step(flow, "app", "running")
            graph_sp = await _graph(
                client, "GET",
                f"/servicePrincipals?$filter=appId eq '{GRAPH_APP_ID}'&$select=id,appRoles")
            graph_sp = (graph_sp.get("value") or [None])[0]
            if not graph_sp:
                raise RuntimeError("Der Dienstprinzipal von Microsoft Graph wurde im "
                                   "Mandanten nicht gefunden.")
            role_id = next((role["id"] for role in graph_sp.get("appRoles", [])
                            if role.get("value") == "Mail.Send"), MAIL_SEND_ROLE_FALLBACK)

            name = APP_NAME
            host = urlparse(settings.site_url or "").hostname
            if host:
                name = f"{APP_NAME} ({host})"
            created_app = await _graph(client, "POST", "/applications", json={
                "displayName": name,
                "signInAudience": "AzureADMyOrg",
                "notes": (f"Von RIMPERIO angelegt am {datetime.now(timezone.utc):%Y-%m-%d} "
                          f"durch {admin_upn}. Verschickt Benachrichtigungen als "
                          f"{from_address}. Löschen beendet den Mailversand."),
                "requiredResourceAccess": [{
                    "resourceAppId": GRAPH_APP_ID,
                    "resourceAccess": [{"id": role_id, "type": "Role"}],
                }],
            })
            sp = await _retry(lambda: _graph(client, "POST", "/servicePrincipals",
                                             json={"appId": created_app["appId"]}))
            _step(flow, "app", "ok", f"{name} — App-ID {created_app['appId']}")

            # --- Zustimmung ---------------------------------------------------
            current_step = "consent"
            _step(flow, "consent", "running")
            await _retry(lambda: _graph(
                client, "POST", f"/servicePrincipals/{graph_sp['id']}/appRoleAssignedTo",
                json={"principalId": sp["id"], "resourceId": graph_sp["id"],
                      "appRoleId": role_id}))
            _step(flow, "consent", "ok")

            # --- Geheimnis ----------------------------------------------------
            current_step = "secret"
            _step(flow, "secret", "running")
            secret = None
            for months in (24, 12, 6, 3):
                end = datetime.now(timezone.utc) + timedelta(days=30 * months)
                try:
                    secret = await _graph(
                        client, "POST", f"/applications/{created_app['id']}/addPassword",
                        json={"passwordCredential": {
                            "displayName": "RIMPERIO",
                            "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ")}})
                    break
                except GraphError as exc:
                    # Eine Richtlinie kann die Laufzeit begrenzen - dann kuerzer.
                    if exc.status != 400:
                        raise
            if not secret:
                raise RuntimeError("Microsoft hat kein Geheimnis ausgegeben (auch nicht "
                                   "mit 3 Monaten Laufzeit).")
            expires = secret.get("endDateTime", "")
            _step(flow, "secret", "ok", f"gültig bis {expires[:10]}")

            # --- Speichern ----------------------------------------------------
            current_step = "save"
            _step(flow, "save", "running")
            async with async_session() as db:
                await mail.save_config(db, {
                    "mode": "m365",
                    "provider": "m365",
                    "from_address": from_address,
                    "from_name": flow["from_name"],
                    "m365_tenant_id": tenant_id,
                    "m365_client_id": created_app["appId"],
                    "m365_client_secret": secret["secretText"],
                    "m365_app_object_id": created_app["id"],
                    "m365_secret_expires": expires,
                    "m365_setup_by": f"{flow['admin']} / {admin_upn}",
                    "m365_setup_at": datetime.now(timezone.utc).isoformat(),
                })
                try:
                    await alerts.refresh(db)
                except Exception:                                   # defensiv
                    pass
            new_app = created_app
            created_app = None      # ab hier nicht mehr aufraeumen
            _step(flow, "save", "ok", f"Absender: {from_address}")

            # --- Alte App -----------------------------------------------------
            current_step = "cleanup"
            old_id = old.get("m365_app_object_id")
            if old_id and old.get("m365_tenant_id") == tenant_id and old_id != new_app["id"]:
                try:
                    await _graph(client, "DELETE", f"/applications/{old_id}")
                    _step(flow, "cleanup", "ok", "frühere RIMPERIO-App gelöscht")
                except GraphError as exc:
                    _step(flow, "cleanup", "warn",
                          f"Konnte die frühere App nicht löschen ({exc}). Bitte im "
                          "Entra-Portal unter App-Registrierungen entfernen.")
            else:
                _step(flow, "cleanup", "ok", "keine vorhanden")

        # --- Warten auf Verteilung -------------------------------------------
        current_step = "ready"
        _step(flow, "ready", "running")
        ready = False
        cfg = {"m365_tenant_id": tenant_id, "m365_client_id": new_app["appId"],
               "m365_client_secret": secret["secretText"]}
        deadline = time.time() + 150
        while time.time() < deadline:
            try:
                token = await _app_token(cfg, force=True)
                if "Mail.Send" in (jwt.get_unverified_claims(token).get("roles") or []):
                    ready = True
                    break
            except Exception:                                       # noch nicht so weit
                pass
            await asyncio.sleep(6)
        if ready:
            _step(flow, "ready", "ok")
        else:
            _step(flow, "ready", "warn",
                  "Microsoft verteilt die Berechtigung noch. In ein, zwei Minuten "
                  "sollte die Probemail durchgehen.")

        flow["status"] = "done"
        flow["result"] = {"from_address": from_address, "tenant_id": tenant_id,
                          "client_id": new_app["appId"], "secret_expires": expires,
                          "admin": admin_upn, "app_name": name}
        logger.warning("Microsoft-365-Mailversand eingerichtet von %s (Mandant %s)",
                       flow["admin"], tenant_id)

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        flow["status"] = "error"
        flow["error"] = _explain_setup(current_step, exc)
        _step(flow, current_step, "error", str(exc))
        logger.warning("M365-Einrichtung fehlgeschlagen bei '%s': %s", current_step, exc)
        if created_app:
            # Halb angelegte App wieder wegraeumen - sonst bleibt eine Leiche
            # im Mandanten liegen.
            try:
                async with httpx.AsyncClient(
                        timeout=20, headers={"Authorization": f"Bearer {admin_token}"}) as client:
                    await _graph(client, "DELETE", f"/applications/{created_app['id']}")
            except Exception:                                       # defensiv
                pass


def _explain_setup(step: str, exc: Exception) -> str:
    denied = isinstance(exc, GraphError) and exc.status == 403
    if denied and step == "app":
        return ("Dieses Konto darf keine App-Registrierungen anlegen. Nötig ist die "
                "Rolle Globaler Administrator (oder Anwendungsadministrator plus "
                f"Administrator für privilegierte Rollen). Microsoft meldet: {exc}")
    if denied and step == "consent":
        return ("Die Admin-Zustimmung für Mail.Send darf nur ein Globaler Administrator "
                "oder ein Administrator für privilegierte Rollen erteilen. Die halb "
                f"angelegte App wurde wieder entfernt. Microsoft meldet: {exc}")
    return f"Einrichtung abgebrochen ({step}): {exc}"


async def _graph(client: httpx.AsyncClient, method: str, path: str,
                 json: Optional[dict] = None) -> dict:
    response = await client.request(method, GRAPH + path, json=json)
    if response.status_code >= 400:
        error = _json(response).get("error") or {}
        raise GraphError(response.status_code, error.get("code", ""),
                         error.get("message") or response.text[:300])
    return _json(response) if response.content else {}


async def _retry(call, attempts: int = 15, delay: float = 4.0):
    """Frisch angelegte Objekte sind in Entra nicht sofort ueberall sichtbar -
    400/404 direkt danach ist normal und verschwindet nach Sekunden."""
    for attempt in range(attempts):
        try:
            return await call()
        except GraphError as exc:
            if exc.status not in (400, 404) or attempt == attempts - 1:
                raise
            await asyncio.sleep(delay)


def _json(response: httpx.Response) -> dict:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


# =============================================================================
# Versand
# =============================================================================
_tokens: Dict[str, tuple] = {}


async def _app_token(config: dict, force: bool = False) -> str:
    tenant = config.get("m365_tenant_id", "")
    client_id = config.get("m365_client_id", "")
    secret = config.get("m365_client_secret", "")
    if not (tenant and client_id and secret):
        raise ValueError("Microsoft 365 ist nicht vollständig eingerichtet "
                         "(Mandant, App-ID oder Geheimnis fehlt).")
    key = f"{tenant}/{client_id}/{hash(secret)}"
    cached = _tokens.get(key)
    if cached and not force and cached[1] > time.time() + 120:
        return cached[0]

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{LOGIN}/{tenant}/oauth2/v2.0/token", data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": secret,
            "scope": "https://graph.microsoft.com/.default",
        })
    data = _json(response)
    if response.status_code != 200 or not data.get("access_token"):
        text = data.get("error_description") or data.get("error") or f"HTTP {response.status_code}"
        if "AADSTS7000222" in text:
            raise RuntimeError("Das Client-Geheimnis ist abgelaufen. Den Assistenten "
                               "„Microsoft 365“ erneut durchlaufen.")
        if "AADSTS7000215" in text:
            raise RuntimeError("Microsoft lehnt das Client-Geheimnis ab. Ist es gerade "
                               "erst angelegt, eine Minute warten. " + text.splitlines()[0])
        raise RuntimeError(f"Anmeldung der App bei Microsoft fehlgeschlagen: {text.splitlines()[0]}")
    _tokens[key] = (data["access_token"], time.time() + int(data.get("expires_in", 3600)))
    return data["access_token"]


async def verify(config: dict) -> dict:
    """Holt ein Token und prueft, ob Mail.Send darin steht."""
    token = await _app_token(config, force=True)
    roles = jwt.get_unverified_claims(token).get("roles") or []
    if "Mail.Send" not in roles:
        raise RuntimeError("Die App darf noch keine Mails senden — das Anwendungsrecht "
                           "Mail.Send fehlt oder hat keine Admin-Zustimmung. Direkt nach "
                           "der Zustimmung kann das ein, zwei Minuten dauern.")
    return {"ok": True, "detail": "Anmeldung bei Microsoft klappt, Mail.Send ist erteilt."}


async def send(config: dict, recipients: List[str], subject: str, body: str) -> dict:
    sender = config.get("from_address", "")
    token = await _app_token(config)
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "from": {"emailAddress": {"address": sender,
                                      "name": config.get("from_name") or "RIMPERIO"}},
            "toRecipients": [{"emailAddress": {"address": address}} for address in recipients],
        },
        "saveToSentItems": False,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(f"{GRAPH}/users/{quote(sender, safe='@')}/sendMail", json=payload,
                                     headers={"Authorization": f"Bearer {token}"})
    if response.status_code != 202:
        error = _json(response).get("error") or {}
        code = error.get("code", "")
        text = error.get("message") or response.text[:300]
        if response.status_code in (401, 403) or code == "ErrorAccessDenied":
            raise RuntimeError("Microsoft verweigert den Versand: die App hat (noch) kein "
                               "Recht Mail.Send oder darf dieses Postfach nicht nutzen. "
                               f"{code}: {text}")
        if response.status_code == 404 or code in ("ErrorInvalidUser", "MailboxNotEnabledForRESTAPI",
                                                   "ResourceNotFound"):
            raise RuntimeError(f"Das Absenderpostfach {sender} gibt es nicht oder es hat "
                               f"keine Exchange-Online-Lizenz. {code}: {text}")
        raise RuntimeError(f"Microsoft hat die Mail abgelehnt: {code}: {text}")

    return {
        "sent": True,
        "recipients": recipients,
        "refused": {},
        "via": "Microsoft 365 (Graph)",
        "at": datetime.utcnow().isoformat(),
    }


def restrict_command(config: dict) -> str:
    """PowerShell, um die App auf das Absenderpostfach zu begrenzen."""
    client_id = config.get("m365_client_id") or "<App-ID>"
    sender = config.get("from_address") or "<absender@firma.de>"
    return (
        "Connect-ExchangeOnline\n"
        f"New-ApplicationAccessPolicy -AppId {client_id} -PolicyScopeGroupId {sender} "
        "-AccessRight RestrictAccess -Description \"RIMPERIO darf nur von diesem Postfach senden\"\n"
        f"Test-ApplicationAccessPolicy -AppId {client_id} -Identity {sender}"
    )
