# Release-Verlauf

Die Versionsgeschichte des Gesamtprojekts — was in welchem Release dazukam.
Bis einschließlich `v2026.08.14.14.00.00` stand diese Liste in der
Haupt-README; sie ist hierher gezogen, damit dort nur noch steht, was LogBot
*ist* und *kann*.

Einzelheiten je Bereich stehen in den [Bereichs-Changelogs](README.md).

← [Changelog-Übersicht](README.md) · [Zur Haupt-README](../README.md)

---

## v2026.10.18.22.00.00 (2026-10-18)

- **Hochgeladenes Zertifikat wurde nicht genutzt.** „Zertifikat speichern“
  meldete Erfolg, Caddy lieferte aber weiter das alte aus: Die neue Datei heißt
  wieder `custom.crt`, die Konfiguration blieb gleich — und bei gleicher
  Konfiguration überspringt Caddy das Neuladen. Caddy wird jetzt immer neu
  geladen; ist der Modus „Eigenes Zertifikat“ aktiv, direkt nach dem Hochladen.
- **Zertifikat und Key werden vor dem Speichern geprüft:** ungültig,
  vertauscht/nicht zusammengehörig oder passwortgeschützt gibt eine klare
  Fehlermeldung. Die Erfolgsmeldung nennt das Ablaufdatum.

## v2026.10.11.21.00.00 (2026-10-11)

- **„Auf Updates prüfen“ sah ein frisch veröffentlichtes Release nicht.** Die
  Release-Liste lag 15 Minuten im Zwischenspeicher, der Knopf fragte nur den
  Commit neu ab — beim Kanal „Stabil“ blieb der alte Stand stehen. Der Knopf holt
  jetzt auch die Release-Liste frisch. Der automatische Beobachter (alle 120 s)
  lässt sie bewusst im Zwischenspeicher, sonst wäre das GitHub-Limit für anonyme
  Abfragen (60/h) allein durch ihn verbraucht; er sieht neue Releases also nach
  spätestens 15 Minuten.

## v2026.10.11.20.00.00 (2026-10-11)

Die restlichen Funde aus der Code-Prüfung nach dem Ausfall vom 10.10. — alles
dieselbe Fehlerklasse: Dinge, die still verloren gehen oder still wachsen.

- **Windows-Agent verlor Ereignisse.** Er holte je Kanal die *neuesten* 100 und
  filterte nach Zeit — kamen zwischen zwei Durchläufen mehr, waren die älteren
  weg. Ein gescheitertes Senden verwarf das Paket. Jetzt liest er lückenlos ab
  einer gemerkten Position je Kanal (RecordId, gespeichert in `positions.json`,
  übersteht Neustarts), die erst nach dem erfolgreichen Senden vorrückt; wachsende
  Pause nach Fehlern, `Retry-After` wird beachtet, dauerhaft abgelehnte Pakete
  blockieren nichts. Zeitstempel und Event-ID gehen mit (Duplikat-Erkennung).
  Auf einem Windows-Rechner getestet: 450 von 450 Ereignissen trotz simuliertem
  Serverausfall, nichts doppelt. Die Aufgabenplanung beendete den Agent nach 365
  Tagen und gab nach drei Abstürzen auf — beides behoben.
- **Ingest-Limit pro Agent statt pro IP.** Hinter NAT teilten sich alle Agents ein
  Limit und bekamen gemeinsam 429.
- **Update-Skript:** kein Datenbank-Abzug mehr, wenn er nicht auf die Platte passt
  (er hätte sie vollgeschrieben); alte Sicherungen werden auch nach Fehlschlägen
  aufgeräumt — vorher häufte jeder gescheiterte Versuch einen vollen Abzug an.
- **Aufbewahrung je Gerät** löscht häppchenweise statt mit einem riesigen DELETE.
- **Abgewiesene Lieferungen** (zurückgezogener Schlüssel) schreiben höchstens alle
  10 Minuten einen Tagebucheintrag statt alle 5 Sekunden.
- **Syslog:** höchstens 5.000 UDP-Meldungen gleichzeitig in Arbeit (vorher ohne
  Grenze, wenn die Datenbank langsam war); Geräte-Zwischenspeicher wird ausgedünnt.
- **Zusatzdienste** (n8n, Open WebUI, Postfix, Portainer, Tugtainer) haben jetzt
  auch eine Log-Obergrenze.

> **Windows-Agents:** Installer einfach erneut ausführen. Beim ersten Start beginnt
> jeder Kanal „ab jetzt“ — es gibt keine Flut alter Ereignisse.

## v2026.10.11.00.15.00 (2026-10-11)

- **Konsole: Rechtsklick bot nur „Kopieren“.** Das unsichtbare Eingabefeld wurde über
  Vue unter den Mauszeiger geschoben — das kam erst nach dem Rechtsklick-Menü an,
  der Browser sah nur Text. Jetzt wird es sofort direkt am Element verschoben (wie
  xterm.js): über HTTP zeigt das Browsermenü „Einfügen“, über HTTPS fügt der
  Rechtsklick direkt ein. Lehnt man die Zwischenablage-Freigabe ab, kommt danach das
  Browsermenü statt nichts.

## v2026.10.10.23.45.00 (2026-10-10)

- **Konsole: Einfügen mit Strg+V und Rechtsklick.** Der Bildschirm war ein `div` —
  dort löst der Browser weder bei Strg+V ein Einfügen aus noch bietet er im
  Rechtsklick-Menü „Einfügen“ an. Die Tastatur läuft jetzt über ein unsichtbares
  Eingabefeld (wie bei xterm.js). Rechtsklick fügt direkt ein wie in PuTTY; über
  HTTP, wo der Browser die Zwischenablage nicht direkt herausgibt, erscheint das
  normale Menü mit „Einfügen“. Dazu Umschalt+Einfg, Strg+Umschalt+C und
  Handy-Tastaturen.
- Hinweis unter der Konsole: LogBot-Updates nicht dort starten — das Update baut den
  Container neu, über den die Konsole läuft.

## v2026.10.10.23.30.00 (2026-10-10)

