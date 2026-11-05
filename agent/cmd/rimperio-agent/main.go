// ---------------------------------------------------------------------------
// Datei:        main.go
// Projekt:      RIMPERIO — Agent
// Paket:        main
// Autor:        Phydran6
// Version:      2026.10.31.15.40.00
//
// Beschreibung: Kommandozeile des Agents. install meldet an und richtet den
//               Dienst ein; run lässt ihn laufen (vom Dienstmanager gerufen);
//               dazu enroll, start/stop/restart, uninstall, once und status.
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
		doSetup(os.Args[2:])
	case "bootstrap":
		doBootstrap(os.Args[2:])
	case "install":
		// Dienst registrieren und starten (Anmeldung ist bereits geschehen
		// oder passiert beim ersten Start selbst).
		must(core.ServiceControl("install"), "Dienst einrichten (Administrator/root nötig?)")
		if err := core.ServiceControl("start"); err != nil {
			fmt.Fprintf(os.Stderr, "Hinweis: Dienst nicht sofort gestartet: %v\n", err)
		}
		fmt.Println("Dienst eingerichtet.")
	case "enroll":
		doEnroll(os.Args[2:])
	case "uninstall":
		doUninstall()
	case "start", "stop", "restart":
		must(core.ServiceControl(os.Args[1]), "Dienstaktion")
		fmt.Printf("Dienst: %s ausgeführt.\n", os.Args[1])
	case "run":
		// Vom Dienstmanager aufgerufen; blockiert bis zum Stopp.
		must(core.RunService(), "Dienst läuft nicht")
	case "once":
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

func doUninstall() {
	if err := core.ServiceControl("stop"); err != nil {
		fmt.Fprintf(os.Stderr, "Hinweis: Stoppen: %v\n", err)
	}
	must(core.ServiceControl("uninstall"), "Dienst entfernen")
	if err := os.Remove(core.ConfigPath()); err != nil && !os.IsNotExist(err) {
		fmt.Fprintf(os.Stderr, "Hinweis: Konfiguration blieb liegen: %v\n", err)
	}
	fmt.Println("Agent entfernt.")
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
                        Anmelden + Dienst einrichten + starten (manueller Weg).
  rimperio-agent bootstrap  -server URL -secret GEHEIMNIS [-site S] [-desc D] [-insecure]
                        Nur Konfiguration anlegen (für den MSI-Installer).
  rimperio-agent install    Dienst registrieren und starten.
  rimperio-agent enroll     -server URL -secret GEHEIMNIS [...]   Nur anmelden.
  rimperio-agent start | stop | restart | uninstall
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
