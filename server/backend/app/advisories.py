# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Update-Quellen: RSS/Atom, CISA-KEV, GitHub-Releases, Websuche
# ==============================================================================
"""
Woher RIMPERIO weiss, was ein Update taugt.

Bevor ein Update freigegeben oder automatisch installiert wird, schaut
RIMPERIO nach, was die Welt darueber sagt:

* **Feeds** (RSS, Atom, RDF): Microsoft Security Update Guide, Borns
  Tech-Blog (bekannte Probleme mit Windows-Updates), BleepingComputer, Ubuntu-
  und Debian-Sicherheitsmeldungen, Proxmox-Ankuendigungen, Docker-Engine.
  Eigene Feeds lassen sich jederzeit dazunehmen.
* **CISA KEV**: der Katalog aktiv ausgenutzter Luecken. Behebt ein Update eine
  davon, ist es dringend und ueberspringt die Rueckstellung.
* **GitHub-Releases**: fuer Container-Images, deren Quelle auf GitHub liegt
  (Label org.opencontainers.image.source oder ghcr.io), werden die Release-
  Notizen automatisch als Quelle angelegt.
* **Websuche** (wahlweise): SearXNG (eigene Instanz), Brave Search API oder die
  eingebaute Websuche der KI (Claude). Wird nur bei der Einschaetzung
  einzelner Updates benutzt, nicht im Takt.

Jede Meldung wird auf Kennungen untersucht (KB-Nummern, CVEs, Paketnamen) und
so den Updates zugeordnet. Alles ist in der Oberflaeche einsehbar und
abschaltbar.
"""

from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, Iterable, List, Optional, Set

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import async_session
from .models import Advisory, Setting, UpdateSource

logger = logging.getLogger("rimperio.advisories")

SETTING_KEY = "advisory_config"
LOOP_SECONDS = 600
RETENTION_DAYS = 120
MAX_FEED_BYTES = 16 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (compatible; RIMPERIO-Updatequellen)"

# Die mitgelieferten Quellen. Abschaltbar, aber nicht loeschbar.
BUILTIN_SOURCES = [
    {"key": "msrc", "name": "Microsoft Security Update Guide", "kind": "rss",
     "url": "https://api.msrc.microsoft.com/update-guide/rss", "platforms": ["windows"],
     "interval_minutes": 360, "language": "en",
     "note": "Alle Sicherheitslücken, die Microsoft mit Updates behebt (CVE-genau)."},
    {"key": "borncity", "name": "Borns Tech and Windows World", "kind": "rss",
     "url": "https://borncity.com/blog/feed/", "platforms": ["windows", "linux"],
     "interval_minutes": 180, "language": "de",
     "note": "Deutschsprachig. Meldet früh, wenn Windows-Updates Probleme machen."},
    {"key": "bleeping", "name": "BleepingComputer", "kind": "rss",
     "url": "https://www.bleepingcomputer.com/feed/", "platforms": ["windows", "linux", "docker"],
     "interval_minutes": 180, "language": "en",
     "note": "Sicherheitsnachrichten, bekannte Update-Probleme, Rückzüge von Updates."},
    {"key": "ubuntu_usn", "name": "Ubuntu Security Notices", "kind": "rss",
     "url": "https://ubuntu.com/security/notices/rss.xml", "platforms": ["linux"],
     "interval_minutes": 360, "language": "en", "note": "USN — inklusive Regressionen."},
    {"key": "debian_dsa", "name": "Debian Security Advisories", "kind": "rss",
     "url": "https://www.debian.org/security/dsa", "platforms": ["linux"],
     "interval_minutes": 360, "language": "en", "note": "DSA."},
    {"key": "proxmox", "name": "Proxmox Ankündigungen", "kind": "rss",
     "url": "https://forum.proxmox.com/forums/announcements.7/index.rss", "platforms": ["linux"],
     "interval_minutes": 720, "language": "en", "note": "Neue PVE/PBS-Versionen und bekannte Probleme."},
    {"key": "docker_engine", "name": "Docker Engine (moby) Releases", "kind": "github",
     "url": "https://github.com/moby/moby/releases.atom", "platforms": ["linux", "docker"],
     "interval_minutes": 720, "language": "en", "note": "Versionshinweise der Docker-Engine."},
    {"key": "cisa_kev", "name": "CISA Known Exploited Vulnerabilities", "kind": "kev",
     "url": "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
     "platforms": ["windows", "linux", "docker"], "interval_minutes": 720, "language": "en",
     "note": "Aktiv ausgenutzte Lücken. Ein Update, das eine davon schließt, gilt als dringend."},
]

