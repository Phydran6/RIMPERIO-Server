# Installer, Signatur und Virenscanner

Wie aus dem Agent eine `.exe` und eine `.msi` werden — und wie man dafür sorgt,
dass Windows und Virenscanner ihnen vertrauen.

← [Übersicht](../README.md) · [Agent](../agent/README.md)

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

In der CI übernimmt das [`.github/workflows/agent-release.yml`](../.github/workflows/agent-release.yml)
— bauen, signieren, als Release-Artefakte anhängen.

---

## Verteilen

**Einzeln / schnell (ohne MSI):**

```powershell
# als Administrator
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/installer/windows/install-agent.ps1))) `
  -Server https://rmm.example.de -Secret GEHEIMNIS -Site Buero
```

**Per MSI (GPO, Intune, SCCM):**

```powershell
msiexec /i rimperio-agent.msi SERVER=https://rmm.example.de SECRET=GEHEIMNIS SITE=Buero /qn
```

Das MSI schreibt die Eckdaten nach `HKLM\SOFTWARE\RIMPERIO\Agent`, registriert den
Dienst `rimperio-agent` und startet ihn. Beim **ersten Start** meldet der Dienst sich
selbst am Server an — die Installation selbst braucht also kein Netz.

**Linux:**

```bash
sudo bash install-agent.sh --server https://rmm.example.de --secret GEHEIMNIS --site Buero
```

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