- **Kanalauswahl verschwand beim zweiten Öffnen der Update-Seite.** Die Antwort
  aus dem Zwischenspeicher der Release-Liste enthielt die drei Kanäle nicht — übrig
  blieb nur „Übernehmen“, man kam nicht mehr von „Stabil“ weg. Behoben im Server;
  die Oberfläche kennt die Kanäle zusätzlich selbst und hakt den gespeicherten an,
  auch wenn GitHub gerade nicht antwortet.

## v2026.10.10.23.00.00 (2026-10-10)

Kleine Korrektur an der Update-Seite, gefunden beim ersten Test:

- **„Entspricht GitHub“ stimmte nicht immer.** Beim Kanal „Stabil“ wird gegen das
  neueste *Release* geprüft. Liegt auf `main` schon ein neuerer Stand ohne Release,
  stand dort trotzdem „aktuell“ — und als Quelle `…@main`, obwohl gegen das
  Release-Tag geprüft wurde. Jetzt steht dort „Geprüft gegen …@&lt;Tag&gt;“, und ein
  Hinweis nennt den neueren Stand auf `main` samt Weg dorthin.
- **Uhrzeiten passten scheinbar nicht zusammen.** Sicherungen heißen nach der
  Serverzeit (meist UTC), „Update vom“ zeigte die Zeit des Browsers — zwei Stunden
  auseinander. Die Sicherungsliste zeigt jetzt die Browserzeit (Name daneben),
  die Zeilen im Update-Protokoll tragen ihre Zeitzone.

## v2026.10.10.22.00.00 (2026-10-10)

Ein Release aus einem echten Ausfall. Am 10.10. reagierte LogBot „nicht mehr“:
Die Platte war zu 100 % voll, Postgres startete nicht mehr, und **seit dem 23.09.
war kein einziger Log gespeichert worden — ohne dass es jemand gemerkt hat.**
Die Oberfläche lud, `/api/health` meldete „healthy“. Ursache war eine
Endlosschleife im Linux-Agent, die einen Proxmox-Host 14 Millionen Zeilen am Tag
schicken ließ. Der rote Faden dieses Releases: **ein Log-Sammler darf nie still
stehen bleiben — und wenn doch, muss er es laut sagen.**

### Die Ursache: Wiederholschleifen

- **Linux-Agent schickte bei jedem Fehler alles noch einmal.** Scheiterte ein
  Paket mitten im Rückstand (Server kurz weg, „zu viele Anfragen“, ein abgelehntes
  Paket), rückte die Position nicht vor — beim nächsten Durchlauf ging der ganze
  Rückstand erneut raus, alle 5 s, für immer. Jetzt rückt die Position nach jedem
  angekommenen Paket vor, Fehler führen zu wachsender Wartezeit (5 s … 5 min),
  Rückstand wird gedrosselt nachgeholt, und ein dauerhaft abgelehntes Paket
  blockiert nicht mehr alles dahinter.
- **Der Agent schickt jetzt den echten Zeitstempel mit.** Der Server erkennt damit
  doppelt gelieferte Einträge und verwirft sie, und nachgeholte Logs landen zur
  richtigen Uhrzeit statt alle „jetzt“.
- **Server lehnt keine ganzen Pakete mehr wegen Kleinigkeiten ab:** zu lange
  Quellnamen werden gekürzt (vorher 422 für das ganze Paket), NUL-Zeichen
  entfernt (vorher 500), große Lieferungen in Stücken gespeichert (vorher 500 ab
  ~2.700 Einträgen). Gilt auch für ältere Agents, sofort.
- **Windows-Agent:** mehrzeilige Meldungen gingen fast alle verloren, Umlaute
  ließen den Server ganze Pakete ablehnen, und zwischen den Kanälen gingen
  Ereignisse durch die Lücke.

### Nie wieder unbemerkt

- **Benachrichtigungen per Mail, Telegram oder Webhook (n8n)** — neue Seite
  System → Benachrichtigungen. Gemeldet wird: Datenbank weg, Platte voll,
  keine neuen Logs mehr, ein Gerät flutet, ein Gerät verstummt, neues Update.
  Mit Entwarnung, sobald es wieder passt. **Funktioniert auch ohne Datenbank** —
  die Einstellungen liegen zusätzlich als Datei auf dem Server.
- **Ehrlicher Health-Check.** `/api/health` meldet jetzt `degraded` samt Grund,
  wenn die Datenbank fehlt oder nichts mehr ankommt. Neu: `/api/health/ready`
  für Uptime Kuma & Co. — 503, sobald LogBot nicht sammelt.
- **Die alten Mail-Haken „Benachrichtigen bei …“ hatten nie eine Wirkung** (es gab
  keinen Aufrufer). Ihre Einstellung wird übernommen.

### Die Platte darf den Server nicht mehr umbringen

- **Notreserve:** LogBot legt eine Platzhalter-Datei an (Standard 1 GB, höchstens
  5 % der Platte) und gibt sie bei 97 % frei — dann hat Postgres Luft zum Weiterlaufen
  und der Wächter zum Aufräumen. Vorher half nur Handarbeit per SSH.
- **Annahme-Stopp ab 95 %:** Agents bekommen „später nochmal“ und puffern bei
  sich, statt die Datenbank zu ersticken.
- **Die eingestellte Aufbewahrung gilt jetzt immer** — stündlich. Bisher wurde sie
  erst durchgesetzt, wenn die Platte schon zu 80 % voll war.
- **INFO/DEBUG dürfen früher altern** als Warnungen und Fehler (Einstellungen →
  Aufbewahrung). Gespeichert wird weiterhin alles.
- **Plattenwächter kürzt sanfter:** Zwischen 80 und 88 % halbierte er jede Minute
  weiter, bis nur noch 24 h übrig waren — auch wenn ganz andere Dateien die Platte
  füllten.
- **Docker-Protokolle haben eine Obergrenze** (5 × 10 MB je Container). Vorher wuchsen
  sie ohne Grenze — 1,3 GB beim Ausfall.

### Wiederholungen zusammenfassen statt wegwerfen

- Dieselbe Meldung mehrfach innerhalb einer Minute wird **eine Zeile mit Anzahl**
  („×5.000“ in der Logliste). Die Information bleibt vollständig, der Platz nicht.
  Gilt für HTTPS-Agents und Syslog; die FRITZ!Box ist ausgenommen.

