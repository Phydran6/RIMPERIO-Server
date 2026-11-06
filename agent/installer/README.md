# Installer, Signatur und Virenscanner

Wie aus dem Agent eine `.exe` und eine `.msi` werden — und wie man dafür sorgt,
dass Windows und Virenscanner ihnen vertrauen.

← [Übersicht](../README.md) · [Agent](../README.md)

---

## Ehrliche Vorbemerkung: „von jedem Antivirus als OK"

Das lässt sich **nicht garantieren** — von niemandem, mit keinem Werkzeug. Wer
das verspricht, verkauft Schlangenöl. Ein RMM-Agent ist ein Fernwartungs­werkzeug;
genau solche Programme schauen sich Virenscanner besonders genau an. Was man
**seriös** tun kann, senkt die Beanstandungen aber auf nahezu null:

1. **Mit einem echten Zertifikat signieren** (der mit Abstand größte Hebel).
2. **Sauber bauen** — kein Packer, kein Obfuscator, kein „Crypter". Genau diese
   Tarn-Techniken lösen Heuristik-Alarme aus; sie machen es *schlimmer*, nicht
   besser. RIMPERIO baut deshalb ein schlankes, statisches, unverschleiertes Go-Binary.
3. **Reputation aufbauen** und **Fehlalarme melden** (unten).
4. Beim Kunden/Betrieb den Signierer **freigeben** (Allowlist) — der realistische
   Weg in verwalteten Umgebungen.

Was RIMPERIO bewusst **nicht** tut und wobei dieses Projekt nicht hilft: Signaturen
verschleiern, Scanner umgehen, Erkennung austricksen. Das wäre genau das
Verhalten, das echte Schadsoftware zeigt — und der sichere Weg auf jede
Blockliste.

---

## Der große Hebel: Code Signing

Eine gültige Authenticode-Signatur ist das, woran SmartScreen und Microsoft
Defender „vertrauenswürdig" festmachen. Drei Wege, nach Budget:

| Weg | Kosten | Vertrauen | Für wen |
|---|---|---|---|
| **SignPath Foundation** | **kostenlos** für Open Source | gut (OV, gespendetes Zertifikat) | dieses Projekt ist MIT — passt |
| **Azure Trusted Signing** | ~10 USD/Monat | sehr gut, von Microsoft betrieben | kleine Teams, CI-freundlich |
| **OV/EV-Zertifikat** (Sectigo, DigiCert …) | ~200–400 EUR/Jahr | EV: sofortige SmartScreen-Reputation | Firmen mit Budget |
| *selbstsigniert* | 0 | **nur** auf Geräten, die das Zertifikat per GPO kennen | reines Labor/Eigennetz |

> **Empfehlung:** Als Open-Source-Projekt zuerst **SignPath Foundation** anfragen.
> Wer sofort und unabhängig signieren will: **Azure Trusted Signing**.

### SignPath Foundation einrichten (kostenlos)

Die CI ist fertig vorbereitet; es fehlen nur die Zugangsdaten. Ablauf:

