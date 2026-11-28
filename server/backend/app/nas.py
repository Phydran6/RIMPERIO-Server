# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - NAS per HTTPS: Synology DSM (voll), TrueNAS (Basis),
#               Erkennung, Systemzustand, Firmware- und Paket-Updates, SNMP
# ==============================================================================
"""
NAS-Systeme ohne Agent, nur ueber ihre Web-API.

Ein NAS laesst selten fremde Software zu - jedes NAS spricht aber HTTPS. Genau
darueber laeuft hier alles: dieselben Aufrufe, die auch die Weboberflaeche des
NAS benutzt.

Synology DSM (Schwerpunkt)
--------------------------
* **Erkennung** ohne Anmeldung: ``/webapi/query.cgi?api=SYNO.API.Info`` - das
  beantwortet nur DSM. Die Netzwerksuche fragt so jedes System mit Port
  5000/5001 und schreibt "Synology DSM" an den Eintrag.
* **Anmeldung** mit einem Benutzer der Gruppe *administrators*. Ist fuer ihn
  Zwei-Faktor (TOTP) aktiv, fragt RIMPERIO beim Einrichten einmal den Code ab
  und laesst sich von DSM ein **Geraete-Token** geben ("diesem Geraet
  vertrauen"). Spaetere Anmeldungen brauchen dann keinen Code mehr. Wer das
  TOTP-Geheimnis hinterlegt (in Vaultwarden als TOTP des Eintrags), dem
  erzeugt RIMPERIO den Code notfalls selbst.
* **Zustand**: Modell, DSM-Version, Laufzeit, Temperatur, CPU/RAM,
  Speicherpools, Volumes, Platten (S.M.A.R.T., Temperatur), Pakete,
  verfuegbare Updates, SNMP, Auto-Update-Einstellungen, Hyper-Backup-Aufgaben.
* **Updates**: Paket-Updates (Download -> Upgrade) und DSM-Updates (Download ->
  Installation -> Neustart -> warten -> Version pruefen); von Hand oder im
  Wartungsfenster (``automation``).
* **SNMP**: Beim Einrichten wird SNMP v2c mit einer zufaelligen Community
  eingeschaltet, von RIMPERIO aus geprueft und das NAS unter SNMP-Geraete
  angelegt (Vorlagen System, Schnittstellen, Host, Synology, Last).

Die DSM-Web-API ist nicht vollstaendig dokumentiert. Jeder Schritt schreibt
deshalb ins Protokoll des Vorgangs, was er gefragt und was DSM geantwortet
hat - faellt ein Aufruf in einer neuen DSM-Version weg, steht dort genau,
welcher.

TrueNAS
-------
REST-API v2.0 mit API-Schluessel: Systeminfo, Pools, Alarme, Update pruefen
und einspielen.

Andere NAS / Geraete mit Weboberflaeche
---------------------------------------
Nur Erreichbarkeit und Ablauf des Zertifikats.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import socket
import ssl
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import journal, vault
from .models import (Agent, DiscoveredDevice, NasDevice, NasJob, SnmpCredential, SnmpDevice)

logger = logging.getLogger("rimperio.nas")

VENDOR_LABELS = {"synology": "Synology DSM", "truenas": "TrueNAS", "qnap": "QNAP QTS", "generic": "Weboberfläche"}
PROBE_PORTS = ((5001, "https"), (5000, "http"), (443, "https"), (80, "http"))
DEFAULT_AUTOMATION: Dict[str, Any] = {
    "dsm": "notify",            # off | notify | auto
    "packages": "notify",       # off | notify | auto
    "dsm_major": False,         # Hauptversionen (7.2 -> 8.0) nie automatisch
    "days": [6],                # 0 = Montag … 6 = Sonntag
    "start": "03:00",
    "duration_min": 180,
    "tz": "Europe/Berlin",
    "exclude": [],              # Paket-IDs, die nie automatisch aktualisiert werden
    "backup_config_first": True,
}
UPDATE_CHECK_HOURS = 12

# Raspberry-Pi-Hersteller-Kennungen (MAC-OUI): Raspberry Pi Foundation / Trading.
PI_OUIS = ("b8:27:eb", "dc:a6:32", "e4:5f:01", "28:cd:c1", "d8:3a:dd", "2c:cf:67", "88:a2:9e")


class NasError(Exception):
    """Das NAS hat abgelehnt oder ist nicht erreichbar."""

    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


class NasNeedsOtp(NasError):
    """DSM verlangt einen Zwei-Faktor-Code."""


def is_raspberry_mac(mac: Optional[str]) -> bool:
    return bool(mac) and mac.lower()[:8] in PI_OUIS


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def base_url(scheme: str, host: str, port: Optional[int]) -> str:
    h = host.strip()
    if ":" in h and not h.startswith("["):
        h = f"[{h}]"
    default = 443 if scheme == "https" else 80
    return f"{scheme}://{h}" + (f":{port}" if port and port != default else "")


def _net_error(where: str, exc: Exception) -> NasError:
    text = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return NasError(f"{where}: Zertifikat nicht vertrauenswürdig – „Zertifikat prüfen“ ausschalten "
                        "oder in DSM ein gültiges Zertifikat einrichten.")
    if isinstance(exc, httpx.ConnectTimeout):
        return NasError(f"{where}: keine Antwort (Firewall? falsche IP?)")
    if isinstance(exc, httpx.TimeoutException):
        return NasError(f"{where}: Zeitüberschreitung")
    if isinstance(exc, httpx.ConnectError):
        return NasError(f"{where}: keine Verbindung – stimmen IP und Port (Synology: 5001 für HTTPS)?")
    return NasError(f"{where}: {text or exc.__class__.__name__}")


# =============================================================================
# Erkennung (ohne Anmeldung)
# =============================================================================
async def probe(host: str, port: Optional[int] = None, scheme: Optional[str] = None,
                timeout: float = 4.0) -> Optional[dict]:
    """Was antwortet unter dieser Adresse? None = kein bekanntes NAS."""
    candidates = [(port, scheme or ("https" if port in (5001, 443, 8443) else "http"))] if port else list(PROBE_PORTS)
    async with httpx.AsyncClient(verify=False, timeout=timeout, follow_redirects=False) as c:
        for p, sch in candidates:
            base = base_url(sch, host, p)
            try:
                r = await c.get(base + "/webapi/query.cgi", params={
                    "api": "SYNO.API.Info", "version": "1", "method": "query",
                    "query": "SYNO.API.Auth,SYNO.Core.System,SYNO.Core.Upgrade.Server"})
            except httpx.HTTPError:
                continue
            try:
                data = r.json()
            except ValueError:
                data = None
            if isinstance(data, dict) and data.get("success") and "SYNO.API.Auth" in (data.get("data") or {}):
                auth = data["data"]["SYNO.API.Auth"]
                return {"vendor": "synology", "label": "Synology DSM", "host": host, "port": p, "scheme": sch,
                        "url": base, "dsm7": int(auth.get("maxVersion", 0)) >= 7,
                        "auth_max": auth.get("maxVersion"), "tls": sch == "https"}
            # Kein DSM - vielleicht etwas anderes mit Weboberflaeche?
            try:
                root = await c.get(base + "/")
                text = root.text[:20000]
            except httpx.HTTPError:
                continue
            low = text.lower()
            if "truenas" in low or "freenas" in low:
                return {"vendor": "truenas", "label": "TrueNAS", "host": host, "port": p, "scheme": sch,
                        "url": base, "tls": sch == "https"}
            if "qnap" in low or "qts" in low and "cgi-bin" in low:
                return {"vendor": "qnap", "label": "QNAP", "host": host, "port": p, "scheme": sch,
                        "url": base, "tls": sch == "https"}
    return None


def cert_info(host: str, port: int, timeout: float = 4.0) -> Optional[dict]:
    """Ablauf und Aussteller des Zertifikats (ohne es zu pruefen)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
    except OSError:
        return None
    try:
        from cryptography import x509
        cert = x509.load_der_x509_certificate(der)
        not_after = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
        if not_after.tzinfo is not None:
            not_after = not_after.replace(tzinfo=None)
        issuer = cert.issuer.rfc4514_string()
        subject = cert.subject.rfc4514_string()
        return {"not_after": _iso(not_after), "days_left": (not_after - datetime.utcnow()).days,
                "issuer": issuer[:200], "subject": subject[:200], "self_signed": issuer == subject}
    except Exception:                                               # defensiv
        return None


# =============================================================================
# Synology DSM
# =============================================================================
DSM_ERRORS = {
    100: "unbekannter Fehler", 101: "Angabe fehlt oder ungültig", 102: "Schnittstelle gibt es nicht",
    103: "Methode gibt es nicht", 104: "Version wird nicht unterstützt", 105: "keine Berechtigung (Konto ist kein Administrator?)",
    106: "Sitzung abgelaufen", 107: "Sitzung durch zweite Anmeldung beendet", 114: "Angabe fehlt",
    117: "Funktion nur im Netz des NAS", 119: "Sitzung unbekannt", 120: "Angabe ungültig",
}
AUTH_ERRORS = {
    400: "Benutzer oder Passwort falsch", 401: "Konto deaktiviert", 402: "Anmeldung für dieses Konto nicht erlaubt",
    403: "Zwei-Faktor-Code nötig", 404: "Zwei-Faktor-Code falsch", 406: "DSM verlangt für dieses Konto Zwei-Faktor – bitte erst in DSM einrichten",
    407: "IP-Adresse gesperrt (zu viele Fehlversuche) – in DSM unter Sicherheit → Schutz entsperren",
    408: "Passwort abgelaufen", 409: "Passwort abgelaufen", 410: "Passwort muss geändert werden",
}


