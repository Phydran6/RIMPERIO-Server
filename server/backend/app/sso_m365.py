# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Anmeldung ueber Microsoft 365 automatisch einrichten:
#               App-Registrierung per Admin-Anmeldung anlegen lassen
# ==============================================================================
"""
Microsoft-365-Anmeldung ohne Klicken im Entra-Portal.

Gleiches Verfahren wie beim Mailversand (mail_m365.py): Der Administrator
meldet sich per Geraetecode bei Microsoft an - ein Fenster mit der
Microsoft-Anmeldung geht auf, Code eingeben, Konto waehlen, zustimmen. Mit
dem kurzlebigen Token legt RIMPERIO im Mandanten an:

* die App-Registrierung "RIMPERIO Anmeldung" mit der Rueckadresse dieses
  Servers, Gruppen im ID-Token und der App-Rolle ``RIMPERIO.Admin``,
* den Dienstprinzipal,
* die Admin-Zustimmung fuer ``openid profile email User.Read`` (fuer alle
  Benutzer - niemand muss beim ersten Anmelden einzeln zustimmen),
* die Rolle ``RIMPERIO.Admin`` fuer den Administrator, der gerade einrichtet -
  sonst wuerde er sich nach der Einrichtung als normaler Benutzer anmelden,
* ein Client-Geheimnis.

Gespeichert wird danach nur Mandant, App-ID und Geheimnis; das Admin-Token
wird verworfen. Eine fruehere, von RIMPERIO angelegte Anmelde-App im selben
Mandanten wird geloescht.

Noetig: ein Konto mit der Rolle Globaler Administrator (oder
Anwendungsadministrator + Administrator fuer privilegierte Rollen). Wer das
nicht will oder darf, traegt die App-Registrierung weiterhin von Hand ein.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx
from jose import jwt

from .mail_m365 import CLI_CLIENT_ID, GRAPH_APP_ID, LOGIN, GraphError, _graph, _json, _retry

logger = logging.getLogger("rimperio.sso.m365")

SETUP_SCOPES = ("https://graph.microsoft.com/Application.ReadWrite.All "
                "https://graph.microsoft.com/AppRoleAssignment.ReadWrite.All "
                "https://graph.microsoft.com/DelegatedPermissionGrant.ReadWrite.All "
                "https://graph.microsoft.com/User.Read")

# Delegierte Rechte, die die Anmelde-App braucht. Die IDs werden aus dem
# Graph-Dienstprinzipal des Mandanten gelesen; das hier ist nur der Rueckfall.
SIGNIN_SCOPES = {
    "openid": "37f7f235-527c-4136-accd-4a02d197296e",
    "profile": "14dad69e-099b-42c9-810b-d002981feec1",
    "email": "64a6cdd6-aab1-4aaf-94b8-3cc8405e90d0",
    "User.Read": "e1fe6dd8-ba31-4d61-89e7-88639da4683d",
}
ADMIN_ROLE_VALUE = "RIMPERIO.Admin"
APP_NAME = "RIMPERIO Anmeldung"
FLOW_TTL_SECONDS = 1200

STEPS = [
    ("login", "Bei Microsoft angemeldet"),
    ("app", "App-Registrierung anlegen"),
    ("consent", "Admin-Zustimmung für die Anmeldung erteilen"),
    ("role", "Eigenes Konto als RIMPERIO-Administrator eintragen"),
    ("secret", "Client-Geheimnis erzeugen"),
    ("save", "Einstellung speichern und Anmeldung einschalten"),
    ("cleanup", "Frühere RIMPERIO-Anmelde-App entfernen"),
]

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
        "flow": flow["id"], "status": flow["status"], "user_code": flow.get("user_code"),
        "verification_uri": flow.get("verification_uri"), "expires_at": flow.get("expires_at"),
        "steps": flow["steps"], "error": flow.get("error"), "result": flow.get("result"),
    }


def _step(flow: dict, step_id: str, status: str, detail: str = "") -> None:
    for step in flow["steps"]:
        if step["id"] == step_id:
            step["status"] = status
            if detail:
                step["detail"] = detail


def check_redirect(redirect_uri: str) -> Optional[str]:
    """Entra nimmt fuer Web-Apps nur https:// (oder http://localhost) an."""
    parsed = urlparse(redirect_uri or "")
    if not parsed.scheme or not parsed.hostname:
        return "Die Rückadresse dieses Servers ist unbekannt. Bitte unter Einstellungen → HTTPS die Adresse setzen."
    if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
        return (f"Microsoft akzeptiert als Rückadresse nur https:// — dieser Server ist unter "
                f"{parsed.scheme}://{parsed.hostname} erreichbar. Bitte zuerst HTTPS einrichten "
                f"(Einstellungen → HTTPS) und die Seite über https:// öffnen.")
    return None


