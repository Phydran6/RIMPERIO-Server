# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Container: wer gehoert wem, und was ist zu aktualisieren
# ==============================================================================
"""
Die Container dieses Servers - und die eine Frage, die dabei wirklich zaehlt:
**wie aktualisiert man das Ding?**

Darauf gibt es genau zwei Antworten, und jeder Container hier gehoert in eine
der beiden Schubladen:

* ``source``  - **RIMPERIO-eigen.** Der Container wird aus diesem Quellcode
  gebaut (``rimperio-app-backend``, ``-frontend``, ``-syslog``). Es gibt kein
  fertiges Image irgendwo, also auch nichts zu ziehen. Aktualisiert wird er
  ueber *RIMPERIO-Server -> Updates*: neuer Stand von GitHub, Container neu bauen.

* ``image``   - **Fremd.** PostgreSQL, Caddy, n8n, Portainer, Open WebUI,
  Postfix, Tugtainer. Ganz normale Images aus einer Registry, die regelmaessig
  ein Update bekommen wie ueberall sonst auch. Aktualisiert wird hier, auf
  dieser Seite: neueres Image holen, Container neu erzeugen.

Frueher hiessen alle Container ``rimperio-*``, und damit sah es so aus, als
gehoerte PostgreSQL irgendwie zu RIMPERIO. Tut es nicht. Deshalb tragen sie
jetzt ``rimperio-app-*`` bzw. ``rimperio-ext-*`` und zusaetzlich Labels
(``de.rimperio.origin``, ``de.rimperio.update``), an denen dieses Modul sich
orientiert. Ein Container ohne Labels - etwa aus einer aelteren Installation -
wird ueber eine Namensliste zugeordnet, damit auch der alte Bestand richtig
einsortiert erscheint.

**Update-Pruefung ohne fremde Hilfe.** Dieses Modul fragt die Registry direkt:
es holt den Abdruck (Digest), den die Registry fuer den benutzten Tag fuehrt,
und vergleicht ihn mit dem Abdruck des lokal vorhandenen Images. Kein
Herunterladen, kein Austauschen - nur nachsehen. Damit braucht es fuer die
Frage "steht ein Update an?" keinen Zusatzdienst.

Tugtainer (optional, ``server/deploy/optional.yml``) macht dasselbe mit eigener
Oberflaeche, ueber mehrere Hosts hinweg und nach Zeitplan. Wer das will, nimmt
es dazu. Wer es nicht will, verliert hier nichts.

**Was dieses Modul nicht tut:** von selbst aktualisieren. Nie. Jedes Einspielen
kommt von einem Klick oder einem Befehl, und jedes Einspielen landet im
Systemtagebuch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Dict, List, Optional, Tuple

import httpx

from . import hostexec

logger = logging.getLogger("rimperio.containers")

# Wie lange das Ergebnis einer Registry-Abfrage gilt. Registries moegen es
# nicht, im Minutentakt gefragt zu werden - und die Antwort aendert sich selten.
_CHECK_TTL = 3600.0
_check_cache: Dict[str, Tuple[float, dict]] = {}

# Zuordnung fuer Container ohne Labels (Installationen vor dieser Fassung).
LEGACY_MAP: Dict[str, dict] = {
    "rimperio-backend":    {"origin": "rimperio", "update": "source", "component": "backend"},
    "rimperio-frontend":   {"origin": "rimperio", "update": "source", "component": "frontend"},
    "rimperio-syslog":     {"origin": "rimperio", "update": "source", "component": "syslog"},
    "rimperio-postgres":   {"origin": "upstream", "update": "image", "component": "postgres"},
    "rimperio-caddy":      {"origin": "upstream", "update": "image", "component": "caddy"},
    "rimperio-n8n":        {"origin": "upstream", "update": "image", "component": "n8n"},
    "rimperio-portainer":  {"origin": "upstream", "update": "image", "component": "portainer"},
    "rimperio-postfix":    {"origin": "upstream", "update": "image", "component": "postfix"},
    "rimperio-watchtower": {"origin": "upstream", "update": "image", "component": "watchtower"},
}

# Klartext fuer die Oberflaeche.
ROLE_HINTS: Dict[str, str] = {
    "backend": "API, Oberflächen-Logik, Patchmanagement, Terminal",
    "frontend": "Die Weboberfläche",
    "syslog": "Nimmt Syslog auf Port 514 entgegen (UDP und TCP)",
    "postgres": "Datenbank — hier liegen alle Logs",
    "caddy": "Reverse Proxy und TLS-Zertifikate",
    "n8n": "Automatisierung: Workflows, Benachrichtigungen",
    "portainer": "Container-Oberfläche im Browser",
    "postfix": "Mailversand",
    "openwebui": "KI-Oberfläche, auch für lokale Modelle",
    "tugtainer": "Prüft, ob für die fremden Images Updates bereitliegen",
    "watchtower": "Abgelöst durch Tugtainer — kann entfernt werden",
}

# Welcher Compose-Dienst gehoert zu welchem Container? Fuer `docker compose`.
SERVICE_BY_COMPONENT: Dict[str, str] = {
    "backend": "backend", "frontend": "frontend", "syslog": "syslog",
    "postgres": "postgres", "caddy": "caddy", "n8n": "n8n",
    "portainer": "portainer", "postfix": "postfix",
    "openwebui": "openwebui", "tugtainer": "tugtainer",
}

# Diese Dienste liegen hinter einem Compose-Profil - ohne `--profile` tut sich
# nichts.
PROFILED = {"portainer", "postfix", "n8n", "openwebui", "tugtainer", "watchtower"}


# =============================================================================
# Bestand aufnehmen
# =============================================================================
def _docker_available() -> bool:
    return hostexec.nsenter_available()


async def _docker_json(args: List[str], timeout: float = 25.0) -> List[dict]:
    """Fuehrt einen docker-Befehl aus, der je Zeile ein JSON-Objekt ausgibt."""
    result = await hostexec.run_host(["docker", *args], timeout=timeout)
    if not result.ok:
        logger.debug("docker %s fehlgeschlagen: %s", " ".join(args),
                     (result.stderr or result.error or "").strip())
        return []
    rows = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _classify(name: str, labels: Dict[str, str]) -> dict:
    """Wem gehoert dieser Container, und wie wird er aktualisiert?"""
    origin = (labels.get("de.rimperio.origin") or "").strip()
    update = (labels.get("de.rimperio.update") or "").strip()
    component = (labels.get("de.rimperio.component") or "").strip()

    if not origin:
        legacy = LEGACY_MAP.get(name)
        if legacy:
            origin, update, component = legacy["origin"], legacy["update"], legacy["component"]

    if not component:
        # Aus dem Namen ableiten: rimperio-app-backend -> backend
        component = re.sub(r"^rimperio-(app|ext)-", "", name) or name

    if not origin:
        # Weder Label noch bekannter Name: gehoert nicht zu RIMPERIO.
        origin, update = "foreign", "image"

    return {"origin": origin, "update": update or "image", "component": component}


async def inventory(include_foreign: bool = True) -> dict:
    """Alle Container - einsortiert nach Herkunft."""
    if not _docker_available():
        return {
            "available": False,
            "reason": ("Kein Zugriff auf den Host. Die Container-Übersicht braucht ein "
                       "Backend mit privileged/pid:host (Standard-docker-compose.yml). "
                       "Auf der Kommandozeile geht es weiterhin: 'docker ps'."),
            "containers": [],
        }

    rows = await _docker_json(["ps", "-a", "--no-trunc", "--format", "{{json .}}"])
    items: List[dict] = []

    for row in rows:
        name = (row.get("Names") or "").split(",")[0].strip()
        if not name:
            continue

        labels: Dict[str, str] = {}
        for pair in (row.get("Labels") or "").split(","):
            if "=" in pair:
                key, _, value = pair.partition("=")
                labels[key.strip()] = value.strip()

        kind = _classify(name, labels)
        is_ours = kind["origin"] in ("rimperio", "upstream")
        if not is_ours and not include_foreign:
            continue

        state = (row.get("State") or "").lower()
        items.append({
            "name": name,
            "image": row.get("Image") or "",
            "state": state,
            "status": row.get("Status") or "",
            "running": state == "running",
            "created": row.get("CreatedAt") or "",
            "ports": row.get("Ports") or "",
            "origin": kind["origin"],
            "update_path": kind["update"],
            "component": kind["component"],
            "service": SERVICE_BY_COMPONENT.get(kind["component"], ""),
            "profile": kind["component"] if kind["component"] in PROFILED else "",
            "role": (labels.get("de.rimperio.role")
                     or ROLE_HINTS.get(kind["component"], "")),
            "critical": labels.get("de.rimperio.critical") == "true",
            "stack": labels.get("de.rimperio.stack") or ("core" if is_ours else ""),
            # Wird erst durch check_updates() gefuellt.
            "update": None,
        })

    order = {"rimperio": 0, "upstream": 1, "foreign": 2}
    items.sort(key=lambda item: (order.get(item["origin"], 9), item["name"]))

    own = [i for i in items if i["origin"] == "rimperio"]
    upstream = [i for i in items if i["origin"] == "upstream"]
    foreign = [i for i in items if i["origin"] == "foreign"]

    return {
        "available": True,
        "containers": items,
        "groups": [
            {
                "key": "rimperio",
                "title": "RIMPERIO selbst",
                "explain": ("Aus dem Quellcode dieses Projekts gebaut. Es gibt kein "
                            "fertiges Image — aktualisiert wird über RIMPERIO-Server → Updates."),
                "update_hint": "RIMPERIO-Server → Updates",
                "items": own,
            },
            {
                "key": "upstream",
                "title": "Fremde Dienste, die RIMPERIO benutzt",
                "explain": ("Ganz normale Images (PostgreSQL, Caddy, …). Sie gehören "
                            "nicht zu RIMPERIO und brauchen ihre eigenen, regelmäßigen "
                            "Updates — die laufen hier."),
                "update_hint": "Hier auf dieser Seite",
                "items": upstream,
            },
            {
                "key": "foreign",
                "title": "Andere Container auf diesem Server",
                "explain": ("Läuft auf demselben Docker, gehört aber nicht zu RIMPERIO. "
                            "Nur zur Information — angefasst wird hier nichts."),
                "update_hint": "",
                "items": foreign,
            },
        ],
        "counts": {"rimperio": len(own), "upstream": len(upstream), "foreign": len(foreign)},
    }


# =============================================================================
# Update-Pruefung: die Registry fragen, nichts herunterladen
# =============================================================================
def parse_image(reference: str) -> Optional[dict]:
    """Zerlegt 'ghcr.io/foo/bar:1' in Registry, Repository und Tag."""
    reference = (reference or "").strip()
    if not reference or reference.startswith("sha256:"):
        return None

    # Ein Abdruck statt eines Tags: dann ist der Stand festgenagelt, da gibt es
    # nichts zu pruefen.
    if "@" in reference:
        reference = reference.split("@", 1)[0]

    remainder, _, tag = reference.rpartition(":")
    if not remainder or "/" in tag:
        remainder, tag = reference, "latest"

    parts = remainder.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry, repository = parts[0], "/".join(parts[1:])
    else:
        registry, repository = "registry-1.docker.io", remainder
        if "/" not in repository:
            # Offizielle Images liegen unter library/.
            repository = f"library/{repository}"

    if registry in ("docker.io", "index.docker.io"):
        registry = "registry-1.docker.io"

    return {"registry": registry, "repository": repository, "tag": tag,
            "reference": f"{remainder}:{tag}"}


async def _registry_token(client: httpx.AsyncClient, registry: str, repository: str) -> str:
    """Holt einen Lesezugriff-Token. Oeffentliche Images brauchen keine Anmeldung."""
    if registry == "registry-1.docker.io":
        url = ("https://auth.docker.io/token?service=registry.docker.io"
               f"&scope=repository:{repository}:pull")
    elif registry == "ghcr.io":
        url = f"https://ghcr.io/token?scope=repository:{repository}:pull"
    else:
        # Andere Registries: erst ohne Token versuchen. Verlangt sie einen,
        # steht im 401 ein WWW-Authenticate - dem folgen wir unten.
        return ""
    try:
        response = await client.get(url, timeout=12.0)
        if response.status_code == 200:
            data = response.json()
            return data.get("token") or data.get("access_token") or ""
    except (httpx.HTTPError, ValueError) as exc:
        logger.debug("Kein Registry-Token für %s/%s: %s", registry, repository, exc)
    return ""


_ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])


async def remote_digest(image: str) -> Tuple[str, str]:
    """Welchen Abdruck fuehrt die Registry fuer diesen Tag? Gibt (Digest, Fehler)."""
    parsed = parse_image(image)
    if not parsed:
        return "", "Das Image ist auf einen festen Abdruck genagelt — da ändert sich nichts."
    if parsed["registry"] in ("localhost", "localhost:5000") or parsed["reference"].endswith(":local"):
        return "", "Selbst gebautes Image — es gibt keine Registry, die man fragen könnte."

    url = (f"https://{parsed['registry']}/v2/{parsed['repository']}"
           f"/manifests/{parsed['tag']}")
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            headers = {"Accept": _ACCEPT}
            token = await _registry_token(client, parsed["registry"], parsed["repository"])
            if token:
                headers["Authorization"] = f"Bearer {token}"

            response = await client.head(url, headers=headers)
            if response.status_code == 401 and not token:
                # Registry verlangt doch einen Token und sagt im Kopf, woher.
                challenge = response.headers.get("www-authenticate", "")
                realm = re.search(r'realm="([^"]+)"', challenge)
                service = re.search(r'service="([^"]+)"', challenge)
                if realm:
                    token_url = realm.group(1)
                    params = {"scope": f"repository:{parsed['repository']}:pull"}
                    if service:
                        params["service"] = service.group(1)
                    token_response = await client.get(token_url, params=params, timeout=12.0)
                    if token_response.status_code == 200:
                        token = (token_response.json().get("token")
                                 or token_response.json().get("access_token") or "")
                        headers["Authorization"] = f"Bearer {token}"
                        response = await client.head(url, headers=headers)

            if response.status_code == 404:
                return "", f"Die Registry kennt '{parsed['reference']}' nicht."
            if response.status_code >= 400:
                return "", (f"Die Registry hat mit HTTP {response.status_code} geantwortet "
                            f"(evtl. ein privates Image, dafür braucht es eine Anmeldung).")

            digest = response.headers.get("docker-content-digest", "")
            if not digest:
                # Manche Registries liefern den Abdruck nur bei GET mit.
                response = await client.get(url, headers=headers)
                digest = response.headers.get("docker-content-digest", "")
            return digest, "" if digest else "Die Registry nennt keinen Abdruck."
    except httpx.HTTPError as exc:
        return "", f"Registry nicht erreichbar: {exc}"
    except Exception as exc:                                        # defensiv
        return "", f"Prüfung fehlgeschlagen: {exc}"


async def local_digests(image: str) -> List[str]:
    """Welche Abdruecke traegt das lokal vorhandene Image?"""
    result = await hostexec.run_host(
        ["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"],
        timeout=20.0)
    if not result.ok:
        return []
    try:
        entries = json.loads((result.stdout or "[]").strip() or "[]")
    except json.JSONDecodeError:
        return []
    return [str(entry).split("@", 1)[-1] for entry in entries if "@" in str(entry)]


async def check_image(image: str, force: bool = False) -> dict:
    """Steht fuer dieses Image ein Update an?"""
    cached = _check_cache.get(image)
    if cached and not force and time.time() - cached[0] < _CHECK_TTL:
        return {**cached[1], "cached": True}

    parsed = parse_image(image)
    outcome = {
        "image": image,
        "reference": (parsed or {}).get("reference", image),
        "checked_at": time.time(),
        "update_available": False,
        "local_digest": "",
        "remote_digest": "",
        "error": "",
        "cached": False,
    }

    if image.endswith(":local"):
        outcome["error"] = ("Dieses Image wird hier gebaut — eine Registry gibt es dafür "
                            "nicht. Updates laufen über RIMPERIO-Server → Updates.")
        _check_cache[image] = (time.time(), outcome)
        return outcome

    digest, error = await remote_digest(image)
    if error:
        outcome["error"] = error
        _check_cache[image] = (time.time(), outcome)
        return outcome

    locals_ = await local_digests(image)
    outcome["remote_digest"] = digest
    outcome["local_digest"] = locals_[0] if locals_ else ""

    if not locals_:
        outcome["error"] = ("Das lokale Image trägt keinen Abdruck (typisch für selbst "
                            "gebaute Images) — ein Vergleich ist nicht möglich.")
    else:
        outcome["update_available"] = digest not in locals_

    _check_cache[image] = (time.time(), outcome)
    return outcome


async def check_updates(force: bool = False, include_foreign: bool = False) -> dict:
    """Prueft alle Container, deren Update ueber ein Image laeuft."""
    data = await inventory(include_foreign=include_foreign)
    if not data.get("available"):
        return data

    targets = [item for item in data["containers"]
               if item["update_path"] == "image" and item["image"]]

    # Nebenlaeufig, aber gebremst: fuenf gleichzeitige Abfragen reichen und
    # bringen keine Registry gegen uns auf.
    gate = asyncio.Semaphore(5)

    async def one(item: dict) -> None:
        async with gate:
            item["update"] = await check_image(item["image"], force=force)

    await asyncio.gather(*(one(item) for item in targets), return_exceptions=True)

    pending = [item["name"] for item in targets
               if (item.get("update") or {}).get("update_available")]
    data["updates_pending"] = pending
    data["checked_at"] = time.time()
    data["summary"] = (
        f"{len(pending)} von {len(targets)} fremden Images haben ein Update."
        if targets else "Es gibt keine fremden Images zu prüfen."
    )
    return data


# =============================================================================
# Einspielen - immer auf Ansage, nie von selbst
# =============================================================================
async def _compose(args: List[str], timeout: float = 600.0) -> hostexec.HostResult:
    install_dir = hostexec.host_paths()["install_dir"]
    quoted = " ".join(f"'{a}'" for a in args)
    return await hostexec.run_host(
        ["sh", "-c", f"cd '{install_dir}' && docker compose {quoted} 2>&1"],
        timeout=timeout)


async def pull_and_recreate(component: str) -> dict:
    """Holt ein neueres Image und erzeugt genau diesen einen Container neu.

    Bewusst nur dieser eine: ein `docker compose up -d` ueber den ganzen Stack
    wuerde nebenbei auch alles andere anfassen, und das will niemand, der
    gerade nur PostgreSQL aktualisieren wollte.
    """
    service = SERVICE_BY_COMPONENT.get(component)
    if not service:
        raise ValueError(f"Unbekannter Container '{component}'.")
    if not _docker_available():
        raise RuntimeError("Kein Zugriff auf den Host — von hier aus geht das nicht.")

    profile: List[str] = ["--profile", service] if service in PROFILED else []

    pull = await _compose([*profile, "pull", service], timeout=900.0)
    if not pull.ok:
        raise RuntimeError(f"Das Image konnte nicht geholt werden: "
                           f"{(pull.stdout or pull.stderr or '').strip()[-1500:]}")

    up = await _compose([*profile, "up", "-d", service], timeout=600.0)
    if not up.ok:
        raise RuntimeError(f"Der Container ließ sich nicht neu erzeugen: "
                           f"{(up.stdout or up.stderr or '').strip()[-1500:]}")

    # Der Zwischenspeicher ist jetzt falsch.
    _check_cache.clear()

    return {
        "component": component,
        "service": service,
        "output": ((pull.stdout or "") + "\n" + (up.stdout or "")).strip()[-4000:],
        "message": f"{component} läuft jetzt mit dem neueren Image.",
    }


async def lifecycle(component: str, action: str) -> dict:
    """start / stop / restart fuer einen einzelnen Container."""
    if action not in ("start", "stop", "restart"):
        raise ValueError(f"Unbekannte Aktion '{action}'.")
    service = SERVICE_BY_COMPONENT.get(component)
    if not service:
        raise ValueError(f"Unbekannter Container '{component}'.")
    if not _docker_available():
        raise RuntimeError("Kein Zugriff auf den Host — von hier aus geht das nicht.")
    if component == "backend" and action in ("stop", "restart"):
        # Das Backend ist genau der Prozess, der diesen Aufruf gerade bearbeitet.
        raise ValueError("Das Backend kann sich nicht selbst stoppen. Dafür gibt es "
                         "den Neustart unter RIMPERIO-Server → Neustart.")

    profile: List[str] = ["--profile", service] if service in PROFILED else []
    result = await _compose([*profile, action, service], timeout=180.0)
    output = (result.stdout or result.stderr or "").strip()[-2000:]
    if not result.ok:
        raise RuntimeError(f"'{action}' ist fehlgeschlagen: {output}")
    return {"component": component, "action": action, "output": output}


async def logs(name: str, lines: int = 200) -> List[str]:
    """Die letzten Protokollzeilen eines Containers - fuer die Fehlersuche."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}", name or ""):
        raise ValueError("Unzulässiger Containername.")
    if not _docker_available():
        return []
    result = await hostexec.run_host(
        ["docker", "logs", "--tail", str(max(10, min(int(lines), 2000))), name],
        timeout=30.0)
    return hostexec.split_lines((result.stdout or "") + (result.stderr or ""), limit=lines)


async def prune_images() -> dict:
    """Raeumt Images weg, die kein Container mehr benutzt.

    Nach mehreren Updates liegen die alten Staende sonst als Karteileichen
    herum und fressen genau den Platz, den der Plattenwaechter gerade
    freigeraeumt hat.
    """
    if not _docker_available():
        raise RuntimeError("Kein Zugriff auf den Host.")
    result = await hostexec.run_host(["docker", "image", "prune", "-af"], timeout=300.0)
    output = (result.stdout or result.stderr or "").strip()
    reclaimed = ""
    match = re.search(r"Total reclaimed space:\s*(.+)", output)
    if match:
        reclaimed = match.group(1).strip()
    return {"output": output[-2000:], "reclaimed": reclaimed,
            "message": (f"{reclaimed} freigegeben." if reclaimed
                        else "Es gab nichts wegzuräumen.")}