DEFAULT_CONFIG = {
    "websearch": {
        "provider": "ai",          # off | ai | searxng | brave
        "url": "",                 # SearXNG: https://searx.example
        "api_key": "",             # Brave
        "max_results": 5,
    },
    "github_auto": True,           # Release-Notizen fuer Container-Images automatisch anlegen
}

_KB = re.compile(r"\bKB\s?(\d{6,8})\b", re.I)
_CVE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.I)
_WORD = re.compile(r"[^\W_][\w.+-]{1,60}")
_TAGS = re.compile(r"<[^>]+>")
_STOP = {"the", "and", "for", "with", "update", "updates", "security", "vulnerability",
         "vulnerabilities", "fix", "fixes", "issue", "issues", "new", "version", "release",
         "der", "die", "das", "und", "mit", "für", "von", "ein", "eine", "regression",
         "remote", "code", "execution", "denial", "service", "elevation", "privilege"}


# =============================================================================
# Einstellungen
# =============================================================================
async def load_config(db: AsyncSession) -> dict:
    row = (await db.execute(select(Setting).where(Setting.key == SETTING_KEY))).scalar_one_or_none()
    cfg = {"websearch": dict(DEFAULT_CONFIG["websearch"]), "github_auto": DEFAULT_CONFIG["github_auto"]}
    if row and isinstance(row.value, dict):
        cfg["websearch"].update({k: v for k, v in (row.value.get("websearch") or {}).items()
                                 if k in cfg["websearch"]})
        if "github_auto" in row.value:
            cfg["github_auto"] = bool(row.value["github_auto"])
    return cfg


def public_config(cfg: dict) -> dict:
    ws = {k: v for k, v in cfg["websearch"].items() if k != "api_key"}
    ws["api_key_set"] = bool(cfg["websearch"].get("api_key"))
    return {"websearch": ws, "github_auto": cfg["github_auto"]}


async def save_config(db: AsyncSession, incoming: dict) -> dict:
    cfg = await load_config(db)
    ws = incoming.get("websearch") or {}
    if "provider" in ws:
        if ws["provider"] not in ("off", "ai", "searxng", "brave"):
            raise ValueError("Websuche: off, ai, searxng oder brave.")
        cfg["websearch"]["provider"] = ws["provider"]
    if "url" in ws:
        url = str(ws["url"] or "").strip()
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("Die SearXNG-Adresse muss mit http:// oder https:// beginnen.")
        cfg["websearch"]["url"] = url
    if ws.get("api_key"):
        cfg["websearch"]["api_key"] = str(ws["api_key"]).strip()
    if ws.get("clear_api_key"):
        cfg["websearch"]["api_key"] = ""
    if "max_results" in ws:
        cfg["websearch"]["max_results"] = max(1, min(int(ws["max_results"]), 10))
    if "github_auto" in incoming:
        cfg["github_auto"] = bool(incoming["github_auto"])
    p = cfg["websearch"]
    if p["provider"] == "searxng" and not p["url"]:
        raise ValueError("Für SearXNG fehlt die Adresse.")
    if p["provider"] == "brave" and not p["api_key"]:
        raise ValueError("Für Brave Search fehlt der API-Schlüssel.")
    row = (await db.execute(select(Setting).where(Setting.key == SETTING_KEY))).scalar_one_or_none()
    if row:
        row.value = cfg
    else:
        db.add(Setting(key=SETTING_KEY, value=cfg, description="Update-Quellen und Websuche"))
    await db.commit()
    return public_config(cfg)


async def ensure_builtin(db: AsyncSession) -> None:
    have = {s.key for s in (await db.execute(select(UpdateSource).where(UpdateSource.key.is_not(None)))).scalars()}
    for spec in BUILTIN_SOURCES:
        if spec["key"] not in have:
            db.add(UpdateSource(builtin=True, enabled=True, **spec))
    await db.commit()


def public_source(s: UpdateSource) -> dict:
    return {"id": s.id, "key": s.key, "name": s.name, "kind": s.kind, "url": s.url,
            "platforms": s.platforms or [], "enabled": s.enabled, "builtin": s.builtin,
            "interval_minutes": s.interval_minutes, "language": s.language, "note": s.note,
            "last_fetch_at": _iso(s.last_fetch_at), "last_status": s.last_status,
            "item_count": s.item_count}