### Updates: Konsole wie bei Proxmox

- Die Update-Seite hat jetzt ein Terminal: die fertige Befehlszeile steht da,
  **Enter** → Rückfrage `[J/n]` zur Sicherung → das Update läuft, und die Ausgabe
  vom Server läuft live mit. Das Update-Protokoll beginnt pro Lauf neu (vorher
  wuchs es endlos und die Live-Ansicht zeigte zuerst den alten Lauf).

### Weitere Fehler aus der Code-Prüfung

- Halb gebaute Indizes (abgebrochener Start) ließen jede Lieferung mit Zeitstempel
  scheitern — werden jetzt erkannt und neu gebaut.
- Mail-Benachrichtigungs-Schalter, Archivierungs-Verlauf und „Gerät stilllegen“
  zeigten „gespeichert“, speicherten aber nichts.
- Ein Syslog-Stapel scheiterte komplett an einer einzigen kaputten Meldung.
- Der Live-Kanal ins Browserfenster konnte unbemerkt abreißen.
- Ein Geräteschlüssel für das falsche Gerät bekam 500 statt 403.
- Das Systemtagebuch wurde bei häufigen Neustarts nie aufgeräumt.

> **Für bestehende Linux-Agents:** den Installations-Einzeiler einfach noch einmal
> ausführen. Er ersetzt das Agent-Skript, behält die Position und startet neu.

## v2026.10.08.22.00.00 (2026-10-08)

**Was im Netz hängt, findet und bindet sich selbst ein.**

- **Systeme im Netz** (*Überwachung → Systeme im Netz*). LogBot sucht das Netz
  ab — eigene Subnetze, eingetragene Bereiche und **subnetzübergreifend über
  Router** (SNMP: ARP-, Adress- und Routingtabelle). Jedes gefundene System
  wird mit den Geräten abgeglichen, die schon liefern, und für den Rest sagt
  LogBot, wie man es einbindet: HTTPS-Agent für Linux und Windows, Syslog für
  Netzwerkgeräte, Drucker und NAS, der n8n-Sammler für die FRITZ!Box.
  Smartphones werden als „nicht einbindbar“ erkannt. Gesucht wird automatisch
  (einmal nach dem Start, danach täglich) oder auf Knopfdruck.
- **Linux per SSH selbst einbinden.** Fingerabdruck bestätigen, Benutzer und
  Passwort eingeben — den Rest macht der Server: verbinden (normaler Benutzer
  mit sudo, nicht root), prüfen, Installer über dieselbe SSH-Verbindung
  übertragen und ausführen, warten bis Logs kommen. Die Zugangsdaten gelten nur
  für den einen Vorgang und werden danach verworfen; nichts davon landet in der
  Datenbank oder im Tagebuch.
- **Installer vom Server.** Der Einzeiler funktioniert auch ohne GitHub-Zugang:
  erst GitHub, sonst dieser LogBot-Server.

Nur Administratoren. Jeder Suchlauf und jedes Einbinden steht im Systemtagebuch
(neuer Bereich „Netzwerksuche“). Im gehärteten Betrieb bleibt die Suche auf
eingetragene Bereiche und Router beschränkt.

---

## v2026.09.23.10.00.00 (2026-09-23)

Ein großes Release. Der rote Faden: **es soll auf diesem Server nichts mehr
passieren, das hinterher niemand nachvollziehen kann** — und nichts mehr
stillschweigend schiefgehen.

### Sicherheit

- **Agent-Schlüssel waren für jedes angemeldete Konto im Klartext lesbar.** Das
  war eine echte Lücke: ein Konto mit reinen Leserechten auf die Logs kam an den
  Generalschlüssel. Die Schlüsselverwaltung ist jetzt Administratoren
  vorbehalten.
- **Jedes Gerät bekommt einen eigenen Schlüssel.** Bisher lag auf jedem Rechner
  derselbe. Wer einen aufmachte, hatte den Schlüssel für alle — und konnte im
  Namen jedes beliebigen Geräts Logzeilen erfinden oder Geräte samt Logs
  löschen. Die Agent-Installer holen sich beim Einrichten ihren eigenen.
- **Kein Schlüssel liegt mehr im Klartext in der Datenbank.** Nur noch ein
  SHA-256-Abdruck; angezeigt wird der Schlüssel genau einmal nach dem Erzeugen.
  Bestehende Schlüssel funktionieren unverändert weiter und lassen sich mit
  einem Klick überführen.
- **Der Generalschlüssel bleibt** — für Sammler wie n8n und für den Notfall —,
  ist aber eingrenzbar auf Absenderbereiche, mit Ablaufdatum versehbar, und
  jede Benutzung wie jeder Fehlversuch steht im Systemtagebuch.
- **Anmeldung über Microsoft 365** (OpenID Connect). Ohne Zusatzkosten: SAML für
  eine eigene Anwendung verlangt bei Microsoft einen kostenpflichtigen
  Entra-Plan, eine OIDC-App-Registrierung ist in jedem Tarif enthalten. Andere
  Anbieter (Keycloak, Authentik, Google Workspace, Okta) gehen genauso.

### Behoben

- **Die Aufräum-Automatik versagte genau ab 95 % — also dann, wenn man sie
  braucht.** Ursache: Sie rief bei 90 % `VACUUM FULL`. Das schreibt die Tabelle
  komplett neu und braucht dafür noch einmal so viel freien Platz, wie sie groß
  ist. Bei 90 % Belegung ist der nicht da; der Lauf brach ab oder trieb die
  Belegung über 95 %, und ab dort half nur noch „alle Logs weg“. Dazu kam der
  erste Blick auf die Platte erst fünf Minuten nach dem Start.

  Der Plattenwächter ist neu geschrieben: sieht im Minutentakt nach und sofort
  nach dem Start, rechnet wie `df` statt zu optimistisch, fängt ab 80 % an,
  löscht in Häppchen statt in einer Riesentransaktion, und `VACUUM FULL` läuft
  nur noch, wenn der Platz dafür wirklich reicht. **Ein automatisches „alles
  weg“ gibt es nicht mehr:** die letzten 24 Stunden bleiben immer stehen.

### Neu

