// ---------------------------------------------------------------------------
// Datei:        appliance.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.27.21.00.00
//
// Beschreibung: Läuft der Agent auf einem NAS? Synology DSM erkennt er an
//               /etc.defaults/VERSION (Version, Build) und dem Modell aus
//               /proc/sys/kernel/syno_hw_version bzw. synoinfo.conf. Die
//               Inventur trägt das als "appliance" — der Server schlägt den
//               Agent dann beim Einrichten des NAS vor (Adresse 127.0.0.1).
//
//               Als DSM-Paket „RIMPERIO Agent“ (server/backend/app/dsmpackage.py)
//               setzt das Startskript RIMPERIO_PACKAGE=synology und
//               RIMPERIO_HOME auf das Datenverzeichnis des Pakets.
// ---------------------------------------------------------------------------

package core

import (
	"bufio"
	"os"
	"strings"
)

// ApplianceInfo beschreibt ein Gerät mit eigenem Betriebssystem (NAS).
type ApplianceInfo struct {
	Vendor   string `json:"vendor"`             // synology
	Model    string `json:"model,omitempty"`    // z. B. DS920+
	Version  string `json:"version,omitempty"`  // z. B. 7.2.1
	Build    string `json:"build,omitempty"`    // z. B. 69057
	Packaged bool   `json:"packaged,omitempty"` // Agent läuft als Paket des NAS
}

// Packaged nennt die Paketform, in der der Agent läuft ("" = normaler Dienst).
func Packaged() string {
	return strings.ToLower(strings.TrimSpace(os.Getenv("RIMPERIO_PACKAGE")))
}

// readKV liest eine Datei mit Zeilen der Form key="value".
func readKV(path string) map[string]string {
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	out := map[string]string{}
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		k, v, ok := strings.Cut(line, "=")
		if !ok || strings.HasPrefix(line, "#") {
			continue
		}
		out[strings.TrimSpace(k)] = strings.Trim(strings.TrimSpace(v), `"`)
	}
	return out
}

// detectAppliance erkennt Synology DSM. nil = kein bekanntes NAS.
func detectAppliance() *ApplianceInfo {
	return detectSynology("")
}

func detectSynology(root string) *ApplianceInfo {
	ver := readKV(root + "/etc.defaults/VERSION")
	if ver == nil || (ver["productversion"] == "" && ver["majorversion"] == "") {
		return nil
	}
	a := &ApplianceInfo{Vendor: "synology", Packaged: Packaged() == "synology"}
	a.Version = ver["productversion"]
	if a.Version == "" {
		a.Version = strings.Trim(ver["majorversion"]+"."+ver["minorversion"], ".")
	}
	a.Build = ver["buildnumber"]
	if raw, err := os.ReadFile(root + "/proc/sys/kernel/syno_hw_version"); err == nil {
		a.Model = strings.TrimSpace(string(raw))
	}
	if a.Model == "" {
		if info := readKV(root + "/etc.defaults/synoinfo.conf"); info != nil {
			a.Model = info["upnpmodelname"]
		}
	}
	return a
}

// String für die Inventur: "Synology DSM 7.2.1-69057".
func (a *ApplianceInfo) OSName() string {
	s := "Synology DSM " + a.Version
	if a.Build != "" {
		s += "-" + a.Build
	}
	return s
}
