// ---------------------------------------------------------------------------
// Datei:        runner.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.07.12.00.00
//
// Beschreibung: Herzschlag und Dienstlogik. Als Systemdienst (Windows-Service
//               bzw. systemd/launchd über kardianos/service): im Takt Messwerte
//               erheben, einchecken, mitgegebene Befehle ausführen, Ergebnisse
//               zurückmelden, ab und zu die Inventur erneuern. Dazu Anmeldung,
//               das Ein-/Ausrichten des Dienstes und die vom Server verlangte
//               Deinstallation (Befehl "uninstall", siehe uninstall.go).
// ---------------------------------------------------------------------------

package core

import (
	"fmt"
	"log"
	"os"
	"sync"
	"time"

	"github.com/kardianos/service"
)

var logger service.Logger

func logf(format string, args ...any) {
	msg := fmt.Sprintf(format, args...)
	if logger != nil {
		_ = logger.Info(msg)
	}
	log.Println(msg)
}

// --- Dienst ----------------------------------------------------------------
type program struct {
	cfg       *Config
	stop      chan struct{}
	wg        sync.WaitGroup
	checkLast map[int]time.Time // wann ein Check zuletzt lief
	jobs      chan PendingCommand
	snmp      *snmpScheduler
}

// worker arbeitet lange Aufträge (Patch-Suche, Installation, Docker) der Reihe
// nach ab — neben dem Check-in, damit der Rechner währenddessen online bleibt.
// Nacheinander, weil sich Windows Update und Paketmanager nicht gern teilen.
func (p *program) worker(client *Client) {
	for {
		select {
		case <-p.stop:
			return
		case c := <-p.jobs:
			logf("Starte langen Auftrag #%d (%s).", c.ID, c.Kind)
			res := HandleCommand(c, client)
			for attempt := 0; attempt < 10; attempt++ {
				if err := client.SendResults([]CommandResult{res}); err == nil {
					break
				} else {
					logf("Ergebnis #%d senden fehlgeschlagen (%v) — neuer Versuch.", c.ID, err)
				}
				select {
				case <-p.stop:
					return
				case <-time.After(30 * time.Second):
				}
			}
			logf("Langer Auftrag #%d (%s) fertig: %s.", c.ID, c.Kind, res.Status)
		}
	}
}

func (p *program) Start(s service.Service) error {
	cfg, err := Load()
	if err != nil || cfg.AgentToken == "" {
		// Noch nicht angemeldet? Dann gelten die Eckdaten, die der MSI-Installer
		// in die Registry geschrieben hat — auch vor einer älteren, noch nicht
		// angemeldeten Konfiguration. So greift eine Neuinstallation mit
		// korrigierter Server-Adresse oder neuem Schlüssel.
		if boot, berr := bootstrapFromSystem(); berr == nil {
			cfg, err = boot, nil
		} else if err != nil {
			return fmt.Errorf("keine Konfiguration und kein Bootstrap: %w", berr)
		}
	}
	if cfg.AgentToken != "" {
		clearBootstrapSecret() // auch nach einer MSI-Reparatur
	}
	p.cfg = cfg
	p.stop = make(chan struct{})
	p.checkLast = map[int]time.Time{}
	p.wg.Add(1)
	go p.run()
	return nil
}

func (p *program) Stop(s service.Service) error {
	if p.stop != nil {
		close(p.stop)
	}
	// Höchstens 15 s warten: Ein langer Befehl soll weder den Stopp noch eine
	// Deinstallation aufhalten (das MSI wartet auf den Dienst).
	done := make(chan struct{})
	go func() { p.wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(15 * time.Second):
		logf("Laufender Befehl blockiert den Stopp — beende trotzdem.")
	}
	return nil
}

func (p *program) run() {
	defer p.wg.Done()

	// Erster Start nach einer MSI-Installation: Es liegt nur ein Geheimnis,
	// noch kein Token vor. Dann meldet sich der Agent jetzt selbst an und
	// versucht es geduldig erneut, falls der Server gerade nicht erreichbar ist.
	for p.cfg.AgentToken == "" {
		if err := p.selfEnroll(); err != nil {
			logf("Selbst-Anmeldung fehlgeschlagen: %v — neuer Versuch in 30s.", err)
			select {
			case <-p.stop:
				return
			case <-time.After(30 * time.Second):
				continue
			}
		}
	}

	client := NewClient(p.cfg.ServerURL, p.cfg.AgentToken, p.cfg.Insecure)
	logf("RIMPERIO-Agent %s startet. Server: %s, Takt: %ds", Version, p.cfg.ServerURL, p.cfg.Interval)
	p.jobs = make(chan PendingCommand, 32)
	p.snmp = newSnmpScheduler()
	go p.worker(client)

	if err := client.SendInventory(CollectInventory()); err != nil {
		logf("Inventur zu Beginn fehlgeschlagen: %v", err)
	}

	interval := time.Duration(p.cfg.Interval) * time.Second
	if interval < 10*time.Second {
		interval = 60 * time.Second
	}

	if p.cycle(client) { // sofort, nicht erst nach einem Takt
		return // deinstalliert — der Dienst wird gleich gestoppt
	}
	lastInv := time.Now()

	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-p.stop:
			logf("RIMPERIO-Agent hält an.")
			return
		case <-ticker.C:
			if p.cycle(client) {
				return
			}
			if time.Since(lastInv) > 30*time.Minute {
				if err := client.SendInventory(CollectInventory()); err == nil {
					lastInv = time.Now()
				}
			}
		}
	}
}

