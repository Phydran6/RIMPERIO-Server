// ---------------------------------------------------------------------------
// Datei:        checks.go
// Projekt:      LRMM — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.05.12.00.00
//
// Beschreibung: Führt die vom Server zugewiesenen Checks aus — im eigenen Takt
//               des Agents. Jeder Check liefert pass/fail samt Messwert zurück;
//               der Server macht daraus Alarme. Typen: cpu, mem, disk, service,
//               port, ping, script.
// ---------------------------------------------------------------------------

package core

import (
	"fmt"
	"net"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/shirou/gopsutil/v4/cpu"
	"github.com/shirou/gopsutil/v4/disk"
	"github.com/shirou/gopsutil/v4/mem"
)

func pFloat(m map[string]any, key string, def float64) float64 {
	if v, ok := m[key]; ok {
		switch n := v.(type) {
		case float64:
			return n
		case int:
			return float64(n)
		case string:
			if f, err := strconv.ParseFloat(n, 64); err == nil {
				return f
			}
		}
	}
	return def
}

func pStr(m map[string]any, key, def string) string {
	if v, ok := m[key]; ok {
		if s, ok := v.(string); ok && s != "" {
			return s
		}
	}
	return def
}

func pInt(m map[string]any, key string, def int) int {
	return int(pFloat(m, key, float64(def)))
}

func pass(id int, value string) CheckResultOut {
	return CheckResultOut{CheckID: id, Status: "pass", Value: value}
}
func fail(id int, value, output string) CheckResultOut {
	return CheckResultOut{CheckID: id, Status: "fail", Value: value, Output: output}
}

// RunCheck führt einen Check aus und liefert das Ergebnis.
func RunCheck(spec CheckSpec) CheckResultOut {
	switch spec.Type {
	case "cpu":
		max := pFloat(spec.Params, "max", 90)
		v := 0.0
		if p, err := cpu.Percent(500*time.Millisecond, false); err == nil && len(p) > 0 {
			v = p[0]
		}
		val := fmt.Sprintf("%.0f%%", v)
		if v >= max {
			return fail(spec.ID, val, fmt.Sprintf("CPU %.0f%% ≥ Schwelle %.0f%%", v, max))
		}
		return pass(spec.ID, val)

	case "mem":
		max := pFloat(spec.Params, "max", 90)
		v := 0.0
		if vm, err := mem.VirtualMemory(); err == nil {
			v = vm.UsedPercent
		}
		val := fmt.Sprintf("%.0f%%", v)
		if v >= max {
			return fail(spec.ID, val, fmt.Sprintf("RAM %.0f%% ≥ Schwelle %.0f%%", v, max))
		}
		return pass(spec.ID, val)

	case "disk":
		max := pFloat(spec.Params, "max", 90)
		mount := pStr(spec.Params, "mount", "")
		worst := 0.0
		worstMount := ""
		parts, _ := disk.Partitions(false)
		for _, p := range parts {
			if mount != "" && !strings.EqualFold(p.Mountpoint, mount) {
				continue
			}
			if u, err := disk.Usage(p.Mountpoint); err == nil && u.Total > 0 && u.UsedPercent > worst {
				worst = u.UsedPercent
				worstMount = p.Mountpoint
			}
		}
		val := fmt.Sprintf("%s %.0f%%", worstMount, worst)
		if worst >= max {
			return fail(spec.ID, val, fmt.Sprintf("Platte %.0f%% ≥ Schwelle %.0f%%", worst, max))
		}
		return pass(spec.ID, val)

	case "service":
		name := pStr(spec.Params, "name", "")
		if name == "" {
			return fail(spec.ID, "", "Kein Dienstname (params.name) angegeben.")
		}
		if serviceRunning(name) {
			return pass(spec.ID, "läuft")
		}
		return fail(spec.ID, "gestoppt", fmt.Sprintf("Dienst %q läuft nicht.", name))

	case "port":
		host := pStr(spec.Params, "host", "127.0.0.1")
		port := pInt(spec.Params, "port", 0)
		if port == 0 {
			return fail(spec.ID, "", "Kein Port (params.port) angegeben.")
		}
		addr := net.JoinHostPort(host, strconv.Itoa(port))
		conn, err := net.DialTimeout("tcp", addr, 5*time.Second)
		if err != nil {
			return fail(spec.ID, "zu", fmt.Sprintf("%s nicht erreichbar: %v", addr, err))
		}
		conn.Close()
		return pass(spec.ID, "offen")

	case "ping":
		host := pStr(spec.Params, "host", "")
		if host == "" {
			return fail(spec.ID, "", "Kein Ziel (params.host) angegeben.")
		}
		return pingCheck(spec.ID, host)

	case "script":
		shell := pStr(spec.Params, "shell", "powershell")
		body := pStr(spec.Params, "body", "")
		expect := pInt(spec.Params, "expect_exit", 0)
		if body == "" {
			return fail(spec.ID, "", "Kein Skript (params.body) angegeben.")
		}
		r := Execute(PendingCommand{Shell: shell, Command: body, Timeout: 120})
		code := -1
		if r.ExitCode != nil {
			code = *r.ExitCode
		}
		if code == expect {
			return pass(spec.ID, fmt.Sprintf("Exit %d", code))
		}
		return fail(spec.ID, fmt.Sprintf("Exit %d", code), strings.TrimSpace(r.Stdout+"\n"+r.Stderr))

	default:
		return CheckResultOut{CheckID: spec.ID, Status: "unknown", Value: "unbekannter Typ: " + spec.Type}
	}
}

// serviceRunning prüft plattformabhängig, ob ein Dienst läuft.
func serviceRunning(name string) bool {
	if runtime.GOOS == "windows" {
		r := Execute(PendingCommand{Shell: "cmd", Command: `sc query "` + name + `"`, Timeout: 15})
		return strings.Contains(strings.ToUpper(r.Stdout), "RUNNING")
	}
	r := Execute(PendingCommand{Shell: "sh", Command: "systemctl is-active --quiet " + name, Timeout: 15})
	return r.ExitCode != nil && *r.ExitCode == 0
}

// pingCheck ruft das System-ping einmal auf.
func pingCheck(id int, host string) CheckResultOut {
	var cmd PendingCommand
	if runtime.GOOS == "windows" {
		cmd = PendingCommand{Shell: "cmd", Command: fmt.Sprintf("ping -n 1 -w 2000 %s", host), Timeout: 15}
	} else {
		cmd = PendingCommand{Shell: "sh", Command: fmt.Sprintf("ping -c 1 -W 2 %s", host), Timeout: 15}
	}
	r := Execute(cmd)
	if r.ExitCode != nil && *r.ExitCode == 0 {
		return pass(id, "erreichbar")
	}
	return fail(id, "keine Antwort", fmt.Sprintf("%s antwortet nicht auf ping.", host))
}
