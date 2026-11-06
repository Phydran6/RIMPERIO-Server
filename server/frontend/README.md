# Frontend

Vue 3 mit Vite, Pinia und Tailwind. Ohne Komponentenbibliothek — das
Design-System steht in `src/assets/css/main.css`.

← [Übersicht](../../README.md) · [Betrieb](../../docs/operate/README.md) · [Alle Dokumente](../../docs/README.md)

## Aufbau

```
src/
  main.js            Einstiegspunkt
  App.vue            Rahmen, Branding, Hinweisbalken
  router/            Routen und Zugriffsprüfung
  views/
    Layout.vue       Seitenmenü (drei Ebenen), Kopfleiste
    Logs, Dashboard, Agents, Users, Webhooks, Health, Settings
    Updates, Backup, AiSettings, Stacks, MailSettings, Terminal
  components/
    AppIcon.vue      Icon-Set als Inline-SVG
    BackupPrompt.vue Die Rückfrage vor jedem Systemeingriff
    LogTable.vue     Logliste mit Filtern
  stores/            auth, branding, theme
  i18n/              Sprachen: index.js, de.js, en.js
  assets/css/        Design-System
public/              RIMPERIO-Zeichen und Standard-Favicon
```

## Drei bewusste Entscheidungen

**Kein vue-i18n.** Gebraucht wird ein Wörterbuch, eine reaktive Auswahl und
`t()`. Das sind fünfzig Zeilen in `src/i18n/index.js`. Eine weitere
Abhängigkeit im Build wäre dafür zu viel — und der Container baut so auch ohne
Netz durch. Fehlt ein Schlüssel in einer Sprache, greift Deutsch; fehlt er auch
dort, steht der Schlüssel selbst da. Das fällt beim Testen sofort auf, statt
still eine leere Stelle zu hinterlassen.

**Kein Icon-Paket.** `AppIcon.vue` enthält die Icons als SVG-Pfade auf einem
24×24-Raster. Sie erben die Textfarbe und kosten keine Abhängigkeit.

**Kein xterm.js.** Das Terminal bringt einen schlanken eigenen Bildschirm mit
(Zeilenpuffer, ANSI-Farben, das Nötigste an Cursorsteuerung). 300 kB für ein
Nebenwerkzeug wären zu viel. Der Preis: Programme mit voller
Bildschirmsteuerung — `top`, `nano`, `vim` — stellt er nicht sauber dar. Das
steht auch so in der Oberfläche.

## Sprache hinzufügen

1. `src/i18n/de.js` kopieren, z. B. nach `fr.js`, und übersetzen.
2. In `src/i18n/index.js` importieren, in `MESSAGES` und `LOCALES` eintragen.

Fertig — der Umschalter im Menü zeigt sie dann von selbst. Unübersetzte
Schlüssel fallen auf Deutsch zurück.

## Entwickeln

In das Verzeichnis wechseln:

```bash
cd frontend
```

Abhängigkeiten installieren:

```bash
npm install
```

Entwicklungsserver starten (Vite auf :5173, API-Aufrufe siehe `vite.config.js`):

```bash
npm run dev
```

Für den Betrieb bauen (nach `dist/`):

```bash
npm run build
```

Änderungen gehören ins [Changelog](../../docs/changelog/frontend.md).
