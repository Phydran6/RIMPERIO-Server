# Changelog — Frontend

Vue-3-Weboberfläche (`frontend/`). Versionsformat: `YYYY.MM.DD.HH.MM.SS`.

## 2026.10.18.22.00.00

### Fixed
- Einstellungen → Caddy: Nach „Zertifikat speichern“ zeigt die Meldung, ob Caddy neu geladen wurde, und das Ablaufdatum.

## 2026.10.11.00.15.00

### Fixed
- Konsole: Rechtsklick bot kein „Einfügen“. Das Eingabefeld wird beim Rechtsklick jetzt synchron per `element.style` unter den Zeiger gelegt (vorher reaktiv über Vue — zu spät für das Kontextmenü). Direktes Einfügen nur, wenn die Clipboard-Freigabe nicht verweigert ist (`permissions.query`); nach einer Ablehnung kommt das Browsermenü.

## 2026.10.10.23.45.00

### Fixed
- **Konsole (`views/Terminal.vue`): Einfügen ging nicht.** Strg+V und Rechtsklick → Einfügen funktionieren auf einem fokussierbaren `div` nicht. Tastatur und Zwischenablage laufen jetzt über ein unsichtbares `textarea`; Rechtsklick fügt direkt ein (Clipboard-API, nur HTTPS/localhost), sonst öffnet das Browsermenü auf dem Eingabefeld. Zeilenumbrüche aus der Zwischenablage werden zu `\r`. Umschalt+Einfg, Strg+Umschalt+C, Eingabe über Handy-Tastaturen (`input`-Ereignis).

### Added
- Warnhinweis unter der Konsole: Updates nicht dort starten (die Sitzung läuft über den Container, den das Update neu baut).

## 2026.10.10.23.30.00

### Fixed
- Updates: Kanalauswahl hängt nicht mehr an der Release-Liste von GitHub (feste Rückfallliste); der gespeicherte Kanal wird aus dem Status angehakt, eine noch nicht übernommene Auswahl bleibt beim Nachladen stehen.

## 2026.10.10.23.00.00

### Fixed
- Updates: „Quelle …@main“ war falsch, wenn gegen ein Release geprüft wird — jetzt „Geprüft gegen …@&lt;Bezugspunkt&gt;“. Hinweis, wenn auf dem Zweig ein neuerer Stand ohne Release liegt.
- Updates: Sicherungen zeigen ihre Uhrzeit in Browserzeit (der Name ist Serverzeit/UTC) und „Stand davor“ statt nur der Version — man sah nicht, dass die Sicherung den *vorherigen* Stand enthält.

## 2026.10.10.22.00.00

### Added
- **System → Benachrichtigungen** (`views/Alerts.vue`): Zustand (Datenbank, letzter Log, Platte, Notreserve, Flut, verstummte Geräte), Wege Mail/Telegram/Webhook mit Probemeldung, Auswahl der Ereignisse und Schwellen.
- **Update-Konsole** (`views/Updates.vue`): Terminal mit der fertigen Befehlszeile; Enter → `[J/n]`-Sicherungsfrage → Update mit Live-Ausgabe, Schritt und Fortschritt in der Titelleiste, Verlauf des letzten Laufs beim Öffnen. Bewusst keine echte Shell (das Update baut den Container neu, in dem sie liefe).
- Logliste: Abzeichen **„×N“** für zusammengefasste Wiederholungen.
- Einstellungen → Aufbewahrung: Frist für INFO/DEBUG und ein eigener Speichern-Knopf (vorher nur über „Allgemein“ speicherbar).

### Changed
- Mail-Einstellungen: die wirkungslosen Haken „Benachrichtigen bei …“ sind ersetzt durch einen Verweis auf die neue Seite.

### Fixed
- Einstellungen: Erfolgsmeldung zeigte „âœ““ (doppelt kodiertes ✓).

## 2026.10.08.22.00.00

