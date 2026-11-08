// ---------------------------------------------------------------------------
// Datei:        collect.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.08.17.00.00
//
// Beschreibung: Erhebt, was der Server wissen will: einmalig die Inventur
//               (OS, Hardware, Platten, Netz) und laufend die Messwerte
//               (CPU, RAM, Plattenbelegung, Laufzeit, auffällige Prozesse).
//               Plattformübergreifend über gopsutil.
// ---------------------------------------------------------------------------

package core

import (
	"net"
	"runtime"
	"sort"
	"time"

	"github.com/shirou/gopsutil/v4/cpu"
	"github.com/shirou/gopsutil/v4/disk"
	"github.com/shirou/gopsutil/v4/host"
	"github.com/shirou/gopsutil/v4/mem"
	"github.com/shirou/gopsutil/v4/process"
)

type DiskInfo struct {
	Mount   string  `json:"mount"`
	Percent float64 `json:"percent"`
	UsedGB  float64 `json:"used_gb"`
	TotalGB float64 `json:"total_gb"`
}

type ProcInfo struct {
	Name string  `json:"name"`
	CPU  float64 `json:"cpu"`
	Mem  float64 `json:"mem"`
}

type Telemetry struct {
	CPUPercent float64    `json:"cpu_percent"`
	MemPercent float64    `json:"mem_percent"`
	MemUsedMB  float64    `json:"mem_used_mb"`
	MemTotalMB float64    `json:"mem_total_mb"`
	UptimeSecs int        `json:"uptime_seconds"`
	BootTime   *time.Time `json:"boot_time,omitempty"`
	Disk       []DiskInfo `json:"disk"`
	TopProcs   []ProcInfo `json:"top_procs"`
}

type SoftwareItem struct {
	Name      string `json:"name"`
	Version   string `json:"version,omitempty"`
	Publisher string `json:"publisher,omitempty"`
}

type Inventory struct {
	Hostname    string         `json:"hostname"`
	OS          string         `json:"os"`
	Platform    string         `json:"platform"`
	Arch        string         `json:"arch"`
	KernelArch  string         `json:"kernel_arch"`
	CPUModel    string         `json:"cpu_model"`
	CPUCores    int            `json:"cpu_cores"`
	MemTotalMB  float64        `json:"mem_total_mb"`
	Disk        []DiskInfo     `json:"disk"`
	LocalIP     string         `json:"local_ip"`
	MACs        []string       `json:"macs"`
	UptimeSecs  int            `json:"uptime_seconds"`
	Software    []SoftwareItem `json:"software"`
	AgentVer    string         `json:"agent_version"`
	AgentUser   string         `json:"agent_user,omitempty"` // Konto, unter dem der Agent läuft (Linux: Dienstkonto)
	CollectedAt time.Time      `json:"collected_at"`
}

const gib = 1024 * 1024 * 1024
const mib = 1024 * 1024

// collectDisks sammelt alle beschreibbaren Dateisysteme. Fehlerhafte oder leere
// (z. B. CD-ROM) werden übersprungen.
func collectDisks() []DiskInfo {
	out := []DiskInfo{}
	parts, err := disk.Partitions(false)
	if err != nil {
		return out
	}
	for _, p := range parts {
		u, err := disk.Usage(p.Mountpoint)
		if err != nil || u.Total == 0 {
			continue
		}
		out = append(out, DiskInfo{
			Mount:   p.Mountpoint,
			Percent: round1(u.UsedPercent),
			UsedGB:  round1(float64(u.Used) / gib),
			TotalGB: round1(float64(u.Total) / gib),
		})
	}
	return out
}