- **Systemtagebuch.** Jeder Eingriff steht dort: Update, Sicherung,
  Aufräumlauf, Container-Aktion, Terminal-Sitzung, Anmeldung und abgewiesene
  Anmeldung, geänderte Einstellung, erzeugter Schlüssel. Eigene Tabelle, damit
  ein Aufräumlauf die eigene Spur nicht mitnimmt. Läuft live mit.
- **Live-Ausgabe beim Update.** Ein Fortschrittsbalken sagt „43 %“ — er sagt
  nicht, woran es hängt. Jetzt läuft mit, was das Wartungsskript auf dem Server
  tatsächlich ausgibt.
- **Container-Übersicht.** Für jeden Container steht da, *wie* er aktualisiert
  wird: LogBot-eigen (aus dem Quellcode) oder fremdes Image. Die Prüfung fragt
  die Registry direkt, ohne Zusatzdienst; eingespielt wird auf Klick.
- **Konsole im Browser**, wie in Proxmox VE — eine echte Shell auf dem Server,
  ab Werk verfügbar und lückenlos protokolliert.
- **Lesbare Logs.** Der Anzeige-Parser wirkt jetzt auch in der Liste: statt der
  Rohzeile ein Satz plus die wichtigsten Angaben als Abzeichen. Die Rohzeile
  bleibt darunter stehen.
- **Open WebUI als KI-Anbieter.** Mit einem lokalen Modell (Ollama) dahinter
  verlässt bei der Auswertung keine Logzeile den Server.
- **Speicherplatz-Seite** mit Belegung, Schwellen und einem Bericht darüber, was
  beim Aufräumen getan wurde — und was bewusst nicht.
- **Über LogBot & FAQ**: Herkunft, Lizenz, Verweise ins Repository, und wie man
  prüft, dass hier wirklich das läuft, was auf GitHub steht.

### Geändert

- **Die Container heißen jetzt so, dass man ihnen ansieht, wem sie gehören.**
  `logbot-app-*` wird aus diesem Quellcode gebaut (Update über System →
  Updates), `logbot-ext-*` sind fremde Images wie PostgreSQL und Caddy (Update
  über System → Container). Vorher hieß alles `logbot-…`, und damit sah es so
  aus, als gehörte PostgreSQL zu LogBot. Alte Namen bleiben im Docker-Netz als
  Alias erreichbar.
- **Menüstruktur nach Fragen statt nach Technik** — fünf Bereiche, sauber
  verschachtelt, jeder mit einer klaren Zuständigkeit.
- Die Fußzeile sagt nicht mehr „All rights reserved“. Bei einer MIT-Lizenz sind
  die Rechte eingeräumt, nicht vorbehalten.

### Entfernt

- **Watchtower.** Es hat Container eigenmächtig ausgetauscht — nachts, ohne
  Ansage, hinterher schwer zuzuordnen. **Tugtainer** nimmt seinen Platz ein und
  macht nur eines: nachsehen und melden. Der Docker-Socket ist dort nur lesend
  eingehängt, es *kann* also gar nichts austauschen.

---

## v2026.09.15.20.30.00 (2026-09-15)

Erstes Release seit `v2026.09.10.20.00.00`. Es enthält auch die Stände
`2026.09.12.12.00.00` und `2026.09.15.20.00.00` (unten), die nicht einzeln
veröffentlicht wurden.

### Geändert

- **Doku zum Kopieren gebaut.** In allen READMEs steht jeder Befehl in einem
  eigenen Codeblock, die Erklärung als Text darüber. Der Kopier-Knopf nimmt damit
  immer genau einen lauffähigen Befehl mit, ohne Kommentare. `.env`- und
  rsyslog-Ausschnitte enthalten nur die einzutragenden Zeilen, die Liste der
  Log-Endpunkte in der API-Doku ist eine Tabelle.
- **Agents deinstallieren als Einzeiler.** Die Agents-README beginnt den Abschnitt
  mit den fertigen Befehlen für Linux und Windows („komplett“ oder „nur hier“)
  und einer Tabelle, was jeweils entfernt wird. Die Technik der Zuordnung auf dem
  Server ist eingeklappt.

### Aus 2026.09.12.12.00.00

- **`--ref` im Installer:** `install.sh` holt ein bestimmtes Release, Tag oder
  Commit, beim Installieren wie beim Aktualisieren. Vorher verwarf der Installer
  den Schalter und aktualisierte trotzdem auf den neuesten Stand. Die Update-Seite
  gibt genau diesen Einzeiler aus.
- `install.sh` setzt `safe.directory` vor Git-Zugriffen. Sonst bricht `git pull`
  als root mit „dubious ownership“ ab.
- README und `docs/updates` mit allen Update-Einzeilern an einem Ort.

## v2026.09.15.20.00.00 (2026-09-15)

### Neu

- **Setup-Assistent** für Linux: `curl -sSL …/setup.sh | sudo bash`. Ein Menü für Server
  und Linux-Agent (installieren, aktualisieren, testen, deinstallieren, komplett
  entfernen) plus Systemprüfung. Alle Optionen werden abgefragt, und vor dem Start
  steht der passende direkte Einzeiler.
- README: **Deinstallieren** als eigener Abschnitt, mit einer Tabelle, was bleibt und was
  gelöscht wird, für Server, Linux- und Windows-Agent.
- Linux-Agent: `--proto udp|tcp` für den Syslog-Modus.

### Behoben

- **Linux-Agent erschien als „Windows-Agent“ mit einer Docker-IP wie `172.18.0.3`.**
  Die IP gehörte zu Caddy: Das Backend nahm die Adresse des Proxys statt der des
  Geräts. Der Typ fiel mangels Angabe auf „Windows“ zurück. Der Server nimmt jetzt
  die echte Absender-IP. Die Agents melden Geräteart und eigene IP selbst, das
  funktioniert auch hinter NPM. Schon vorhandene falsche Karten korrigiert der
  Server beim nächsten Eingang von selbst.

### Sicherheit

- Login- und Webhook-Rate-Limits zählten für alle Nutzer gemeinsam (alle kamen
  „von Caddy“). Jetzt zählen sie pro Client.

## v2026.09.09.22.00.00 (2026-09-09)

### Neu

