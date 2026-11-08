# Betrieb

Der Alltag mit einem laufenden RIMPERIO.

← [Übersicht](../../README.md) · [Alle Dokumente](../README.md)

---

## Die Oberfläche

Alles hängt am **linken Menü**, in drei Ebenen. Die fünf Bereiche sind nach
Fragen geschnitten, nicht nach Technik — jeder beantwortet genau eine:

```
Überwachung        Was ist im Netz passiert?
  Dashboard · Logs · Geräte · Systemzustand

Auswertung         Was mache ich damit?
  KI-Auswertung · Webhooks · App verbinden

Verwaltung         Wer darf was, und wie lange bleiben Daten liegen?
  Benutzer
  Zugang & Sicherheit
      Passwort · Anmeldesicherheit · Single Sign-on (M365)
      Verzeichnis (LDAP) · Zugangsschlüssel
  Daten & Aufbewahrung
      Aufbewahrung · Speicherplatz · Archivierung · Datenbank

System             Womit läuft das hier, und was tut es gerade?
  Container
      Übersicht & Image-Updates · Zusatzdienste
  Updates (RIMPERIO) · Systemtagebuch · Sicherung · Konsole
  Netzwerk & Mail · Erscheinungsbild · Allgemein · Neustart

Hilfe              Was ist das eigentlich, und wo kommt es her?
  Über RIMPERIO & FAQ
```

Die Trennlinie zwischen *Verwaltung* und *System* ist bewusst diese:
**Verwaltung handelt von Menschen und Daten, System von der Maschine.** Vorher
lagen Passwort, LDAP, Datenbank, Netzwerk und Erscheinungsbild alle im selben
Reiterstapel unter „Einstellungen“ — richtig zu raten, wo etwas steckt, war
Glückssache.

Die Einstellungen haben ihre Unterpunkte links im Baum — nicht mehr als
Reiterleiste rechts im Inhalt. Wer die alten Adressen gespeichert hat:
`/settings/ldap` und Konsorten funktionieren unverändert weiter.

**Einklappen:** Der Pfeil unten links ist immer sichtbar und zeigt in die
Richtung, in die es geht — nach links zum Einklappen, nach rechts zum
Ausklappen. Eingeklappt bleibt eine reine Icon-Leiste; ein Punkt markiert den
Bereich, in dem die offene Seite liegt. Der Zustand wird gemerkt.

---

## Sprache

Unten links im Menü, neben dem Weltkugel-Zeichen: **Deutsch** oder **English**.

Die Wahl gilt sofort, ohne Neuladen, und wird im Browser gemerkt — sie ist also
pro Person und Gerät, nicht pro Server. Ohne gemerkte Wahl entscheidet die
Spracheinstellung des Browsers; versteht der Server sie nicht, gilt Deutsch.

Fehlt eine Übersetzung, erscheint der deutsche Text — nie eine leere Stelle.
Neue Sprachen kommen als Datei in [`frontend/src/i18n/`](../../server/frontend/README.md)
dazu.

---

## Logs ansehen

**Filter:** Host, Zeitraum, Schweregrad (dieser und alles Dringendere),
Kategorie (Auth, Kernel, Netzwerk, Firewall, Container, Cron, Mail, System,
Audit), Syslog-Facility und Geräteart.

Die Filter stehen in der Adresse. Damit ist jede Ansicht verlinkbar, als
Lesezeichen ablegbar — und der Export (CSV/JSON) liefert genau das, was auf dem
Bildschirm steht.

**Rohdaten lesbar:** Über der Tabelle steht ein Schalter **„Lesbar“** — ab Werk
an. Jede Zeile läuft dann durch den Anzeige-Parser. Aus

```
<134>Sep  9 12:00:01 fw01 kernel: [12345.6] IN=eth0 OUT= MAC=aa:bb:… SRC=192.168.1.10 DST=8.8.8.8 PROTO=TCP SPT=44321 DPT=443
```

wird

