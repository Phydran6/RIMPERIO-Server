// ---------------------------------------------------------------------------
// Datei:        client.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.07.12.00.00
//
// Beschreibung: Der schmale HTTP-Weg zum Server. Anmeldung (enroll), Check-in
//               mit Messwerten, Inventur, Ergebnisse und Abmeldung. Immer HTTPS mit dem
//               persönlichen Agent-Token im Authorization-Header; -insecure
//               nur fürs Labor mit Selbstzertifikat.
// ---------------------------------------------------------------------------

package core

import (
	"bytes"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

type Client struct {
	BaseURL string
	Token   string
	http    *http.Client
}

func NewClient(baseURL, token string, insecure bool) *Client {
	tr := &http.Transport{Proxy: http.ProxyFromEnvironment}
	if insecure {
		tr.TLSClientConfig = &tls.Config{InsecureSkipVerify: true} // #nosec G402 — bewusst, nur mit -insecure
	}
	return &Client{
		BaseURL: strings.TrimRight(baseURL, "/"),
		Token:   token,
		http:    &http.Client{Timeout: 60 * time.Second, Transport: tr},
	}
}

// --- Datenformen (Gegenstück zu backend/app/schemas.py) --------------------
type EnrollRequest struct {
	EnrollSecret string `json:"enroll_secret"`
	Hostname     string `json:"hostname"`
	OS           string `json:"os"`
	Platform     string `json:"platform"`
	Arch         string `json:"arch"`
	Version      string `json:"version"`
	LocalIP      string `json:"local_ip"`
	Site         string `json:"site"`
	Description  string `json:"description"`
}

type EnrollResponse struct {
	AgentUID        string `json:"agent_uid"`
	AgentToken      string `json:"agent_token"`
	CheckinInterval int    `json:"checkin_interval"`
}

type CheckinRequest struct {
	Version   string     `json:"version"`
	LocalIP   string     `json:"local_ip"`
	PublicIP  string     `json:"public_ip,omitempty"`
	Telemetry *Telemetry `json:"telemetry,omitempty"`
	// Erster Check-in nach dem Start: Der Server bricht Aufträge ab, die
	// noch als "läuft" stehen — der alte Prozess meldet sie nicht mehr.
	Restarted bool `json:"restarted,omitempty"`
}

type WaitResponse struct {
	Commands []PendingCommand `json:"commands"`
	Relay    []RelayRequest   `json:"relay"` // ab Server 0.4.0: weitergeleitete HTTP-Anfragen
}

// waitRequest kündigt an, was der Agent kann (z. B. "relay").
type waitRequest struct {
	Caps []string `json:"caps"`
}

// ErrNoWait: Der Server kennt die Warte-Verbindung nicht (älterer Server).
var ErrNoWait = errors.New("Server ohne Sofort-Zustellung")

type PendingCommand struct {
	ID      int    `json:"id"`
	Shell   string `json:"shell"`
	Command string `json:"command"`
	Timeout int    `json:"timeout"`
	Kind    string `json:"kind"` // shell | patch_scan | patch_apply | docker_scan | docker_update | health_snapshot | reboot | software_inventory | uninstall
}

type CheckSpec struct {
	ID       int            `json:"id"`
	Type     string         `json:"type"`
	Params   map[string]any `json:"params"`
	Interval int            `json:"interval"`
}

type CheckinResponse struct {
	Interval     int              `json:"interval"`
	OfflineAfter int              `json:"offline_after"`
	Commands     []PendingCommand `json:"commands"`
	Checks       []CheckSpec      `json:"checks"`
	Snmp         []SnmpTask       `json:"snmp"`
}

type CommandResult struct {
	ID       int    `json:"id"`
	Status   string `json:"status"`
	ExitCode *int   `json:"exit_code"`
	Stdout   string `json:"stdout"`
	Stderr   string `json:"stderr"`
}

type CheckResultOut struct {
	CheckID int    `json:"check_id"`
	Status  string `json:"status"` // pass | fail | unknown
	Value   string `json:"value"`
	Output  string `json:"output,omitempty"`
}

// doJSON schickt body als JSON und liest die Antwort in out (falls != nil).
func (c *Client) doJSON(method, path, bearer string, body, out any) error {
	var reader io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(raw)
	}
	req, err := http.NewRequest(method, c.BaseURL+path, reader)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "rimperio-agent/"+Version)
	if bearer != "" {
		req.Header.Set("Authorization", "Bearer "+bearer)
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	data, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if (resp.StatusCode == http.StatusNotFound || resp.StatusCode == http.StatusMethodNotAllowed) &&
		path == "/api/rmm/agent/wait" {
		return ErrNoWait
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("%s %s: HTTP %d: %s", method, path, resp.StatusCode, strings.TrimSpace(string(data)))
	}
	if out != nil && len(data) > 0 {
		return json.Unmarshal(data, out)
	}
	return nil
}