async def start(redirect_uri: str, admin_username: str) -> dict:
    problem = check_redirect(redirect_uri)
    if problem:
        raise ValueError(problem)
    _expire()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{LOGIN}/organizations/oauth2/v2.0/devicecode",
                                     data={"client_id": CLI_CLIENT_ID, "scope": SETUP_SCOPES})
    data = _json(response)
    if response.status_code != 200 or "device_code" not in data:
        raise RuntimeError("Microsoft hat keinen Anmeldecode ausgegeben: "
                           f"{data.get('error_description') or data.get('error') or response.status_code}")
    flow_id = secrets.token_urlsafe(16)
    now = time.time()
    _flows[flow_id] = {
        "id": flow_id, "created": now, "status": "waiting",
        "device_code": data["device_code"], "user_code": data["user_code"],
        "verification_uri": data.get("verification_uri") or "https://microsoft.com/devicelogin",
        "expires_at": now + int(data.get("expires_in", 900)),
        "interval": max(int(data.get("interval", 5)), 3), "next_poll": now + 3,
        "redirect_uri": redirect_uri, "admin": admin_username,
        "steps": [{"id": sid, "label": label, "status": "pending", "detail": ""} for sid, label in STEPS],
        "error": None, "result": None, "task": None,
    }
    return _public(_flows[flow_id])


async def status(flow_id: str) -> dict:
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
            "client_id": CLI_CLIENT_ID, "device_code": flow["device_code"]})
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
        text = data.get("error_description") or error or f"HTTP {response.status_code}"
        if "AADSTS65001" in text or "consent" in text.lower():
            text = ("Microsoft verlangt eine Zustimmung, die dieses Konto nicht geben darf. Bitte mit "
                    "einem Globalen Administrator anmelden und „Im Namen Ihrer Organisation "
                    "zustimmen“ anhaken. " + text)
        elif "AADSTS53003" in text or "AADSTS50105" in text:
            text = ("Eine Richtlinie für bedingten Zugriff blockiert die Anmeldung per Gerätecode. "
                    "Richtlinie kurz ausnehmen oder die App-Registrierung von Hand eintragen. " + text)
        flow["error"] = f"Anmeldung bei Microsoft fehlgeschlagen: {text}"


