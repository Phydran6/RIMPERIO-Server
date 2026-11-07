# Integrationen

Webhooks, KI, Mail und die Zusatzdienste.

← [Übersicht](../../README.md) · [Alle Dokumente](../README.md)

---

## Webhooks

Zugriff auf gefilterte Logs **ohne Anmeldung** — gedacht für Abholdienste wie
n8n, Make oder Zapier.

```
GET /api/webhook/{id}/call?token={token}
```

Einrichten unter *Webhooks → Neuer Webhook*: Filter festlegen (Hostname,
Quelle, Level), Höchstzahl der Ergebnisse, ob die Rohzeilen mitkommen. Die
fertige URL kommt in den HTTP-Request-Knoten.

Gebremst auf 60 Aufrufe pro Minute: Der Endpunkt ist ohne Anmeldung erreichbar
und liefert Logdaten — das reicht für Abholdienste und begrenzt zugleich, was
jemand mit geratenen Tokens anrichten kann.

---

## KI-Auswertung

*System → KI-Auswertung* — einrichten dürfen nur Administratoren.

Vier Wege, keiner davon voreingestellt:

| Weg | Was passiert | Wann sinnvoll |
|-----|--------------|---------------|
| **Aus** | nichts verlässt den Server | Voreinstellung |
| **Claude (Anthropic)** | direkt an die API | „Erklär mir diese 200 Zeilen“ |
| **ChatGPT (OpenAI)** | direkt an die API | dasselbe, anderer Anbieter |
| **n8n extern** | an einen Webhook außerhalb | wenn n8n schon läuft |
| **n8n als Container** | an das n8n daneben | alles auf einer Maschine |

**Warum alle vier?** n8n kann mehr — Telegram, Ticketsystem, Datenbanken. Es ist
aber ein weiteres System, das laufen und gepflegt werden muss. Wer nur eine
Erklärung zu ein paar Logzeilen will, braucht das nicht.

> **Was den Server verlässt:** Bei jedem Weg außer „Aus“ gehen die
> **ausgewählten Logzeilen** an den eingestellten Empfänger. Das steht auch so
> in der Oberfläche. Der Knopf *Vorschau* zeigt vorher genau, was gesendet
> würde — Zeichenzahl, geschätzte Token und die ersten Zeilen im Klartext.

### Einrichten

1. Weg wählen.
2. API-Schlüssel bzw. Webhook-Adresse eintragen. Der Schlüssel wird **nie**
   zurückgegeben — die Oberfläche zeigt nur, *dass* einer hinterlegt ist. Ein
   leeres Feld beim Speichern heißt „unverändert lassen“.
3. Höchstzahl der Zeilen festlegen (Standard 100, Maximum 500). Ohne Grenze
   schickt ein unbedachter Klick den halben Logbestand ins Internet — und die
   Rechnung kommt später.
4. *Verbindung testen* — läuft mit einer **erfundenen** Logzeile, nicht mit
   echten Daten.
5. Optional für alle angemeldeten Benutzer freigeben. Standardmäßig dürfen nur
   Administratoren fragen.

### Was gesendet wird

Nicht die Rohzeilen, sondern die **zerlegte** Fassung: Zeitstempel, Host, Level,
Quelle, die lesbare Zusammenfassung und die erkannten Felder. Eine
Firewall-Zeile mit dreißig `key=value`-Paaren kostet sonst mehr Zeichen, als sie
an Erkenntnis bringt. Rohzeilen kommen nur mit, wenn man es anhakt.

### Fertiger n8n-Workflow

Unter [`n8n/`](../../server/n8n/README.md) liegt ein Telegram-Bot, der Logs vom
Webhook holt, von Claude auswerten lässt und die Antwort in den Chat schickt.

---

## Mail

*System → Mail* — nur für Administratoren.

**Anbieter anklicken, der Assistent erledigt den Rest.** Für jeden gängigen
Anbieter gibt es eine Kachel mit eigenem Assistenten. Gespeichert wird erst,
wenn die Anmeldung geprüft ist; danach lässt sich direkt eine Probemail
schicken.

