// ---------------------------------------------------------------------------
// Datei:        main.go
// Projekt:      RIMPERIO — Agent
// Paket:        main
// Autor:        Phydran6
// Version:      2026.11.08.17.00.00
//
// Beschreibung: Kommandozeile des Agents. install meldet an und richtet den
//               Dienst ein; run lässt ihn laufen (vom Dienstmanager gerufen);
//               dazu enroll, start/stop/restart, uninstall (mit Abmeldung am
//               Server), cleanup (für das MSI), once und status.
//
//               Linux: Den Dienst richtet install-agent.sh ein (eigenes
//               Dienstkonto, alles unter /opt/RMM/rimperio) — setup/install
//               verweisen dorthin. enroll, once und run laufen nie als root:
//               mit sudo gerufen, startet sich der Agent unter dem Dienstkonto
//               neu.
// ---------------------------------------------------------------------------

package main

import (
	"flag"
	"fmt"
	"os"

	"github.com/Phydran6/RIMPERIO-Server/agent/internal/core"
)

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(1)
	}

	switch os.Args[1] {
	case "setup":
		must(core.ManualSetupAllowed(), "Einrichtung")
		doSetup(os.Args[2:])
	case "bootstrap":
		core.DropRoot() // unter Linux schreibt das Dienstkonto seine Konfiguration selbst
		doBootstrap(os.Args[2:])
	case "install":
		// Dienst registrieren und starten (Anmeldung ist bereits geschehen
		// oder passiert beim ersten Start selbst).
		must(core.ManualSetupAllowed(), "Einrichtung")
		must(core.ServiceControl("install"), "Dienst einrichten (Administrator/root nötig?)")
		if err := core.ServiceControl("start"); err != nil {
			fmt.Fprintf(os.Stderr, "Hinweis: Dienst nicht sofort gestartet: %v\n", err)
		}
		fmt.Println("Dienst eingerichtet.")
	case "enroll":
		core.DropRoot() // Konfiguration gehört dem Dienstkonto, nicht root
		doEnroll(os.Args[2:])
	case "uninstall":
		doUninstall(os.Args[2:])
	case "cleanup":
		// Vom MSI beim Deinstallieren gerufen: abmelden + Konfiguration löschen.
		doCleanup(os.Args[2:])
	case "start", "stop", "restart":
		must(core.ServiceControl(os.Args[1]), "Dienstaktion")
		fmt.Printf("Dienst: %s ausgeführt.\n", os.Args[1])
	case "run":
		// Vom Dienstmanager aufgerufen; blockiert bis zum Stopp.
		core.DropRoot()
		must(core.RunService(), "Dienst läuft nicht")
	case "once":
		// Befehle vom Server laufen nie als root — auch nicht beim Testlauf mit sudo.
		core.DropRoot()
		must(core.RunOnce(), "Testdurchlauf")
		fmt.Println("Testdurchlauf ok.")
	case "status":
		doStatus()
	case "version", "-v", "--version":
		fmt.Println("rimperio-agent", core.Version)
	default:
		usage()
		os.Exit(1)
	}
}

// doSetup ist die manuelle Einrichtung in einem Schritt: anmelden, Dienst
// registrieren, Dienst starten.
func doSetup(args []string) {
	fs := flag.NewFlagSet("setup", flag.ExitOnError)
	server := fs.String("server", "", "Server-URL, z. B. https://rmm.example.de")
	secret := fs.String("secret", "", "Enrollment-Geheimnis")
	site := fs.String("site", "Standard", "Standort/Gruppe")
	desc := fs.String("desc", "", "Beschreibung des Rechners")
	insecure := fs.Bool("insecure", false, "TLS-Prüfung aus (nur Labor/Selbstzertifikat)")
	_ = fs.Parse(args)

	if *server == "" || *secret == "" {
		fmt.Fprintln(os.Stderr, "Fehlt: -server und -secret sind nötig.")
		os.Exit(2)
	}

	fmt.Println("Melde Agent am Server an …")
	res, err := core.EnrollAndSave(*server, *secret, *site, *desc, *insecure)
	must(err, "Anmeldung")
	fmt.Printf("Angemeldet. Agent-ID: %s\n", res.AgentUID)

	fmt.Println("Richte Systemdienst ein …")
	must(core.ServiceControl("install"), "Dienst einrichten (Administrator/root nötig?)")
	if err := core.ServiceControl("start"); err != nil {
		fmt.Fprintf(os.Stderr, "Hinweis: Dienst konnte nicht sofort starten: %v\n", err)
	}
	fmt.Println("Fertig. Der Agent läuft als Dienst und meldet sich am Server.")
}

