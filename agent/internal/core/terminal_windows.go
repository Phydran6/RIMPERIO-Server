//go:build windows

// ---------------------------------------------------------------------------
// Datei:        terminal_windows.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      0.4.0
//
// Beschreibung: Live-Shell unter Windows. Bevorzugt ConPTY (Windows 10 1809 /
//               Server 2019 und neuer): ein echtes Konsolenfenster ohne Fenster,
//               Farben und Programme wie in einer lokalen PowerShell. Ältere
//               Systeme bekommen PowerShell über Pipes (ohne Vollbild-Programme).
//               Die Shell läuft als Konto des Dienstes (SYSTEM).
// ---------------------------------------------------------------------------

package core

import (
	"errors"
	"os"
	"os/exec"
	"sync"
	"syscall"
	"unsafe"

	"golang.org/x/sys/windows"
)

func shellCommandLine(want string) string {
	switch want {
	case "cmd":
		return `C:\Windows\System32\cmd.exe`
	default:
		if p, err := exec.LookPath("pwsh.exe"); err == nil && want == "pwsh" {
			return `"` + p + `" -NoLogo`
		}
		return `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoLogo -NoExit -ExecutionPolicy Bypass`
	}
}

func startTerminal(want string, cols, rows int) (terminal, error) {
	t, err := startConPTY(shellCommandLine(want), cols, rows)
	if err == nil {
		return t, nil
	}
	return startPipeShell(want)
}

// ---------------------------------------------------------------- ConPTY ----

type conPTY struct {
	hpc       windows.Handle
	inWrite   *os.File
	outRead   *os.File
	process   windows.Handle
	thread    windows.Handle
	attrs     *windows.ProcThreadAttributeListContainer
	closeOnce sync.Once
}