| Anbieter | Wie | Was du tust |
|----------|-----|-------------|
| **Microsoft 365** | Microsoft Graph, App-Registrierung entsteht automatisch | mit einem Globalen Administrator bei Microsoft anmelden |
| **Google / Gmail** | smtp.gmail.com:587 | App-Passwort anlegen (Link im Assistenten) |
| **GMX** | mail.gmx.net:587 | im Postfach „POP3 und IMAP Zugriff erlauben“ einschalten |
| **WEB.DE** | smtp.web.de:587 | im Postfach „POP3 und IMAP Zugriff erlauben“ einschalten |
| **T-Online** | securesmtp.t-online.de:465 | separates E-Mail-Passwort anlegen |
| **IONOS, STRATO, Posteo, mailbox.org** | fester Server | Adresse + Passwort |
| **iCloud, Yahoo** | fester Server | App-Passwort anlegen |
| **Anderer Mailserver** | SMTP, alles von Hand | Server, Port, Anmeldung |
| **Postfix auf diesem Server** | eigener Container, optional Smarthost | Profil `postfix` einschalten |

### Microsoft 365 ohne Entra-Portal

1. Absenderpostfach wählen (leer = das Konto des anmeldenden Admins; ein
   freigegebenes Postfach ohne Lizenz geht auch).
2. *Bei Microsoft anmelden* zeigt einen Code. Auf
   <https://microsoft.com/devicelogin> eingeben, mit einem **Globalen
   Administrator** anmelden und *Im Namen Ihrer Organisation zustimmen*
   anhaken. Das Verfahren braucht keine Redirect-Adresse, funktioniert also
   auch, wenn RIMPERIO nur im LAN erreichbar ist.
3. RIMPERIO legt an: App-Registrierung *RIMPERIO Mailversand*, das
   Anwendungsrecht `Mail.Send` mit Admin-Zustimmung und ein Client-Geheimnis
   (24 Monate; kürzer, wenn eine Richtlinie das verlangt). Eine frühere
   RIMPERIO-App im selben Mandanten wird gelöscht.
4. Das Admin-Token wird verworfen. Gespeichert werden nur Mandant, App-ID und
   Geheimnis.

Angemeldet wird über die öffentliche Microsoft-App *Microsoft Graph Command
Line Tools* (die von `Connect-MgGraph`) mit den Rechten
`Application.ReadWrite.All` und `AppRoleAssignment.ReadWrite.All`.

`Mail.Send` als Anwendungsrecht darf von **jedem** Postfach im Mandanten
senden. Begrenzen lässt sich das nur in Exchange Online. Den fertigen
PowerShell-Befehl (`New-ApplicationAccessPolicy`) zeigt die Mail-Seite nach
der Einrichtung an.

Blockiert eine Richtlinie für bedingten Zugriff die Anmeldung per Gerätecode,
gibt es im Assistenten *Vorhandene App-Registrierung eintragen* (Mandant,
App-ID, Geheimnis).

Läuft das Geheimnis ab, warnt die Seite 30 Tage vorher. Zum Erneuern den
Assistenten einfach noch einmal durchlaufen.

### Warum Google kein „Anmelden und fertig“ hat

Google erlaubt nicht, OAuth-Clients für Gmail per API anzulegen, und für
Gmail-Rechte ist das Gerätecode-Verfahren gesperrt. Ein gemeinsamer
RIMPERIO-Client müsste eine kostenpflichtige Sicherheitsprüfung bei Google
durchlaufen. Das App-Passwort ist der Weg, den Google für genau diesen Fall
vorsieht.

### Postfix-Container

Was eingetragen wird, schreibt das Backend nach `data/postfix/settings.env`;
der Container liest das beim Start. Der Knopf *Erzeugte Postfix-Konfiguration*
zeigt, was in der Datei steht.

Beim Container lässt sich zusätzlich ein **Smarthost** eintragen. Meist nötig:
Die wenigsten Anschlüsse dürfen selbst zustellen (Port 25 gesperrt, kein
PTR-Eintrag). Dann übergibt Postfix an den Provider.

Der Container nimmt nur aus dem Docker-Netz an. Ein offenes Relay im Internet
ist binnen Stunden als Spamschleuder in Benutzung.

Braucht das Profil `postfix` unter *System → Zusatzdienste*.

**Benachrichtigungen:** Wann eine Mail verschickt wird, steht für alle Wege
gemeinsam unter *Benachrichtigungen*.

---

## Portainer

Container-Oberfläche im Browser: Zustände, Protokolle, Konsole.

Standardmäßig nur vom Server selbst erreichbar (`127.0.0.1:9000`). Wer aus dem
Netz herankommen will, setzt `PORTAINER_BIND=0.0.0.0` — und sollte vorher HTTPS
einrichten.

