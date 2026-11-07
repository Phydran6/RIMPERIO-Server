# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Herkunft, Lizenz und haeufige Fragen
# ==============================================================================
"""
Woher kommt dieses Programm, wem gehoert es, und wo kann man nachsehen?

Warum das ein eigener Bereich ist: In der Fusszeile stand bisher
"© 2026 RIMPERIO. All rights reserved." Das war schlicht falsch. RIMPERIO steht
unter der MIT-Lizenz - jeder darf es benutzen, aendern und weitergeben.
"Alle Rechte vorbehalten" behauptet das Gegenteil und schreckt genau die
Leute ab, die sich den Quellcode ansehen sollen.

An die Stelle tritt etwas Nuetzlicheres: ein Bereich, der sagt, was das hier
ist, woher es kommt, unter welcher Lizenz es steht und wo der Quellcode liegt -
mit Verweisen, die man anklicken kann. Dazu die Fragen, die immer wieder
kommen.

Die Antworten stehen hier im Code und nicht in der Datenbank. Das ist Absicht:
sie gehoeren zum Programmstand, wandern mit jedem Update mit und koennen nicht
aus Versehen leer sein.
"""

import os
from typing import List

from fastapi import APIRouter, Depends

from ..auth import get_current_user
from ..config import settings

router = APIRouter(prefix="/api/about", tags=["Über RIMPERIO"])

REPO_SLUG = os.getenv("RIMPERIO_REPO_SLUG", "Phydran6/RIMPERIO-Server")
REPO_URL = f"https://github.com/{REPO_SLUG}"


def _links() -> List[dict]:
    """Alles, was mit dem Repository zu tun hat - an einer Stelle."""
    return [
        {"key": "repo", "label": "Quellcode auf GitHub", "url": REPO_URL,
         "hint": "Das ganze Programm, Zeile für Zeile nachlesbar."},
        {"key": "releases", "label": "Releases und Versionsgeschichte",
         "url": f"{REPO_URL}/releases",
         "hint": "Was in welcher Fassung dazugekommen ist."},
        {"key": "changelog", "label": "Changelog im Repository",
         "url": f"{REPO_URL}/tree/main/docs/changelog",
         "hint": "Nach Bereichen getrennt: Backend, Frontend, Agents, Datenbank."},
        {"key": "issues", "label": "Fehler melden und Wünsche äußern",
         "url": f"{REPO_URL}/issues",
         "hint": "Auch der richtige Ort für Fragen, die hier nicht beantwortet sind."},
        {"key": "license", "label": "Lizenz (MIT)", "url": f"{REPO_URL}/blob/main/LICENSE",
         "hint": "Der vollständige Lizenztext."},
        {"key": "docs", "label": "Dokumentation", "url": f"{REPO_URL}/tree/main/docs",
         "hint": "Installation, Betrieb, Sicherung, Updates, Anbindungen."},
        {"key": "install", "label": "Installationsskript",
         "url": f"{REPO_URL}/blob/main/install.sh",
         "hint": "Genau das Skript, das der Einzeiler herunterlädt. Vorher lesbar."},
        {"key": "compose", "label": "Container-Aufbau (docker-compose.yml)",
         "url": f"{REPO_URL}/blob/main/docker-compose.yml",
         "hint": "Welche Container es gibt, welche eigen und welche fremd sind."},
        {"key": "security", "label": "Sicherheitslücke melden",
         "url": f"{REPO_URL}/security/advisories/new",
         "hint": "Bitte nicht als öffentliches Issue — hier ist es vertraulich."},
    ]