- **Sicherung und Wiederherstellung** unter *System → Sicherung*. Sicherungen sind
  ZIP-Dateien, granular nach sechs Bereichen (Einstellungen, Erscheinungsbild,
  Benutzer, Geräte, Webhooks, Logdaten) — beim Sichern wie beim Zurückspielen.
  Optional mit Passwort verschlüsselt (AES-256-GCM, PBKDF2). Das Manifest bleibt
  dabei immer lesbar, damit die **Versionsprüfung vor** der Passwortabfrage läuft:
  eine Sicherung, die neuer ist als der Server, wird abgewiesen. Herunterladen,
  hochladen und auf einem anderen Server einspielen inbegriffen.
- **Sicherungsfrage vor jedem Systemeingriff.** Update, Rückfall, Zurückspielen und
  das Zu- oder Abschalten eines Zusatzdienstes laufen durch denselben Dialog:
  vorher sichern — ja oder nein? Die Frage hängt am Endpunkt, nicht an der
  Oberfläche: ohne beantwortete Entscheidung antwortet der Server mit HTTP 400.
  Scheitert die Sicherung, läuft der Eingriff gar nicht erst an.
- **Sofortmeldung bei neuem Stand.** Der Server sieht selbst alle zwei Minuten bei
  GitHub nach und meldet einen neuen Stand per Server-Sent Events in jedes offene
  Fenster — ohne Neuladen. Wer den Server aus dem Internet erreichbar hat, trägt
  zusätzlich einen **GitHub-Webhook** ein (HMAC-signiert); dann kommt die Meldung
  im Moment des Pushes.
- **Release-Auswahl.** Drei Kanäle: *Stabil* (letztes Release), *Aktuell* (Kopf des
  Zweiges) und *Festgelegt* (ein bestimmtes Release, Tag oder Commit). Das
  Wartungsskript kennt dafür `--ref`.
- **KI-Auswertung** unter *System → KI-Auswertung*, mit vier Wegen: aus (Vorgabe),
  Claude direkt, ChatGPT direkt, n8n extern per Webhook oder n8n als Container
  daneben. Vorher zeigt eine Vorschau genau, was den Server verlassen würde.
  Standardmäßig dürfen nur Administratoren fragen.
- **Zusatzdienste** unter *System → Zusatzdienste*: Portainer, Watchtower, n8n und
  Postfix, je einzeln zuschaltbar (`deploy/optional.yml`, Compose-Profile). Die
  Zugangsdaten sind dort für Administratoren jederzeit einsehbar — ein Passwort,
  das man nur beim Installieren einmal sieht, ist zwei Wochen später verloren.
- **Mailversand** unter *System → Mail*. Die gesamte Postfix-Konfiguration entsteht
  im Browser; das Backend schreibt daraus `data/postfix/settings.env`. Wahlweise
  über den mitgelieferten Container (mit Smarthost) oder direkt an einen
  vorhandenen Mailserver. Mit Probemail und Benachrichtigungen je Ereignis.
- **Terminal im Browser** unter *System → Terminal*: eine Root-Shell auf dem Server,
  über WebSocket. Bewusst standardmäßig **aus** (`LOGBOT_WEBSHELL=true`), nur für
  Administratoren, mit Sitzungsgrenze, Leerlauf-Abbruch und Protokollierung.
- **Sprachumschaltung** (Deutsch / English) unten im Seitenmenü. Gilt sofort, wird
  im Browser gemerkt. Fehlt eine Übersetzung, greift Deutsch.
- **Anzeige-Parser für Rohzeilen** (`backend/app/logparse.py`): macht aus RFC 5424 und
  3164, UniFi, Cisco IOS, `key=value` (Fortinet, Netfilter), JSON und journald eine
  lesbare Zusammenfassung samt benannten Feldern und Abzeichen. Die Rohzeile bleibt
  unverändert daneben stehen; die Datenbank wird nie angefasst.
- **Eigene Schnittstelle für die App** unter `/api/app`: Blättern per Cursor statt
  Seitenzahl, Nachlaufen per `since_id`, kompakte Antworten ohne Rohzeilen und
  lesbar aufbereitete Logzeilen. `bootstrap` liefert alles für den Start in einem
  Aufruf und meldet `api_level` samt `capabilities`.
- **Systemprüfung vor der Installation** (`install/preflight.sh`): Architektur,
  Kernel, RAM, Platte, Kerne, Docker, Ports, DNS, Uhrzeit — gerechnet gegen die
  Auswahl an Zusatzdiensten. Dreistufige Antwort: passt / wird knapp / geht nicht.
- **Feature-Auswahl im Installer**: `--with portainer,watchtower`, `--no-addons`,
  `--skip-preflight`. Im manuellen Modus als Menü.
- **Windows-Agent als Einzeiler**: `install-windows.ps1` nimmt jetzt Parameter
  (`-Action`, `-Fqdn`, `-Token`, `-Mode`, `-Yes`, `-PurgeServer`) und läuft ohne
  Menü durch.

### Geändert

- **Seitenmenü vollständig links.** Die Einstellungen haben ihre Unterpunkte jetzt
  im linken Baum statt als Reiterleiste rechts im Inhalt — drei Ebenen: Bereich,
  Eintrag, Unterpunkt. Alte Adressen wie `/settings/ldap` funktionieren weiter.
- **Einklappen klappt wirklich ein.** Vorher blieben Text und Unterpunkte stehen,
  weil nur einzelne Elemente ausgeblendet wurden; jetzt schaltet die Leiste auf
  reine Icons um. Der Pfeil ist immer sichtbar und zeigt in die Richtung, in die
  es geht.
- **Repository aufgeräumt.** Compose-Varianten nach `deploy/`, der n8n-Ablauf nach
  `n8n/telegram-chat-flow.json`, die Dokumentation nach `docs/`. Jedes Verzeichnis
  hat eine eigene README; die Haupt-README beschreibt nur noch, was LogBot ist und
  kann — ohne Version und ohne Changelog.
- **Release-Verlauf aus der Haupt-README** hierher verschoben.
- Das **LogBot-Zeichen** aus der Android-App ist jetzt Standard-Favicon und
  Kopfbild der README.

### Behoben