def public_advisory(a: Advisory, source_name: str = "") -> dict:
    return {"id": a.id, "source_id": a.source_id, "source": source_name, "title": a.title,
            "link": a.link, "summary": (a.summary or "")[:600],
            "published_at": _iso(a.published_at), "refs": a.refs or {}}


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


# =============================================================================
# Kennungen aus Text ziehen
# =============================================================================
def extract_refs(*texts: str) -> dict:
    blob = " ".join(t or "" for t in texts)
    kbs = sorted({f"KB{m}" for m in _KB.findall(blob)})
    cves = sorted({m.upper() for m in _CVE.findall(blob)})
    title = (texts[0] if texts else "") or ""
    words = sorted({w.strip(".-_") for w in _WORD.findall(title.lower())
                    if len(w) > 2 and w not in _STOP and not w.isdigit()})[:40]
    return {"kb": kbs, "cve": cves, "words": words}


def _clean(html: str, limit: int = 4000) -> str:
    text = _TAGS.sub(" ", html or "")
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&#8217;|&#039;|&apos;", "'", text)
    text = re.sub(r"&quot;|&#8220;|&#8221;", '"', text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(microsecond=0)


# =============================================================================
# Feeds lesen
# =============================================================================
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child(el: ET.Element, *names: str) -> Optional[ET.Element]:
    for c in el:
        if _local(c.tag) in names:
            return c
    return None


def _text(el: Optional[ET.Element]) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def parse_feed(raw: bytes) -> List[dict]:
    """RSS 2.0, RSS 1.0 (RDF) und Atom in einheitliche Eintraege."""
    root = ET.fromstring(raw)
    entries = []
    for el in root.iter():
        name = _local(el.tag)
        if name not in ("item", "entry"):
            continue
        title = _text(_child(el, "title"))
        link_el = _child(el, "link")
        link = ""
        if link_el is not None:
            link = link_el.get("href") or _text(link_el)
        if not link:
            link = el.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about", "")
        guid = _text(_child(el, "guid", "id")) or link or title
        summary = _text(_child(el, "description", "summary", "content", "encoded"))
        date = _text(_child(el, "pubdate", "published", "updated", "date"))
        entries.append({"guid": guid[:500], "title": _clean(title, 1000), "link": link[:1000],
                        "summary": _clean(summary), "published_at": _date(date)})
    return entries


async def _get(url: str, timeout: float = 30.0) -> bytes:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT}) as client:
        async with client.stream("GET", url) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}")
            chunks, size = [], 0
            async for chunk in r.aiter_bytes():
                size += len(chunk)
                if size > MAX_FEED_BYTES * (8 if url.endswith(".json") else 1):
                    raise RuntimeError("Antwort zu groß")
                chunks.append(chunk)
    return b"".join(chunks)


async def fetch_entries(source: UpdateSource) -> List[dict]:
    raw = await _get(source.url)
    if source.kind == "kev":
        import json
        data = json.loads(raw)
        out = []
        for v in data.get("vulnerabilities", []):
            cve = v.get("cveID", "")
            title = f"{cve}: {v.get('vendorProject', '')} {v.get('product', '')} — {v.get('vulnerabilityName', '')}"
            out.append({"guid": cve, "title": title[:1000],
                        "link": f"https://nvd.nist.gov/vuln/detail/{cve}",
                        "summary": _clean(f"{v.get('shortDescription', '')} Maßnahme: {v.get('requiredAction', '')}"),
                        "published_at": _date(v.get("dateAdded"))})
        return out
    return parse_feed(raw)


async def refresh_source(db: AsyncSession, source: UpdateSource) -> int:
    """Holt eine Quelle und legt neue Meldungen an. Gibt die Zahl neuer Meldungen."""
    try:
        entries = await fetch_entries(source)
    except Exception as exc:
        source.last_fetch_at = datetime.utcnow()
        source.last_status = f"Fehler: {exc}"[:500]
        await db.commit()
        return 0
    known = {g for (g,) in (await db.execute(
        select(Advisory.guid).where(Advisory.source_id == source.id))).all()}
    added = 0
    for e in entries:
        if not e["guid"] or e["guid"] in known:
            continue
        known.add(e["guid"])
        refs = extract_refs(e["title"], e["summary"])
        if source.kind == "kev":
            refs["kev"] = True
        db.add(Advisory(source_id=source.id, guid=e["guid"], title=e["title"], link=e["link"],
                        summary=e["summary"], published_at=e["published_at"] or datetime.utcnow(),
                        refs=refs))
        added += 1
    source.last_fetch_at = datetime.utcnow()
    source.last_status = f"ok — {len(entries)} Einträge, {added} neu"
    await db.commit()
    from sqlalchemy import func
    source.item_count = (await db.execute(select(func.count()).select_from(Advisory)
                                          .where(Advisory.source_id == source.id))).scalar_one()
    await db.commit()
    return added


