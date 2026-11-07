# n8n-Workflows

Fertige Abläufe für n8n. Importieren, Zugangsdaten zuweisen, aktivieren.

← [Übersicht](../../README.md) · [Integrationen](../../docs/integrations/README.md) · [Alle Dokumente](../../docs/README.md)

| Datei | Was sie tut |
|-------|-------------|
| [`rimperio.json`](rimperio.json) | Grundgerüst: Logs vom Webhook holen und weiterverarbeiten |
| [`telegram-chat-flow.json`](telegram-chat-flow.json) | Telegram-Bot mit Claude-Analyse (unten beschrieben) |

## n8n selbst betreiben

Zwei Wege:

* **Vorhandenes n8n** — irgendwo im Netz. Für die KI-Auswertung im Web-UI unter
  *Automatisierung → KI-Auswertung* den Weg **„n8n extern"** wählen und die Webhook-Adresse
  eintragen.
* **Als Container neben RIMPERIO** — unter *RIMPERIO-Server → Zusatzdienste* einschalten.
  Die Adresse ist dann `http://rimperio-n8n:5678/webhook/rimperio`, und die Daten
  bleiben im Docker-Netz.

---

## Telegram-Bot mit Claude-Analyse

Telegram-Bot, der auf Anfrage Logs von einem RIMPERIO-Webhook abholt und sie von Claude (Anthropic) analysieren lässt. Antwort kommt zurück in den Telegram-Chat.

## Flow

1. **Telegram-Trigger** — User schreibt dem Bot eine Frage.
2. **HTTP Request** — Logs werden vom RIMPERIO-Webhook geholt.
3. **IF-Node** — Prüft, ob überhaupt Logs zurückgekommen sind.
4. **Code-Node** — Formatiert die Logs für den LLM-Input.
5. **AI Agent (Claude Haiku 4.5)** — Beantwortet die User-Frage anhand der Logs.
6. **Code-Node** — Trennt `CHATID:` aus der Antwort heraus.
7. **Telegram** — Schickt die Analyse zurück an den User.

## Setup

### 1. Workflow importieren
In n8n: **Workflows → Import from File → `telegram-chat-flow.json`** auswählen.

### 2. Credentials anlegen
Folgende Credentials in n8n einrichten und im Workflow zuweisen:

| Credential-Typ        | Wofür                              |
|-----------------------|------------------------------------|
| `Anthropic API`       | Anthropic Chat Model (Claude)      |
| `Telegram Bot`        | Telegram Trigger + 2x Telegram Send|

Im JSON stehen aktuell die Platzhalter `REPLACE_WITH_YOUR_ANTHROPIC_CREDENTIAL_ID` und `REPLACE_WITH_YOUR_TELEGRAM_CREDENTIAL_ID`. Nach dem Import einfach in den jeweiligen Nodes die echten Credentials auswählen — die Platzhalter werden dann überschrieben.

### 3. RIMPERIO-Webhook eintragen
Im Node **„Logs werden vom RIMPERIO geholt"** den Platzhalter `REPLACE_WITH_YOUR_RIMPERIO_WEBHOOK_URL` durch deine eigene RIMPERIO-Webhook-URL ersetzen (inkl. Token, falls erforderlich).

### 4. Aktivieren
Workflow steht standardmäßig auf `active: false`. Nach dem Setup oben rechts in n8n aktivieren.

## Anpassen

- **Modell wechseln**: Im Node „Anthropic Chat Model" das Modell ändern (z.B. auf `claude-sonnet-4-5` für komplexere Analysen).
- **System-Prompt**: Im Node „AI Agent" unter `Options → System Message` — dort sind die Formatierungs- und Verhaltensregeln definiert.
- **Sprache**: Aktuell deutsch, im System-Prompt anpassbar.

## Hinweise

- Der Bot antwortet nur auf konkrete Fragen — keine ungefragten Gesamt-Zusammenfassungen.
- Telegram-Plaintext, kein Markdown (sonst Render-Probleme).
- Die `CHATID:`-Zeile am Ende der LLM-Antwort ist ein Workaround, um den Chat-Kontext durch den Agent-Node durchzureichen.

---

## Ohne n8n geht es auch

Wer nur „erklär mir diese Logzeilen" will, braucht n8n nicht: RIMPERIO kann Claude
und ChatGPT auch direkt ansprechen. Siehe
[Integrationen → KI-Auswertung](../../docs/integrations/README.md#ki-auswertung).