### Added
- **Linux automatisch einbinden** direkt aus der Detailansicht: Knopf „Automatisch einbinden (SSH)“, Bestätigung des Hostschlüssels, Eingabe von Benutzer und Passwort (sudo-Passwort optional), danach ein mitlaufender Fortschritt Schritt für Schritt. Die Passwörter werden nach dem Absenden sofort aus der Seite entfernt. Alternativ weiterhin der Einzeiler zum Kopieren — jetzt mit Rückfall auf den LogBot-Server, falls GitHub nicht erreichbar ist.
- **Systeme im Netz** (*Überwachung → Systeme im Netz*, nur Administratoren). Liste aller gefundenen Systeme mit Status (*liefert Logs*, *liefert nicht mehr*, *nicht eingebunden*, *nicht einbindbar*), Art, Subnetz und offenen Diensten. Ein Klick zeigt Details und die passende Anleitung: HTTPS-Agent für Linux und Windows (Einzeiler mit der Adresse dieses Servers), Syslog-Ziel samt Fundort der Einstellung für Netzwerkgeräte, Drucker und NAS, n8n-Sammler für die FRITZ!Box. Systeme lassen sich ausblenden und mit einer Notiz versehen. Die Suche läuft mit Fortschrittsanzeige; Bereiche, Router und Zeitplan stellt man auf derselben Seite ein.

## 2026.09.23.10.00.00

### Changed
- **Menüstruktur nach Fragen statt nach Technik.** Fünf Bereiche, jeder beantwortet einen: *Überwachung* (was ist im Netz passiert?), *Auswertung* (was mache ich damit?), *Verwaltung* (wer darf was, und wie lange bleiben Daten liegen?), *System* (womit läuft das hier?) und *Hilfe*. Die Trennlinie zwischen Verwaltung und System ist bewusst diese: Verwaltung handelt von Menschen und Daten, System von der Maschine. Vorher lagen Passwort, LDAP, Datenbank, Netzwerk und Erscheinungsbild alle im selben Reiterstapel unter „Einstellungen“ — richtig zu raten, wo etwas steckt, war Glückssache. Die Reiter der Einstellungsseite tragen jetzt dieselben Gruppenüberschriften wie das Menü, damit man nicht zweimal sucht.
- **Die Logliste ist lesbar.** Ein Schalter über der Tabelle zeigt statt der Rohzeile den Satz, den der Parser daraus macht, dazu die wichtigsten Angaben (Quell-IP, Ziel, Benutzer, Ergebnis) als Abzeichen. Die Rohzeile bleibt eine Zeile darunter stehen — liegt die Erkennung daneben, sieht man es sofort. Die Wahl bleibt gemerkt. Die Detailansicht zeigt zusätzlich alle erkannten Felder.
- **„Terminal“ heißt jetzt „Konsole“** — das Vorbild ist die Konsole in Proxmox VE, und genau danach sucht man.
- Die Fußzeile verweist auf „Über LogBot“ statt einen Rechtevorbehalt zu behaupten, den es bei einer MIT-Lizenz nicht gibt.
- Die App-Seite (QR-Code) hatte bisher gar keinen Menüpunkt und war nur über die Adresse erreichbar.

### Added
- **Container** (`/containers`): zeigt für jeden Container, *wie* er aktualisiert wird — LogBot-eigen (aus dem Quellcode, über System → Updates) oder fremdes Image (hier, auf Knopfdruck). Update-Prüfung gegen die Registry, Einspielen mit Sicherungsfrage, Protokollansicht, Aufräumen ungenutzter Images.
- **Systemtagebuch** (`/journal`): jeder Eingriff mit Zeit, Verursacher, Ziel und Ausgang. Filter nach Bereich, Stufe, Zeitraum und Freitext; neue Einträge laufen live mit.
- **Speicherplatz** (`/storage`): Belegung mit den Schwellen als Marken auf dem Balken, Größe von Logtabelle und Datenbank, eine Erklärung, wie aufgeräumt wird — und ein Knopf, um es sofort zu tun. Der Bericht danach sagt, was getan wurde und was bewusst nicht.
- **Single Sign-on** (`/sso`): Einrichtung für Microsoft 365 mit Rückadresse zum Kopieren und Schritt-für-Schritt-Anleitung fürs Entra-Portal. Auf dem Anmeldeschirm erscheint ein zusätzlicher Knopf; Benutzername und Passwort bleiben daneben bestehen, sonst würde ein Fehler beim Anbieter alle aussperren.
- **Über LogBot & FAQ** (`/about`): Herkunft, Lizenz, Verweise ins Repository und die Fragen, die immer wieder kommen — darunter, wie man prüft, dass hier wirklich das läuft, was auf GitHub steht.
- **Live-Konsole beim Update:** die Ausgabe des Wartungsskripts läuft im Fenster mit, statt dass nur ein Balken wandert.
- Neue Symbole: `shield`, `help`, `mobile`, `container`, `palette`.