// doBootstrap legt nur die Konfiguration an (ohne Netz, ohne Dienst). Der vom
// MSI registrierte Dienst meldet sich beim ersten Start selbst an.
func doBootstrap(args []string) {
	fs := flag.NewFlagSet("bootstrap", flag.ExitOnError)
	server := fs.String("server", "", "Server-URL")
	secret := fs.String("secret", "", "Enrollment-Geheimnis")
	site := fs.String("site", "Standard", "Standort/Gruppe")
	desc := fs.String("desc", "", "Beschreibung")
	insecure := fs.Bool("insecure", false, "TLS-Prüfung aus")
	_ = fs.Parse(args)
	if *server == "" || *secret == "" {
		fmt.Fprintln(os.Stderr, "Fehlt: -server und -secret sind nötig.")
		os.Exit(2)
	}
	must(core.Bootstrap(*server, *secret, *site, *desc, *insecure), "Konfiguration anlegen")
	fmt.Printf("Konfiguration angelegt: %s\n", core.ConfigPath())
}

func doEnroll(args []string) {
	fs := flag.NewFlagSet("enroll", flag.ExitOnError)
	server := fs.String("server", "", "Server-URL")
	secret := fs.String("secret", "", "Enrollment-Geheimnis")
	site := fs.String("site", "Standard", "Standort/Gruppe")
	desc := fs.String("desc", "", "Beschreibung")
	insecure := fs.Bool("insecure", false, "TLS-Prüfung aus")
	_ = fs.Parse(args)
	if *server == "" || *secret == "" {
		fmt.Fprintln(os.Stderr, "Fehlt: -server und -secret sind nötig.")
		os.Exit(2)
	}
	res, err := core.EnrollAndSave(*server, *secret, *site, *desc, *insecure)
	must(err, "Anmeldung")
	fmt.Printf("Angemeldet (ohne Dienst). Agent-ID: %s\nKonfiguration: %s\n", res.AgentUID, core.ConfigPath())
}

// keepRecordFlag liest "-keep-record": den Eintrag auf dem Server behalten.
func keepRecordFlag(name string, args []string) bool {
	fs := flag.NewFlagSet(name, flag.ExitOnError)
	keep := fs.Bool("keep-record", false, "Eintrag auf dem Server behalten (nur vom Rechner entfernen)")
	_ = fs.Parse(args)
	return *keep
}

// doUninstall entfernt den Agent vom Rechner und (ohne -keep-record) vom Server.
func doUninstall(args []string) {
	for _, n := range core.Uninstall(keepRecordFlag("uninstall", args)) {
		fmt.Println(n)
	}
}

func doCleanup(args []string) {
	for _, n := range core.Cleanup(keepRecordFlag("cleanup", args)) {
		fmt.Println(n)
	}
}

func doStatus() {
	cfg, err := core.Load()
	if err != nil {
		fmt.Println("Nicht eingerichtet:", err)
		os.Exit(1)
	}
	fmt.Printf("Server:        %s\n", cfg.ServerURL)
	fmt.Printf("Agent-ID:      %s\n", cfg.AgentUID)
	fmt.Printf("Takt:          %ds\n", cfg.Interval)
	fmt.Printf("Standort:      %s\n", cfg.Site)
	fmt.Printf("Konfiguration: %s\n", core.ConfigPath())
}

func usage() {
	fmt.Fprintf(os.Stderr, `RIMPERIO-Agent %s

Verwendung:
  rimperio-agent setup      -server URL -secret GEHEIMNIS [-site S] [-desc D] [-insecure]
                        Anmelden + Dienst einrichten + starten (manueller Weg;
                        unter Linux statt dessen install-agent.sh).
  rimperio-agent bootstrap  -server URL -secret GEHEIMNIS [-site S] [-desc D] [-insecure]
                        Nur Konfiguration anlegen (für den MSI-Installer).
  rimperio-agent install    Dienst registrieren und starten (nicht unter Linux).
  rimperio-agent enroll     -server URL -secret GEHEIMNIS [...]   Nur anmelden.
  rimperio-agent uninstall  [-keep-record]
                        Agent vom Rechner entfernen und am Server abmelden
                        (-keep-record: Eintrag auf dem Server behalten).
  rimperio-agent start | stop | restart
  rimperio-agent once       Ein Testdurchlauf.
  rimperio-agent status
  rimperio-agent version
`, core.Version)
}

func must(err error, what string) {
	if err != nil {
		fmt.Fprintf(os.Stderr, "Fehler (%s): %v\n", what, err)
		os.Exit(1)
	}
}