- **Agent-Installer blieb an Rückfragen stehen.** `ask` wartete unbegrenzt — über
  eine Pipe, per SSH ohne Terminal oder aus einem Automatismus heraus blieb das
  Skript an der ersten Frage hängen. Jetzt: 60 s Zeitlimit
  (`LOGBOT_ASK_TIMEOUT`), ohne Terminal wird gar nicht erst gefragt.
- **Deinstallation des Agents hing.** `systemctl stop` bekommt jetzt 20 s, danach
  wird der Dienst abgeschossen; der Aufruf an den Server hat ein Zeitlimit von
  20 s statt gar keines. Beim Abmelden wird nur noch gefragt, was wirklich fehlt.
- **Aufräumen auf dem Server traf womöglich den Falschen.** Der Agent merkt sich
  jetzt die Gerätenummer, die der Server beim ersten Senden zurückmeldet
  (`agent_id` in der Ingest-Antwort), und nennt sie beim Deinstallieren. Zusätzlich
  räumt `all_for_hostname` alle weiteren Einträge desselben Hostnamens ab — nach
  einem IP-Wechsel blieben sonst Karteileichen samt Logs liegen.
- **Zurückspielen scheiterte an Zeitstempeln.** In JSON gibt es keinen
  Datumstyp; der Treiber verlangte beim Einfügen aber echte `datetime`-Objekte.
  Jeder Wert wird jetzt als Text übergeben und per doppeltem `CAST` von PostgreSQL
  selbst umgewandelt. ID-Zähler werden nach dem Einspielen nachgezogen.

---

## v2026.08.14.14.00.00 (2026-08-14)
- FIX: **Systemzustand zeigte die Datenbank rot, obwohl sie verbunden war.** Die vier Kacheln teilten sich eine Farbregel, die für Auslastung gedacht ist (ab 80 % rot) — die Datenbank-Kachel setzte bei „verbunden" aber 100 als vollen Balken. Reiner Anzeigefehler, der Zustand war immer korrekt. Jede Kachel bringt ihre Farbe jetzt selbst mit; die Datenbank steht auf grün/„Verbunden" bzw. rot/„Nicht erreichbar".

## v2026.08.14.12.00.00 (2026-08-14)
- **Systemcheck** unter *System → Systemzustand*: 24 Prüfungen in fünf Bereichen (Kern, Dienste, Sicherheit, Betrieb, Netz & Updates) auf einen Knopfdruck. Zu jedem Fund steht, was nicht geht, woran das gemessen wurde und was zu tun ist — angezeigt werden zuerst nur die Auffälligkeiten.
- **Patchmanagement** unter *System → Updates*: vergleicht den installierten Stand mit GitHub, spielt das Update ein und kann auf eine vorherige Sicherung zurückfallen. Vor jedem Eingriff steht eine Rückfrage, die auf möglichen Datenverlust hinweist; die Datenbank lässt sich vorher sichern. Schlägt das Update fehl, fährt das System **selbsttätig** auf den alten Stand zurück. Als Kommandozeilen-Weg bleibt der Einzeiler `curl -sSL … | sudo bash -s -- update -y` — ein Update von Hand mit `docker compose build` ist nicht mehr nötig.
- **Linkes Menü aufgeräumt**: sichtbar sind nur noch *Überwachung*, *Verwaltung* und *System*; die Unterpunkte klappen bei Bedarf auf.
- **System entwirrt**: *Verzeichnis (LDAP)*, *Archivierung*, *Erscheinungsbild* und *Anmeldesicherheit* sind keine eigenen Menüpunkte mehr, sondern Reiter der Einstellungen. Alte Links wie `/settings/ldap` funktionieren weiter.
- ENTFERNT: Impressum, Datenschutzerklärung und Cookie-Hinweis. LogBot ist ein internes Werkzeug, keine Website.

## v2026.08.02.20.00.00 (2026-08-02)
- FIX: **Backend startete nicht, alles antwortete mit HTTP 502** (Oberfläche, Login und Agenten-Ingest). `backend/app/branding.py` lag als Windows-1252 statt UTF-8 vor — ein einzelnes Byte statt „ü". Python liest Quelldateien immer als UTF-8 und brach beim Import ab. Datei zurück auf UTF-8 gebracht; alle übrigen Dateien wurden byteweise geprüft.

## v2026.08.02.18.00.00 (2026-08-02)
- **Anmeldung mit Passkey** (Windows Hello, Face ID, Fingerabdruck, Sicherheitsschlüssel). Einrichten unter *System → Anmeldesicherheit*, anmelden über den Knopf auf der Login-Seite. Der geheime Teil bleibt auf dem Gerät, die Signatur gilt nur für diese Adresse — eine nachgebaute Anmeldeseite bekommt nichts Verwertbares. Voraussetzung: HTTPS mit gültigem Zertifikat.
- SICHERHEIT: **PostgreSQL war auf allen Netzwerkschnittstellen erreichbar** — der Port ist jetzt auf `127.0.0.1` beschränkt (`DB_BIND=0.0.0.0` hebt das bewusst wieder auf).
- SICHERHEIT: Neues **`docker-compose.hardened.yml`** nimmt dem Backend die weitreichenden Container-Rechte (`privileged`, `pid: host`, `SYS_BOOT`). Sie existieren nur für den Neustart-Knopf und die DNS-Übernahme, heben aber die Trennung zwischen Container und Server praktisch auf. Start: `docker compose -f docker-compose.yml -f docker-compose.hardened.yml up -d`.
- PERFORMANCE: Die Systemzustand-Seite zählt die Logzeilen nicht mehr einzeln durch, sondern nimmt die Schätzung der Datenbank.