// cycle ist ein einzelner Takt: Messwerte, Check-in, Befehle, Checks. Liefert
// true, wenn der Server die Deinstallation verlangt hat und sie läuft.
func (p *program) cycle(client *Client) (uninstalled bool) {
	tel := CollectTelemetry()
	resp, err := client.Checkin(CheckinRequest{
		Version:   Version,
		LocalIP:   LocalIP(),
		Telemetry: &tel,
	})
	if err != nil {
		logf("Check-in fehlgeschlagen: %v", err)
		return false
	}

	// Befehle ausführen (gewöhnliche Shell oder eingebaute Aktionen wie Patch).
	// Lange Aufträge gehen an den Hintergrund-Arbeiter.
	if len(resp.Commands) > 0 {
		results := make([]CommandResult, 0, len(resp.Commands))
		for _, c := range resp.Commands {
			if c.Kind == "uninstall" {
				// Was schon lief, noch melden — danach gibt es kein Token mehr.
				_ = client.SendResults(results)
				uninstallRequested(client, true)
				return true
			}
			if longRunning(c.Kind) && p.jobs != nil {
				select {
				case p.jobs <- c:
					logf("Auftrag #%d (%s) eingereiht.", c.ID, c.Kind)
				default:
					results = append(results, CommandResult{ID: c.ID, Status: "error", ExitCode: intPtr(1),
						Stderr: "Warteschlange des Agents ist voll — später erneut versuchen."})
				}
				continue
			}
			logf("Führe Befehl #%d aus (%s).", c.ID, c.Kind)
			results = append(results, HandleCommand(c, client))
		}
		if err := client.SendResults(results); err != nil {
			logf("Ergebnisse senden fehlgeschlagen: %v", err)
		}
	}

	// Fällige Checks ausführen.
	p.runDueChecks(client, resp.Checks)

	// Zugewiesene SNMP-Geräte abfragen (nebenher, eine Runde zur Zeit).
	if p.snmp != nil {
		if due := p.snmp.due(resp.Snmp); len(due) > 0 {
			go p.snmp.run(client, due)
		}
	}
	return false
}

// runDueChecks führt jene Checks aus, deren Intervall abgelaufen ist, und meldet
// die Ergebnisse zurück.
func (p *program) runDueChecks(client *Client, specs []CheckSpec) {
	now := time.Now()
	out := make([]CheckResultOut, 0)
	for _, spec := range specs {
		interval := time.Duration(spec.Interval) * time.Second
		if interval < 30*time.Second {
			interval = 30 * time.Second
		}
		if last, ok := p.checkLast[spec.ID]; ok && now.Sub(last) < interval {
			continue
		}
		p.checkLast[spec.ID] = now
		out = append(out, RunCheck(spec))
	}
	if len(out) > 0 {
		if err := client.SendCheckResults(out); err != nil {
			logf("Check-Ergebnisse senden fehlgeschlagen: %v", err)
		}
	}
}

// --- Anmeldung -------------------------------------------------------------

// enrollRequest baut die Anmeldung aus der frisch erhobenen Inventur.
func enrollRequest(secret, site, desc string) EnrollRequest {
	inv := CollectInventory()
	if inv.Hostname == "" {
		if h, err := os.Hostname(); err == nil {
			inv.Hostname = h
		}
	}
	return EnrollRequest{
		EnrollSecret: secret,
		Hostname:     inv.Hostname,
		OS:           inv.OS,
		Platform:     inv.Platform,
		Arch:         inv.Arch,
		Version:      Version,
		LocalIP:      inv.LocalIP,
		Site:         site,
		Description:  desc,
	}
}

// EnrollAndSave meldet den Agent am Server an und legt die Konfiguration mit
// dem erhaltenen Token ab. Für die manuelle/PowerShell-Einrichtung.
func EnrollAndSave(server, secret, site, desc string, insecure bool) (*EnrollResponse, error) {
	client := NewClient(server, "", insecure)
	res, err := client.Enroll(enrollRequest(secret, site, desc))
	if err != nil {
		return nil, err
	}
	cfg := &Config{
		ServerURL:   server,
		AgentUID:    res.AgentUID,
		AgentToken:  res.AgentToken,
		Interval:    res.CheckinInterval,
		Site:        site,
		Description: desc,
		Insecure:    insecure,
	}
	if err := cfg.Save(); err != nil {
		return nil, fmt.Errorf("Konfiguration speichern: %w", err)
	}
	return res, nil
}