func startConPTY(cmdline string, cols, rows int) (*conPTY, error) {
	var inRead, inWrite, outRead, outWrite windows.Handle
	if err := windows.CreatePipe(&inRead, &inWrite, nil, 0); err != nil {
		return nil, err
	}
	if err := windows.CreatePipe(&outRead, &outWrite, nil, 0); err != nil {
		windows.CloseHandle(inRead)
		windows.CloseHandle(inWrite)
		return nil, err
	}
	var hpc windows.Handle
	size := windows.Coord{X: int16(cols), Y: int16(rows)}
	if err := windows.CreatePseudoConsole(size, inRead, outWrite, 0, &hpc); err != nil {
		for _, h := range []windows.Handle{inRead, inWrite, outRead, outWrite} {
			windows.CloseHandle(h)
		}
		return nil, err
	}
	// Die Enden, die jetzt der Pseudokonsole gehören, schließen.
	windows.CloseHandle(inRead)
	windows.CloseHandle(outWrite)

	attrs, err := windows.NewProcThreadAttributeList(1)
	if err != nil {
		windows.ClosePseudoConsole(hpc)
		return nil, err
	}
	if err := attrs.Update(windows.PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, *(*unsafe.Pointer)(unsafe.Pointer(&hpc)), unsafe.Sizeof(hpc)); err != nil {
		attrs.Delete()
		windows.ClosePseudoConsole(hpc)
		return nil, err
	}
	si := &windows.StartupInfoEx{ProcThreadAttributeList: attrs.List()}
	si.Cb = uint32(unsafe.Sizeof(*si))
	si.Flags = windows.STARTF_USESTDHANDLES
	pi := &windows.ProcessInformation{}
	cmd, err := windows.UTF16PtrFromString(cmdline)
	if err != nil {
		attrs.Delete()
		windows.ClosePseudoConsole(hpc)
		return nil, err
	}
	dir, _ := windows.UTF16PtrFromString(os.Getenv("SystemDrive") + `\`)
	env := append(os.Environ(), "TERM=xterm-256color")
	block := createEnvBlock(env)
	err = windows.CreateProcess(nil, cmd, nil, nil, false,
		windows.EXTENDED_STARTUPINFO_PRESENT|windows.CREATE_UNICODE_ENVIRONMENT, block, dir, &si.StartupInfo, pi)
	if err != nil {
		attrs.Delete()
		windows.ClosePseudoConsole(hpc)
		return nil, err
	}
	return &conPTY{
		hpc: hpc, attrs: attrs, process: pi.Process, thread: pi.Thread,
		inWrite: os.NewFile(uintptr(inWrite), "conpty-in"), outRead: os.NewFile(uintptr(outRead), "conpty-out"),
	}, nil
}

func createEnvBlock(env []string) *uint16 {
	var b []uint16
	for _, kv := range env {
		u, err := windows.UTF16FromString(kv)
		if err != nil {
			continue
		}
		b = append(b, u...)
	}
	b = append(b, 0)
	return &b[0]
}

func (c *conPTY) Read(p []byte) (int, error)  { return c.outRead.Read(p) }
func (c *conPTY) Write(p []byte) (int, error) { return c.inWrite.Write(p) }
func (c *conPTY) Resize(cols, rows int) error {
	return windows.ResizePseudoConsole(c.hpc, windows.Coord{X: int16(cols), Y: int16(rows)})
}
func (c *conPTY) Close() error {
	c.closeOnce.Do(func() {
		_ = windows.TerminateProcess(c.process, 1)
		windows.ClosePseudoConsole(c.hpc)
		_ = c.inWrite.Close()
		_ = c.outRead.Close()
		c.attrs.Delete()
	})
	return nil
}
func (c *conPTY) Wait() int {
	_, _ = windows.WaitForSingleObject(c.process, windows.INFINITE)
	var code uint32
	_ = windows.GetExitCodeProcess(c.process, &code)
	windows.CloseHandle(c.process)
	windows.CloseHandle(c.thread)
	return int(code)
}

// ------------------------------------------------------- Pipes (Ersatz) ----

type pipeShell struct {
	cmd *exec.Cmd
	in  interface {
		Write([]byte) (int, error)
		Close() error
	}
	out *os.File
	pw  *os.File
}

func startPipeShell(want string) (terminal, error) {
	exe := `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`
	args := []string{"-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "-"}
	if want == "cmd" {
		exe, args = `C:\Windows\System32\cmd.exe`, []string{"/Q"}
	}
	cmd := exec.Command(exe, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	pr, pw, err := os.Pipe()
	if err != nil {
		return nil, err
	}
	cmd.Stdout, cmd.Stderr = pw, pw
	in, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	_, _ = pw.Write([]byte("RIMPERIO: einfache Konsole (ohne ConPTY) – Vollbild-Programme gehen hier nicht.\r\n"))
	return &pipeShell{cmd: cmd, in: in, out: pr, pw: pw}, nil
}

func (s *pipeShell) Read(p []byte) (int, error) { return s.out.Read(p) }
func (s *pipeShell) Write(p []byte) (int, error) {
	// Ohne Terminal gibt es kein Echo - zurückschreiben, was getippt wurde.
	echo := make([]byte, 0, len(p))
	for _, b := range p {
		if b == '\r' {
			echo = append(echo, '\r', '\n')
			continue
		}
		echo = append(echo, b)
	}
	_, _ = s.pw.Write(echo)
	q := make([]byte, 0, len(p))
	for _, b := range p {
		if b == '\r' {
			b = '\n'
		}
		q = append(q, b)
	}
	return s.in.Write(q)
}
func (s *pipeShell) Resize(int, int) error { return nil }
func (s *pipeShell) Close() error {
	_ = s.in.Close()
	if s.cmd.Process != nil {
		_ = s.cmd.Process.Kill()
	}
	_ = s.pw.Close()
	return s.out.Close()
}
func (s *pipeShell) Wait() int {
	if err := s.cmd.Wait(); err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			return ee.ExitCode()
		}
		return -1
	}
	return 0
}