Zugangsdaten: *System → Zusatzdienste → Zugangsdaten anzeigen*. Dort lässt sich
auch ein neues Passwort setzen.

> Portainer bekommt den Docker-Socket **nur lesend**. Die Oberfläche zeigt damit
> alles an, kann aber nichts starten, stoppen oder löschen. Wer auch verwalten
> will, nimmt das `:ro` in [`server/deploy/optional.yml`](../../server/deploy/optional.yml)
> heraus — und weiß, worauf er sich einlässt: Schreibzugriff auf den Socket ist
> faktisch root auf dem Server.

---

## Tugtainer (löst Watchtower ab)

Sieht nach, ob für die **fremden** Images Updates bereitliegen — und meldet sie.
Mehr nicht.

**Warum Watchtower raus ist:** Es hat Images geholt *und* Container
eigenmächtig ausgetauscht. Nachts, ohne Ansage, und hinterher war schwer
zuzuordnen, warum ein Dienst plötzlich anders lief. Auf einem Log-Server, der
Nachvollziehbarkeit verspricht, ist das der falsche Mechanismus.

Tugtainer bekommt den Docker-Socket deshalb nur **lesend**: es *kann* gar
nichts austauschen, selbst wenn jemand die Automatik einschaltet. Eingespielt
wird bewusst — in RIMPERIOs Oberfläche unter *System → Container*, in Tugtainers
eigener Oberfläche oder auf der Kommandozeile.

**Zum Zusammenspiel mit dem Patchmanagement:** RIMPERIOs eigene Container
(`rimperio-app-*`) werden aus dem Quellcode gebaut, es gibt für sie gar kein
Image in einer Registry. Tugtainer kann dort also nichts finden; zuständig ist
*System → Updates*. Tugtainer kümmert sich um die fertigen Images:
PostgreSQL, Caddy, n8n, Portainer, Open WebUI, Postfix.

Oberfläche unter `127.0.0.1:9412`. In die `.env`, falls man den Socket doch
schreibbar machen will:

```
TUGTAINER_SOCKET_MODE=rw
```

> RIMPERIO prüft Image-Updates auch **ohne** Tugtainer: *System → Container →
> Auf Updates prüfen* fragt die Registry direkt nach dem Abdruck des benutzten
> Tags. Tugtainer bringt Zeitplan, eigene Oberfläche und mehrere Hosts dazu.

---

## Open WebUI

KI-Oberfläche, die dieselbe Schnittstelle spricht wie OpenAI — dahinter aber
das Modell hat, das man selbst wählt.

**Warum das interessant ist:** Mit einem lokalen Modell über Ollama verlässt
bei der KI-Auswertung **keine einzige Logzeile den Server**. Für Logdaten, in
denen Benutzernamen, interne Adressen und Fehlermeldungen stehen, ist das oft
der Unterschied zwischen „geht“ und „geht nicht“.

Ollama selbst ist bewusst **nicht** Teil dieses Stacks: Ein Sprachmodell will
mehrere Gigabyte Arbeitsspeicher und möglichst eine Grafikkarte, und das gehört
nicht nebenbei auf einen Log-Server. In die `.env` kommt die Adresse einer
vorhandenen Installation:

```
OLLAMA_BASE_URL=http://192.168.1.50:11434
```

Einzurichten unter *Auswertung → KI-Auswertung*, Anbieter **Open WebUI**. Es
braucht die Adresse (intern: `http://rimperio-openwebui:8080`) und einen
API-Schlüssel aus Open WebUI (dort unter *Profil → Einstellungen → Konto*).
Der Knopf „Modelle holen“ liest die verfügbaren Modelle aus, damit der Name
nicht geraten werden muss.

---

## n8n als Container

Läuft neben RIMPERIO, erreichbar unter `127.0.0.1:5678`. Anmeldung ist Pflicht —
ohne stünde die Oberfläche offen, und mit ihr jeder hinterlegte Zugang zu
Telegram, Mail oder KI-Anbietern.

Zugangsdaten wie bei Portainer unter *System → Zusatzdienste*.

Für die KI-Auswertung ist die interne Adresse
`http://rimperio-n8n:5678/webhook/rimperio` einzutragen — dann bleiben die Daten im
Docker-Netz, solange der Workflow sie nicht weitergibt.

---

## Weiter

- [API](../api/README.md)
- [Installation → Zusatzdienste](../install/README.md#zusatzdienste)
- [n8n-Workflows](../../server/n8n/README.md)