> **von 192.168.1.10 nach 8.8.8.8:443 (TCP)**
> `Quelle 192.168.1.10` `Ziel 8.8.8.8` `Ziel-Port 443` `Protokoll TCP` `Eingang eth0`

Die Rohzeile bleibt daneben stehen — wenn die Erkennung danebenliegt, muss man
nachsehen können, was wirklich ankam. Der Parser fasst die Datenbank nie an, er
stellt nur anders dar.

Erkannt werden RFC 5424 und 3164, UniFi Netconsole und MAC/Modell, Cisco IOS,
`key=value` (Fortinet, Netfilter), JSON und journald.

Das gilt in der **Liste** wie in der **Detailansicht**; dort stehen zusätzlich
alle erkannten Felder als Tabelle. Bei mehr als 300 Zeilen pro Seite schaltet
sich die lesbare Darstellung ab — darüber kostet das Zerlegen mehr, als es beim
Überfliegen bringt.

---

## Systemcheck

*System → Systemzustand → **Systemcheck starten***

24 Prüfungen in fünf Bereichen auf einen Knopfdruck: Datenbank, Dienste,
Sicherheit, Betrieb, Netz. Zu jedem Fund steht **was** nicht geht, **woran** das
gemessen wurde und **was zu tun ist**. Angezeigt werden zuerst nur die
Auffälligkeiten.

---

## Konsole im Browser

*System → Konsole* — nur für Administratoren.

Eine echte Shell auf dem Server, **als root**, direkt im Browser — das Vorbild
ist die Konsole in Proxmox VE. Befehle laufen auf dem Host, nicht im Container.

Sie ist **ab Werk an**. Das klingt gewagt, ist aber die ehrlichere Einstellung:
Wer diese Oberfläche als Administrator erreicht, kann ohnehin Updates
einspielen, Container neu bauen und den Host neu starten — also in jedem Fall
Code auf diesem Server ausführen. Ein Schalter davor hat keinen Angreifer
aufgehalten, nur den Betreiber.

Wer sie trotzdem nicht will, trägt in die `.env` ein:

```
RIMPERIO_WEBSHELL=false
```

Danach `docker compose up -d backend`. Dann antwortet der Endpunkt mit 403 und
es wird gar nichts gestartet.

Abgesichert ist sie so:

