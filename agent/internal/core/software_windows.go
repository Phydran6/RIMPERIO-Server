//go:build windows

// ---------------------------------------------------------------------------
// Datei:        software_windows.go
// Projekt:      LRMM — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.05.12.00.00
//
// Beschreibung: Installierte Software unter Windows — aus den Uninstall-Schlüsseln
//               der Registry (64-Bit, 32-Bit und je Benutzer).
// ---------------------------------------------------------------------------

package core

import (
	"sort"
	"strings"

	"golang.org/x/sys/windows/registry"
)

func collectSoftware() []SoftwareItem {
	seen := map[string]SoftwareItem{}
	const path = `SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall`
	roots := []struct {
		key    registry.Key
		access uint32
	}{
		{registry.LOCAL_MACHINE, registry.READ | registry.WOW64_64KEY},
		{registry.LOCAL_MACHINE, registry.READ | registry.WOW64_32KEY},
		{registry.CURRENT_USER, registry.READ},
	}

	for _, r := range roots {
		base, err := registry.OpenKey(r.key, path, r.access)
		if err != nil {
			continue
		}
		names, _ := base.ReadSubKeyNames(-1)
		for _, n := range names {
			sub, err := registry.OpenKey(r.key, path+`\`+n, r.access)
			if err != nil {
				continue
			}
			name, _, _ := sub.GetStringValue("DisplayName")
			ver, _, _ := sub.GetStringValue("DisplayVersion")
			pub, _, _ := sub.GetStringValue("Publisher")
			sysComp, _, _ := sub.GetIntegerValue("SystemComponent")
			sub.Close()
			if name == "" || sysComp == 1 {
				continue
			}
			seen[name] = SoftwareItem{Name: name, Version: ver, Publisher: pub}
		}
		base.Close()
	}

	out := make([]SoftwareItem, 0, len(seen))
	for _, v := range seen {
		out = append(out, v)
	}
	sort.Slice(out, func(i, j int) bool {
		return strings.ToLower(out[i].Name) < strings.ToLower(out[j].Name)
	})
	return out
}
