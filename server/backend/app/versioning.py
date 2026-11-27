# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Version:     2026.11.27.14.00.00
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Versionen lesen und vergleichen (Semantic Versioning)
# ==============================================================================
"""
Versionen lesen und vergleichen - an genau einer Stelle.

Ab 0.0.1 folgt RIMPERIO dem allgemeinen Standard *Semantic Versioning 2.0.0*
(https://semver.org/lang/de/): ``MAJOR.MINOR.PATCH``, optional mit Vorab-
Kennung (``1.2.0-rc.1``) und Build-Angabe (``1.2.0+build.5``).

Davor trug jeder Stand einen Zeitstempel (``2026.11.25.22.00.00``). Diese
alten Versionen bleiben lesbar - fuer Sicherungen, Tags und Server, die noch
darauf stehen - und gelten **immer als aelter als jede SemVer-Version**. Sonst
saehe ``0.0.1`` neben ``2026.11.25`` wie ein Rueckschritt aus, und kein Server
bekaeme das erste SemVer-Release angeboten.

Rangfolge (``version_key``):

* unlesbar           -> ``None`` (kein Vergleich moeglich)
* alter Zeitstempel  -> ``(0, (jahr, monat, tag, ...), ())``
* SemVer             -> ``(1, (major, minor, patch), vorab)``

Die Vorab-Kennung ordnet nach SemVer 2.0.0, Abschnitt 11: ``1.0.0-alpha <
1.0.0-alpha.1 < 1.0.0-beta < 1.0.0-rc.1 < 1.0.0``. Die Build-Angabe zaehlt
nicht.
"""

import re
from typing import Optional

# Offizieller Ausdruck aus der SemVer-Spezifikation, ergaenzt um ein optionales
# fuehrendes "v" (Tag-Schreibweise v1.2.3).
SEMVER_RE = re.compile(
    r"^[vV]?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

# Altes Schema: JJJJ.MM.TT[.HH.MM.SS] bzw. Tags v2026.11.21.3. Ein Anhaengsel
# wie "-rc1" kippte den Vergleich schon frueher nicht - das bleibt so.
# Achtung: "v2026.11.25" waere formal auch gueltiges SemVer (Major 2026). Darum
# wird das alte Schema ZUERST geprueft - eine vierstellige Jahreszahl vorne ist
# immer ein alter Zeitstempel. Majors 1900-2199 sind damit nie SemVer.
LEGACY_RE = re.compile(r"^[vV]?((?:19|20|21)\d{2})\.(\d{1,2})\.(\d{1,2})((?:\.\d+)*)(?![\d])")


def _clean(value) -> str:
    text = str(value or "").strip()
    return text.splitlines()[0].strip() if text else ""


def is_legacy(value) -> bool:
    """Ist das eine Version im alten Zeitstempel-Schema?"""
    return bool(LEGACY_RE.match(_clean(value)))


def is_semver(value) -> bool:
    """Ist das eine gueltige SemVer-Version (mit oder ohne fuehrendes v)?"""
    text = _clean(value)
    return bool(SEMVER_RE.match(text)) and not LEGACY_RE.match(text)


def _prerelease_key(pre: Optional[str]) -> tuple:
    # Ohne Vorab-Kennung steht die Version ueber allen ihren Vorab-Staenden.
    if not pre:
        return (1,)
    ids = []
    for part in pre.split("."):
        # Zahlen vor Text, Zahlen numerisch, Text nach ASCII (SemVer 11.4).
        ids.append((0, int(part), "") if part.isdigit() else (1, 0, part))
    return (0, tuple(ids))


def version_key(value) -> Optional[tuple]:
    """Sortierschluessel einer Version; ``None``, wenn sie nicht lesbar ist."""
    text = _clean(value)
    if not text:
        return None
    m = LEGACY_RE.match(text)
    if m:
        numbers = [int(m.group(1)), int(m.group(2)), int(m.group(3))]
        numbers += [int(p) for p in m.group(4).split(".") if p]
        # Auf sechs Stellen begrenzt wie bisher; fehlende Stellen zaehlen als 0,
        # damit v2026.11.21 und 2026.11.21.00.00.00 gleich sind.
        numbers = (numbers + [0, 0, 0, 0, 0, 0])[:6]
        return (0, tuple(numbers), ())
    m = SEMVER_RE.match(text)
    if m:
        return (1, (int(m.group(1)), int(m.group(2)), int(m.group(3))), _prerelease_key(m.group(4)))
    return None


def compare_versions(a, b) -> Optional[int]:
    """-1 (a aelter), 0 (gleich), 1 (a neuer) - ``None``, wenn eine unlesbar ist."""
    ka, kb = version_key(a), version_key(b)
    if ka is None or kb is None:
        return None
    return (ka > kb) - (ka < kb)


def is_newer(candidate, reference) -> bool:
    """Ist ``candidate`` sicher neuer als ``reference``? Unlesbares -> False."""
    return compare_versions(candidate, reference) == 1


def is_prerelease(value) -> bool:
    """Traegt die Version eine SemVer-Vorab-Kennung (1.2.0-rc.1)?"""
    text = _clean(value)
    m = SEMVER_RE.match(text)
    return bool(m and m.group(4)) and not LEGACY_RE.match(text)
