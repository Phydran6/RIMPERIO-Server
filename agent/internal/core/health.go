// ---------------------------------------------------------------------------
// Datei:        health.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.07.12.00.00
//
// Beschreibung: Gesundheitsbild für den Vorher-Nachher-Vergleich beim
//               Patchen: welche Dienste laufen, welche automatisch startenden
//               stehen, welche systemd-Units sind fehlgeschlagen, welche
//               Container laufen, steht ein Neustart an. Der Server vergleicht
//               das Bild vor dem Update mit dem danach.
// ---------------------------------------------------------------------------

package core

import (
	"time"

	"github.com/shirou/gopsutil/v4/host"
)

type HealthSnapshot struct {
	TakenAt         time.Time `json:"taken_at"`
	UptimeSeconds   uint64    `json:"uptime_seconds"`
	BootTime        time.Time `json:"boot_time"`
	Running         []string  `json:"running"`        // laufende Dienste
	AutoStopped     []string  `json:"auto_stopped"`   // Windows: Starttyp Automatisch, läuft aber nicht
	Failed          []string  `json:"failed"`         // Linux: fehlgeschlagene systemd-Units
	Containers      []string  `json:"containers"`     // laufende Container (Name)
	ContainersBad   []string  `json:"containers_bad"` // unhealthy oder in Neustartschleife
	RebootPending   bool      `json:"reboot_pending"`
	DockerAvailable bool      `json:"docker_available"`
}

func collectHealth() HealthSnapshot {
	h := HealthSnapshot{TakenAt: time.Now().UTC(), RebootPending: rebootPending()}
	if up, err := host.Uptime(); err == nil {
		h.UptimeSeconds = up
	}
	if bt, err := host.BootTime(); err == nil {
		h.BootTime = time.Unix(int64(bt), 0).UTC()
	}
	h.Running, h.AutoStopped, h.Failed = serviceState()
	if dc, err := newDockerClient(); err == nil {
		if list, err := dc.list(false); err == nil {
			h.DockerAvailable = true
			for _, c := range list {
				name := c.name()
				h.Containers = append(h.Containers, name)
				if containsAny(c.Status, "unhealthy", "Restarting") {
					h.ContainersBad = append(h.ContainersBad, name)
				}
			}
		}
	}
	return h
}