async def _provision(flow: dict, admin_token: str) -> None:
    from . import sso
    from .database import async_session

    current_step = "login"
    created_app: Optional[dict] = None
    headers = {"Authorization": f"Bearer {admin_token}"}
    try:
        tenant_id = jwt.get_unverified_claims(admin_token).get("tid", "")
        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            me = await _graph(client, "GET", "/me?$select=id,displayName,userPrincipalName,mail")
            admin_upn = me.get("userPrincipalName") or me.get("mail") or "?"
            _step(flow, "login", "ok", f"{me.get('displayName') or admin_upn} ({admin_upn})")
            if not tenant_id:
                org = await _graph(client, "GET", "/organization?$select=id")
                tenant_id = (org.get("value") or [{}])[0].get("id", "")

            async with async_session() as db:
                old = await sso.load_config(db)

            # --- App-Registrierung --------------------------------------------
            current_step = "app"
            _step(flow, "app", "running")
            graph_sp = await _graph(client, "GET",
                                    f"/servicePrincipals?$filter=appId eq '{GRAPH_APP_ID}'"
                                    f"&$select=id,oauth2PermissionScopes")
            graph_sp = (graph_sp.get("value") or [None])[0]
            if not graph_sp:
                raise RuntimeError("Der Dienstprinzipal von Microsoft Graph fehlt im Mandanten.")
            known = {s.get("value"): s.get("id") for s in graph_sp.get("oauth2PermissionScopes") or []}
            scope_ids = {name: known.get(name) or fallback for name, fallback in SIGNIN_SCOPES.items()}

            role_id = str(uuid.uuid4())
            host = urlparse(flow["redirect_uri"]).hostname or ""
            name = f"{APP_NAME} ({host})" if host else APP_NAME
            created_app = await _graph(client, "POST", "/applications", json={
                "displayName": name,
                "signInAudience": "AzureADMyOrg",
                "notes": (f"Von RIMPERIO angelegt am {datetime.now(timezone.utc):%Y-%m-%d} durch "
                          f"{admin_upn}. Anmeldung an RIMPERIO mit dem Firmenkonto. Löschen "
                          f"schaltet die Microsoft-365-Anmeldung ab."),
                "web": {"redirectUris": [flow["redirect_uri"]],
                        "implicitGrantSettings": {"enableIdTokenIssuance": False,
                                                  "enableAccessTokenIssuance": False}},
                "groupMembershipClaims": "SecurityGroup",
                "optionalClaims": {"idToken": [{"name": "email", "essential": False},
                                               {"name": "upn", "essential": False}]},
                "appRoles": [{
                    "id": role_id, "allowedMemberTypes": ["User"], "isEnabled": True,
                    "displayName": "RIMPERIO-Administrator", "value": ADMIN_ROLE_VALUE,
                    "description": "Meldet sich an RIMPERIO als Administrator an.",
                }],
                "requiredResourceAccess": [{
                    "resourceAppId": GRAPH_APP_ID,
                    "resourceAccess": [{"id": sid, "type": "Scope"} for sid in scope_ids.values()],
                }],
            })
            sp = await _retry(lambda: _graph(client, "POST", "/servicePrincipals",
                                             json={"appId": created_app["appId"]}))
            _step(flow, "app", "ok", f"{name} — App-ID {created_app['appId']}")

            # --- Zustimmung fuer alle Benutzer --------------------------------
            current_step = "consent"
            _step(flow, "consent", "running")
            await _retry(lambda: _graph(client, "POST", "/oauth2PermissionGrants", json={
                "clientId": sp["id"], "consentType": "AllPrincipals", "resourceId": graph_sp["id"],
                "scope": " ".join(SIGNIN_SCOPES)}))
            _step(flow, "consent", "ok", " ".join(SIGNIN_SCOPES))

            # --- Einrichtender Admin wird RIMPERIO-Admin -----------------------
            current_step = "role"
            _step(flow, "role", "running")
            try:
                await _retry(lambda: _graph(client, "POST", f"/servicePrincipals/{sp['id']}/appRoleAssignedTo",
                                            json={"principalId": me["id"], "resourceId": sp["id"],
                                                  "appRoleId": role_id}))
                _step(flow, "role", "ok", admin_upn)
            except GraphError as exc:
                _step(flow, "role", "warn", f"Nicht möglich ({exc}). Im Entra-Portal unter "
                                            f"Unternehmensanwendungen → {name} → Benutzer und Gruppen nachholen.")

            # --- Geheimnis ----------------------------------------------------
            current_step = "secret"
            _step(flow, "secret", "running")
            secret = None
            for months in (24, 12, 6, 3):
                end = datetime.now(timezone.utc) + timedelta(days=30 * months)
                try:
                    secret = await _graph(client, "POST", f"/applications/{created_app['id']}/addPassword",
                                          json={"passwordCredential": {
                                              "displayName": "RIMPERIO",
                                              "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ")}})
                    break
                except GraphError as exc:
                    if exc.status != 400:
                        raise
            if not secret:
                raise RuntimeError("Microsoft hat kein Geheimnis ausgegeben (auch nicht mit 3 Monaten).")
            expires = secret.get("endDateTime", "")
            _step(flow, "secret", "ok", f"gültig bis {expires[:10]}")

            # --- Speichern ----------------------------------------------------
            current_step = "save"
            _step(flow, "save", "running")
            async with async_session() as db:
                await sso.store_raw(db, {
                    "enabled": True, "provider": "entra",
                    "label": old.get("label") or "Mit Microsoft 365 anmelden",
                    "tenant_id": tenant_id, "client_id": created_app["appId"],
                    "client_secret": secret["secretText"], "redirect_uri": flow["redirect_uri"],
                    "admin_role_claim": ADMIN_ROLE_VALUE, "setup_mode": "auto",
                    "app_object_id": created_app["id"], "secret_expires": expires,
                    "setup_by": f"{flow['admin']} / {admin_upn}",
                    "setup_at": datetime.now(timezone.utc).isoformat(),
                })
            new_app = created_app
            created_app = None
            _step(flow, "save", "ok", "Anmeldung mit Microsoft 365 ist eingeschaltet")

            # --- Alte App -----------------------------------------------------
            current_step = "cleanup"
            old_id = old.get("app_object_id")
            if old_id and old.get("tenant_id") == tenant_id and old_id != new_app["id"]:
                try:
                    await _graph(client, "DELETE", f"/applications/{old_id}")
                    _step(flow, "cleanup", "ok", "frühere RIMPERIO-Anmelde-App gelöscht")
                except GraphError as exc:
                    _step(flow, "cleanup", "warn", f"Konnte die frühere App nicht löschen ({exc}).")
            else:
                _step(flow, "cleanup", "ok", "keine vorhanden")

        flow["status"] = "done"
        flow["result"] = {"tenant_id": tenant_id, "client_id": new_app["appId"], "app_name": name,
                          "secret_expires": expires, "admin": admin_upn}
        logger.warning("Microsoft-365-Anmeldung automatisch eingerichtet von %s (Mandant %s)",
                       flow["admin"], tenant_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        flow["status"] = "error"
        denied = isinstance(exc, GraphError) and exc.status == 403
        if denied and current_step in ("app", "consent"):
            flow["error"] = ("Dieses Konto hat nicht genug Rechte in Entra ID. Nötig ist Globaler "
                             "Administrator (oder Anwendungsadministrator + Administrator für "
                             f"privilegierte Rollen). Microsoft meldet: {exc}")
        else:
            flow["error"] = f"Einrichtung abgebrochen ({current_step}): {exc}"
        _step(flow, current_step, "error", str(exc))
        logger.warning("SSO-Einrichtung fehlgeschlagen bei '%s': %s", current_step, exc)
        if created_app:
            try:
                async with httpx.AsyncClient(timeout=20, headers=headers) as client:
                    await _graph(client, "DELETE", f"/applications/{created_app['id']}")
            except Exception:                                       # defensiv
                pass
