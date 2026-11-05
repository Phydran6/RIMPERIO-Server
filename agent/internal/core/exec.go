// ---------------------------------------------------------------------------
// Datei:        exec.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.10.31.15.40.00
//
// Beschreibung: Führt die Befehle aus, die der Server im Check-in mitgibt — in
//               der gewünschten Shell, mit Zeitlimit, und meldet Rückgabecode,
//               stdout und stderr zurück. Nichts wird vom Agent selbst erdacht;
//               er führt nur aus, was ein angemeldeter Benutzer veranlasst hat.
// ---------------------------------------------------------------------------

package core

import (
	"bytes"
	"context"
	"fmt"
	"os/exec"
	"runtime"
	"strings"
	"time"
)

// shellInvocation übersetzt (Shell, Befehl) in Programm + Argumente.
func shellInvocation(shell, command string) (string, []string, error) {
	switch strings.ToLower(shell) {
	case "powershell":
		exe := "powershell"
		if runtime.GOOS != "windows" {
			exe = "pwsh" // PowerShell Core, falls auf Linux gewünscht
		}
		return exe, []string{"-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command}, nil
	case "cmd":
		return "cmd", []string{"/C", command}, nil
	case "bash":
		return "bash", []string{"-c", command}, nil
	case "sh":
		return "sh", []string{"-c", command}, nil
	default:
		return "", nil, fmt.Errorf("unbekannte Shell: %s", shell)
	}
}

// Execute führt einen Befehl aus und liefert das Ergebnis.
func Execute(pc PendingCommand) CommandResult {
	res := CommandResult{ID: pc.ID, Status: "done"}

	timeout := pc.Timeout
	if timeout <= 0 {
		timeout = 300
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeout)*time.Second)
	defer cancel()

	name, args, err := shellInvocation(pc.Shell, pc.Command)
	if err != nil {
		code := -1
		return CommandResult{ID: pc.ID, Status: "error", ExitCode: &code, Stderr: err.Error()}
	}

	cmd := exec.CommandContext(ctx, name, args...)
	hideWindow(cmd) // auf Windows: kein aufblitzendes Konsolenfenster

	var out, errb bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &errb
	runErr := cmd.Run()

	res.Stdout = truncate(out.String(), 200_000)
	res.Stderr = truncate(errb.String(), 200_000)

	if ctx.Err() == context.DeadlineExceeded {
		code := -1
		res.Status = "timeout"
		res.ExitCode = &code
		if res.Stderr == "" {
			res.Stderr = fmt.Sprintf("Zeitlimit von %ds überschritten.", timeout)
		}
		return res
	}
	if runErr != nil {
		res.Status = "error"
		if ee, ok := runErr.(*exec.ExitError); ok {
			code := ee.ExitCode()
			res.ExitCode = &code
		} else {
			code := -1
			res.ExitCode = &code
			if res.Stderr == "" {
				res.Stderr = runErr.Error()
			}
		}
		return res
	}
	code := 0
	res.ExitCode = &code
	return res
}

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n] + "\n…(gekürzt)"
	}
	return s
}