// topProcesses liefert die n Prozesse mit der höchsten CPU-Last (annähernd).
func topProcesses(n int) []ProcInfo {
	procs, err := process.Processes()
	if err != nil {
		return nil
	}
	list := make([]ProcInfo, 0, len(procs))
	for _, p := range procs {
		name, err := p.Name()
		if err != nil || name == "" {
			continue
		}
		cpuPct, _ := p.CPUPercent()
		memPct, _ := p.MemoryPercent()
		list = append(list, ProcInfo{Name: name, CPU: round1(cpuPct), Mem: round1(float64(memPct))})
	}
	sort.Slice(list, func(i, j int) bool { return list[i].CPU > list[j].CPU })
	if len(list) > n {
		list = list[:n]
	}
	return list
}

// CollectTelemetry erhebt eine Momentaufnahme der Auslastung.
func CollectTelemetry() Telemetry {
	t := Telemetry{Disk: collectDisks()}

	if pct, err := cpu.Percent(500*time.Millisecond, false); err == nil && len(pct) > 0 {
		t.CPUPercent = round1(pct[0])
	}
	if vm, err := mem.VirtualMemory(); err == nil {
		t.MemPercent = round1(vm.UsedPercent)
		t.MemUsedMB = round1(float64(vm.Used) / mib)
		t.MemTotalMB = round1(float64(vm.Total) / mib)
	}
	if up, err := host.Uptime(); err == nil {
		t.UptimeSecs = int(up)
	}
	if bt, err := host.BootTime(); err == nil {
		b := time.Unix(int64(bt), 0).UTC()
		t.BootTime = &b
	}
	t.TopProcs = topProcesses(5)
	return t
}

// CollectInventory erhebt die vollständige Inventur.
func CollectInventory() Inventory {
	inv := Inventory{
		Platform:    runtime.GOOS,
		Arch:        runtime.GOARCH,
		Disk:        collectDisks(),
		LocalIP:     LocalIP(),
		MACs:        macAddresses(),
		AgentVer:    Version,
		AgentUser:   agentUser(),
		CollectedAt: time.Now().UTC(),
	}
	if info, err := host.Info(); err == nil {
		inv.Hostname = info.Hostname
		inv.OS = prettyOS(info.Platform, info.PlatformVersion)
		inv.KernelArch = info.KernelArch
		inv.UptimeSecs = int(info.Uptime)
	}
	if ci, err := cpu.Info(); err == nil && len(ci) > 0 {
		inv.CPUModel = ci[0].ModelName
	}
	inv.CPUCores = runtime.NumCPU()
	if vm, err := mem.VirtualMemory(); err == nil {
		inv.MemTotalMB = round1(float64(vm.Total) / mib)
	}
	inv.Software = collectSoftware()
	return inv
}

func prettyOS(platform, version string) string {
	if platform == "" {
		return runtime.GOOS
	}
	if version == "" {
		return platform
	}
	return platform + " " + version
}

// LocalIP ermittelt die vom System bevorzugte ausgehende IPv4 — ohne ein Paket
// zu senden (UDP-Socket wählt nur die Quelladresse).
func LocalIP() string {
	if conn, err := net.Dial("udp", "8.8.8.8:80"); err == nil {
		defer conn.Close()
		if a, ok := conn.LocalAddr().(*net.UDPAddr); ok {
			return a.IP.String()
		}
	}
	addrs, _ := net.InterfaceAddrs()
	for _, a := range addrs {
		if ipnet, ok := a.(*net.IPNet); ok && !ipnet.IP.IsLoopback() {
			if ip4 := ipnet.IP.To4(); ip4 != nil {
				return ip4.String()
			}
		}
	}
	return ""
}

func macAddresses() []string {
	out := []string{}
	ifaces, err := net.Interfaces()
	if err != nil {
		return out
	}
	for _, i := range ifaces {
		if i.Flags&net.FlagLoopback != 0 || i.HardwareAddr == nil {
			continue
		}
		mac := i.HardwareAddr.String()
		if mac != "" {
			out = append(out, mac)
		}
	}
	return out
}

func round1(f float64) float64 {
	return float64(int64(f*10+0.5)) / 10
}