### Security
- **Die Schlüsselverwaltung zeigte alle Agent-Schlüssel im Klartext, und zwar jedem angemeldeten Benutzer.** Neu geschrieben: nur für Administratoren, der Schlüssel wird genau einmal nach dem Erzeugen angezeigt, danach steht in der Liste nur noch die Kennung. Schlüssel aus früheren Fassungen sind als „liegt noch im Klartext“ markiert, mit einem Knopf zum Überführen.
- Das Passwortformular verlangt jetzt das **aktuelle** Passwort. Eine offene Sitzung allein reicht nicht mehr, um das Passwort zu setzen.
- Anlegen, Ändern und Löschen von Webhooks sind nur noch für Administratoren sichtbar — ein Webhook gibt Logdaten ohne Anmeldung heraus.

### Fixed
- Der Ersatzweg zur API im Frontend-Container (nginx) reichte weder das WebSocket-Upgrade noch ungepufferte Ereignisströme durch. Über Caddy fiel das nicht auf, beim direkten Zugriff auf den Container hätten Konsole und Live-Ausgabe aber nicht funktioniert.

## 2026.09.09.22.00.00
### Added
- **Sprachen** (`src/i18n/`): Deutsch und English, umschaltbar unten im Seitenmenü. Bewusst
  ohne `vue-i18n` — Wörterbuch, reaktive Auswahl und `t()` sind rund fünfzig Zeilen, und der
  Container baut so auch ohne Netz durch. Fehlende Schlüssel fallen auf Deutsch zurück; fehlt
  er auch dort, steht der Schlüssel selbst da und fällt beim Testen auf.
- **`BackupPrompt.vue`**: die Rückfrage vor jedem Systemeingriff. Nicht wegklickbar, zwei
  gültige Antworten — und „nein" muss man wählen.
- **Neue Seiten**: `Backup.vue` (sichern, herunterladen, hochladen, granular zurückspielen),
  `AiSettings.vue`, `Stacks.vue`, `MailSettings.vue`, `Terminal.vue`.
- **Terminal ohne xterm.js**: eigener schlanker Bildschirm mit Zeilenpuffer, ANSI-Farben und
  der Cursorsteuerung, die eine Shell im Alltag benutzt. 300 kB Abhängigkeit für ein
  Nebenwerkzeug wären zu viel; die Grenze (kein `top`, kein `vim`) steht in der Oberfläche.
- **Update-Hinweis in der Kopfleiste**, gespeist aus dem Ereignisstrom des Servers — erscheint
  ohne Neuladen, sobald etwas Neues da ist.
- **Release-Auswahl** auf der Update-Seite: Kanal wählen, Release festnageln.
- Icons für die neuen Bereiche in `AppIcon.vue`; LogBot-Zeichen als Standard-Favicon.

### Changed
- **Seitenmenü vollständig links, drei Ebenen.** Die Einstellungen haben ihre Unterpunkte im
  Baum statt in einer Reiterleiste rechts im Inhalt — vorher klickte man sich in den Bereich
  und musste dort weitersuchen. Alte Adressen wie `/settings/ldap` funktionieren weiter.
