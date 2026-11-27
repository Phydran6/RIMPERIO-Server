// ---------------------------------------------------------------------------
// Datei:        hardware.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.26.22.00.00
//
// Beschreibung: Angaben zur Hardware, die gopsutil nicht kennt – vor allem für
//               Einplatinenrechner wie den Raspberry Pi: Modell, Temperatur des
//               SoC und ob die Firmware wegen Unterspannung oder Hitze drosselt.
// ---------------------------------------------------------------------------

package core

import (
	"strconv"
	"strings"
)

// HardwareInfo steht nur in der Inventur, wenn es etwas zu sagen gibt.
type HardwareInfo struct {
	Model         string   `json:"model,omitempty"`
	Board         bool     `json:"board,omitempty"` // Einplatinenrechner (Device Tree)
	RaspberryPi   bool     `json:"raspberry_pi,omitempty"`
	TempC         *float64 `json:"temp_c,omitempty"`
	Throttled     string   `json:"throttled,omitempty"`      // Rohwert von vcgencmd, z. B. 0x50005
	ThrottledText []string `json:"throttled_text,omitempty"` // lesbar
}

// throttleBits übersetzt den Wert von "vcgencmd get_throttled".
var throttleBits = []struct {
	bit  uint
	text string
}{
	{0, "Unterspannung jetzt"},
	{1, "CPU-Takt gedeckelt jetzt"},
	{2, "gedrosselt jetzt"},
	{3, "Temperaturgrenze jetzt"},
	{16, "Unterspannung seit dem Start"},
	{17, "CPU-Takt gedeckelt seit dem Start"},
	{18, "gedrosselt seit dem Start"},
	{19, "Temperaturgrenze seit dem Start"},
}

// ParseThrottled liest "throttled=0x50005" und gibt Rohwert und Klartext zurück.
func ParseThrottled(out string) (string, []string) {
	v := strings.TrimSpace(out)
	if i := strings.Index(v, "="); i >= 0 {
		v = v[i+1:]
	}
	n, err := strconv.ParseUint(strings.TrimPrefix(v, "0x"), 16, 32)
	if err != nil {
		return "", nil
	}
	var texts []string
	for _, b := range throttleBits {
		if n&(1<<b.bit) != 0 {
			texts = append(texts, b.text)
		}
	}
	return v, texts
}
