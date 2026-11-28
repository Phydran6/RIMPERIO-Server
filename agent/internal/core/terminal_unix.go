//go:build !windows

// ---------------------------------------------------------------------------
// Datei:        terminal_unix.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      0.4.0
//
// Beschreibung: Live-Shell unter Linux/macOS: echtes Terminal (PTY), damit auch
//               top, vim, less & Co. funktionieren. Läuft als das Konto des
//               Agents (Linux: Dienstkonto rimperio).
// ---------------------------------------------------------------------------

package core

import (
	"errors"
	"os"
	"os/exec"

	"github.com/creack/pty"
)

type unixTerminal struct {
	f   *os.File
	cmd *exec.Cmd
}

func startTerminal(want string, cols, rows int) (terminal, error) {
	shell := ""
	for _, c := range []string{want, os.Getenv("SHELL"), "bash", "sh"} {
		if c == "" || c == "powershell" || c == "cmd" {
			continue
		}
		if p, err := exec.LookPath(c); err == nil {
			shell = p
			break
		}
	}
	if shell == "" {
		return nil, errors.New("keine Shell gefunden")
	}
	cmd := exec.Command(shell, "-l")
	cmd.Env = append(os.Environ(), "TERM=xterm-256color", "COLORTERM=truecolor")
	if home, err := os.UserHomeDir(); err == nil {
		cmd.Dir = home
	}
	f, err := pty.StartWithSize(cmd, &pty.Winsize{Cols: uint16(cols), Rows: uint16(rows)})
	if err != nil {
		return nil, err
	}
	return &unixTerminal{f: f, cmd: cmd}, nil
}

func (t *unixTerminal) Read(p []byte) (int, error)  { return t.f.Read(p) }
func (t *unixTerminal) Write(p []byte) (int, error) { return t.f.Write(p) }
func (t *unixTerminal) Close() error {
	if t.cmd.Process != nil {
		_ = t.cmd.Process.Signal(os.Interrupt)
		_ = t.cmd.Process.Kill()
	}
	return t.f.Close()
}
func (t *unixTerminal) Resize(cols, rows int) error {
	return pty.Setsize(t.f, &pty.Winsize{Cols: uint16(cols), Rows: uint16(rows)})
}
func (t *unixTerminal) Wait() int {
	if err := t.cmd.Wait(); err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			return ee.ExitCode()
		}
		return -1
	}
	return 0
}