## v2026.08.02.16.00.00 (2026-08-02)
- **Externe Datenbank:** LogBot kann die Daten auf einem eigenen Datenbankserver ablegen und selbst nur noch als Anwendung laufen. Konfiguration über `DATABASE_URL` (oder `DB_HOST`/`DB_PORT`) und `DB_SSLMODE` in der `.env`, Start mit `docker compose -f docker-compose.yml -f docker-compose.external-db.yml up -d`. Der mitgelieferte Postgres-Container bleibt dabei stehen, das Volume unangetastet — der Rückweg ist offen. Unter *Systemzustand* steht, welche Datenbank tatsächlich aktiv ist und ob die Verbindung verschlüsselt läuft.
- **Anmeldung über LDAP / Active Directory** (optional, unter *System → Verzeichnis*): Schlägt die lokale Anmeldung fehl, wird zusätzlich das Verzeichnis gefragt — lokale Konten bleiben unberührt. Gruppen lassen sich auf Rollen abbilden, eine Pflichtgruppe kann den Zugang begrenzen, neue Benutzer werden auf Wunsch beim ersten Anmelden angelegt. Ein Testfeld spielt eine echte Anmeldung durch und zeigt Gruppen und Rolle.
- **Archivierung alter Logs** (unter *System → Archivierung*): Logs ab einem einstellbaren Alter werden gepackt auf **SFTP, FTPS, FTP, SMB** oder in einen eingebundenen Ordner geschrieben — täglich zur festgelegten Stunde oder auf Knopfdruck, auf Wunsch mit anschließendem Löschen in der Datenbank. Mit Verbindungstest und Historie der letzten Läufe.

## v2026.08.02.14.00.00 (2026-08-02)
- **Oberfläche neu gestaltet.** Neues Design-System (Farb-Tokens, Karten, Knöpfe, Formularfelder, Abzeichen, Tabellen) — die Zustandsfarben leiten sich aus der Markenfarbe ab, eine im Branding geänderte Primärfarbe zieht überall mit. Dunkles Schema kontrastreicher, helles ruhiger.
- **Seitenmenü** mit gruppierter Navigation (Überwachung / Verwaltung / System), eigenen Icons statt Emoji und klar erkennbarem aktivem Eintrag — auch im angedockten Zustand. Darüber eine Kopfleiste mit dem Titel der aktuellen Seite.
- **Anmeldung** zweispaltig mit Markenseite; **Dashboard** mit Kennzahl-Kacheln, die in die passend gefilterte Log-Ansicht führen, plus Verteilung nach Schweregrad und aktivsten Quellen; **Geräte** als Karten mit Statuspunkt.
- SICHERHEIT: **Branding-Endpunkte waren ohne Anmeldung beschreibbar** — inklusive `custom_css`, das jedem Besucher ausgeliefert wird. Jetzt nur noch für Admins. Ebenfalls behoben: Pfad-Ausbruch beim Asset-Abruf und Uploads ohne Größenlimit.
- FIX: Zoom auf Mobilgeräten war gesperrt; weißes Aufblitzen beim Start; Tastatur-Fokus jetzt durchgängig sichtbar.
- PERFORMANCE: Der globale CSS-Übergang galt für jedes Element im Dokument und bremste lange Loglisten — gilt jetzt nur noch für die Bausteine, die beim Theme-Wechsel wirklich umfärben.

## v2026.07.31.23.30.00 (2026-07-31)
- **FRITZ!Box-Logs anbindbar:** `POST /api/agents/ingest` nimmt jetzt Lieferungen von Sammlern (z. B. n8n) an — `ip_address`, `device_type` und pro Eintrag `timestamp`/`event_id`/`group`/`facility`. Das gemeldete Gerät erscheint mit eigener IP und eigenem Namen in der Geräteliste statt unter der IP des Sammlers. Paketgröße 50 → 1000.
- **Keine doppelten Logs mehr:** Einträge mit eigenem Zeitstempel bekommen einen `dedup_key`; wiederholte Lieferungen derselben Ereignisse werden verworfen (`ON CONFLICT DO NOTHING`). Die Antwort meldet `accepted` und `duplicates`. Bestands-Datenbanken bekommen Spalte + Index beim Start automatisch.
- **Einstufung von FRITZ!Box-Ereignissen** (`backend/app/fritzbox.py`): die Box liefert keinen Schweregrad — bekannte Ereignis-IDs werden über eine Tabelle eingestuft, unbekannte über Stichwörter. Die Dienstnamen (`fritzbox-net`, `-wlan`, `-sys`, `-auth`, `-audit`) zählen zu den vorhandenen Logtyp-Kategorien.
- **UI:** Geräteart „FRITZ!Box" mit Klarnamen in Geräteliste, Geräte-Ansicht und Log-Filter.

## v2026.07.31.23.00.00 (2026-07-31)
- **Reverse Proxy:** Port 80 bleibt in jeder Konfiguration ein vollwertiger Zugang (kein Zwangs-Redirect mehr) – IP und FQDN funktionieren parallel auf 80 und 443. Konfiguration wird vor dem Anwenden geprüft, bei Fehler automatischer Rollback. Neu: selbstsigniertes HTTPS (interne CA), zusätzliche HTTPS-Adressen, „Zurück auf HTTP" und Notausstieg `CADDY_FORCE_HTTP=true`. FIX: manuelle Änderungen im Caddyfile-Editor wurden verworfen.
- **Logs:** Filter nach **Logtypen** – Schweregrad-Gruppen, Kategorie (Auth, Kernel, Netzwerk, Firewall, Container, Cron, Mail, System, Audit), Syslog-Facility und Geräteart. Filter stehen in der URL und gelten auch für den Export.
- **Log-Ansicht pro Gerät** unter `/devices/<hostname>` mit Steckbrief; erreichbar per Klick auf Agent-Karte, Dashboard- oder Listen-Hostname.
- **UI:** Seitenmenü lässt sich zur Icon-Leiste einklappen (Zustand wird gemerkt); Hinweisbalken bei unverschlüsselter Verbindung.
- **Installation:** `install.sh` als One-Liner (`curl … | sudo bash`) mit `install`/`update`/`uninstall`/`uninstall-purge`. FIX: bestehende `.env` wird nicht mehr überschrieben (das neue DB-Passwort passte nicht zum vorhandenen Postgres-Volume).
- **Aufräumen:** `portainer-agent` ist nicht mehr Teil des Stacks (mountete den Docker-Socket = Root-Zugriff auf den Host).
- FIX: CSV-/JSON-Export der Logs funktionierte nicht – der Endpoint `/api/logs/export` fehlte im Backend.

## v2026.07.18.18.30.00 (2026-07-18)
- FIX: **Linux-Installer ignorierte Tastatureingabe beim One-Liner.** Statt 5-s-Timeout pro Abfrage jetzt **ein** Countdown am Start: Taste drücken = manueller Modus (alle Werte werden blockierend abgefragt), sonst automatischer Ablauf. Details: [agents.md](agents.md).