func (c *Client) Enroll(req EnrollRequest) (*EnrollResponse, error) {
	var res EnrollResponse
	if err := c.doJSON(http.MethodPost, "/api/rmm/agent/enroll", "", req, &res); err != nil {
		return nil, err
	}
	return &res, nil
}

func (c *Client) Checkin(req CheckinRequest) (*CheckinResponse, error) {
	var res CheckinResponse
	if err := c.doJSON(http.MethodPost, "/api/rmm/agent/checkin", c.Token, req, &res); err != nil {
		return nil, err
	}
	return &res, nil
}

// Wait hält eine Verbindung offen, bis der Server einen Auftrag hat (höchstens
// ~25 s, dann leere Liste). So kommt ein Befehl aus der Oberfläche sofort an
// statt erst beim nächsten Check-in.
func (c *Client) Wait(caps []string) (*WaitResponse, error) {
	var res WaitResponse
	if caps == nil {
		caps = []string{}
	}
	if err := c.doJSON(http.MethodPost, "/api/rmm/agent/wait", c.Token, waitRequest{Caps: caps}, &res); err != nil {
		return nil, err
	}
	return &res, nil
}

// SendRelayResults schickt Antworten weitergeleiteter Anfragen zurück. Eigene,
// längere Zeitgrenze: eine Antwort kann einige MB groß sein.
func (c *Client) SendRelayResults(results []RelayResult) error {
	if len(results) == 0 {
		return nil
	}
	long := *c
	hc := *c.http
	hc.Timeout = 5 * time.Minute
	long.http = &hc
	return long.doJSON(http.MethodPost, "/api/rmm/agent/relay-results", c.Token, results, nil)
}

func (c *Client) SendInventory(inv Inventory) error {
	return c.doJSON(http.MethodPost, "/api/rmm/agent/inventory", c.Token, inv, nil)
}

func (c *Client) SendResults(results []CommandResult) error {
	if len(results) == 0 {
		return nil
	}
	return c.doJSON(http.MethodPost, "/api/rmm/agent/results", c.Token, results, nil)
}

func (c *Client) SendCheckResults(results []CheckResultOut) error {
	if len(results) == 0 {
		return nil
	}
	return c.doJSON(http.MethodPost, "/api/rmm/agent/check-results", c.Token, results, nil)
}

func (c *Client) SendSnmpResults(results []SnmpResult) error {
	if len(results) == 0 {
		return nil
	}
	return c.doJSON(http.MethodPost, "/api/rmm/agent/snmp-results", c.Token, results, nil)
}

// Unregister meldet den Agent beim Server ab. Der Server löscht dann Messwerte,
// Befehle und Patch-Stand; ein Gerät ohne Logs verschwindet ganz.
func (c *Client) Unregister() error {
	return c.doJSON(http.MethodPost, "/api/rmm/agent/unregister", c.Token, struct{}{}, nil)
}