FAQ: List[dict] = [
    {
        "key": "what",
        "category": "Grundsätzliches",
        "question": "Was ist RIMPERIO eigentlich?",
        "answer": (
            "Ein zentraler Log-Server zum Selbstbetreiben. Geräte im Netz — Server, "
            "Switches, Firewalls, die FRITZ!Box, Windows- und Linux-Rechner — schicken "
            "ihre Protokolle hierher, entweder per Syslog auf Port 514 oder per HTTPS "
            "über einen Agenten. Hier werden sie gesammelt, durchsuchbar gemacht, "
            "aufbewahrt und wieder aufgeräumt.\n\n"
            "Was RIMPERIO ausdrücklich *nicht* ist: ein Dienst in fremder Hand. Es läuft "
            "auf Ihrem Server, die Daten liegen in Ihrer Datenbank, und ohne dass Sie "
            "es einrichten, verlässt keine Zeile das Haus."
        ),
    },
    {
        "key": "origin",
        "category": "Grundsätzliches",
        "question": "Woher kommt das Programm, und wem gehört es?",
        "answer": (
            "RIMPERIO ist ein offenes Projekt von Phydran6. Der vollständige Quellcode "
            "liegt auf GitHub — nichts davon ist vorkompiliert, verschlüsselt oder "
            "sonstwie der Einsicht entzogen. Was auf diesem Server läuft, ist genau "
            "das, was dort steht: die Installation holt sich den Stand aus demselben "
            "Repository, und die Update-Seite zeigt, welcher Commit gerade installiert "
            "ist.\n\n"
            "Das ist der Sinn der Übung. Ein Log-Server sieht alles, was im Netz "
            "passiert. Bei so einem Programm sollte man nachlesen können, was es tut."
        ),
    },
    {
        "key": "license",
        "category": "Grundsätzliches",
        "question": "Unter welcher Lizenz steht RIMPERIO? Darf ich es einsetzen?",
        "answer": (
            "MIT-Lizenz. Sie dürfen RIMPERIO benutzen, verändern, weitergeben und auch "
            "kommerziell einsetzen. Bedingung ist im Wesentlichen nur, dass der "
            "Lizenztext mit dem Urheberhinweis erhalten bleibt.\n\n"
            "In der Fußzeile stand früher „All rights reserved“. Das war falsch und "
            "ist deshalb raus: bei einer MIT-Lizenz sind die Rechte gerade nicht "
            "vorbehalten, sie sind eingeräumt. Wie bei jeder freien Software gilt "
            "dabei: ohne Gewähr und ohne Haftung."
        ),
    },
    {
        "key": "containers",
        "category": "Betrieb",
        "question": "Welche Container gehören zu RIMPERIO — und welche nicht?",
        "answer": (
            "Drei Container werden aus diesem Quellcode gebaut und heißen deshalb "
            "`rimperio-app-*`: Backend, Frontend und der Syslog-Empfänger. Sie werden "
            "über RIMPERIO-Server → Updates aktualisiert; ein fertiges Image gibt es für sie "
            "nirgends.\n\n"
            "Alles andere sind fremde, ganz normale Images und heißt `rimperio-ext-*`: "
            "PostgreSQL, Caddy und, falls eingeschaltet, n8n, Portainer, Open WebUI, "
            "Postfix und Tugtainer. Die gehören nicht zu RIMPERIO und brauchen ihre "
            "eigenen, regelmäßigen Updates — zu finden unter RIMPERIO-Server → Container.\n\n"
            "Früher hießen alle `rimperio-…`, und damit sah es so aus, als gehörte "
            "PostgreSQL irgendwie dazu. Tut es nicht."
        ),
    },
    {
        "key": "updates",
        "category": "Betrieb",
        "question": "Aktualisiert sich RIMPERIO von selbst?",
        "answer": (
            "Nein, und das ist Absicht. RIMPERIO sieht nach, ob auf GitHub etwas Neues "
            "liegt, und sagt Bescheid. Eingespielt wird es erst auf Klick — mit "
            "Bestätigungswort, mit der Frage nach einer Sicherung vorher und mit "
            "mitlaufender Protokollausgabe.\n\n"
            "Dasselbe gilt für die fremden Images: Tugtainer (optional) prüft und "
            "meldet, ausgetauscht wird auf Ansage. Der Vorgänger Watchtower hat "
            "eigenmächtig getauscht — genau das will auf einem Log-Server niemand."
        ),
    },
    {
        "key": "retention",
        "category": "Betrieb",
        "question": "Was passiert, wenn die Festplatte voll läuft?",
        "answer": (
            "Der Plattenwächter sieht jede Minute nach und fängt ab 80 % Belegung an "
            "aufzuräumen — also lange bevor es eng wird. Er arbeitet sich vom "
            "Unwichtigsten nach oben: abgelaufene Einmal-Token, altes Systemtagebuch, "
            "dann Logzeilen jenseits der eingestellten Aufbewahrung, und wenn das "
            "nicht reicht, verkürzt er die Aufbewahrung schrittweise.\n\n"
            "Zwei Dinge tut er nicht: Er löscht niemals von selbst alles. Die letzten "
            "24 Stunden bleiben immer stehen — ein Log-Server ohne die letzten Stunden "
            "ist bei einem Zwischenfall wertlos. Und er startet kein VACUUM FULL, wenn "
            "der Platz dafür nicht da ist; genau daran ist die frühere Fassung "
            "gescheitert.\n\n"
            "Jeder Aufräumlauf steht mit Uhrzeit, Anlass und Zeilenzahl im "
            "Systemtagebuch."
        ),
    },
    {
        "key": "journal",
        "category": "Betrieb",
        "question": "Wie sehe ich, was auf dem System passiert ist?",
        "answer": (
            "RIMPERIO-Server → Systemtagebuch. Dort steht jeder Eingriff: Updates, Sicherungen, "
            "Aufräumläufe, Container-Aktionen, geöffnete Terminal-Sitzungen, "
            "Anmeldungen und abgewiesene Anmeldungen, geänderte Einstellungen, "
            "erzeugte und zurückgezogene Schlüssel.\n\n"
            "Das Tagebuch liegt in einer eigenen Tabelle — ein Aufräumlauf, der die "
            "Logs kürzt, nimmt die eigene Spur also nicht mit. Es läuft live mit, "
            "während etwas passiert, und lässt sich hinterher filtern und durchsuchen."
        ),
    },
    {
        "key": "tokens",
        "category": "Sicherheit",
        "question": "Warum bekommt jedes Gerät einen eigenen Schlüssel?",
        "answer": (
            "Weil ein einziger Schlüssel für alle Geräte bedeutet: Wer einen Rechner "
            "aufmacht, hat den Schlüssel für alle. Genau so war es vorher.\n\n"
            "Jetzt bekommt jeder Agent beim Installieren seinen eigenen. Geht ein "
            "Gerät verloren, entwertet man diesen einen — alle anderen laufen weiter. "
            "Ein Geräteschlüssel darf außerdem nur für sein eigenes Gerät liefern und "
            "nur sich selbst abmelden.\n\n"
            "Der Generalschlüssel des Administrators gibt es weiterhin, für Sammler "
            "wie n8n und für den Notfall. Er liegt aber nur noch als Prüfsumme in der "
            "Datenbank, wird genau einmal angezeigt und lässt sich auf bestimmte "
            "Absenderbereiche eingrenzen."
        ),
    },
    {
        "key": "sso",
        "category": "Sicherheit",
        "question": "Kann ich mich mit dem Microsoft-365-Konto anmelden?",
        "answer": (
            "Ja, ohne Zusatzkosten. Einzurichten unter Sicherheit → Single Sign-on.\n\n"
            "RIMPERIO nimmt dafür OpenID Connect (also OAuth 2.0 mit Identitätsschicht) "
            "statt SAML. Der Grund ist praktischer Natur: SAML-Anmeldung für eine "
            "eigene, nicht im Katalog gelistete Anwendung verlangt bei Microsoft einen "
            "kostenpflichtigen Entra-ID-Plan; eine App-Registrierung mit OpenID "
            "Connect ist in jedem Microsoft-365-Tarif enthalten. Sicherheitstechnisch "
            "gelten beide als gleichwertig, und OIDC ist der Weg, den Microsoft selbst "
            "empfiehlt.\n\n"
            "Andere Anbieter — Keycloak, Authentik, Google Workspace, Okta — gehen "
            "über dieselbe Einstellung."
        ),
    },
    {
        "key": "shell",
        "category": "Sicherheit",
        "question": "Was ist die Konsole unter System, und ist das gefährlich?",
        "answer": (
            "Eine echte Shell auf dem Server, im Browser — wie die Konsole in Proxmox "
            "VE. Sie läuft als root auf dem *Host*, nicht im Container.\n\n"
            "Das ist der weitreichendste Knopf im ganzen Programm, und er ist "
            "entsprechend abgesichert: nur Administratoren, der Token wird bei jedem "
            "Verbindungsaufbau geprüft, die Zahl gleichzeitiger Sitzungen ist "
            "begrenzt, Leerlauf beendet die Sitzung von selbst, und jede Sitzung "
            "steht mit Benutzer, Uhrzeit und Dauer im Systemtagebuch.\n\n"
            "Wer sie nicht haben will, setzt `RIMPERIO_WEBSHELL=false` in der .env. "
            "Dann gibt es sie schlicht nicht."
        ),
    },
    {
        "key": "ai",
        "category": "Sicherheit",
        "question": "Gehen meine Logs an eine KI im Internet?",
        "answer": (
            "Nur wenn Sie das einrichten. Ab Werk ist die KI-Auswertung aus, und dann "
            "verlässt keine Zeile den Server.\n\n"
            "Wenn Sie sie einschalten, haben Sie die Wahl: direkt an Anthropic oder "
            "OpenAI, über einen n8n-Workflow, oder an Open WebUI. Der letzte Weg ist "
            "der interessante, wenn die Daten im Haus bleiben sollen: Open WebUI läuft "
            "als Container neben RIMPERIO und kann dahinter ein lokales Modell (Ollama) "
            "haben. Dann geht die Auswertung nirgendwohin.\n\n"
            "Vor jedem Versand zeigt die Vorschau genau an, was rausgehen würde."
        ),
    },
    {
        "key": "backup",
        "category": "Betrieb",
        "question": "Wie sichere ich, und wie komme ich zurück?",
        "answer": (
            "RIMPERIO-Server → Sicherung. Eine Sicherung ist eine ZIP-Datei, optional mit "
            "AES-256 verschlüsselt; der Umfang ist wählbar, und beim Zurückspielen "
            "ebenso.\n\n"
            "Vor jedem Eingriff — Update, Rückfall, Container-Update, Neustart — fragt "
            "RIMPERIO, ob vorher gesichert werden soll. Diese Frage lässt sich nicht "
            "überspringen: auch ein „nein“ muss bewusst gegeben werden. Scheitert die "
            "Sicherung, läuft der Eingriff gar nicht erst an."
        ),
    },
    {
        "key": "verify",
        "category": "Grundsätzliches",
        "question": "Wie prüfe ich, dass hier wirklich das läuft, was auf GitHub steht?",
        "answer": (
            "Unter RIMPERIO-Server → Updates steht die installierte Version, der Git-Commit "
            "dieser Installation und der Stand auf GitHub. Beide Commit-Nummern lassen "
            "sich im Repository direkt nachschlagen.\n\n"
            "Auf dem Server selbst: `git -C /opt/rimperio log -1` zeigt denselben "
            "Commit, `git -C /opt/rimperio status` zeigt, ob jemand lokal etwas "
            "verändert hat. Die Container werden aus genau diesem Verzeichnis gebaut."
        ),
    },
]


@router.get("")
async def about(_=Depends(get_current_user)):
    """Herkunft, Lizenz, Verweise und häufige Fragen."""
    return {
        "name": "RIMPERIO",
        "version": settings.app_version,
        "tagline": "Zentraler Log-Server zum Selbstbetreiben.",
        "author": "Phydran6",
        "license": {
            "id": "MIT",
            "name": "MIT-Lizenz",
            "summary": ("Benutzen, verändern, weitergeben und auch kommerziell "
                        "einsetzen — erlaubt. Der Lizenztext mit dem Urheberhinweis "
                        "muss erhalten bleiben. Ohne Gewähr und ohne Haftung."),
            "url": f"{REPO_URL}/blob/main/LICENSE",
            "note": ("Deshalb steht in der Fußzeile kein „All rights reserved“ mehr: "
                     "bei einer MIT-Lizenz sind die Rechte eingeräumt, nicht "
                     "vorbehalten."),
        },
        "repository": {"slug": REPO_SLUG, "url": REPO_URL,
                       "branch": os.getenv("RIMPERIO_BRANCH", "main")},
        "links": _links(),
        "faq": FAQ,
        "categories": sorted({entry["category"] for entry in FAQ}),
    }