async def ensure_github_source(db: AsyncSession, repo: str, platform: str = "docker") -> Optional[UpdateSource]:
    """Legt fuer ein GitHub-Repository (owner/name) die Release-Quelle an."""
    repo = repo.strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        return None
    key = f"gh:{repo.lower()}"[:64]
    src = (await db.execute(select(UpdateSource).where(UpdateSource.key == key))).scalar_one_or_none()
    if src is None:
        src = UpdateSource(key=key, name=f"GitHub-Releases {repo}", kind="github",
                           url=f"https://github.com/{repo}/releases.atom", platforms=[platform],
                           enabled=True, builtin=False, interval_minutes=720,
                           note="Automatisch angelegt für ein Container-Image.")
        db.add(src)
        await db.commit()
        await db.refresh(src)
    return src


def github_repo_for_image(image: str, labels: Optional[dict] = None) -> Optional[str]:
    src = (labels or {}).get("org.opencontainers.image.source", "")
    m = re.match(r"https?://github\.com/([^/]+/[^/#?]+)", src or "")
    if m:
        return m.group(1).removesuffix(".git")
    m = re.match(r"ghcr\.io/([^/:@]+/[^/:@]+)", image or "")
    if m:
        return m.group(1)
    return None


# =============================================================================
# Zuordnen
# =============================================================================
class Index:
    """Alle aktuellen Meldungen, nach Kennung aufgeschluesselt."""

    def __init__(self) -> None:
        self.by_kb: Dict[str, List[int]] = {}
        self.by_cve: Dict[str, List[int]] = {}
        self.by_word: Dict[str, List[int]] = {}
        self.by_source: Dict[int, List[int]] = {}
        self.kev: Set[str] = set()
        self.items: Dict[int, dict] = {}

    def add(self, a: Advisory, source: UpdateSource) -> None:
        refs = a.refs or {}
        if refs.get("kev"):
            self.kev.update(refs.get("cve") or [])
            return                                   # KEV nur als Merkmal, nicht als Treffer
        self.items[a.id] = {"id": a.id, "title": a.title, "link": a.link,
                            "source": source.name, "published_at": _iso(a.published_at),
                            "summary": (a.summary or "")[:500]}
        for kb in refs.get("kb") or []:
            self.by_kb.setdefault(kb.upper(), []).append(a.id)
        for cve in refs.get("cve") or []:
            self.by_cve.setdefault(cve.upper(), []).append(a.id)
        for w in refs.get("words") or []:
            self.by_word.setdefault(w, []).append(a.id)
        self.by_source.setdefault(source.id, []).append(a.id)


async def build_index(db: AsyncSession, days: int = 90) -> Index:
    idx = Index()
    since = datetime.utcnow() - timedelta(days=days)
    sources = {s.id: s for s in (await db.execute(select(UpdateSource).where(UpdateSource.enabled.is_(True)))).scalars()}
    if not sources:
        return idx
    rows = (await db.execute(select(Advisory).where(Advisory.source_id.in_(list(sources)))
                             .where((Advisory.published_at >= since) | (Advisory.source_id.in_(
                                 [s.id for s in sources.values() if s.kind == "kev"]))))).scalars()
    for a in rows:
        idx.add(a, sources[a.source_id])
    return idx


def _package_words(name: str) -> Iterable[str]:
    name = (name or "").lower()
    head, _, arch = name.rpartition(".")
    if head and arch in ("x86_64", "noarch", "i686", "aarch64", "armv7hl", "ppc64le", "s390x"):
        name = head
    yield name
    base = re.sub(r"[-_]?(libs?|dev|common|bin|data|utils|core|server|client)$", "", name)
    base = re.sub(r"\d+(\.\d+)*$", "", base).rstrip("-.")
    if base and base != name:
        yield base
    if name.startswith("lib") and len(name) > 5:
        yield re.sub(r"\d+(\.\d+)*$", "", name[3:]).rstrip("-.")