- Kopfleiste zeigt zusätzlich den Pfad im Baum („System · Einstellungen").

### Fixed
- **Einklappen klappte nur halb ein.** Vorher wurden einzelne Elemente per `md:hidden`
  ausgeblendet, Text und Unterpunkte blieben aber im Layout stehen. Jetzt entscheidet ein
  einziges `isCollapsed`, das die Breite umschaltet und die Beschriftungen gar nicht erst
  rendert — und es gilt nur am Desktop, weil die Sidebar auf dem Handy ein Overlay ist.
- **Der Einklapp-Pfeil war nur im ausgeklappten Zustand sichtbar** und lag im eingeklappten
  woanders. Er sitzt jetzt fest im Fussbereich, ist immer da und zeigt in die Richtung, in
  die es geht: nach links zum Einklappen, nach rechts zum Ausklappen.

## 2026.08.14.14.00.00
### Fixed
- **Systemzustand: die Kachel „Datenbank" war rot, obwohl die Datenbank verbunden war.**
  Alle vier Kacheln teilten sich dieselbe Farbregel `usageColor(percent)`, die für
  Auslastungswerte gedacht ist: ab 80 % rot. Die Datenbank-Kachel setzte bei *verbunden*
  aber 100 — gemeint als „voller Balken", gelesen als „kritisch voll". Ergebnis: ein
  vollflächig roter Balken über dem Wort „OK", während der Health-Check zu Recht meldete,
  dass alles läuft. Nur die Anzeige war falsch, der Zustand nie.
  Jede Kachel bringt ihre Farbe jetzt selbst mit: Auslastung wie bisher grün → gelb → rot,
  die Datenbank grün bei „Verbunden" und rot bei „Nicht erreichbar" — mit ausgeschriebenem
  Text statt „OK".

## 2026.08.14.12.00.00
### Added
- **Systemcheck** (`components/SystemCheck.vue`, eingebunden im *Systemzustand*): ein Knopf
  prüft das ganze System durch. Das Ergebnis steht nach Bereichen sortiert da; standardmäßig
  nur die Auffälligkeiten, aufgeklappt mit Messwert und Handlungsempfehlung. „Alle anzeigen"
  blendet die unauffälligen Prüfungen dazu. Beim Öffnen erscheint der letzte Bericht, ohne
  neu zu messen.
- **Updates** (`views/Updates.vue`, *System → Updates*): installierter Stand gegen GitHub,
  Update einspielen, Rückfall auf eine Sicherung, Fortschrittsanzeige und Protokoll.
  - Vor Update **und** Rückfall kommt eine Rückfrage, die ausdrücklich auf möglichen
    Datenverlust hinweist; erst ein gesetzter Haken gibt den Knopf frei. Beim Update lässt
    sich der Datenbank-Abzug an- und abwählen.
  - Während des Laufs ist der Server zeitweise weg — die Seite erkennt das, sagt es und
    verbindet sich von selbst wieder.
  - Der Einzeiler für die Kommandozeile steht mit Kopierknopf daneben.

### Changed
- **Linkes Menü aufgeräumt** (`views/Layout.vue`): sichtbar sind nur noch die drei Bereiche
  *Überwachung*, *Verwaltung*, *System*. Ein Klick klappt die Unterpunkte auf; der Bereich der
  gerade offenen Seite klappt selbsttätig auf. Vorher standen bis zu zehn Einträge untereinander.
- **System entwirrt** (`views/Settings.vue`): *Verzeichnis (LDAP)*, *Archivierung*,
  *Erscheinungsbild* und *Anmeldesicherheit* sind keine eigenen Menüpunkte mehr, sondern Reiter
  der Einstellungen — gruppiert nach *Betrieb*, *Infrastruktur*, *Konto & Anmeldung* und
  *Darstellung*. Geladen werden sie erst beim Öffnen des Reiters.
  Die Adresse zieht mit (`/settings/ldap`), damit sich ein Bereich verlinken lässt und die
  bisherigen Links weiter funktionieren. Der Reiter „System" heißt jetzt „Neustart" — das ist,
  was er tut.

### Removed
- **Impressum, Datenschutz und Cookie-Hinweis** ersatzlos entfernt (Ansichten, Routen,
  Fußzeilen-Links auf der Anmeldeseite und im Layout). LogBot ist ein internes Werkzeug und
  keine Website; die Seiten standen nur im Weg.

## 2026.08.02.18.00.00
### Added
- **Passkeys**: neue Ansicht `views/SecuritySettings.vue` (*System → Anmeldesicherheit*) zum
  Anlegen, Umbenennen und Entfernen eigener Passkeys, dazu ein Knopf **„Mit Passkey anmelden"**
  auf der Anmeldeseite. `utils/webauthn.js` übersetzt zwischen base64url und den Binärdaten,
  die der Browser erwartet, und übersetzt Browser-Fehler in verständliche Sätze
  (abgebrochen, bereits hinterlegt, kein HTTPS …).
- Fehlt HTTPS oder die Browser-Unterstützung, erscheinen die Passkey-Knöpfe gar nicht erst,
  sondern ein Hinweis, woran es liegt.

## 2026.08.02.16.00.00
### Added
- **Verzeichnis (LDAP)** — neue Ansicht `views/LdapSettings.vue` unter *System → Verzeichnis*:
  Server, Dienstkonto, Suchfilter, Attribute, Pflicht- und Admin-Gruppe. Dazu ein Testfeld, das
  eine echte Anmeldung durchspielt und DN, Gruppen und die daraus folgende Rolle anzeigt.
- **Archivierung** — neue Ansicht `views/ArchivingSettings.vue` unter *System → Archivierung*:
  Ziel (SFTP/FTPS/FTP/SMB/eingebundener Ordner), Zugangsdaten, Zielordner, Alter der Logs,
  Zeitplan und „nach Übertragung löschen". Knöpfe für Verbindungstest und sofortigen Lauf,
  darunter die Historie der letzten Läufe mit Menge, Größe und Dauer.
- **Systemzustand** (`views/Health.vue`) neu aufgebaut und um einen **Datenbank-Bereich**
  erweitert: lokal oder extern, Server, Verschlüsselung, PostgreSQL-Version, Größe,
  Verbindungen, Antwortzeit. Warnt ausdrücklich, wenn eine externe Datenbank ohne TLS
  angebunden ist.
- Router: Ansichten können `meta.admin` setzen — Nicht-Admins landen auf dem Dashboard statt in
  einer Ansicht, die ihnen nur Fehlermeldungen zeigen würde.

### Changed
- Branding-Store schickt bei Speichern, Uploads und Zurücksetzen den Anmelde-Token mit
  (die Endpunkte sind seit 2026.08.02.14.00.00 Admins vorbehalten).

## 2026.08.02.14.00.00
### Changed
- **Oberfläche neu gestaltet.** Grundlage ist ein Design-System in
  `assets/css/main.css`: Tokens für Radien, Schatten, Abstände und Zustandsfarben plus
  fertige Klassen (`.card`, `.btn`, `.input`, `.badge`, `.table`, `.stat-card`, `.modal`, …).
  Die Zustandsfarben (Hover, „soft"-Flächen, Fokusring) entstehen per `color-mix()` aus den
  Markenfarben — eine im Branding geänderte Primärfarbe zieht damit überall automatisch mit.
- **Farbschema**: dunkler, kontrastreicher Hintergrund (`#16161f`) statt des blaugrauen
  Vorgängers; hell entsprechend ruhiger. Bestehende Installationen, die die Farben nie
  angepasst haben, werden beim Start automatisch übernommen (siehe `CHANGELOG/backend.md`).
- **Seitenmenü**: Navigation in Gruppen (Überwachung / Verwaltung / System), eigene
  Icon-Komponente (`components/AppIcon.vue`, Inline-SVG statt Emoji), aktiver Eintrag mit
  Farbfläche und Markierungsbalken — auch im angedockten Zustand erkennbar. Marke, Theme-
  Umschalter und Benutzer sitzen in eigenen Bereichen; darüber liegt eine Kopfleiste mit
  dem Titel der aktuellen Seite.
- **Anmeldung**: zweispaltig mit Markenseite (Logo, Tagline) und Formular, Passwort ein-/
  ausblendbar, Fehler als Hinweisfeld statt roter Box, MFA-Schritt mit eigenem Code-Feld.
- **Dashboard**: Kennzahlen als Kacheln mit Icon und Einordnung, die in die passend
  gefilterte Log-Ansicht führen (z. B. „Fehler & kritisch" → `?min_severity=error`),
  dazu Verteilung nach Schweregrad und aktivste Quellen als Balken sowie Ladeskelette.
- **Geräte**: Karten mit Statuspunkt, Eckdaten als Definitionsliste und eigenen Knöpfen
  für Aufbewahrung/Löschen; Aufbewahrungs-Dialog auf die neue Modal-Klasse umgestellt.
- Log-Level-Abzeichen nutzen jetzt die Badge-Klassen des Design-Systems statt fester
  Tailwind-Farben (`bg-red-500` …) — dadurch im hellen Theme lesbar und brandingfähig.

### Fixed
- **Zoom auf Mobilgeräten war gesperrt** (`user-scalable=no` in `index.html`) — entfernt.
- Beim Start blitzte kurz ein weißer Hintergrund auf (`<body class="bg-gray-100">`);
  die Seite startet jetzt direkt im Theme-Hintergrund.
- Tastatur-Fokus ist durchgängig sichtbar (`:focus-visible`), Scrollbars folgen dem Theme.

### Performance
- Der globale Übergang `* { transition: … }` galt für **jedes** Element im Dokument und
  kostete bei Loglisten mit tausenden Zeilen spürbar Rechenzeit. Er gilt jetzt nur noch für
  die Bausteine, die beim Theme-Wechsel tatsächlich die Farbe wechseln; zusätzlich werden
  Animationen bei `prefers-reduced-motion` abgeschaltet.

## 2026.07.31.23.30.00
### Added
- Geräteart **FRITZ!Box** wird in Geräteliste, Geräte-Ansicht und Log-Filter mit Klarnamen
  angezeigt (statt `fritzbox`) und ist im Typ-Filter der Geräteliste auswählbar.

## 2026.07.31.23.00.00
### Added
- **Hinweis bei unverschlüsselter Verbindung** (`components/InsecureConnectionBanner.vue`):
  wird die Oberfläche über `http://` geladen, erscheint oben ein Balken mit Link auf HTTPS.
  Wegklickbar, die Entscheidung gilt bis zum Schließen des Tabs. Auf localhost erscheint er nicht.
- Reverse-Proxy-Einstellungen: Modus **selbstsigniert (interne CA)**, Feld für **zusätzliche
  HTTPS-Adressen** (mit „Aktuelle Adresse übernehmen"), Anzeige von Server-Warnungen und
  ein Knopf **„Zurück auf HTTP"** als Notausstieg.

### Fixed
- „Apply" wendet jetzt **exakt den Editor-Inhalt** an (vorher überschrieb der gewählte Modus
  serverseitig alle manuellen Änderungen).
- Beim Wechsel auf den HTTP-Modus wird die Seite **nicht mehr automatisch umgeleitet** und es
  öffnet sich kein neuer Tab mehr — es erscheint nur ein Hinweis mit Link. Die harte
  Weiterleitung riss die laufende Sitzung mitten im Speichern ab.
- Vorlagen werden beim Tippen im FQDN-Feld gebündelt nachgeladen (400 ms) statt bei jedem Zeichen.

## 2026.07.31.22.10.00
### Added
- **Logtyp-Filter im Filter-Panel** (`components/LogTable.vue`), oberhalb der bisherigen Felder:
  - **Schweregrad** als Knopfreihe (Alle / Nur kritisch / Fehler und dringender / …) —
    ein Klick statt Level-Dropdown, der aktive Knopf ist farbig.
  - **Kategorie** (Anmeldung & Rechte, Kernel, Netzwerk, Firewall, Container, Cron, Mail,
    System, Audit), **Syslog-Facility** und **Geräteart** als Auswahlfelder.
- Die neuen Filter erscheinen als Chips, landen in der URL und gelten auch für den Export.
- In der Geräte-Ansicht ist die Geräteart ausgeblendet (dort ist das Gerät bereits gesetzt).

## 2026.07.31.21.10.00
### Added
- **Log-Ansicht pro Gerät**: neue Route `/devices/:hostname` (`views/DeviceLogs.vue`) mit
  Steckbrief (Status, IP, MAC, Typ, erst-/zuletzt gesehen, gespeicherte Logs, Retention)
  und darunter der Logliste, fest auf dieses Gerät gefiltert (exakter Hostname-Vergleich).
- **Einstiege dorthin**: Klick auf eine Agent-Karte (`views/Agents.vue`), Klick auf einen
  Hostnamen im Dashboard oder in der allgemeinen Logliste, sowie ein Link im Log-Detail.
  Die Kachel „Hosts" im Dashboard führt zur Geräte-Übersicht.
- **`components/LogTable.vue`**: Filter, Tabelle, Pagination, Detail-Dialog und Export als
  eine wiederverwendbare Komponente — genutzt von der allgemeinen und der Geräte-Ansicht.
  `views/Logs.vue` ist dadurch nur noch ein dünner Rahmen.
- Filter der allgemeinen Log-Ansicht stehen jetzt in der URL (teilbare, reload-feste Links).
- **Ein-/ausklappbares Seitenmenü** (`views/Layout.vue`): Button im Sidebar-Kopf klappt die
  Navigation auf Desktop zu einer schmalen Icon-Leiste (`md:w-16`) zusammen und wieder auf.
  Beschriftungen erscheinen im eingeklappten Zustand als Tooltip (`title`).
- Zustand wird in `localStorage` (`logbot.sidebarCollapsed`) gemerkt und beim nächsten
  Reload wiederhergestellt; ohne verfügbaren `localStorage` gilt er nur für die Sitzung.
- Mobile: zusätzlicher **Schließen-Button** im Sidebar-Kopf (bisher nur Klick auf das Overlay).

### Fixed
- **CSV-/JSON-Export funktioniert wieder.** Die Buttons riefen `/api/logs/export` auf, das es
  im Backend nicht gab (die Anfrage landete auf `/api/logs/{log_id}` und schlug fehl).
  Der Download läuft jetzt über `fetch` + Blob, weil `window.open` keinen Auth-Header setzen kann.

### Changed
- Navigationseinträge kommen jetzt aus einer Liste (`navItems`) statt aus acht einzeln
  ausgeschriebenen `<li>` — Icons/Labels an einer Stelle pflegbar.
- Angezeigte Versionsnummer in der Sidebar kommt jetzt aus `package.json` (war fest auf
  einem alten Stand von 2026.05 verdrahtet und musste doppelt gepflegt werden).
- Footer sitzt jetzt immer am unteren Rand des Inhaltsbereichs (`flex flex-col` + `flex-1`).

## 2026.07.18.16.00.00
### Added
- **Agents:** Gerätetyp **Linux-Agent** (`linux_agent`) im Typ-Filter und in der Typ-Beschriftung ergänzt – passend zum neuen Linux-HTTPS-Agent.

## 2026.07.11.13.03.42
### Changed
- Einstellungen: Reiter **Reverse Proxy** ersetzt durch **Netzwerk** mit zwei Bereichen — **Reverse Proxy** (unverändert) und **DNS** (neu).

### Added
- **DNS**-Bereich: Umschaltung DHCP-automatisch / manuell, Anzeige der erkannten System-DNS, eigene Server + Such-Domains, aktive Nameserver und „Auflösung testen".

## 2026.05.30.18.02.35
### Added
- Ausgangsbasis dieses Changelogs.

_Änderungen vor Einführung des Changelogs wurden nicht einzeln erfasst._