1. **Antrag stellen:** <https://signpath.org/apply>. Die Voraussetzungen erfüllt
   das Repo bereits: MIT-Lizenz, öffentlicher Quellcode, Build in GitHub Actions,
   und die Abschnitte *Code signing policy*, Rollen und Datenschutz stehen im
   [README](../README.md#code-signing-policy). Zusätzlich verlangt die Foundation
   **2FA** auf GitHub und bei SignPath sowie ein **bereits veröffentlichtes
   Release** (unsigniert ist ok). Die Prüfung dauert einige Tage.
2. **Nach der Freigabe** in SignPath:
   - den *Trusted Build System* „GitHub.com" mit dem Projekt verknüpfen,
   - zwei Artefakt-Konfigurationen anlegen, Inhalt aus
     [`signpath/agent-exe.xml`](signpath/agent-exe.xml) (Slug `agent-exe`) und
     [`signpath/agent-msi.xml`](signpath/agent-msi.xml) (Slug `agent-msi`),
   - einen API-Token für einen Benutzer mit *Submitter*-Rolle erzeugen.
3. **Im GitHub-Repo** unter *Settings → Secrets and variables → Actions*:

   | Art | Name | Wert |
   |---|---|---|
   | Secret | `SIGNPATH_API_TOKEN` | der API-Token |
   | Variable | `SIGNPATH_ORGANIZATION_ID` | Organisations-ID aus SignPath |
   | Variable | `SIGNPATH_PROJECT_SLUG` | Projekt-Slug aus SignPath |
   | Variable | `SIGNPATH_SIGNING_POLICY_SLUG` | optional, Standard `release-signing` (zum Testen `test-signing`) |

4. **Release taggen** (`git tag v2026.11.06 && git push --tags`). Der Workflow
   schickt erst die exe, dann das MSI (mit der signierten exe darin) zu SignPath.
   Jeder Auftrag wartet bis zu einer Stunde auf deine **manuelle Freigabe** im
   SignPath-Webportal; danach hängen die signierten Dateien am Release.

Ist SignPath nicht eingerichtet, nimmt die CI ein PFX-Secret
(`RIMPERIO_PFX_BASE64`/`RIMPERIO_PFX_PASSWORD`), sonst baut sie unsigniert.

### Signieren im Projekt

[`sign.ps1`](windows/sign.ps1) kapselt `signtool`. Gesteuert wird über
Umgebungs­variablen — **nie** ein Zertifikat oder Passwort ins Repo:

```powershell
# Azure Trusted Signing
$env:RIMPERIO_SIGN_METHOD   = "azure"
$env:RIMPERIO_AZURE_DLIB    = "C:\pfad\Azure.CodeSigning.Dlib.dll"
$env:RIMPERIO_AZURE_METADATA= "C:\pfad\metadata.json"

# oder: klassisches PFX
$env:RIMPERIO_SIGN_METHOD = "pfx"
$env:RIMPERIO_PFX_PATH    = "C:\pfad\zertifikat.pfx"
$env:RIMPERIO_PFX_PASSWORD= "…"      # in CI aus einem Secret
```

Immer **mit Zeitstempel** (macht `sign.ps1` automatisch) — so bleibt die Signatur
gültig, wenn das Zertifikat später abläuft.

---

## Bauen

Voraussetzungen: **Go** (für die .exe), **WiX v4** (für die .msi,
`dotnet tool install --global wix`), optional **goversioninfo** für die
Versions-Ressource (`go install github.com/josephspurrier/goversioninfo/cmd/goversioninfo@latest`).

```powershell
# 1) Agent-.exe bauen (und signieren)
cd installer\windows
pwsh .\build-agent.ps1 -Sign

# 2) MSI bauen (signiert die exe vor dem Einpacken und danach das MSI)
pwsh .\build-msi.ps1 -Sign
```

Ergebnis: `agent\bin\rimperio-agent.exe` und `installer\windows\rimperio-agent.msi`.

In der CI übernimmt das [`.github/workflows/agent-release.yml`](../../.github/workflows/agent-release.yml)
— bauen, signieren, als Release-Artefakte anhängen.

---

## Verteilen

`SCHLÜSSEL` ist ein Zugangsschlüssel aus der Oberfläche (*Verwaltung → Zugang & Sicherheit →
Zugangsschlüssel*), am besten eine Einladung. Die fertigen Befehle mit eingesetzter
Server-Adresse stehen unter *Fernwartung → Rechner → Agent hinzufügen*.

**Einzeln / schnell (ohne MSI):**

```powershell
# als Administrator
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/installer/windows/install-agent.ps1))) `
  -Server https://rmm.example.de -Secret SCHLÜSSEL -Site Buero
```

**Per MSI (GPO, Intune, SCCM):**

```powershell
msiexec /i rimperio-agent.msi SERVER=https://rmm.example.de SECRET=SCHLÜSSEL SITE=Buero /qn
```

Das MSI schreibt die Eckdaten nach `HKLM\SOFTWARE\RIMPERIO\Agent`, registriert den
Dienst `rimperio-agent` und startet ihn. Beim **ersten Start** meldet der Dienst sich
selbst am Server an — die Installation selbst braucht also kein Netz.

**Linux:**

```bash
sudo bash install-agent.sh --server https://rmm.example.de --secret SCHLÜSSEL --site Buero
```

---

## Deinstallieren

**Windows, per MSI installiert:** *Einstellungen → Apps → RIMPERIO Agent →
Deinstallieren*, oder still:

```powershell
msiexec /x rimperio-agent.msi /qn
```

Das MSI stoppt und entfernt den Dienst und löscht das Programmverzeichnis.

**Windows, per Skript installiert** (als Administrator):

```powershell
& "C:\Program Files\RIMPERIO Agent\rimperio-agent.exe" uninstall   # Dienst + Konfiguration
Remove-Item "C:\Program Files\RIMPERIO Agent" -Recurse -Force
```

**Linux:**

```bash
sudo rimperio-agent uninstall          # Dienst + Konfiguration
sudo rm /usr/local/bin/rimperio-agent
```

Den Rechner danach in der Oberfläche unter *Agent → Entfernen* austragen (nur Admins).

---

## Wenn ein Scanner doch anschlägt

Trotz Signatur kann es anfangs (mangels Reputation) zu Fehlalarmen kommen. Dann:

- **Microsoft Defender — Fehlalarm melden:**
  <https://www.microsoft.com/wdsi/filesubmission> (signierte Datei hochladen,
  „incorrectly detected as malware" wählen).
- **Weitere Hersteller** haben eigene „False Positive"-Portale (Avast, Bitdefender,
  ESET, Kaspersky, Sophos …) — dort die **signierte** Datei einreichen.
- **Im eigenen Betrieb:** den Signierer bzw. den Pfad
  `C:\Program Files\RIMPERIO Agent\` in Defender/EDR **freigeben** (Allowlist).
- **Reputation wächst** mit jeder signierten, verbreiteten Version. Ein
  **EV-Zertifikat** überspringt die Wartezeit bei SmartScreen.

---

## Warum das so ist

Virenscanner bewerten *Verhalten* und *Herkunft*. Ein Fernwartungs-Agent darf
Befehle ausführen — das ist sein Zweck und sieht für eine Heuristik erst einmal
verdächtig aus. Die Antwort darauf ist **Transparenz und Herkunftsnachweis**
(Signatur, klare Metadaten, offener Quellcode, Meldewege), nicht Tarnung.
Deshalb geht RIMPERIO genau diesen Weg.
