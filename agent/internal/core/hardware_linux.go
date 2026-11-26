//go:build linux

// ---------------------------------------------------------------------------
// Datei:        hardware_linux.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.26.22.00.00
//
// Beschreibung: Hardware unter Linux: Modell aus dem Device Tree (Raspberry Pi,
//               andere ARM-Boards) bzw. DMI, SoC-Temperatur, Drosselung per
//               vcgencmd (nur wenn vorhanden).
// ---------------------------------------------------------------------------

package core

import (
	"context"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

func collectHardware() *HardwareInfo {
	hw := &HardwareInfo{}
	if b, err := os.ReadFile("/proc/device-tree/model"); err == nil {
		hw.Model = strings.TrimSpace(strings.TrimRight(string(b), "\x00"))
		hw.Board = true
	} else if b, err := os.ReadFile("/sys/class/dmi/id/product_name"); err == nil {
		hw.Model = strings.TrimSpace(string(b))
	}
	hw.RaspberryPi = strings.Contains(strings.ToLower(hw.Model), "raspberry pi")
	if !hw.Board {
		// Nur für Boards: auf PCs und Servern liefern Sensoren und DMI schon genug.
		if hw.Model == "" {
			return nil
		}
		return &HardwareInfo{Model: hw.Model}
	}
	if b, err := os.ReadFile("/sys/class/thermal/thermal_zone0/temp"); err == nil {
		if milli, err := strconv.ParseFloat(strings.TrimSpace(string(b)), 64); err == nil {
			t := round1(milli / 1000)
			hw.TempC = &t
		}
	}
	if path, err := exec.LookPath("vcgencmd"); err == nil {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if out, err := exec.CommandContext(ctx, path, "get_throttled").Output(); err == nil {
			hw.Throttled, hw.ThrottledText = ParseThrottled(string(out))
		}
	}
	return hw
}