def match(idx: Index, item: dict, limit: int = 8) -> List[dict]:
    """Passende Meldungen fuer ein Update (dict wie PatchItem-Felder + data)."""
    hits: Dict[int, int] = {}
    data = item.get("data") or {}
    for kb in [k.strip().upper() for k in (item.get("kb") or "").split(",") if k.strip()]:
        for aid in idx.by_kb.get(kb, []):
            hits[aid] = hits.get(aid, 0) + 3
    for cve in data.get("cves") or []:
        for aid in idx.by_cve.get(str(cve).upper(), []):
            hits[aid] = hits.get(aid, 0) + 2
    if item.get("platform") == "linux":
        for w in set(_package_words(data.get("package") or item.get("key") or "")):
            if len(w) > 2:
                for aid in idx.by_word.get(w, []):
                    hits[aid] = hits.get(aid, 0) + 1
    if item.get("platform") == "docker":
        image = str(data.get("image") or "")
        repo = image.split("@")[0].rsplit(":", 1)[0].rsplit("/", 1)[-1].lower()
        for aid in idx.by_word.get(repo, []):
            hits[aid] = hits.get(aid, 0) + 1
        gh_source = data.get("_github_source_id")
        if gh_source:
            for aid in idx.by_source.get(gh_source, [])[-5:]:
                hits[aid] = hits.get(aid, 0) + 2
    ranked = sorted(hits.items(), key=lambda kv: (-kv[1], -(kv[0])))[:limit]
    return [idx.items[aid] for aid, _ in ranked if aid in idx.items]


def is_urgent(idx: Index, item: dict) -> bool:
    cves = {str(c).upper() for c in ((item.get("data") or {}).get("cves") or [])}
    return bool(cves & idx.kev)


# =============================================================================
# Websuche
# =============================================================================
async def web_search(cfg: dict, query: str) -> List[dict]:
    """[{title, url, snippet}] - leer, wenn aus oder nicht erreichbar."""
    ws = cfg.get("websearch") or {}
    provider = ws.get("provider", "off")
    n = int(ws.get("max_results") or 5)
    try:
        async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": USER_AGENT}) as client:
            if provider == "searxng" and ws.get("url"):
                r = await client.get(ws["url"].rstrip("/") + "/search",
                                     params={"q": query, "format": "json"})
                r.raise_for_status()
                return [{"title": x.get("title", ""), "url": x.get("url", ""),
                         "snippet": _clean(x.get("content", ""), 400)}
                        for x in (r.json().get("results") or [])[:n]]
            if provider == "brave" and ws.get("api_key"):
                r = await client.get("https://api.search.brave.com/res/v1/web/search",
                                     params={"q": query, "count": n},
                                     headers={"X-Subscription-Token": ws["api_key"],
                                              "Accept": "application/json"})
                r.raise_for_status()
                return [{"title": x.get("title", ""), "url": x.get("url", ""),
                         "snippet": _clean(x.get("description", ""), 400)}
                        for x in ((r.json().get("web") or {}).get("results") or [])[:n]]
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("Websuche (%s) fehlgeschlagen: %s", provider, exc)
    return []


# =============================================================================
# Hintergrund
# =============================================================================
async def refresh_due(force: bool = False) -> int:
    added = 0
    async with async_session() as db:
        await ensure_builtin(db)
        now = datetime.utcnow()
        sources = (await db.execute(select(UpdateSource).where(UpdateSource.enabled.is_(True)))).scalars().all()
        for s in sources:
            due = (s.last_fetch_at is None or force or
                   now - s.last_fetch_at >= timedelta(minutes=max(15, s.interval_minutes or 360)))
            if due:
                added += await refresh_source(db, s)
        kev_ids = [s.id for s in sources if s.kind == "kev"]
        await db.execute(delete(Advisory).where(
            Advisory.published_at < now - timedelta(days=RETENTION_DAYS),
            Advisory.source_id.notin_(kev_ids or [-1])))
        await db.commit()
    return added


async def loop() -> None:
    await asyncio.sleep(45)
    while True:
        try:
            added = await refresh_due()
            if added:
                logger.info("Update-Quellen: %s neue Meldungen", added)
        except Exception as exc:                                    # defensiv
            logger.warning("Update-Quellen: %s", exc)
        await asyncio.sleep(LOOP_SECONDS)
