# Marke / Icons

Hier liegt das Markenzeichen von RIMPERIO.

| Datei | Wofür |
|---|---|
| [`../frontend/public/favicon.svg`](../frontend/public/favicon.svg) | Favicon + Logo in der Oberfläche (SVG, skaliert überall) |
| `rimperio-icon.png` *(hier ablegen)* | dein echtes Logo als PNG, 256×256 oder 512×512 |
| `../installer/windows/rimperio.ico` *(erzeugt)* | Symbol der `.exe` und `.msi` |

## Dein Logo überall einsetzen

1. Lege dein Logo als **`brand/rimperio-icon.png`** ab (quadratisch, am besten 512×512).
2. Erzeuge daraus das Windows-Symbol:

   ```bash
   node scripts/make-ico.mjs brand/rimperio-icon.png installer/windows/rimperio.ico
   ```

3. Baue den Agent neu — `build-agent.ps1` bindet das Symbol automatisch ein, wenn
   `installer/windows/rimperio.ico` vorhanden ist.
4. Für das Favicon kannst du das SVG behalten (empfohlen) oder dein PNG als
   `frontend/public/favicon.png` zusätzlich hinterlegen.

Das SVG unter `frontend/public/favicon.svg` ist eine saubere Nachbildung des
Markenzeichens (dunkelblaues Feld, goldene Wortmarke, Monitor + Zahnrad) und wird
sofort überall in der Oberfläche verwendet.