// Bootstrap legt die Konfiguration an, ohne das Netz zu berühren: Server und
// Enrollment-Geheimnis werden hinterlegt, die eigentliche Anmeldung passiert
// beim ersten Dienststart. So braucht der MSI-Installer keine Verbindung.
func Bootstrap(server, secret, site, desc string, insecure bool) error {
	cfg := &Config{
		ServerURL:    server,
		Interval:     60,
		Site:         site,
		Description:  desc,
		Insecure:     insecure,
		EnrollSecret: secret,
	}
	if err := cfg.Save(); err != nil {
		return fmt.Errorf("Konfiguration speichern: %w", err)
	}
	return nil
}

// selfEnroll meldet den Agent anhand des hinterlegten Geheimnisses an und
// löscht dieses danach aus der Konfiguration.
func (p *program) selfEnroll() error {
	if p.cfg.EnrollSecret == "" {
		return fmt.Errorf("kein Enrollment-Geheimnis hinterlegt")
	}
	client := NewClient(p.cfg.ServerURL, "", p.cfg.Insecure)
	res, err := client.Enroll(enrollRequest(p.cfg.EnrollSecret, p.cfg.Site, p.cfg.Description))
	if err != nil {
		return err
	}
	p.cfg.AgentUID = res.AgentUID
	p.cfg.AgentToken = res.AgentToken
	if res.CheckinInterval > 0 {
		p.cfg.Interval = res.CheckinInterval
	}
	p.cfg.EnrollSecret = "" // Geheimnis nicht länger als nötig aufbewahren.
	if err := p.cfg.Save(); err != nil {
		return fmt.Errorf("Konfiguration speichern: %w", err)
	}
	clearBootstrapSecret()
	logf("Selbst-Anmeldung erfolgreich. Agent-ID: %s", res.AgentUID)
	return nil
}

// --- Dienststeuerung -------------------------------------------------------
func svcConfig() *service.Config {
	return &service.Config{
		Name:        "rimperio-agent",
		DisplayName: "RIMPERIO Agent",
		Description: "RIMPERIO — Remote Monitoring & Management Agent.",
		Arguments:   []string{"run"},
	}
}

func newService() (service.Service, error) {
	s, err := service.New(&program{}, svcConfig())
	if err != nil {
		return nil, err
	}
	if l, err := s.Logger(nil); err == nil {
		logger = l
	}
	return s, nil
}

// ServiceControl führt eine Dienstaktion aus: install, uninstall, start, stop, restart.
func ServiceControl(action string) error {
	s, err := newService()
	if err != nil {
		return err
	}
	return service.Control(s, action)
}

// RunService läuft als Dienst (blockiert bis zum Stopp durch den Dienstmanager).
func RunService() error {
	s, err := newService()
	if err != nil {
		return err
	}
	return s.Run()
}

// RunOnce führt genau einen Takt aus — zum Prüfen der Einrichtung.
func RunOnce() error {
	cfg, err := Load()
	if err != nil {
		return err
	}
	client := NewClient(cfg.ServerURL, cfg.AgentToken, cfg.Insecure)
	if err := client.SendInventory(CollectInventory()); err != nil {
		return fmt.Errorf("Inventur: %w", err)
	}
	tel := CollectTelemetry()
	resp, err := client.Checkin(CheckinRequest{Version: Version, LocalIP: LocalIP(), Telemetry: &tel})
	if err != nil {
		return fmt.Errorf("Check-in: %w", err)
	}
	logf("Check-in ok. CPU %.0f%%, RAM %.0f%%, %d Befehl(e), %d Check(s).",
		tel.CPUPercent, tel.MemPercent, len(resp.Commands), len(resp.Checks))
	results := make([]CommandResult, 0, len(resp.Commands))
	for _, c := range resp.Commands {
		if c.Kind == "uninstall" {
			_ = client.SendResults(results)
			uninstallRequested(client, false)
			return nil
		}
		results = append(results, HandleCommand(c, client))
	}
	if err := client.SendResults(results); err != nil {
		return err
	}
	crs := make([]CheckResultOut, 0, len(resp.Checks))
	for _, spec := range resp.Checks {
		crs = append(crs, RunCheck(spec))
	}
	if err := client.SendCheckResults(crs); err != nil {
		return err
	}
	snmp := make([]SnmpResult, 0, len(resp.Snmp))
	for _, t := range resp.Snmp {
		snmp = append(snmp, pollSnmp(t))
	}
	return client.SendSnmpResults(snmp)
}