- nur Administratoren, Token wird bei jedem Verbindungsaufbau geprüft
- nur mit Host-Zugriff (bei der gehärteten Variante gibt es sie schlicht nicht)
- jede Sitzung steht mit Benutzer, IP, Uhrzeit und Dauer im
  [Systemtagebuch](#systemtagebuch)
- höchstens 3 Sitzungen gleichzeitig (`RIMPERIO_WEBSHELL_MAX_SESSIONS`)
- nach 15 Minuten ohne Eingabe endet die Sitzung (`RIMPERIO_WEBSHELL_IDLE_TIMEOUT`)

**Was das Terminal nicht kann:** Programme mit voller Bildschirmsteuerung —
`top`, `htop`, `nano`, `vim`. Der Bildschirm ist bewusst schlank gehalten
(keine 300-kB-Bibliothek für ein Nebenwerkzeug) und hängt Ausgabe hinten an,
statt einen Cursor zu führen. Für solche Fälle: SSH.

---

## Befehle auf dem Server

Zuerst ins Installationsverzeichnis wechseln:

```bash
cd /opt/rimperio
```

Status:

```bash
sudo docker compose ps
```

Protokolle:

```bash
sudo docker compose logs -f
```

Nur das Backend:

```bash
sudo docker compose logs -f backend
```

Neustart:

```bash
sudo docker compose restart
```

Stoppen:

```bash
sudo docker compose down
```

Starten:

```bash
sudo docker compose up -d
```

---

## Aufbewahrung und Archivierung

**Global:** Einstellungen → Aufbewahrung. Logs älter als X Tage werden gelöscht.

**Pro Gerät:** in der Geräte-Ansicht — nach Alter oder nach Anzahl. Nützlich für
das eine geschwätzige Gerät, das sonst alles zumüllt.

**Archivierung:** Einstellungen → Archivierung. Alte Logs gepackt auf SFTP,
FTPS, FTP, SMB oder in einen eingebundenen Ordner — täglich zur festgelegten
Stunde oder auf Knopfdruck, auf Wunsch mit anschließendem Löschen. Mit
Verbindungstest und Verlauf.

### Platte läuft voll

*Verwaltung → Daten & Aufbewahrung → Speicherplatz* zeigt die Belegung, die
Schwellen, die Größe von Logtabelle und Datenbank — und erlaubt, den
Aufräumlauf sofort anzustoßen.

Der **Plattenwächter** sieht jede Minute nach und arbeitet sich vom
Unwichtigsten nach oben:

| Belegung | Was passiert |
|---------:|--------------|
| ab 80 % | abgelaufene Anmelde-Token und altes Systemtagebuch weg; dann Logzeilen jenseits der eingestellten Aufbewahrung, in Häppchen von 50.000, danach je ein `VACUUM` |
| ab 88 % | die Aufbewahrung wird schrittweise halbiert, bis wieder Luft ist; `VACUUM FULL` nur, wenn der Platz dafür reicht |
| ab 93 % | dasselbe mit Nachdruck — **aber die letzten 24 Stunden bleiben stehen** |

Zwei Dinge tut er ausdrücklich **nicht**:

- **Er löscht nie von selbst alles.** Ein Log-Server ohne die letzten Stunden
  ist bei einem Zwischenfall wertlos. Wer den Rundumschlag trotzdem automatisch
  will, setzt `DISK_ALLOW_TRUNCATE=true` — bewusst und nachlesbar.
- **Er startet kein `VACUUM FULL`, wenn der Platz dafür fehlt.** Das schreibt
  die Tabelle komplett neu und braucht noch einmal so viel freien Platz, wie sie
  groß ist. Genau daran ist die frühere Automatik gescheitert: Sie versuchte es
  bei 90 % Belegung, scheiterte, und ab 95 % blieb nur noch „alles weg“.

Jeder Aufräumlauf steht mit Uhrzeit, Anlass und Zeilenzahl im
[Systemtagebuch](#systemtagebuch). Wer mehr Luft will: kürzere Aufbewahrung,
[Archivierung](#aufbewahrung-und-archivierung) oder eine größere Platte.

Feineinstellung über die `.env` (`DISK_USAGE_WARN`, `DISK_USAGE_TARGET`,
`DISK_MIN_KEEP_HOURS`, …) — siehe `.env.example`.

---

## Systeme im Netz

*Überwachung → Systeme im Netz* — nur für Administratoren.

Beantwortet die Frage: **Was hängt in meinem Netz, und liefert es schon Logs?**
RIMPERIO sucht das Netz selbst ab, gleicht jedes gefundene System mit den Geräten
ab, die schon liefern, und sagt für den Rest, wie man es einbindet.

**Woher die Systeme kommen**

- **Eigene Subnetze** des Servers — gelesen vom Host, nicht vom Container.
  Ein sehr großes Netz (größer als /22) wird nur rund um den Server abgesucht.
- **Router per SNMP.** RIMPERIO ist kein Gateway, aber der Router weiß, was hinter
  ihm hängt: seine ARP-Tabelle nennt IP und MAC, seine Adress- und
  Routingtabelle die Subnetze. Das Standard-Gateway wird von selbst gefragt
  (Community `public`), weitere Router trägt man ein. So wird
  **subnetzübergreifend** gesucht.
- **Eingetragene Bereiche** — `10.20.0.0/24`, `10.20.0.5` oder
  `10.20.0.10-10.20.0.80`, etwa für Netze hinter einem VPN.

**Wie gesucht wird:** Ping und ein paar Verbindungsversuche auf typische Ports
(SSH, SMB, RDP, HTTP, Druckerports …), danach die ARP-Tabelle des Hosts — dort
erscheinen auch Geräte, die jede Anfrage verwerfen. Für jedes gefundene System
kommen Hostname, SSH-Kennung und SNMP-Beschreibung dazu. Den Namen fragt die
Suche auf mehreren Wegen gleichzeitig: Rückwärts-DNS (auch direkt beim DNS des
Hosts und beim Router — die FRITZ!Box kennt jeden per DHCP vergebenen Namen),
mDNS (Macs, Linux mit Avahi, Drucker, NAS, IoT), NetBIOS (Windows, Samba) und
SNMP. Findet keiner einen Namen, liefert das System aber schon über einen Agent,
steht dessen Name da. Daraus wird die
Art geraten: Linux, Windows, Netzwerkgerät, Drucker, NAS, IoT, Smartphone.

**Was empfohlen wird**

| Art | Weg |
|---|---|
| Linux, Windows | HTTPS-Agent — verschlüsselt, mit eigenem Schlüssel |
| Switch, Firewall, Drucker, NAS, Appliance | Syslog — dort läuft kein Agent |
| FRITZ!Box | n8n-Sammler (`server/n8n/rimperio.json`) — sie spricht kein Syslog |
| Smartphone, Tablet | nicht einbindbar |

Die Regel dahinter: **HTTPS-Agent überall dort, wo einer läuft; Syslog nur, wo
nichts anderes geht.**

**Linux automatisch einbinden.** Bei einem Linux-System genügt in der
Detailansicht *Automatisch einbinden (SSH)*. RIMPERIO zeigt zuerst den
SSH-Hostschlüssel des Zielsystems — stimmt er, bestätigt man und gibt einen
Benutzer mit `sudo` (nicht root) samt Passwort ein, dazu das **Dienstkonto** für den
Agent (Standard `rimperio`). Der Server verbindet sich, prüft das System, überträgt den
Installer über dieselbe SSH-Verbindung (das Ziel braucht dafür kein Internet) und richtet
den HTTPS-Agent ein. Fertig ist es erst, wenn wirklich Logs ankommen.

Der Agent läuft danach **nicht als root**, sondern unter dem Dienstkonto — fehlt es, legt
der Installer es an (Systemkonto ohne Anmeldung und ohne Passwort). Das Journal liest er
über die Gruppe `systemd-journal`, schreiben darf er nur in sein Verzeichnis. Alles liegt
unter `/opt/RMM/rimperio`; `sudo` braucht nur die Einrichtung. Die Schrittliste zeigt am
Ende, unter welchem Konto der Dienst läuft. Einzelheiten:
[Log-Agent](../../agent/logship/README.md#nicht-als-root-eigenes-dienstkonto).

Die Zugangsdaten werden **nur für diesen einen Vorgang** verwendet: nie
gespeichert, nie ins Tagebuch geschrieben, danach verworfen — auch im
Fehlerfall. Im Tagebuch steht nur, wer wann welches System eingebunden hat und
ob es geklappt hat.

Windows aus der Ferne einzubinden ist bewusst nicht dabei: die Fernverwaltung
ist auf Windows-Clients ab Werk aus und die Wege dahin sind heikel. Für Windows
bleibt der Einzeiler zum Kopieren.

**Automatisch:** Ab Werk sucht RIMPERIO einmal kurz nach dem ersten Start und
danach alle 24 Stunden. Die Suche baut nur Verbindungen auf — eine Firewall mit
Angriffserkennung kann trotzdem anschlagen. Abschalten oder den Takt ändern
unter *Einstellungen* auf der Seite. Jeder Lauf steht im Systemtagebuch
(Bereich „Netzwerksuche“).

Im gehärteten Betrieb (`server/deploy/hardened.yml`) sieht das Backend das Netz des
Hosts nicht: dann werden nur eingetragene Bereiche und Router abgesucht, und
MAC-Adressen fehlen.

---

## Systemtagebuch

*System → Systemtagebuch* — nur für Administratoren.

Der Anspruch dahinter ist hart formuliert und genau so gemeint: **es darf auf
diesem Server nichts passieren, das hinterher niemand mehr nachvollziehen
kann.** Ein „ich weiß nicht, warum das passiert ist“ soll es nicht geben.

Festgehalten wird jeder Eingriff — Update und Rückfall, Sicherung, Aufräumlauf,
Container-Aktion, geöffnete und geschlossene Konsolen-Sitzung, Anmeldung und
abgewiesene Anmeldung, geänderte Einstellung, erzeugter und zurückgezogener
Schlüssel. Jede Zeile nennt Zeit, Bereich, Vorgang, Verursacher, Absender-IP,
Ziel, Ausgang und Dauer.

Filtern lässt sich nach Bereich, Schweregrad, Zeitraum, Verursacher und
Freitext; „nur Fehlschläge“ gibt es als eigenen Schalter. Neue Einträge laufen
**live** mit — während ein Update läuft oder der Wächter aufräumt, kann man
zusehen statt hinterher nachzulesen.

Das Tagebuch liegt in einer **eigenen Tabelle** (`system_events`). Das ist
Absicht: ein Aufräumlauf, der die Logs kürzt, darf ausgerechnet den Eintrag
„Aufräumlauf hat 4,2 Millionen Zeilen gelöscht“ nicht mitnehmen. Aufgehoben
wird ein Jahr; Fehler und Kritisches bleiben darüber hinaus stehen.

---

## Container und ihre Updates

*System → Container → Übersicht & Image-Updates* — nur für Administratoren.

Die Seite beantwortet für jeden Container die einzige Frage, die zählt: **wie
aktualisiere ich das Ding?**

| Gruppe | Was es ist | Weg |
|---|---|---|
| **RIMPERIO selbst** (`rimperio-app-*`) | aus dem Quellcode dieses Projekts gebaut | System → Updates |
| **Fremde Dienste** (`rimperio-ext-*`) | ganz normale Images: PostgreSQL, Caddy, n8n … | hier, auf Knopfdruck |
| **Andere Container** | laufen daneben, gehören nicht dazu | nur zur Information |

„Auf Updates prüfen“ fragt die Registry direkt nach dem Abdruck des benutzten
Tags und vergleicht ihn mit dem lokal vorhandenen Image. Es wird **nichts
heruntergeladen und nichts ausgetauscht** — nur nachgesehen. Dafür braucht es
keinen Zusatzdienst.

Eingespielt wird auf Klick: Bestätigung mit dem Containernamen, Sicherungsfrage
davor, Eintrag im Systemtagebuch danach. Ein Schalter „automatisch
aktualisieren“ fehlt bewusst — auf einem Log-Server will niemand, dass sich
nachts die Datenbank austauscht und am Morgen keiner weiß, warum.

Wer die Prüfung nach Zeitplan und über mehrere Hosts hinweg will, schaltet
**Tugtainer** dazu (*System → Container → Zusatzdienste*). Es hat den
Docker-Socket nur lesend und kann deshalb ebenfalls nur melden, nicht tauschen.

> Der Vorgänger **Watchtower ist raus**: Er hat Container eigenmächtig
> ausgetauscht — nachts, ohne Ansage und hinterher schwer zuzuordnen. Läuft er
> noch, weist die Zusatzdienste-Seite darauf hin und bietet das Entfernen an.

---

## Benutzer und Anmeldung

- **Rollen:** Administrator und Benutzer.
- **MFA/TOTP:** jede gängige Authenticator-App, mit 10 Einmal-Codes.
  10 Fehlversuche → 15 Minuten Sperre. Admins können notfalls zurücksetzen.
- **Passkeys:** Windows Hello, Face ID, Fingerabdruck, Sicherheitsschlüssel.
  Braucht HTTPS mit gültigem Zertifikat.
- **LDAP / Active Directory:** optional. Schlägt die lokale Anmeldung fehl, wird
  zusätzlich das Verzeichnis gefragt. Gruppen lassen sich auf Rollen abbilden.
- **Single Sign-on mit Microsoft 365:** siehe unten.

### Single Sign-on (Microsoft 365 und andere)

*Verwaltung → Zugang & Sicherheit → Single Sign-on.*

**Warum OpenID Connect und nicht SAML.** Beides erledigt dieselbe Aufgabe. Der
Unterschied liegt in der Rechnung: SAML-Anmeldung für eine eigene, nicht im
Katalog gelistete Anwendung verlangt bei Microsoft einen kostenpflichtigen
Entra-ID-Plan. Eine App-Registrierung mit OpenID Connect — also OAuth 2.0 mit
Identitätsschicht — ist in **jedem** Microsoft-365-Tarif enthalten, auch im
kostenlosen. Sicherheitstechnisch gelten beide als gleichwertig, und OIDC ist
der Weg, den Microsoft selbst empfiehlt.

Einzurichten ist es in fünf Minuten; die Seite zeigt die Rückadresse zum
Kopieren und führt Schritt für Schritt durchs Entra-Portal. Gebraucht werden
Verzeichnis-ID (Mandant), Anwendungs-ID und ein Client-Geheimnis.

Wer Administrator wird, entscheidet nicht der Zufall: entweder über die
Objekt-ID einer Entra-Gruppe oder über eine App-Rolle (`RIMPERIO.Admin`). Steht
beides leer, bekommt jeder die Standardrolle — ab Werk **Benutzer**. Zusätzlich
lassen sich zugelassene Domänen eintragen.

Die Anmeldung mit Benutzername und Passwort bleibt bewusst daneben bestehen:
Wäre sie weg, würde ein Fehler beim Identitätsanbieter alle aussperren — auch
den Administrator, der ihn wieder geradeziehen müsste.

Andere Anbieter (Keycloak, Authentik, Google Workspace, Okta) gehen über
dieselbe Einstellung; statt der Verzeichnis-ID trägt man dort die Adresse des
Discovery-Dokuments ein.

### Zugangsschlüssel für Agents

*Verwaltung → Zugang & Sicherheit → Zugangsschlüssel* — nur für Administratoren.

Drei Arten, und die Art entscheidet, was der Schlüssel darf:

| Art | Darf | Gedacht für |
|---|---|---|
| **Einladung** | nur einen Geräteschlüssel anfordern | das Anschließen eines Rechners — der übliche Fall |
| **Gerät** | nur für sein Gerät liefern, nur sich selbst abmelden | wird beim Anmelden automatisch erzeugt |
| **Generalschlüssel** | alles: anmelden, für jedes Gerät liefern, jedes abmelden | Sammler wie n8n, die für fremde Geräte einliefern |

Beim Installieren tauscht der Agent den mitgegebenen Schlüssel selbst gegen
einen **eigenen**, der nur für diesen Rechner gilt. Geht ein Gerät verloren,
entwertet man genau diesen einen — die anderen laufen weiter. Beim
Deinstallieren wird er auf dem Server zurückgezogen.

**Ein Schlüssel wird genau einmal angezeigt**, direkt nach dem Erzeugen.
Danach steht in der Datenbank nur noch seine Prüfsumme; auslesen kann ihn
niemand mehr — auch kein Administrator, auch nicht über einen Datenbankabzug.
Wer ihn verlegt, würfelt ihn neu.

Schlüssel aus früheren Fassungen liegen noch im Klartext und sind als solche
markiert. Der Knopf „In den geschützten Speicher überführen“ trägt die
Prüfsumme nach und löscht den Klartext; **die Schlüssel bleiben dabei gültig**,
auf den Geräten ändert sich nichts.

---

## Erscheinungsbild

Einstellungen → Erscheinungsbild: Firmenname, Slogan, Logo, Favicon,
Primärfarbe, Standard-Theme und eigenes CSS.

Die Zustandsfarben leiten sich aus der Markenfarbe ab — eine geänderte
Primärfarbe zieht überall mit. Ohne eigenes Logo steht das RIMPERIO-Zeichen.

---

## Weiter

- [Updates](../updates/README.md)
- [Sicherung](../backup/README.md)
- [Integrationen](../integrations/README.md)
