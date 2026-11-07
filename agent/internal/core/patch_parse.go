// ---------------------------------------------------------------------------
// Datei:        patch_parse.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.07.12.00.00
//
// Beschreibung: Zerlegt die Ausgaben von apt, dnf und yum in Updates. Ohne
//               Plattform-Bindung, damit es sich überall testen lässt.
// ---------------------------------------------------------------------------

package core

import (
	"regexp"
	"strings"
)

var kernelPkg = regexp.MustCompile(`^(linux-(image|headers|modules|generic|virtual|signed|kvm|aws|azure|gcp)|kernel(-core|-modules)?|pve-kernel|proxmox-kernel)`)

// apt list --upgradable:
// openssl/jammy-updates,jammy-security 3.0.2-0ubuntu1.15 amd64 [upgradable from: 3.0.2-0ubuntu1.14]
var aptFrom = regexp.MustCompile(`\[(?:upgradable from|aktualisierbar von):\s*([^\]]+)\]`)

func parseApt(out string) []PatchUpdate {
	ups := []PatchUpdate{}
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "Listing") || strings.HasPrefix(line, "Auflistung") || !strings.Contains(line, "/") {
			continue
		}
		fields := strings.Fields(line)
		head := strings.SplitN(fields[0], "/", 2)
		name := head[0]
		archives := ""
		if len(head) > 1 {
			archives = head[1]
		}
		ver := ""
		if len(fields) >= 2 {
			ver = fields[1]
		}
		cur := ""
		if m := aptFrom.FindStringSubmatch(line); m != nil {
			cur = strings.TrimSpace(m[1])
		}
		security := strings.Contains(archives, "security")
		cat := "updates"
		if security {
			cat = "security"
		}
		kind := "software"
		if kernelPkg.MatchString(name) {
			kind = "kernel"
		}
		ups = append(ups, PatchUpdate{
			ID: name, Package: name, Title: strings.TrimSpace(name + " " + ver),
			Current: cur, Available: ver, Source: archives, Security: security,
			Category: cat, Kind: kind, Reboot: kind == "kernel", Size: ver,
		})
	}
	return ups
}

// updateinfo: "RHSA-2024:1234 Important/Sec. openssl-libs-1:3.0.7-25.el9.x86_64"
type secInfo struct{ advisory, severity string }

func parseUpdateinfo(out string) map[string]secInfo {
	res := map[string]secInfo{}
	for _, line := range strings.Split(out, "\n") {
		f := strings.Fields(strings.TrimSpace(line))
		if len(f) < 3 || !strings.Contains(strings.ToLower(f[1]), "sec") {
			continue
		}
		nevra := f[2]
		// name-version-release.arch → name
		if i := strings.LastIndex(nevra, "."); i > 0 {
			nevra = nevra[:i]
		}
		parts := strings.Split(nevra, "-")
		if len(parts) < 3 {
			continue
		}
		name := strings.Join(parts[:len(parts)-2], "-")
		sev := strings.SplitN(f[1], "/", 2)[0]
		if strings.EqualFold(sev, "Sec.") {
			sev = ""
		}
		res[name] = secInfo{advisory: f[0], severity: sev}
	}
	return res
}

func parseDnf(out string, sec map[string]secInfo) []PatchUpdate {
	ups := []PatchUpdate{}
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		low := strings.ToLower(line)
		if line == "" || strings.HasPrefix(low, "last metadata") || strings.HasPrefix(low, "obsoleting") ||
			strings.HasPrefix(low, "security:") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 3 || !strings.Contains(fields[0], ".") {
			continue
		}
		name := fields[0]
		if i := strings.LastIndex(name, "."); i > 0 {
			name = name[:i]
		}
		u := PatchUpdate{ID: fields[0], Package: name, Title: name + " " + fields[1],
			Available: fields[1], Source: fields[2], Size: fields[1], Category: "updates", Kind: "software"}
		if s, ok := sec[name]; ok {
			u.Security, u.Category, u.Severity, u.KB = true, "security", s.severity, s.advisory
		}
		if kernelPkg.MatchString(name) {
			u.Kind, u.Reboot = "kernel", true
		}
		ups = append(ups, u)
	}
	return ups
}
