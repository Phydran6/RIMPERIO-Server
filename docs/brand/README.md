# Marke / Icons

Hier liegt das Markenzeichen von RIMPERIO: dunkelblaues Feld, goldene
Wortmarke mit Blitz, Monitor mit Zahnrad. Die Vorlagen in voller Größe liegen in
der [App](https://github.com/Phydran6/RIMPERIO-App/tree/main/assets/icon)
(`rimperio_logo.png` mit Rahmen, `icon.png` vollflächig) — Server, Agent und App
tragen dasselbe Zeichen.

| Datei | Wofür |
|---|---|
| `rimperio-icon.png` | Logo mit Goldrahmen, 256×256, transparente Ecken — Quelle für das Windows-Symbol |
| [`../assets/rimperio-icon.png`](../assets/rimperio-icon.png) | dasselbe in 512×512 für das README (auch im Übersichts-Repo `Phydran6/RIMPERIO` unter demselben Pfad) |
| [`../../server/frontend/public/favicon.png`](../../server/frontend/public/favicon.png) | Favicon der Oberfläche (Logo mit Rahmen, 256×256) |
| [`../../server/frontend/public/rimperio-icon.png`](../../server/frontend/public/rimperio-icon.png) | `apple-touch-icon`, vollflächig ohne Rahmen — iOS rundet selbst ab |
| [`../../agent/installer/windows/rimperio.ico`](../../agent/installer/windows/rimperio.ico) | Symbol der Agent-`.exe` und `.msi` |

## Logo austauschen

1. Neues Logo als **`docs/brand/rimperio-icon.png`** ablegen (quadratisch,
   256×256; größer geht nicht, das ICO-Format endet bei 256).
2. Windows-Symbol daraus erzeugen:

   ```bash
   node docs/brand/make-ico.mjs docs/brand/rimperio-icon.png agent/installer/windows/rimperio.ico
   ```

3. Agent neu bauen — `build-agent.ps1` bettet das Symbol ein, die MSI nimmt es
   für „Apps & Features“.
4. `favicon.png`, `rimperio-icon.png` (Oberfläche) und `docs/assets/rimperio-icon.png`
   (README) ebenfalls ersetzen.

Ein unter *Einstellungen → Erscheinungsbild* hochgeladenes Logo oder Favicon
überschreibt die Dateien der Oberfläche zur Laufzeit; sie bleiben die
Rückfallebene.