## v2026.07.18.16.00.00 (2026-07-18)
- NEU: **Linux-Agent One-Liner-Installation** (`curl … | sudo bash`), teilautomatisch (5 s-Timeout je Abfrage), **Standard = HTTPS** (verschlüsselt + Token, DNS/FQDN, auch übers Internet). FQDN/Token via Parameter, Env-Variable oder Platzhalter. Alle Agent-Daten unter `/opt/logbot-agent/*`. Details: [agents/README.md](../agents/README.md).
- FIX: Linux-Installer Deinstallation/Menü (fehlerhafte Umlaut-Ausgaben, kaputte awk-MAC-Regex); Uninstall räumt Syslog- **und** HTTPS-Modus auf.
- BACKEND: Ingest setzt `device_type` dynamisch aus dem Agent-Token (`linux` → Linux-Agent). UI: Gerätetyp „Linux-Agent" ergänzt.

## v2026.05.30.18.02.35 (2026-05-30)
- UI: Benutzer-Bearbeiten-Modal scrollbar mit fixiertem Header & Footer (max. 90 % Viewport-Höhe) – verhindert dass MFA + App-QR den Bildschirm sprengen.

## v2026.05.30.17.22.26 (2026-05-30)
- NEU: MFA / 2FA via TOTP (Backend-Teil) – kompatibel mit allen gängigen Authenticator-Apps (Google Authenticator, Authy, 1Password, Aegis, Bitwarden, …)
  - Endpoints `/api/auth/mfa/setup`, `/verify`, `/disable`, `/status`, `/backup-codes/regenerate`
  - 10 Einmal-Backup-Codes (bcrypt-gehasht)
  - Zwei-Stufen-Login: `/api/auth/login` liefert bei aktivem MFA ein `mfa_token`, `POST /api/auth/login/mfa` tauscht es gegen Access-Token
  - Lockout: 10 Falschversuche → 15 Min Sperre
  - Admin-Notfall-Reset: `POST /api/users/{id}/mfa/reset`
  - Schema-Migrationen laufen automatisch beim Start

## v2026.05.13.20.58.33 (2026-05-13)
- NEU: MIT-Lizenz hinzugefügt
- DOCS: GitHub Badges in README (Stars, Forks, Issues, letzter Commit, Release)

## v2026.04.17.15.17.18 (2026-04-17)
- FIX: QR-Code App-Login Timezone-Bug (Countdown zeigte sofort "Abgelaufen")
- FIX: SITE_URL wird jetzt korrekt an Backend-Container weitergegeben (docker-compose)
- UI: App-Login QR-Code in Benutzer-Edit-Modal integriert (statt eigenem Nav-Tab)
- DOCS: SITE_URL in .env.example dokumentiert

## v2026.03.31.17.26.46 (2026-03-31)
- Version-Bump Settings-View + Backend-App-Version auf 2026.04.02.16.32.39.

## v2026.03.31.10.48.35 (2026-03-31)
- NEU: Agent-Decommission/Purge Endpoint + Installer-Option „Server + Logs entfernen“.
- NEU: Installer-Hauptmenü (Installieren/Deinstallieren/Test) und FQDN-Check.
- FIX: Umlaute/Encoding in README, Syslog-Server, Agents-UI.
- UI: Delete-Prompt + Label „Löschen“ korrigiert.

## v2026.03.30.10.40.54 (2026-03-19)
- Admin-Button „System neu starten“ ergänzt (Backend führt Reboot über SysRq/nsenter/reboot/shutdown aus; Container pid:host/privileged).
- Health-Uptime bleibt in der Health-Ansicht; System-Karte in den Einstellungen ohne Uptime.
- Backend/Frontend-Versionen auf 2026.03.19.13.26.52 angehoben.

## v2026.03.03.17.18.19 (2026-03-03)
- Datenbank-Passwort im Einstellungsbereich (nur Admins) anzeigen/ausblenden/kopieren; neuer API-Endpoint `/api/settings/database`.

## v2026.02.20 (2026-02-20)
- NEU: Web-UI Seite „Agent Token“ zeigt/erneuert den HTTPS-Agent-Token und Kopierlink.
- NEU: Backend erzeugt beim Start automatisch ein Default-Agent-Token (falls keins vorhanden).
- FIX: Robustere Login-Fehlerbehandlung im Frontend (verhindert JSON-Parse-Fehler).

## v2026.02.16 (2026-02-16)
- FIX: Agent löschen schlug fehl (async SQLAlchemy + FK-Konflikt)
- FIX: Health-Seite nicht erreichbar bei hoher DB-Last
- PERFORMANCE: Syslog Server überarbeitet
  - Agent-Cache im Speicher (vermeidet DB-Lookup pro Nachricht)
  - Batch-Inserts via PostgreSQL COPY
  - Gebündelte `last_seen` Updates (alle 2 s)
  - Ergebnis: ~96 DB-Ops/s → ~2 DB-Ops/s
- PERFORMANCE: Dashboard/Logs/Health `COUNT(*)` über Millionen Zeilen eliminiert
  - Gesamtzahl via `pg_class.reltuples` (Schätzung)
  - Unique Hosts aus `agents` statt `COUNT(DISTINCT)` über `logs`
  - Level/Source-Statistiken nur für heute (nutzt timestamp-Index)
- PERFORMANCE: Index `idx_logs_agent_id` ergänzt

## v2026.01.30.17.30.00 (2026-01-30)
- NEU: Whitelabel-System mit Dark/Light Mode
- NEU: Branding-Einstellungen im Web-Interface
- NEU: Logo- und Favicon-Upload
- NEU: Custom CSS Support
- NEU: Theme-Toggle Komponente

## v2026.01.30.13.30.00 (2026-01-30)
- UniFi Netconsole Parsing Fix (Hex-ID ≠ Hostname)
- Öffentliche Webhook-Endpoints ohne Bearer Token
- Verbessertes Health Monitoring
- Settings-Verwaltung im Web-Interface
- Log-Retention Funktion

## v1.1.0
- Webhook-Integration für n8n
- PostgreSQL statt SQLite
- Verbessertes Agent-Management

## v1.0.0
- Initiale Version
- Basis Syslog-Empfang
- Web-Interface