def _encode(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return str(v)


class Synology:
    """Eine Verbindung zu einem DSM. ``async with`` oder login()/logout()."""

    def __init__(self, base: str, verify: bool = False, timeout: float = 30.0) -> None:
        self.base = base.rstrip("/")
        self.verify = verify
        self.timeout = timeout
        self.apis: Dict[str, dict] = {}
        self.sid = ""
        self.synotoken = ""
        self.device_token = ""
        self.otp_used = False
        self.account = ""
        self._c: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "Synology":
        self._c = httpx.AsyncClient(verify=self.verify, timeout=self.timeout)
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            if self.sid:
                await self.logout()
        finally:
            if self._c:
                await self._c.aclose()

    async def info(self) -> Dict[str, dict]:
        assert self._c
        try:
            r = await self._c.get(self.base + "/webapi/query.cgi",
                                  params={"api": "SYNO.API.Info", "version": "1", "method": "query"})
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise _net_error(self.base, exc) from exc
        if not data.get("success"):
            raise NasError("Keine Synology-Schnittstelle unter dieser Adresse.")
        self.apis = data.get("data") or {}
        return self.apis

    def has(self, api: str) -> bool:
        return api in self.apis

    def _version(self, api: str, wanted: Optional[int]) -> int:
        spec = self.apis.get(api) or {}
        hi, lo = int(spec.get("maxVersion", 1)), int(spec.get("minVersion", 1))
        if wanted is None:
            return hi
        return max(lo, min(hi, int(wanted)))

    async def login(self, account: str, password: str, otp: str = "", device_token: str = "") -> dict:
        if not self.apis:
            await self.info()
        if "SYNO.API.Auth" not in self.apis:
            raise NasError("DSM meldet keine Anmelde-Schnittstelle.")
        ver = self._version("SYNO.API.Auth", 7)
        params = {"api": "SYNO.API.Auth", "version": str(ver), "method": "login", "account": account,
                  "passwd": password, "session": "RIMPERIO", "format": "sid", "enable_syno_token": "yes",
                  "device_name": "RIMPERIO"}
        if otp:
            params.update({"otp_code": otp.replace(" ", ""), "enable_device_token": "yes"})
        elif device_token:
            params["device_id"] = device_token
        path = (self.apis["SYNO.API.Auth"].get("path") or "entry.cgi")
        assert self._c
        try:
            r = await self._c.post(f"{self.base}/webapi/{path}", data=params)
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise _net_error(self.base, exc) from exc
        if not data.get("success"):
            code = int((data.get("error") or {}).get("code", 0) or 0)
            if code in (403, 406) or (code == 404 and not otp and device_token):
                raise NasNeedsOtp(AUTH_ERRORS.get(code, "Zwei-Faktor-Code nötig"), code)
            raise NasError(f"Anmeldung abgelehnt: {AUTH_ERRORS.get(code, DSM_ERRORS.get(code, f'Fehler {code}'))}", code)
        d = data.get("data") or {}
        self.sid = d.get("sid", "")
        self.synotoken = d.get("synotoken", "")
        self.device_token = d.get("did") or d.get("device_id") or device_token or ""
        self.otp_used = bool(otp or device_token)
        self.account = account
        return d

    async def logout(self) -> None:
        try:
            await self.call("SYNO.API.Auth", "logout", version=self._version("SYNO.API.Auth", 6),
                            session="RIMPERIO")
        except NasError:
            pass
        self.sid = ""

    async def call(self, api: str, method: str, version: Optional[int] = None,
                   raw: bool = False, timeout: Optional[float] = None, **params) -> Any:
        if api not in self.apis:
            raise NasError(f"{api} gibt es auf diesem DSM nicht.", 102)
        spec = self.apis[api]
        form = {"api": api, "version": str(self._version(api, version)), "method": method}
        form.update({k: _encode(v) for k, v in params.items() if v is not None})
        if self.sid:
            form["_sid"] = self.sid
        headers = {"X-SYNO-TOKEN": self.synotoken} if self.synotoken else {}
        assert self._c
        try:
            r = await self._c.post(f"{self.base}/webapi/{spec.get('path') or 'entry.cgi'}", data=form,
                                   headers=headers, timeout=timeout or self.timeout)
        except httpx.HTTPError as exc:
            raise _net_error(self.base, exc) from exc
        if raw:
            ctype = r.headers.get("content-type", "")
            if "json" in ctype:
                data = r.json()
                if not data.get("success", True):
                    code = int((data.get("error") or {}).get("code", 0) or 0)
                    raise NasError(f"{api}.{method}: {DSM_ERRORS.get(code, f'Fehler {code}')}", code)
            return r.content
        try:
            data = r.json()
        except ValueError as exc:
            raise NasError(f"{api}.{method}: keine lesbare Antwort (HTTP {r.status_code}).") from exc
        if not data.get("success"):
            code = int((data.get("error") or {}).get("code", 0) or 0)
            raise NasError(f"{api}.{method}: {DSM_ERRORS.get(code, f'Fehler {code}')}", code)
        return data.get("data") if data.get("data") is not None else {}

    async def try_call(self, api: str, method: str, **kw) -> Tuple[Any, Optional[str]]:
        try:
            return await self.call(api, method, **kw), None
        except NasError as exc:
            return None, str(exc)


async def synology_session(db: AsyncSession, nas: NasDevice, otp: str = "") -> Synology:
    """Angemeldete DSM-Verbindung (der Aufrufer schliesst mit __aexit__)."""
    if not nas.secret_id:
        raise NasError("Für dieses NAS ist keine Anmeldung hinterlegt.")
    cred = await vault.get(db, nas.secret_id)
    dsm = Synology(base_url(nas.scheme, nas.host, nas.port), verify=bool(nas.verify_tls))
    await dsm.__aenter__()
    try:
        token = (cred.get("fields") or {}).get("device_token", "")
        code = otp
        if not code and not token and cred.get("totp"):
            code = vault.totp_now(cred["totp"])
        try:
            await dsm.login(cred.get("username", ""), cred.get("password", ""), otp=code, device_token=token)
        except NasNeedsOtp:
            if not code and cred.get("totp"):
                await dsm.login(cred.get("username", ""), cred.get("password", ""),
                                otp=vault.totp_now(cred["totp"]))
            else:
                raise
        if dsm.device_token and dsm.device_token != token:
            await vault.set_field(db, nas.secret_id, "device_token", dsm.device_token)
        return dsm
    except BaseException:
        await dsm.__aexit__(None, None, None)
        raise


# -----------------------------------------------------------------------------
# Zustand lesen
# -----------------------------------------------------------------------------
def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _size(d: Any) -> Tuple[Optional[float], Optional[float]]:
    if not isinstance(d, dict):
        return None, None
    return _num(d.get("total")), _num(d.get("used"))


async def synology_state(dsm: Synology) -> dict:
    """Gesamtzustand. Fehlende Teile stehen in ``errors`` - der Rest kommt trotzdem."""
    st: Dict[str, Any] = {"vendor": "synology", "errors": {}, "read_at": _iso(datetime.utcnow())}

    async def part(key: str, api: str, method: str, **kw):
        if not dsm.has(api):
            return None
        data, err = await dsm.try_call(api, method, **kw)
        if err:
            st["errors"][key] = err
        return data

    sysinfo = await part("system", "SYNO.Core.System", "info", version=1) or {}
    dsminfo = await part("dsm", "SYNO.DSM.Info", "getinfo", version=2) or {}
    st["system"] = {
        "model": sysinfo.get("model") or dsminfo.get("model") or "",
        "serial": sysinfo.get("serial") or dsminfo.get("serial") or "",
        "firmware": sysinfo.get("firmware_ver") or dsminfo.get("version_string") or "",
        "firmware_date": sysinfo.get("firmware_date", ""),
        "uptime": sysinfo.get("up_time") or dsminfo.get("uptime") or "",
        "temperature": _num(sysinfo.get("sys_temp") or dsminfo.get("temperature")),
        "temperature_warning": bool(sysinfo.get("temperature_warning") or dsminfo.get("temperature_warn")),
        "ram_mb": _num(sysinfo.get("ram_size") or dsminfo.get("ram")),
        "cpu": " ".join(str(sysinfo.get(k, "")) for k in ("cpu_vendor", "cpu_family", "cpu_series")).strip(),
        "cpu_cores": sysinfo.get("cpu_cores"),
        "time": sysinfo.get("time", ""),
        "ntp": sysinfo.get("enabled_ntp"),
    }
    util = await part("utilization", "SYNO.Core.System.Utilization", "get", version=1) or {}
    cpu = util.get("cpu") or {}
    mem = util.get("memory") or {}
    st["load"] = {
        "cpu": sum(_num(cpu.get(k)) or 0 for k in ("user_load", "system_load", "other_load")) if cpu else None,
        "load1": _num(cpu.get("1min_load")),
        "mem": _num(mem.get("real_usage")),
        "mem_total_kb": _num(mem.get("total_real")),
        "net": [{"device": n.get("device"), "rx": _num(n.get("rx")), "tx": _num(n.get("tx"))}
                for n in (util.get("network") or []) if isinstance(n, dict)],
    }
    storage = await part("storage", "SYNO.Storage.CGI.Storage", "load_info", version=1) or {}
    st["disks"] = [{
        "id": d.get("id"), "name": d.get("name") or d.get("longName") or d.get("id"), "model": d.get("model", "").strip(),
        "vendor": (d.get("vendor") or "").strip(), "serial": d.get("serial", ""), "status": d.get("status", ""),
        "smart": d.get("smart_status", ""), "temp": _num(d.get("temp")), "size": _num(d.get("size_total")),
        "type": d.get("diskType", ""), "pool": d.get("used_by", ""), "remain_life": d.get("remain_life"),
    } for d in (storage.get("disks") or []) if isinstance(d, dict)]
    st["pools"] = []
    for p in storage.get("storagePools") or []:
        total, used = _size(p.get("size"))
        st["pools"].append({"id": p.get("id"), "name": p.get("display_name") or p.get("id"), "status": p.get("status", ""),
                            "raid": p.get("device_type") or p.get("raidType", ""), "total": total, "used": used})
    st["volumes"] = []
    for v in storage.get("volumes") or []:
        total, used = _size(v.get("size"))
        st["volumes"].append({"id": v.get("id"), "name": v.get("display_name") or v.get("vol_path") or v.get("id"),
                              "path": v.get("vol_path", ""), "status": v.get("status", ""), "fs": v.get("fs_type", ""),
                              "total": total, "used": used,
                              "percent": round(used / total * 100, 1) if total and used is not None else None})
    pk = await part("packages", "SYNO.Core.Package", "list", additional=["status"]) or {}
    st["packages"] = [{"id": p.get("id"), "name": p.get("name") or p.get("id"), "version": p.get("version", ""),
                       "status": ((p.get("additional") or {}).get("status") or "")}
                      for p in (pk.get("packages") or []) if isinstance(p, dict)]
    snmp_cfg = await part("snmp", "SYNO.Core.SNMP", "get") or {}
    st["snmp"] = {k: v for k, v in snmp_cfg.items() if "pass" not in k.lower()} if isinstance(snmp_cfg, dict) else {}
    st["snmp_enabled"] = _snmp_enabled(snmp_cfg)
    st["dsm_autoupdate"] = await part("upgrade_setting", "SYNO.Core.Upgrade.Setting", "get") or {}
    st["pkg_autoupdate"] = await part("package_setting", "SYNO.Core.Package.Setting", "get") or {}
    tasks = await part("hyperbackup", "SYNO.Backup.Task", "list", version=1,
                       additional=["last_bkp_time", "next_bkp_time", "last_bkp_result", "is_modified",
                                   "last_bkp_progress"]) or {}
    st["backup_tasks"] = [_hb_task(t) for t in (tasks.get("task_list") or tasks.get("tasks") or [])
                          if isinstance(t, dict)]
    return st


def _hb_task(t: dict) -> dict:
    add = t.get("additional") or {}
    return {"id": t.get("task_id"), "name": t.get("name", ""), "state": t.get("state", ""),
            "status": t.get("status", ""), "type": t.get("type", ""), "target": t.get("target_type") or t.get("repo_id"),
            "last_time": add.get("last_bkp_time") or t.get("last_bkp_time"),
            "last_result": add.get("last_bkp_result") or t.get("last_bkp_result"),
            "next_time": add.get("next_bkp_time") or t.get("next_bkp_time"),
            "progress": add.get("last_bkp_progress")}


def _snmp_enabled(cfg: Any) -> Optional[bool]:
    if not isinstance(cfg, dict) or not cfg:
        return None
    for k, v in cfg.items():
        kl = k.lower()
        if "enable" in kl and "v3" not in kl and isinstance(v, bool):
            if not v:
                return False
    flags = [v for k, v in cfg.items() if "enable" in k.lower() and isinstance(v, bool)]
    return any(flags) if flags else None


def evaluate(st: dict) -> Tuple[str, List[dict]]:
    """Status und Probleme aus dem Zustand."""
    problems: List[dict] = []

    def add(level: str, what: str, text: str) -> None:
        problems.append({"level": level, "what": what, "text": text})

    for d in st.get("disks") or []:
        s, smart = (d.get("status") or "").lower(), (d.get("smart") or "").lower()
        if s and s not in ("normal", "initialized", "not_use", "notuse", "hotspare"):
            add("critical" if s in ("crashed", "failing", "broken", "abnormal") else "warning",
                f"disk:{d.get('id')}", f"Platte {d.get('name')}: {d.get('status')}")
        if smart and smart not in ("normal", "safe", "ok", ""):
            add("critical" if smart in ("failing", "damage", "abnormal") else "warning",
                f"smart:{d.get('id')}", f"Platte {d.get('name')}: S.M.A.R.T. {d.get('smart')}")
        if d.get("temp") and d["temp"] >= 60:
            add("warning", f"disktemp:{d.get('id')}", f"Platte {d.get('name')}: {d['temp']:.0f} °C")
    for p in st.get("pools") or []:
        s = (p.get("status") or "").lower()
        if s and s != "normal":
            add("critical" if s in ("crashed", "degrade", "degraded") else "warning",
                f"pool:{p.get('id')}", f"Speicherpool {p.get('name')}: {p.get('status')}")
    for v in st.get("volumes") or []:
        s = (v.get("status") or "").lower()
        if s and s not in ("normal", "background"):
            add("critical" if s in ("crashed", "degrade", "degraded", "read_only") else "warning",
                f"vol:{v.get('id')}", f"Volume {v.get('name')}: {v.get('status')}")
        pct = v.get("percent")
        if pct is not None and pct >= 90:
            add("critical" if pct >= 95 else "warning", f"volfull:{v.get('id')}", f"Volume {v.get('name')} zu {pct:.0f} % belegt")
        elif pct is not None and pct >= 80:
            add("warning", f"volfull:{v.get('id')}", f"Volume {v.get('name')} zu {pct:.0f} % belegt")
    sysd = st.get("system") or {}
    if sysd.get("temperature_warning"):
        add("warning", "temp", f"Temperatur zu hoch ({sysd.get('temperature')} °C)")
    for t in st.get("backup_tasks") or []:
        res = (t.get("last_result") or "").lower()
        if res and res not in ("done", "success", "backingup", "none", "resuming", ""):
            add("warning", f"hb:{t.get('id')}", f"Hyper Backup „{t.get('name')}“: letzter Lauf {t.get('last_result')}")
    for key, err in (st.get("errors") or {}).items():
        if key in ("system", "storage"):
            add("warning", f"api:{key}", f"Nicht lesbar ({key}): {err}")
    level = "ok"
    if any(p["level"] == "critical" for p in problems):
        level = "critical"
    elif problems:
        level = "warning"
    return level, problems


# -----------------------------------------------------------------------------
# Updates
# -----------------------------------------------------------------------------
def version_key(v: str) -> Tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


async def synology_updates(dsm: Synology, installed: Optional[List[dict]] = None) -> dict:
    out: Dict[str, Any] = {"dsm": None, "packages": [], "errors": {}, "checked_at": _iso(datetime.utcnow())}
    if dsm.has("SYNO.Core.Upgrade.Server"):
        data, err = await dsm.try_call("SYNO.Core.Upgrade.Server", "check", user_reading=True,
                                       need_auto_smallupdate=True, need_promotion=False, timeout=60)
        if err:
            out["errors"]["dsm"] = err
        elif isinstance(data, dict):
            upd = data.get("update") or {}
            out["dsm"] = {"available": bool(upd.get("available")), "version": upd.get("version", ""),
                          "reboot": upd.get("reboot", ""), "type": upd.get("type", ""),
                          "restart": upd.get("restart", ""),
                          "major": bool(upd.get("type") == "upgrade" or upd.get("is_major")),
                          "detail": {k: v for k, v in upd.items() if isinstance(v, (str, int, bool, float))}}
    if installed is None:
        pk, err = await dsm.try_call("SYNO.Core.Package", "list")
        installed = [{"id": p.get("id"), "version": p.get("version", ""), "name": p.get("name", "")}
                     for p in ((pk or {}).get("packages") or [])]
    if dsm.has("SYNO.Core.Package.Server"):
        data, err = await dsm.try_call("SYNO.Core.Package.Server", "list", blforcereload=True,
                                       blloadothers=False, timeout=90)
        if err:
            out["errors"]["packages"] = err
        else:
            have = {p["id"]: p for p in installed if p.get("id")}
            for p in (data or {}).get("packages") or []:
                pid = p.get("id")
                if pid in have and p.get("version") and version_key(p["version"]) > version_key(have[pid]["version"]):
                    if p.get("beta"):
                        continue
                    out["packages"].append({
                        "id": pid, "name": have[pid].get("name") or p.get("dname") or pid,
                        "installed": have[pid]["version"], "available": p["version"],
                        "link": p.get("link", ""), "md5": p.get("md5", ""), "size": p.get("size", ""),
                        "changelog": (p.get("changelog") or "")[:2000],
                        "reboot": bool(p.get("install_reboot"))})
    return out


async def _wait(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def synology_update_package(dsm: Synology, pkg: dict, log) -> None:
    """Ein Paket aktualisieren: herunterladen, pruefen, upgraden."""
    if not pkg.get("link"):
        raise NasError(f"{pkg['id']}: kein Download-Link im Paketzentrum.")
    log(f"{pkg['id']}: Download {pkg.get('installed')} → {pkg.get('available')}")
    data = await dsm.call("SYNO.Core.Package.Installation", "install", version=1, operation="install", type=0,
                          blqinst=False, url=pkg["link"], name=pkg["id"], checksum=pkg.get("md5", ""),
                          filesize=pkg.get("size", ""))
    task_id = (data or {}).get("taskid")
    if not task_id:
        raise NasError(f"{pkg['id']}: DSM hat den Download nicht gestartet.")
    for _ in range(360):                          # bis 30 min
        st = await dsm.call("SYNO.Core.Package.Installation", "status", version=1, task_id=task_id)
        if st.get("finished"):
            break
        await _wait(5)
    else:
        raise NasError(f"{pkg['id']}: Download nach 30 Minuten nicht fertig.")
    if dsm.has("SYNO.Core.Package.Installation.Download"):
        chk, err = await dsm.try_call("SYNO.Core.Package.Installation.Download", "check", version=1, taskid=task_id)
        if err:
            log(f"{pkg['id']}: Prüfung des Downloads: {err}")
    log(f"{pkg['id']}: installiere …")
    res = await dsm.call("SYNO.Core.Package.Installation", "upgrade", version=1, type=0, check_codesign=False,
                         force=False, installrunpackage=True, task_id=task_id, extra_values={}, timeout=900)
    log(f"{pkg['id']}: {(res or {}).get('message') or 'aktualisiert'}")


async def synology_start_dsm_update(dsm: Synology, log) -> None:
    """DSM-Update herunterladen und Installation anstossen (das NAS startet danach neu)."""
    if dsm.has("SYNO.Core.Upgrade.Server.Download"):
        prog, err = await dsm.try_call("SYNO.Core.Upgrade.Server.Download", "progress")
        if err or not (prog or {}).get("finished", (prog or {}).get("percent") == 100):
            started, err2 = await dsm.try_call("SYNO.Core.Upgrade.Server.Download", "start")
            log("Download gestartet" if not err2 else f"Download starten: {err2}")
            for _ in range(720):                  # bis 60 min
                await _wait(5)
                prog, err = await dsm.try_call("SYNO.Core.Upgrade.Server.Download", "progress")
                if err:
                    log(f"Fortschritt: {err}")
                    continue
                p = prog or {}
                if p.get("finished") or p.get("status") in ("finish", "finished", "downloaded") or p.get("percent") == 100:
                    break
                if p.get("status") in ("error", "failed"):
                    raise NasError(f"DSM-Download fehlgeschlagen: {p}")
            log("Download fertig")
    if dsm.has("SYNO.Core.Upgrade.PreCheck"):
        res, err = await dsm.try_call("SYNO.Core.Upgrade.PreCheck", "start")
        log(f"Vorprüfung: {err or 'ok'}")
    last_err = None
    for params in ({}, {"updatetype": "system"}, {"type": "system"}):
        res, err = await dsm.try_call("SYNO.Core.Upgrade", "start", **params)
        if not err:
            log("Installation gestartet – das NAS startet gleich neu")
            return
        last_err = err
        log(f"Installation starten ({params or 'ohne Angaben'}): {err}")
    raise NasError(f"DSM-Installation ließ sich nicht starten: {last_err}. In DSM unter Systemsteuerung → "
                   "Update & Wiederherstellung von Hand fertigstellen.")


# -----------------------------------------------------------------------------
# SNMP
# -----------------------------------------------------------------------------
def snmp_set_params(current: dict, community: str) -> dict:
    """Aus der aktuellen SNMP-Einstellung die zum Einschalten (v1/v2c) bauen."""
    out: Dict[str, Any] = {}
    for k, v in (current or {}).items():
        kl = k.lower()
        if "pass" in kl or "privacy" in kl and "key" in kl:
            continue
        if "community" in kl:
            out[k] = community
        elif "enable" in kl and isinstance(v, bool):
            out[k] = v if "v3" in kl else True          # v3 bleibt, wie es ist
        elif isinstance(v, (str, int, float, bool)):
            out[k] = v
    if not any("community" in k.lower() for k in out):
        out["rocommunity"] = community
    if not any("enable" in k.lower() for k in out):
        out.update({"enable_snmp": True, "enable_snmpv1v2": True})
    return out


async def synology_enable_snmp(dsm: Synology, community: str, log) -> dict:
    if not dsm.has("SYNO.Core.SNMP"):
        raise NasError("Diese DSM-Version bietet SNMP nicht über die Web-API an.")
    current = await dsm.call("SYNO.Core.SNMP", "get")
    log(f"SNMP vorher: {_snmp_enabled(current)}")
    params = snmp_set_params(current if isinstance(current, dict) else {}, community)
    await dsm.call("SYNO.Core.SNMP", "set", **params)
    after = await dsm.call("SYNO.Core.SNMP", "get")
    log(f"SNMP nachher: {_snmp_enabled(after)}")
    return after if isinstance(after, dict) else {}


# =============================================================================
# TrueNAS (REST v2.0, API-Schluessel)
# =============================================================================
class TrueNas:
    def __init__(self, base: str, api_key: str, verify: bool = False) -> None:
        self.base = base.rstrip("/") + "/api/v2.0"
        self.c = httpx.AsyncClient(verify=verify, timeout=30.0,
                                   headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})

    async def req(self, method: str, path: str, **kw) -> Any:
        try:
            r = await self.c.request(method, self.base + path, **kw)
        except httpx.HTTPError as exc:
            raise _net_error(self.base, exc) from exc
        if r.status_code == 401:
            raise NasError("TrueNAS: API-Schlüssel abgelehnt.")
        if r.status_code >= 400:
            raise NasError(f"TrueNAS {path}: HTTP {r.status_code} {r.text[:200]}")
        return r.json() if r.content else None

    async def close(self) -> None:
        await self.c.aclose()


async def truenas_state(t: TrueNas) -> dict:
    st: Dict[str, Any] = {"vendor": "truenas", "errors": {}, "read_at": _iso(datetime.utcnow())}
    try:
        info = await t.req("GET", "/system/info")
        st["system"] = {"model": info.get("system_product") or info.get("model", ""), "serial": info.get("system_serial", ""),
                        "firmware": info.get("version", ""), "uptime": info.get("uptime", ""),
                        "ram_mb": (_num(info.get("physmem")) or 0) / 1048576 or None, "cpu": info.get("model", ""),
                        "cpu_cores": info.get("cores")}
    except NasError as exc:
        st["errors"]["system"] = str(exc)
        st["system"] = {}
    try:
        st["pools"] = [{"id": p.get("id"), "name": p.get("name"), "status": p.get("status", ""),
                        "healthy": p.get("healthy"), "total": _num(p.get("size")), "used": _num(p.get("allocated"))}
                       for p in await t.req("GET", "/pool")]
        st["volumes"] = [{"id": p["id"], "name": p["name"], "status": "normal" if p.get("healthy") else p.get("status"),
                          "total": p["total"], "used": p["used"],
                          "percent": round(p["used"] / p["total"] * 100, 1) if p["total"] and p["used"] is not None else None}
                         for p in st["pools"]]
    except NasError as exc:
        st["errors"]["storage"] = str(exc)
    try:
        st["alerts"] = [{"level": a.get("level"), "text": a.get("formatted") or a.get("text")}
                        for a in await t.req("GET", "/alert/list") if not a.get("dismissed")]
    except NasError as exc:
        st["errors"]["alerts"] = str(exc)
    return st


# =============================================================================
# Ablauf mit Datenbank
# =============================================================================
def public(nas: NasDevice, detail: bool = False) -> dict:
    st = nas.state or {}
    out = {"id": nas.id, "name": nas.name, "vendor": nas.vendor, "vendor_label": VENDOR_LABELS.get(nas.vendor, nas.vendor),
           "host": nas.host, "port": nas.port, "scheme": nas.scheme, "verify_tls": nas.verify_tls,
           "enabled": nas.enabled, "site": nas.site, "status": nas.status, "model": nas.model,
           "serial": nas.serial, "firmware": nas.firmware, "secret_id": nas.secret_id,
           "snmp_device_id": nas.snmp_device_id, "poll_interval_sec": nas.poll_interval_sec,
           "last_poll_at": _iso(nas.last_poll_at), "last_ok_at": _iso(nas.last_ok_at), "last_error": nas.last_error,
           "problems": st.get("problems", []), "automation": {**DEFAULT_AUTOMATION, **(nas.automation or {})},
           "updates_summary": _upd_summary(nas.updates or {}),
           "url": base_url(nas.scheme, nas.host, nas.port)}
    if detail:
        out.update({"state": st, "checks": nas.checks or [], "updates": nas.updates or {}})
    else:
        vols = st.get("volumes") or []
        out["volumes"] = [{"name": v.get("name"), "percent": v.get("percent"), "status": v.get("status")} for v in vols]
        out["load"] = st.get("load") or {}
        out["temperature"] = (st.get("system") or {}).get("temperature")
    return out


def _upd_summary(u: dict) -> dict:
    dsm = u.get("dsm") or {}
    return {"dsm": dsm.get("version") if dsm.get("available") else None,
            "packages": len(u.get("packages") or []), "checked_at": u.get("checked_at")}


async def _job(db: AsyncSession, nas: NasDevice, kind: str, actor: str, trigger: str = "manual",
               target: Optional[dict] = None) -> NasJob:
    job = NasJob(nas_id=nas.id, kind=kind, trigger=trigger, status="running", target=target or {},
                 log=[], created_by=actor)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


def _logger(job: NasJob):
    def log(text: str) -> None:
        entries = list(job.log or [])
        entries.append({"at": _iso(datetime.utcnow()), "text": str(text)[:2000]})
        job.log = entries[-300:]
        logger.info("NAS-Vorgang %s: %s", job.id, text)
    return log


async def refresh(db: AsyncSession, nas: NasDevice, with_updates: bool = False) -> dict:
    """Zustand lesen und speichern. Meldet neue Probleme."""
    old_status = nas.status
    nas.last_poll_at = datetime.utcnow()
    try:
        if nas.vendor == "synology":
            dsm = await synology_session(db, nas)
            try:
                st = await synology_state(dsm)
                if with_updates or _updates_due(nas):
                    nas.updates = await synology_updates(dsm, st.get("packages"))
            finally:
                await dsm.__aexit__(None, None, None)
        elif nas.vendor == "truenas":
            cred = await vault.get(db, nas.secret_id)
            t = TrueNas(base_url(nas.scheme, nas.host, nas.port), cred.get("password", ""), bool(nas.verify_tls))
            try:
                st = await truenas_state(t)
                if with_updates or _updates_due(nas):
                    try:
                        avail = await t.req("POST", "/update/check_available", json={})
                        nas.updates = {"dsm": {"available": (avail or {}).get("status") == "AVAILABLE",
                                               "version": (avail or {}).get("version", "")},
                                       "packages": [], "checked_at": _iso(datetime.utcnow())}
                    except NasError as exc:
                        nas.updates = {"errors": {"dsm": str(exc)}, "checked_at": _iso(datetime.utcnow())}
            finally:
                await t.close()
        else:
            p = await probe(nas.host, nas.port, nas.scheme)
            st = {"vendor": nas.vendor, "reachable": True, "probe": p, "errors": {}, "read_at": _iso(datetime.utcnow())}
        if nas.scheme == "https":
            st["certificate"] = await asyncio.to_thread(cert_info, nas.host, nas.port or 443)
        level, problems = evaluate(st)
        cert = st.get("certificate") or {}
        if cert and cert.get("days_left") is not None and cert["days_left"] < 14 and not cert.get("self_signed"):
            problems.append({"level": "warning", "what": "cert", "text": f"Zertifikat läuft in {cert['days_left']} Tagen ab"})
            level = "warning" if level == "ok" else level
        st["problems"] = problems
        nas.state = st
        sysd = st.get("system") or {}
        nas.model = (sysd.get("model") or nas.model or "")[:128]
        nas.serial = (sysd.get("serial") or nas.serial or "")[:128]
        nas.firmware = (sysd.get("firmware") or nas.firmware or "")[:128]
        nas.status, nas.last_ok_at, nas.last_error = level, datetime.utcnow(), None
    except (NasError, vault.VaultError) as exc:
        nas.status, nas.last_error = "down", str(exc)
        problems = [{"level": "critical", "what": "down", "text": str(exc)}]
        st = dict(nas.state or {})
        st["problems"] = problems
        nas.state = st
    await db.commit()
    await _notify_change(nas, old_status)
    return public(nas, detail=True)


def _updates_due(nas: NasDevice) -> bool:
    at = (nas.updates or {}).get("checked_at")
    if not at:
        return True
    try:
        when = datetime.fromisoformat(at.rstrip("Z"))
    except ValueError:
        return True
    return datetime.utcnow() - when > timedelta(hours=UPDATE_CHECK_HOURS)


async def _notify_change(nas: NasDevice, old: str) -> None:
    from . import alerts
    rank = {"ok": 0, "unknown": 0, "warning": 1, "critical": 2, "down": 3}
    new = nas.status
    if rank.get(new, 0) > rank.get(old, 0):
        text = "\n".join(p["text"] for p in (nas.state or {}).get("problems", [])[:8])
        await alerts.dispatch("nas_problem", f"RIMPERIO: NAS „{nas.name}“ – {'nicht erreichbar' if new == 'down' else 'Problem'}",
                              text, level="critical" if new in ("critical", "down") else "warning",
                              key=f"nas:{nas.id}", detail={"link": f"/devices/nas/{nas.id}", "nas_id": nas.id})
    elif rank.get(new, 0) == 0 and rank.get(old, 0) > 0:
        alerts.forget(f"nas:{nas.id}")
        await alerts.dispatch("recovered", f"RIMPERIO: NAS „{nas.name}“ wieder in Ordnung", "", level="info",
                              key=f"nas-ok:{nas.id}", detail={"link": f"/devices/nas/{nas.id}"})


# -----------------------------------------------------------------------------
# Einrichten: anmelden, pruefen, SNMP
# -----------------------------------------------------------------------------
def _check(key: str, label: str, status: str, detail: str = "") -> dict:
    return {"key": key, "label": label, "status": status, "detail": detail}


async def onboarding_checks(dsm: Synology, st: dict, upd: dict, probe_info: dict, cert: Optional[dict],
                            secret_backend: str, used_otp: bool) -> List[dict]:
    checks = [_check("reach", "Erreichbar", "ok", f"{dsm.base} – {probe_info.get('label', 'DSM')}")]
    if probe_info.get("scheme") != "https":
        checks.append(_check("tls", "Verschlüsselung", "warn",
                             "Nur HTTP (Port 5000). In DSM HTTPS einschalten und Port 5001 nehmen."))
    elif cert:
        if cert.get("self_signed"):
            checks.append(_check("tls", "Zertifikat", "info", f"selbst signiert, gültig bis {cert.get('not_after')}"))
        elif (cert.get("days_left") or 0) < 14:
            checks.append(_check("tls", "Zertifikat", "warn", f"läuft in {cert.get('days_left')} Tagen ab"))
        else:
            checks.append(_check("tls", "Zertifikat", "ok", f"{cert.get('issuer')}, noch {cert.get('days_left')} Tage"))
    checks.append(_check("login", "Anmeldung", "ok", f"als {dsm.account}"))
    checks.append(_check("2fa", "Zwei-Faktor", "ok" if used_otp else "warn",
                         "TOTP aktiv, RIMPERIO ist als vertrauenswürdiges Gerät angemeldet" if used_otp else
                         "Für dieses Konto ist kein Zwei-Faktor aktiv – in DSM unter Persönlich → Sicherheit einschalten "
                         "und neu verbinden."))
    if dsm.account.lower() == "admin":
        checks.append(_check("account", "Konto", "warn", "Standardkonto „admin“ – besser ein eigenes Administratorkonto."))
    errs = st.get("errors") or {}
    admin_ok = "upgrade_setting" not in errs and "snmp" not in errs
    checks.append(_check("admin", "Administratorrechte", "ok" if admin_ok else "fail",
                         "Updates und SNMP einstellbar" if admin_ok else
                         "Das Konto darf Einstellungen nicht lesen – es muss zur Gruppe administrators gehören."))
    sysd = st.get("system") or {}
    fw = str(sysd.get("firmware") or "?")
    checks.append(_check("dsm", "System", "ok", f"{sysd.get('model', '?')} · {fw if fw.startswith('DSM') else 'DSM ' + fw}"))
    level, problems = evaluate(st)
    checks.append(_check("storage", "Speicher", {"ok": "ok", "warning": "warn", "critical": "fail"}[level],
                         "; ".join(p["text"] for p in problems[:5]) or
                         f"{len(st.get('pools') or [])} Pool(s), {len(st.get('volumes') or [])} Volume(s), "
                         f"{len(st.get('disks') or [])} Platte(n) – alles normal"))
    dsm_u = upd.get("dsm") or {}
    if (upd.get("errors") or {}).get("dsm"):
        checks.append(_check("dsm_update", "DSM-Update", "warn", upd["errors"]["dsm"]))
    else:
        checks.append(_check("dsm_update", "DSM-Update", "info" if dsm_u.get("available") else "ok",
                             f"verfügbar: {dsm_u.get('version')}" if dsm_u.get("available") else "aktuell"))
    n = len(upd.get("packages") or [])
    checks.append(_check("pkg_update", "Paket-Updates", "info" if n else "ok",
                         f"{n} verfügbar: " + ", ".join(p["name"] for p in upd["packages"][:6]) if n else "alle Pakete aktuell"))
    tasks = st.get("backup_tasks") or []
    checks.append(_check("hyperbackup", "Hyper Backup", "ok" if tasks else "info",
                         f"{len(tasks)} Aufgabe(n)" if tasks else "keine Sicherungsaufgabe auf dem NAS – unter Backup einrichtbar"))
    checks.append(_check("secret", "Zugangsdaten", "ok" if secret_backend == "vaultwarden" else "info",
                         "in Vaultwarden" if secret_backend == "vaultwarden" else
                         "lokal verschlüsselt – mit Vaultwarden (Integrationen) zentral ablegen"))
    return checks


async def ensure_snmp_device(db: AsyncSession, nas: NasDevice, community: str) -> SnmpDevice:
    """SNMP-Zugang und -Geraet fuer das NAS anlegen bzw. auffrischen."""
    dev = await db.get(SnmpDevice, nas.snmp_device_id) if nas.snmp_device_id else None
    cred = await db.get(SnmpCredential, dev.credential_id) if dev and dev.credential_id else None
    if cred is None:
        cred = SnmpCredential(name=f"NAS {nas.name}", version="2c", community=community, port=161)
        db.add(cred)
        await db.flush()
    else:
        cred.version, cred.community = "2c", community
    if dev is None:
        dev = (await db.execute(select(SnmpDevice).where(SnmpDevice.host == nas.host))).scalars().first()
    templates = ["system", "interfaces", "host", "synology", "ucd"] if nas.vendor == "synology" else ["system", "interfaces", "host"]
    if dev is None:
        dev = SnmpDevice(name=nas.name, host=nas.host, credential_id=cred.id, poller="server", site=nas.site,
                         templates=templates, custom=[], overrides={}, interval_sec=300, enabled=True,
                         status="unknown", state={}, system={}, discovered_device_id=nas.discovered_device_id)
        db.add(dev)
    else:
        dev.credential_id = cred.id
        dev.templates = sorted(set((dev.templates or []) + templates))
        dev.enabled = True
    await db.flush()
    nas.snmp_device_id = dev.id
    await db.commit()
    return dev


async def snmp_reachable(host: str, community: str, tries: int = 4) -> Optional[dict]:
    from . import snmp
    for i in range(tries):
        info = await snmp.system_info(host, community, timeout=2.0)
        if info:
            return info
        await asyncio.sleep(2 + i * 2)
    return None


async def enable_snmp(db: AsyncSession, nas: NasDevice, actor: str, dsm: Optional[Synology] = None) -> dict:
    """SNMP v2c einschalten, pruefen, als SNMP-Geraet anlegen. Gibt ein Pruef-Ergebnis zurueck."""
    job = await _job(db, nas, "snmp_enable", actor)
    log = _logger(job)
    own = dsm is None
    community = "rimp" + secrets.token_hex(8)
    try:
        if nas.vendor != "synology":
            raise NasError("SNMP automatisch einschalten geht bisher nur bei Synology.")
        if own:
            dsm = await synology_session(db, nas)
        existing = await db.get(SnmpDevice, nas.snmp_device_id) if nas.snmp_device_id else None
        if existing and existing.credential_id:
            cred = await db.get(SnmpCredential, existing.credential_id)
            if cred and cred.community and await snmp_reachable(nas.host, cred.community, tries=1):
                community = cred.community
                log("SNMP antwortet schon mit der hinterlegten Community.")
        await synology_enable_snmp(dsm, community, log)
        info = await snmp_reachable(nas.host, community)
        await ensure_snmp_device(db, nas, community)
        if info:
            log(f"SNMP antwortet: {info.get('sys_descr', '')[:120]}")
            result = _check("snmp", "SNMP v2c", "ok", "eingeschaltet und von RIMPERIO aus erreichbar – unter SNMP-Geräte angelegt")
        else:
            log("SNMP antwortet nicht – Firewall des NAS?")
            result = _check("snmp", "SNMP v2c", "warn",
                            "in DSM eingeschaltet, antwortet aber nicht. Firewall des NAS prüfen: UDP 161 für den "
                            "RIMPERIO-Server freigeben (Systemsteuerung → Sicherheit → Firewall).")
        job.status = "done"
    except (NasError, vault.VaultError) as exc:
        log(f"Fehler: {exc}")
        job.status, job.error = "error", str(exc)
        result = _check("snmp", "SNMP v2c", "fail",
                        f"{exc} – von Hand: Systemsteuerung → Terminal & SNMP → SNMP → SNMPv1, SNMPv2c einschalten.")
    finally:
        job.finished_at = datetime.utcnow()
        await db.commit()
        if own and dsm is not None:
            await dsm.__aexit__(None, None, None)
    checks = [c for c in (nas.checks or []) if c.get("key") != "snmp"] + [result]
    nas.checks = checks
    await db.commit()
    return result


async def connect(db: AsyncSession, *, actor: str, host: str, username: str, password: str,
                  otp: str = "", totp_secret: str = "", name: str = "", port: Optional[int] = None,
                  scheme: Optional[str] = None, verify_tls: bool = False, enable_snmp_now: bool = True,
                  site: str = "Standard", nas_id: Optional[int] = None, vendor: Optional[str] = None) -> dict:
    """Erstes Einrichten (oder neu anmelden). Wirft NasNeedsOtp, wenn ein Code fehlt."""
    info = await probe(host, port, scheme)
    if info is None:
        if vendor == "generic":
            info = {"vendor": "generic", "label": "Weboberfläche", "host": host, "port": port or 443,
                    "scheme": scheme or "https", "url": base_url(scheme or "https", host, port or 443)}
        else:
            raise NasError(f"Unter {host} antwortet kein bekanntes NAS (Synology: Port 5000/5001).")
    vendor = info["vendor"] if info["vendor"] in ("synology", "truenas") else "generic"
    existing = await db.get(NasDevice, nas_id) if nas_id else None
    if existing is None:
        existing = (await db.execute(select(NasDevice).where(NasDevice.host == host))).scalars().first()
    cert = await asyncio.to_thread(cert_info, host, info["port"]) if info["scheme"] == "https" else None

    st: dict = {}
    upd: dict = {}
    checks: List[dict] = []
    dsm: Optional[Synology] = None
    try:
        if vendor == "synology":
            dsm = Synology(info["url"], verify=verify_tls)
            await dsm.__aenter__()
            code = otp or (vault.totp_now(totp_secret) if totp_secret else "")
            await dsm.login(username, password, otp=code)
            st = await synology_state(dsm)
            upd = await synology_updates(dsm, st.get("packages"))
        elif vendor == "truenas":
            t = TrueNas(info["url"], password, verify_tls)
            try:
                st = await truenas_state(t)
                if st.get("errors", {}).get("system"):
                    raise NasError(st["errors"]["system"])
            finally:
                await t.close()
        fields = {"device_token": dsm.device_token} if dsm and dsm.device_token else {}
        secret = await vault.put(db, secret_id=existing.secret_id if existing else None, kind="nas",
                                 name=f"NAS {name or (st.get('system') or {}).get('model') or host}",
                                 username=username, password=password, totp=totp_secret or None,
                                 uri=info["url"], fields=fields)
        nas = existing or NasDevice(host=host)
        nas.name = name.strip() or (existing.name if existing else "") or (st.get("system") or {}).get("model") or host
        nas.vendor, nas.port, nas.scheme = vendor, info["port"], info["scheme"]
        nas.verify_tls, nas.secret_id, nas.site, nas.enabled = verify_tls, secret.id, site or "Standard", True
        nas.automation = {**DEFAULT_AUTOMATION, **(nas.automation or {})}
        disc = (await db.execute(select(DiscoveredDevice).where(DiscoveredDevice.ip_address == host))).scalars().first()
        if disc:
            nas.discovered_device_id = disc.id
        if existing is None:
            db.add(nas)
        st["problems"] = evaluate(st)[1] if st else []
        if cert:
            st["certificate"] = cert
        nas.state, nas.updates = st, upd
        sysd = st.get("system") or {}
        nas.model, nas.serial, nas.firmware = sysd.get("model", "")[:128], sysd.get("serial", "")[:128], sysd.get("firmware", "")[:128]
        nas.status = evaluate(st)[0] if st else "unknown"
        nas.last_ok_at = nas.last_poll_at = datetime.utcnow()
        nas.last_error = None
        await db.commit()
        await db.refresh(nas)
        if dsm:
            checks = await onboarding_checks(dsm, st, upd, info, cert, secret.backend,
                                             used_otp=bool(code or dsm.device_token))
        else:
            checks = [_check("reach", "Erreichbar", "ok", info["url"]),
                      _check("login", "Anmeldung", "ok" if vendor == "truenas" else "info",
                             "API-Schlüssel gültig" if vendor == "truenas" else "nur Erreichbarkeit")]
        nas.checks = checks
        await db.commit()
        if dsm and enable_snmp_now:
            if st.get("snmp_enabled") is True and nas.snmp_device_id:
                pass
            else:
                await enable_snmp(db, nas, actor, dsm=dsm)
        journal.fire(category="rmm", event="nas.connected", actor=actor,
                     message=f"NAS „{nas.name}“ ({info['label']}, {host}) verbunden.")
        await db.refresh(nas)
        return public(nas, detail=True)
    finally:
        if dsm:
            await dsm.__aexit__(None, None, None)


# -----------------------------------------------------------------------------
# Updates ausfuehren
# -----------------------------------------------------------------------------
_running: Dict[int, asyncio.Task] = {}


def busy(nas_id: int) -> bool:
    t = _running.get(nas_id)
    return bool(t and not t.done())


async def run_update(nas_id: int, what: str, packages: Optional[List[str]], actor: str,
                     trigger: str = "manual") -> int:
    """Startet ein Update im Hintergrund. Gibt die Vorgangs-ID zurueck."""
    from .database import async_session
    if busy(nas_id):
        raise NasError("Am NAS läuft schon ein Vorgang.")
    async with async_session() as db:
        nas = await db.get(NasDevice, nas_id)
        if nas is None:
            raise NasError("Unbekanntes NAS.")
        job = await _job(db, nas, "dsm_update" if what == "dsm" else "package_update", actor, trigger,
                         {"packages": packages or []} if what != "dsm" else
                         {"version": ((nas.updates or {}).get("dsm") or {}).get("version", "")})
        job_id = job.id
    task = asyncio.create_task(_execute(nas_id, job_id, what, packages))
    _running[nas_id] = task
    return job_id


async def _execute(nas_id: int, job_id: int, what: str, packages: Optional[List[str]]) -> None:
    from . import alerts
    from .database import async_session
    async with async_session() as db:
        nas = await db.get(NasDevice, nas_id)
        job = await db.get(NasJob, job_id)
        log = _logger(job)
        ok = False
        try:
            if nas.vendor == "truenas":
                cred = await vault.get(db, nas.secret_id)
                t = TrueNas(base_url(nas.scheme, nas.host, nas.port), cred.get("password", ""), bool(nas.verify_tls))
                try:
                    log("TrueNAS-Update starten (mit Neustart)")
                    await t.req("POST", "/update/update", json={"reboot": True})
                finally:
                    await t.close()
                await _await_back(db, nas, log, old_version=nas.firmware)
                ok = True
            elif nas.vendor != "synology":
                raise NasError("Updates gehen nur bei Synology und TrueNAS.")
            else:
                if (nas.automation or {}).get("backup_config_first", True):
                    await _config_backup_before(db, nas, log)
                dsm = await synology_session(db, nas)
                try:
                    upd = await synology_updates(dsm)
                    nas.updates = upd
                    await db.commit()
                    if what == "dsm":
                        info = upd.get("dsm") or {}
                        if not info.get("available"):
                            log("Kein DSM-Update verfügbar.")
                            ok = True
                        else:
                            log(f"DSM {nas.firmware} → {info.get('version')}")
                            await synology_start_dsm_update(dsm, log)
                    else:
                        todo = [p for p in upd.get("packages") or [] if not packages or p["id"] in packages]
                        if not todo:
                            log("Keine Paket-Updates (mehr) verfügbar.")
                        failed = []
                        for p in todo:
                            try:
                                await synology_update_package(dsm, p, log)
                            except NasError as exc:
                                failed.append(p["id"])
                                log(f"{p['id']}: FEHLER {exc}")
                        if failed:
                            raise NasError(f"Nicht aktualisiert: {', '.join(failed)}")
                        ok = True
                finally:
                    await dsm.__aexit__(None, None, None)
                if what == "dsm" and not ok:
                    await _await_back(db, nas, log, old_version=nas.firmware)
                    ok = True
            job.status = "done"
        except (NasError, vault.VaultError) as exc:
            log(f"Abgebrochen: {exc}")
            job.status, job.error = "error", str(exc)
        except Exception as exc:                                    # defensiv
            logger.exception("NAS-Update fehlgeschlagen")
            job.status, job.error = "error", str(exc)
        job.finished_at = datetime.utcnow()
        await db.commit()
        try:
            await refresh(db, nas, with_updates=True)
        except Exception:                                           # defensiv
            pass
        label = "DSM-Update" if what == "dsm" else "Paket-Updates"
        if job.status == "done":
            await alerts.dispatch("nas_update", f"RIMPERIO: {label} auf „{nas.name}“ erledigt",
                                  "\n".join(e["text"] for e in (job.log or [])[-6:]), level="info",
                                  key=f"nas-upd:{job.id}", detail={"link": f"/devices/nas/{nas.id}"})
        else:
            await alerts.dispatch("nas_update_failed", f"RIMPERIO: {label} auf „{nas.name}“ fehlgeschlagen",
                                  job.error or "", level="warning", key=f"nas-upd:{job.id}",
                                  detail={"link": f"/devices/nas/{nas.id}"})
        journal.fire(category="rmm", event=f"nas.{job.kind}", actor=job.created_by or "system",
                     level="info" if job.status == "done" else "warning", ok=job.status == "done",
                     message=f"{label} auf NAS „{nas.name}“: {'erledigt' if job.status == 'done' else job.error}")


async def _await_back(db: AsyncSession, nas: NasDevice, log, old_version: str, minutes: int = 40) -> None:
    """Nach dem Neustart warten, bis das NAS wieder antwortet, und Version vergleichen."""
    log("Warte auf den Neustart …")
    await _wait(90)
    deadline = datetime.utcnow() + timedelta(minutes=minutes)
    while datetime.utcnow() < deadline:
        if await probe(nas.host, nas.port, nas.scheme, timeout=5):
            await _wait(30)
            try:
                if nas.vendor == "synology":
                    dsm = await synology_session(db, nas)
                    try:
                        st = await synology_state(dsm)
                    finally:
                        await dsm.__aexit__(None, None, None)
                    new = (st.get("system") or {}).get("firmware", "")
                else:
                    new = nas.firmware
                log(f"Wieder erreichbar. Version jetzt: {new or '?'}")
                if new and old_version and new == old_version:
                    log("Achtung: Versionsnummer unverändert – Update in DSM prüfen.")
                return
            except (NasError, vault.VaultError) as exc:
                log(f"Noch nicht bereit: {exc}")
        await _wait(20)
    raise NasError(f"Das NAS ist nach {minutes} Minuten nicht wieder erreichbar.")


async def _config_backup_before(db: AsyncSession, nas: NasDevice, log) -> None:
    """Vor einem Update die NAS-Konfiguration sichern (Backup-Auftrag, falls vorhanden)."""
    try:
        from . import backupjobs
        run = await backupjobs.run_nas_config_before_update(db, nas)
        if run:
            log(f"Konfiguration gesichert ({run})")
    except Exception as exc:                                        # defensiv - Update deshalb nicht verhindern
        log(f"Konfigurationssicherung vorher nicht möglich: {exc}")


# -----------------------------------------------------------------------------
# Hyper Backup und Konfigurationssicherung (fuer backupjobs)
# -----------------------------------------------------------------------------
async def hyperbackup_run(db: AsyncSession, nas: NasDevice, task_id: Any) -> None:
    dsm = await synology_session(db, nas)
    try:
        await dsm.call("SYNO.Backup.Task", "backup", version=1, task_id=task_id)
    finally:
        await dsm.__aexit__(None, None, None)


async def hyperbackup_status(db: AsyncSession, nas: NasDevice, task_id: Any) -> dict:
    dsm = await synology_session(db, nas)
    try:
        data = await dsm.call("SYNO.Backup.Task", "status", version=1, task_id=task_id, blOnline=False,
                              additional=["last_bkp_time", "next_bkp_time", "last_bkp_result", "last_bkp_progress"])
        return _hb_task({**(data or {}), "task_id": task_id}) | {"raw_state": (data or {}).get("state")}
    finally:
        await dsm.__aexit__(None, None, None)


async def config_backup(db: AsyncSession, nas: NasDevice) -> bytes:
    """Konfiguration des DSM als .dss-Datei holen."""
    dsm = await synology_session(db, nas)
    try:
        if not dsm.has("SYNO.Backup.Config.Backup"):
            raise NasError("Diese DSM-Version bietet die Konfigurationssicherung nicht über die Web-API an.")
        await dsm.call("SYNO.Backup.Config.Backup", "start", version=2)
        for _ in range(60):
            st, err = await dsm.try_call("SYNO.Backup.Config.Backup", "status", version=2)
            s = st or {}
            if err or s.get("finished") or s.get("status") in ("finish", "finished", "done", "success") or s.get("progress") == 100:
                break
            await _wait(3)
        data = await dsm.call("SYNO.Backup.Config.Backup", "download", version=2, raw=True, timeout=120)
        if not data or len(data) < 64:
            raise NasError("DSM hat keine Konfigurationsdatei geliefert.")
        return data
    finally:
        await dsm.__aexit__(None, None, None)


# -----------------------------------------------------------------------------
# Netzwerksuche und Spezialsysteme
# -----------------------------------------------------------------------------
async def annotate_discovered(session: AsyncSession) -> int:
    """Nach einer Suche: Systeme mit Port 5000/5001 (oder als NAS erkannt) fragen, ob DSM antwortet."""
    rows = (await session.execute(select(DiscoveredDevice))).scalars().all()
    cand = [r for r in rows if not r.ignored and (set(r.open_ports or []) & {5000, 5001} or r.kind == "nas")]
    sem = asyncio.Semaphore(16)

    async def one(r: DiscoveredDevice):
        async with sem:
            return r, await probe(r.ip_address, timeout=3.0)

    found = 0
    for r, info in await asyncio.gather(*(one(r) for r in cand)):
        details = dict(r.details or {})
        if info:
            details["nas"] = info
            r.kind = "nas"
            r.os_guess = info["label"]
            found += 1
        else:
            details.pop("nas", None)
        r.details = details
    pis = [r for r in rows if is_raspberry_mac(r.mac_address)]
    for r in pis:
        details = dict(r.details or {})
        details["raspberry_pi"] = True
        r.details = details
        if r.kind in ("unknown", "linux", "appliance") and "raspberry" not in (r.os_guess or "").lower():
            r.os_guess = ("Raspberry Pi · " + (r.os_guess or "")).strip(" ·")[:120]
    await session.commit()
    return found


async def discovered(db: AsyncSession, live: bool = False) -> List[dict]:
    rows = (await db.execute(select(DiscoveredDevice).order_by(DiscoveredDevice.ip_address))).scalars().all()
    known = {n.host for n in (await db.execute(select(NasDevice))).scalars().all()}
    cand = [r for r in rows if not r.ignored and ((r.details or {}).get("nas") or r.kind == "nas"
                                                  or set(r.open_ports or []) & {5000, 5001})]
    if live:
        results = await asyncio.gather(*(probe(r.ip_address, timeout=3.0) for r in cand))
        for r, info in zip(cand, results):
            d = dict(r.details or {})
            if info:
                d["nas"] = info
            r.details = d
        await db.commit()
    return [{"id": r.id, "ip": r.ip_address, "hostname": r.hostname, "mac": r.mac_address,
             "kind": r.kind, "os_guess": r.os_guess, "ports": r.open_ports or [], "nas": (r.details or {}).get("nas"),
             "connected": r.ip_address in known, "last_seen": _iso(r.last_seen)} for r in cand]


async def special_systems(db: AsyncSession) -> dict:
    """Raspberry Pi & Co.: Rechner mit Agent auf Einplatinen-Hardware und gefundene Pis ohne Agent."""
    from .models import PatchItem
    from .rmm import is_online
    from sqlalchemy import func
    agents = (await db.execute(select(Agent))).scalars().all()
    open_patches = dict((await db.execute(
        select(PatchItem.agent_id, func.count()).where(
            PatchItem.status.in_(("auto", "approved", "needs_approval", "review"))).group_by(PatchItem.agent_id))).all())
    out_agents, log_only = [], []
    pi_macs, pi_ips = set(), set()
    for a in agents:
        inv = a.inventory or {}
        hw = inv.get("hardware") or {}
        model = hw.get("model") or ""
        macs = [m.lower() for m in inv.get("macs") or []]
        if a.mac_address:
            macs.append(a.mac_address.lower())
        arch = str(inv.get("arch") or a.arch or "")
        is_pi = "raspberry" in model.lower() or any(is_raspberry_mac(m) for m in macs)
        is_arm = arch.startswith(("arm", "aarch")) or str(inv.get("kernel_arch", "")).startswith(("arm", "aarch"))
        if not a.agent_uid:
            # Nur Log-Agent (Syslog/Log-Lieferung): angebunden, aber ohne Fernwartung.
            if is_pi:
                log_only.append({"id": a.id, "hostname": a.hostname, "ip": a.ip_address, "mac": a.mac_address,
                                 "last_seen": _iso(a.last_seen)})
                pi_macs.update(macs)
                if a.ip_address:
                    pi_ips.add(a.ip_address)
            continue
        # Auch aeltere Agents ohne Modell-Angabe: jede ARM-Linux-Maschine gehoert hierher.
        if not (is_pi or hw.get("board") or is_arm):
            continue
        pi_macs.update(macs)
        for ip in (a.ip_address, inv.get("local_ip")):
            if ip:
                pi_ips.add(ip)
        temp, throttled = hw.get("temp_c"), hw.get("throttled")
        hints = []
        if is_pi and throttled is None and hw:
            hints.append("Drosselung nicht lesbar: das Dienstkonto braucht die Gruppe video "
                         "(sudo usermod -aG video rimperio && sudo systemctl restart rimperio-agent).")
        if temp is not None and temp >= 80:
            hints.append("SoC über 80 °C – Kühlkörper/Lüfter prüfen, der Pi drosselt sonst dauerhaft.")
        if any("Unterspannung" in t and "jetzt" in t for t in (hw.get("throttled_text") or [])):
            hints.append("Unterspannung jetzt: Netzteil (5,1 V / 3 A bzw. 5 A beim Pi 5) und Kabel tauschen.")
        disk = a.last_disk
        if disk is not None and disk >= 90:
            hints.append(f"Speicherkarte zu {disk:.0f} % voll.")
        out_agents.append({"agent_uid": a.agent_uid, "hostname": a.hostname,
                           "model": model or ("Raspberry Pi" if is_pi else (inv.get("cpu_model") or "ARM-Rechner")),
                           "arch": arch, "os": inv.get("os") or inv.get("platform"),
                           "ip": (inv.get("local_ip") or a.ip_address),
                           "online": is_online(a, 300), "raspberry_pi": is_pi,
                           "temp_c": temp, "throttled": throttled,
                           "throttled_text": hw.get("throttled_text"), "last_seen": _iso(a.rmm_last_seen),
                           "agent_version": inv.get("agent_version"), "open_patches": int(open_patches.get(a.id, 0)),
                           "hints": hints})
    rows = (await db.execute(select(DiscoveredDevice))).scalars().all()
    loose = [{"id": r.id, "ip": r.ip_address, "hostname": r.hostname, "mac": r.mac_address, "os_guess": r.os_guess,
              "ports": r.open_ports or [], "ssh": 22 in (r.open_ports or [])}
             for r in rows if is_raspberry_mac(r.mac_address) and (r.mac_address or "").lower() not in pi_macs
             and r.ip_address not in pi_ips and not r.ignored]
    return {"agents": out_agents, "log_only": log_only, "discovered": loose}


# -----------------------------------------------------------------------------
# Takt: Zustand lesen, Updates im Wartungsfenster
# -----------------------------------------------------------------------------
def in_window(auto: dict, now_utc: Optional[datetime] = None) -> bool:
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(auto.get("tz") or "Europe/Berlin")
    except Exception:                                               # defensiv
        tz = ZoneInfo("UTC")
    now = (now_utc or datetime.utcnow()).replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    try:
        hh, mm = (int(x) for x in str(auto.get("start", "03:00")).split(":")[:2])
    except ValueError:
        hh, mm = 3, 0
    dur = timedelta(minutes=int(auto.get("duration_min") or 120))
    days = auto.get("days") or []
    for back in (0, 1):
        day = (now - timedelta(days=back))
        start = day.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if start.weekday() in days and start <= now < start + dur:
            return True
    return False


_last_auto: Dict[Tuple[int, str], datetime] = {}


async def tick() -> None:
    from .database import async_session
    async with async_session() as db:
        devices = (await db.execute(select(NasDevice).where(NasDevice.enabled.is_(True)))).scalars().all()
        now = datetime.utcnow()
        for nas in devices:
            if busy(nas.id):
                continue
            due = not nas.last_poll_at or (now - nas.last_poll_at).total_seconds() >= (nas.poll_interval_sec or 600)
            if due:
                try:
                    await refresh(db, nas)
                except Exception as exc:                            # defensiv
                    logger.warning("NAS %s: Zustand nicht gelesen: %s", nas.name, exc)
            await _automation(db, nas, now)


async def _automation(db: AsyncSession, nas: NasDevice, now: datetime) -> None:
    from . import alerts
    auto = {**DEFAULT_AUTOMATION, **(nas.automation or {})}
    upd = nas.updates or {}
    dsm = upd.get("dsm") or {}
    pk = [p for p in upd.get("packages") or [] if p["id"] not in (auto.get("exclude") or [])]
    # Hinweis auf neue Updates (einmal je Version)
    fp = f"{dsm.get('version') if dsm.get('available') else ''}|{','.join(sorted(p['id'] + p['available'] for p in pk))}"
    if fp.strip("|") and (nas.state or {}).get("_notified_updates") != fp and \
            ("notify" in (auto["dsm"], auto["packages"]) or "auto" in (auto["dsm"], auto["packages"])):
        st = dict(nas.state or {})
        st["_notified_updates"] = fp
        nas.state = st
        await db.commit()
        lines = ([f"DSM {dsm['version']}"] if dsm.get("available") else []) + [f"{p['name']} {p['available']}" for p in pk]
        await alerts.dispatch("nas_update_available", f"RIMPERIO: Updates für NAS „{nas.name}“",
                              "\n".join(lines[:12]), level="info", key=f"nas-avail:{nas.id}:{fp}",
                              detail={"link": f"/devices/nas/{nas.id}"})
    if not in_window(auto, now):
        return
    day = now.strftime("%Y-%m-%d")
    if auto["packages"] == "auto" and pk and _last_auto.get((nas.id, "pkg")) != day:
        _last_auto[(nas.id, "pkg")] = day
        await run_update(nas.id, "packages", [p["id"] for p in pk], "Zeitplan", trigger="schedule")
        return
    if auto["dsm"] == "auto" and dsm.get("available") and _last_auto.get((nas.id, "dsm")) != day:
        if dsm.get("major") and not auto.get("dsm_major"):
            return
        _last_auto[(nas.id, "dsm")] = day
        await run_update(nas.id, "dsm", None, "Zeitplan", trigger="schedule")


async def ensure_schema() -> None:
    from .database import Base, engine
    from .models import NAS_TABLES, VAULT_TABLES
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=VAULT_TABLES + NAS_TABLES))
        logger.info("NAS-Schema bereit")
    except Exception as exc:                                        # defensiv
        logger.error("NAS-Schema nicht angelegt: %s", exc)


async def loop() -> None:
    await asyncio.sleep(45)
    while True:
        try:
            await tick()
        except Exception as exc:                                    # defensiv
            logger.error("NAS-Takt: %s", exc)
        await asyncio.sleep(60)
