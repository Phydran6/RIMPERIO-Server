//go:build windows

// ---------------------------------------------------------------------------
// Datei:        uninstall_windows.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.07.02.30.00
//
// Beschreibung: Deinstallation unter Windows. Per MSI installiert (ProductCode
//               in der Registry)? Dann übernimmt der Windows Installer — sonst
//               steht der Agent als Programm in "Apps" und lässt sich nicht
//               mehr sauber entfernen. Ohne MSI: Dienst austragen, Konfiguration
//               löschen; den Programmordner löscht ein kurzer cmd-Lauf nach dem
//               Ende des Prozesses (eine laufende exe kann sich nicht löschen).
// ---------------------------------------------------------------------------

package core

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"

	"golang.org/x/sys/windows/registry"
)

const (
	detachedProcess       = 0x00000008
	createNewProcessGroup = 0x00000200
)

func detached(cmd *exec.Cmd, cmdLine string) {
	cmd.SysProcAttr = &syscall.SysProcAttr{
		HideWindow:    true,
		CmdLine:       cmdLine,
		CreationFlags: detachedProcess | createNewProcessGroup,
	}
}

// msiProductCode liefert den ProductCode, wenn der Agent per MSI kam.
func msiProductCode() string {
	k, err := registry.OpenKey(registry.LOCAL_MACHINE, registryKey, registry.QUERY_VALUE)
	if err != nil {
		return ""
	}
	defer k.Close()
	pc, _, _ := k.GetStringValue("ProductCode")
	if strings.HasPrefix(pc, "{") && strings.HasSuffix(pc, "}") {
		return pc
	}
	return ""
}

// ownInstallDir ist der Programmordner — aber nur, wenn er wirklich unserer ist.
func ownInstallDir() string {
	exe, err := os.Executable()
	if err != nil {
		return ""
	}
	dir := filepath.Dir(exe)
	if strings.EqualFold(filepath.Base(dir), "RIMPERIO Agent") {
		return dir
	}
	return ""
}

// removeLater stoppt (falls gewünscht) den Dienst und löscht den Programmordner
// — beides erst, wenn dieser Prozess weg ist.
func removeLater(stopService bool, dir string) error {
	parts := []string{"ping -n 3 127.0.0.1 >nul"}
	if stopService {
		parts = append(parts, "sc stop rimperio-agent >nul", "ping -n 6 127.0.0.1 >nul")
	}
	if dir != "" {
		parts = append(parts, `rmdir /s /q "`+dir+`"`)
	}
	comspec := os.Getenv("ComSpec")
	if comspec == "" {
		comspec = filepath.Join(os.Getenv("SystemRoot"), "System32", "cmd.exe")
	}
	cmd := exec.Command(comspec)
	detached(cmd, `"`+comspec+`" /d /c `+strings.Join(parts, " & "))
	return cmd.Start()
}

func platformUninstall(fromService bool) []string {
	if pc := msiProductCode(); pc != "" {
		// Der Eintrag auf dem Server ist an dieser Stelle schon erledigt.
		cmd := exec.Command("msiexec.exe")
		detached(cmd, "msiexec.exe /x "+pc+" /qn /norestart KEEP_RECORD=1")
		if err := cmd.Start(); err != nil {
			return []string{"Windows Installer ließ sich nicht starten: " + err.Error()}
		}
		return []string{"Per MSI installiert — der Windows Installer entfernt den Agent jetzt im Hintergrund."}
	}

	var notes []string
	if !fromService {
		_ = ServiceControl("stop")
	}
	if err := ServiceControl("uninstall"); err != nil &&
		!strings.Contains(err.Error(), "not installed") && !strings.Contains(err.Error(), "EventLog") {
		notes = append(notes, "Dienst entfernen: "+err.Error())
	}
	if err := RemoveConfig(); err != nil {
		notes = append(notes, err.Error())
	}
	dir := ownInstallDir()
	if fromService || dir != "" {
		if err := removeLater(fromService, dir); err != nil {
			notes = append(notes, "Aufräumen nach dem Beenden: "+err.Error())
		}
	}
	if dir == "" {
		exe, _ := os.Executable()
		notes = append(notes, "Die Programmdatei bleibt liegen: "+exe)
	} else {
		notes = append(notes, "Der Ordner "+dir+" wird in wenigen Sekunden gelöscht.")
	}
	return append(notes, "Agent entfernt.")
}
