# Dokumentation

Alles, was über die [Übersicht](../README.md) hinausgeht — nach Aufgaben
sortiert, nicht nach Bauteilen.

## Nach Aufgabe

| Ich will … | steht in |
|------------|----------|
| RIMPERIO aufsetzen | [Installation](install/README.md) |
| wissen, ob mein Server reicht | [Installation → Systemprüfung](install/README.md#systemprüfung) |
| Portainer, Tugtainer, n8n, Open WebUI, Postfix dazunehmen | [Installation → Zusatzdienste](install/README.md#zusatzdienste) |
| täglich damit arbeiten | [Betrieb](operate/README.md) |
| die Oberfläche auf Englisch stellen | [Betrieb → Sprache](operate/README.md#sprache) |
| eine Shell auf dem Server öffnen | [Betrieb → Konsole](operate/README.md#konsole-im-browser) |
| wissen, wer wann was am System gemacht hat | [Betrieb → Systemtagebuch](operate/README.md#systemtagebuch) |
| verstehen, welcher Container wem gehört | [Betrieb → Container](operate/README.md#container-und-ihre-updates) |
| PostgreSQL, Caddy & Co. aktualisieren | [Betrieb → Container](operate/README.md#container-und-ihre-updates) |
| verhindern, dass die Platte volläuft | [Betrieb → Platte läuft voll](operate/README.md#platte-läuft-voll) |
| mit dem Microsoft-365-Konto anmelden | [Betrieb → Single Sign-on](operate/README.md#single-sign-on-microsoft-365-und-andere) |
| Agents sicher anbinden | [Betrieb → Zugangsschlüssel](operate/README.md#zugangsschlüssel-für-agents) |
| aktualisieren | [Updates](updates/README.md) |
| den aktuellsten Stand per Einzeiler drüberbügeln | [Updates → Einzeiler](updates/README.md#einzeiler-auf-einen-blick) |
| wissen, welcher Update-Weg wann passt | [Updates → Welchen Weg nehmen?](updates/README.md#welchen-weg-nehmen) |
| auf einer bestimmten Version bleiben | [Updates → Kanal](updates/README.md#welchen-stand-soll-der-server-fahren) |
| sofort erfahren, wenn etwas gepusht wurde | [Updates → Sofortmeldung](updates/README.md#sofortmeldung) |
| auf einen früheren Stand zurückfallen | [Updates → Zurückfallen](updates/README.md#zurückfallen) |
| einem fehlgeschlagenen Update nachgehen | [Updates → Fehlersuche](updates/README.md#fehlersuche) |
| sichern und zurückspielen | [Sicherung](backup/README.md) |
| Logs von einer KI auswerten lassen | [Integrationen → KI](integrations/README.md#ki-auswertung) |
| das Ganze mit einer lokalen KI, ohne Datenabfluss | [Integrationen → Open WebUI](integrations/README.md#open-webui) |
| Mails vom Server bekommen | [Integrationen → Mail](integrations/README.md#mail-postfix) |
| die API benutzen | [API](api/README.md) |
| Rechner fernwarten (Befehle, Skripte, Patches, Checks) | [Fernwartung](rmm/README.md) |
| Logs von Rechnern einsammeln | [Log-Agents](../agent/logship/README.md) |
| PostgreSQL migrieren | [Datenbank](../server/db/README.md) |

## Nach Bauteil

| Verzeichnis | Inhalt |
|-------------|--------|
| [`backend/`](../server/backend/README.md) | FastAPI: API, Auth, Patchmanagement, Sicherung, KI, Mail, Konsole, Systemtagebuch |
| [`frontend/`](../server/frontend/README.md) | Vue 3: Oberfläche, Sprachen, Design-System |
| [`syslog/`](../server/syslog/README.md) | Syslog-Empfänger auf UDP/TCP 514 |
| [`db/`](../server/db/README.md) | Schema, Migration, PostgreSQL-Upgrade |
| [`caddy/`](../server/caddy/README.md) | Reverse Proxy und TLS |
| [`deploy/`](../server/deploy/README.md) | Compose-Varianten: externe DB, gehärtet, Zusatzdienste |
| [`install/`](../server/install/README.md) | Systemprüfung vor der Installation |
| [`agents/`](../agent/logship/README.md) | Installer für Linux und Windows |
| [`n8n/`](../server/n8n/README.md) | Fertige Workflows |
| [`docs/changelog/`](changelog/README.md) | Änderungen je Bereich |

## Bilder und Zeichen

Unter [`assets/`](assets/README.md) liegt das RIMPERIO-Zeichen — dasselbe wie in
der Android-App.
