// ---------------------------------------------------------------------------
// Datei:        tunnel.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      0.4.0
//
// Beschreibung: Fernzugriff über einen verschlüsselten Tunnel. Auf Anforderung
//               (Auftrag "tunnel") baut der Agent eine WebSocket-Verbindung
//               ZUM Server auf (HTTPS, ausgehend – keine offenen Ports auf dem
//               Rechner) und hängt daran
//                 - eine Live-Shell (Linux: echtes Terminal/PTY, Windows:
//                   ConPTY, ersatzweise PowerShell über Pipes), oder
//                 - eine TCP-Weiterleitung zu 127.0.0.1:3389 (RDP) bzw.
//                   127.0.0.1:5900 (VNC für die Live-Sitzung) – andere Ziele
//                   nimmt der Agent nicht an.
//               Der Server reicht den Tunnel an den Browser (xterm) bzw. an
//               guacd (RDP/VNC im Browser) weiter.
// ---------------------------------------------------------------------------

package core

import (
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

// TunnelRequest ist der Inhalt eines Auftrags vom Typ "tunnel".
type TunnelRequest struct {
	Session string `json:"session"`
	Kind    string `json:"kind"` // shell | tcp
	Port    int    `json:"port"` // nur tcp: 3389 oder 5900
	Cols    int    `json:"cols"`
	Rows    int    `json:"rows"`
	Shell   string `json:"shell"` // Wunsch: powershell | cmd | bash | sh
}

// Erlaubte Ziele einer Weiterleitung. Bewusst fest: Der Tunnel soll kein
// allgemeiner Sprungbrett-Proxy ins Netz des Kunden sein.
var allowedTunnelPorts = map[int]bool{3389: true, 5900: true}

type control struct {
	Type string `json:"type"`
	Cols int    `json:"cols,omitempty"`
	Rows int    `json:"rows,omitempty"`
	Code int    `json:"code,omitempty"`
	Msg  string `json:"message,omitempty"`
}

// terminal ist eine laufende Shell mit Ein-/Ausgabe und Größenänderung.
type terminal interface {
	io.ReadWriteCloser
	Resize(cols, rows int) error
	Wait() int
}

// StartTunnel baut den Tunnel im Hintergrund auf und kehrt sofort zurück.
func StartTunnel(raw string, client *Client) error {
	var req TunnelRequest
	if err := json.Unmarshal([]byte(strings.TrimSpace(raw)), &req); err != nil {
		return fmt.Errorf("Tunnel-Auftrag nicht lesbar: %w", err)
	}
	if req.Session == "" || client == nil {
		return fmt.Errorf("Tunnel ohne Sitzung")
	}
	switch req.Kind {
	case "shell":
	case "tcp":
		if !allowedTunnelPorts[req.Port] {
			return fmt.Errorf("Weiterleitung zu Port %d ist nicht erlaubt", req.Port)
		}
	default:
		return fmt.Errorf("unbekannte Tunnel-Art %q", req.Kind)
	}
	go func() {
		if err := runTunnel(req, client); err != nil {
			log.Printf("Tunnel %s (%s): %v", req.Session, req.Kind, err)
		}
	}()
	return nil
}

func tunnelURL(base, session string) (string, error) {
	u, err := url.Parse(strings.TrimRight(base, "/") + "/api/rmm/agent/tunnel/" + url.PathEscape(session))
	if err != nil {
		return "", err
	}
	switch u.Scheme {
	case "https":
		u.Scheme = "wss"
	case "http":
		u.Scheme = "ws"
	}
	return u.String(), nil
}

func dialTunnel(client *Client, session string) (*websocket.Conn, error) {
	target, err := tunnelURL(client.BaseURL, session)
	if err != nil {
		return nil, err
	}
	d := websocket.Dialer{Proxy: http.ProxyFromEnvironment, HandshakeTimeout: 20 * time.Second}
	if tr, ok := client.http.Transport.(*http.Transport); ok && tr.TLSClientConfig != nil {
		d.TLSClientConfig = tr.TLSClientConfig.Clone()
	} else {
		d.TLSClientConfig = &tls.Config{MinVersion: tls.VersionTLS12}
	}
	h := http.Header{}
	h.Set("Authorization", "Bearer "+client.Token)
	conn, resp, err := d.Dial(target, h)
	if err != nil {
		if resp != nil {
			return nil, fmt.Errorf("Tunnel abgelehnt: HTTP %d", resp.StatusCode)
		}
		return nil, err
	}
	return conn, nil
}

// wsWriter serialisiert Schreibzugriffe (gorilla erlaubt nur einen Schreiber).
type wsWriter struct {
	mu   sync.Mutex
	conn *websocket.Conn
}

func (w *wsWriter) binary(p []byte) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	_ = w.conn.SetWriteDeadline(time.Now().Add(30 * time.Second))
	return w.conn.WriteMessage(websocket.BinaryMessage, p)
}

func (w *wsWriter) json(v any) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	_ = w.conn.SetWriteDeadline(time.Now().Add(30 * time.Second))
	return w.conn.WriteJSON(v)
}

func runTunnel(req TunnelRequest, client *Client) error {
	conn, err := dialTunnel(client, req.Session)
	if err != nil {
		return err
	}
	defer conn.Close()
	conn.SetReadLimit(4 << 20)
	w := &wsWriter{conn: conn}

	var stream io.ReadWriteCloser
	var term terminal
	switch req.Kind {
	case "shell":
		cols, rows := req.Cols, req.Rows
		if cols <= 0 {
			cols = 120
		}
		if rows <= 0 {
			rows = 32
		}
		term, err = startTerminal(req.Shell, cols, rows)
		if err != nil {
			_ = w.json(control{Type: "error", Msg: "Shell startet nicht: " + err.Error()})
			return err
		}
		stream = term
	case "tcp":
		c, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", req.Port), 10*time.Second)
		if err != nil {
			msg := fmt.Sprintf("Port %d auf diesem Rechner antwortet nicht", req.Port)
			if req.Port == 3389 {
				msg += " – ist Remotedesktop eingeschaltet?"
			} else {
				msg += " – ist der VNC-Dienst für die Live-Sitzung installiert?"
			}
			_ = w.json(control{Type: "error", Msg: msg})
			return err
		}
		stream = c
	}
	defer stream.Close()
	_ = w.json(control{Type: "ready"})

	done := make(chan struct{})
	// Rechner -> Server
	go func() {
		defer close(done)
		buf := make([]byte, 32*1024)
		for {
			n, err := stream.Read(buf)
			if n > 0 {
				if werr := w.binary(buf[:n]); werr != nil {
					return
				}
			}
			if err != nil {
				return
			}
		}
	}()
	// Server -> Rechner
	go func() {
		for {
			mt, data, err := conn.ReadMessage()
			if err != nil {
				_ = stream.Close()
				return
			}
			if mt == websocket.BinaryMessage {
				if _, err := stream.Write(data); err != nil {
					return
				}
				continue
			}
			var c control
			if json.Unmarshal(data, &c) != nil {
				continue
			}
			switch c.Type {
			case "resize":
				if term != nil && c.Cols > 0 && c.Rows > 0 {
					_ = term.Resize(c.Cols, c.Rows)
				}
			case "close":
				_ = stream.Close()
				return
			}
		}
	}()
	<-done
	code := 0
	if term != nil {
		code = term.Wait()
	}
	_ = w.json(control{Type: "exit", Code: code})
	w.mu.Lock()
	_ = conn.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""), time.Now().Add(time.Second))
	w.mu.Unlock()
	return nil
}
