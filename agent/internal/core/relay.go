// ---------------------------------------------------------------------------
// Datei:        relay.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.27.21.00.00
//
// Beschreibung: HTTPS-Weiterleitung für Geräte am Standort des Agents — vor
//               allem ein NAS an einem anderen Ort als der RIMPERIO-Server.
//
//               Der Server erreicht das NAS nicht (anderes Netz, kein VPN, kein
//               offener Port). Der Agent schon: Er steht im selben Netz oder
//               läuft als Paket auf dem NAS selbst. Über die ohnehin offene
//               Warte-Verbindung (/api/rmm/agent/wait) bekommt er HTTP-Anfragen
//               mit, stellt sie hier im eigenen Netz und schickt die Antwort an
//               /api/rmm/agent/relay-results zurück. Es bleibt bei: nur
//               ausgehend, nur HTTPS zum Server.
//
//               Schutz: Ziele nur im lokalen Netz (privat, Loopback,
//               Link-Local, CGNAT/VPN) — der Agent wird so kein offener
//               Proxy ins Internet. In agent.json "relay": "lan" (Standard),
//               "any" (auch öffentliche Adressen) oder "off" (abgeschaltet);
//               RIMPERIO_RELAY überschreibt den Wert.
// ---------------------------------------------------------------------------

package core

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"syscall"
	"time"
)

// RelayRequest kommt vom Server (Gegenstück zu backend/app/relay.py).
type RelayRequest struct {
	ID       string            `json:"id"`
	Method   string            `json:"method"`
	URL      string            `json:"url"`
	Headers  map[string]string `json:"headers"`
	Body     string            `json:"body"` // Base64
	Timeout  int               `json:"timeout"`
	Insecure bool              `json:"insecure"` // Zertifikat nicht prüfen (NAS mit Selbstzertifikat)
	Cert     bool              `json:"cert"`     // Zertifikat der Gegenstelle mitschicken
}

// RelayResult geht an den Server zurück.
type RelayResult struct {
	ID        string              `json:"id"`
	Status    int                 `json:"status,omitempty"`
	Headers   map[string][]string `json:"headers,omitempty"`
	Body      string              `json:"body,omitempty"` // Base64
	Cert      string              `json:"cert,omitempty"` // DER, Base64
	Error     string              `json:"error,omitempty"`
	ErrorKind string              `json:"error_kind,omitempty"` // connect | timeout | tls | denied | other
	Ms        int64               `json:"ms"`
}

const (
	relayMaxBody     = 32 << 20 // wie MAX_BODY auf dem Server
	relayParallel    = 8
	relayMaxTimeout  = 600
	relayDefaultTime = 30
)

var (
	relaySem   = make(chan struct{}, relayParallel)
	relayMu    sync.Mutex
	relayTrans = map[string]map[bool]*http.Transport{} // Einstellung -> (false = Zertifikat prüfen, true = nicht prüfen)
)

// errRelayDenied: Ziel liegt außerhalb dessen, was die Einstellung erlaubt.
var errRelayDenied = errors.New("Ziel nicht erlaubt")

// RelayPolicy liest die Einstellung: lan (Standard), any oder off.
func RelayPolicy(cfg *Config) string {
	p := strings.ToLower(strings.TrimSpace(os.Getenv("RIMPERIO_RELAY")))
	if p == "" && cfg != nil {
		p = strings.ToLower(strings.TrimSpace(cfg.Relay))
	}
	switch p {
	case "any", "off":
		return p
	default:
		return "lan"
	}
}

// Capabilities kündigt der Agent bei der Warte-Anfrage an.
func Capabilities(cfg *Config) []string {
	if RelayPolicy(cfg) == "off" {
		return []string{}
	}
	return []string{"relay"}
}

var cgnat = mustCIDR("100.64.0.0/10") // Carrier-NAT, auch Tailscale/WireGuard-Netze

func mustCIDR(s string) *net.IPNet {
	_, n, err := net.ParseCIDR(s)
	if err != nil {
		panic(err)
	}
	return n
}

// relayTargetAllowed prüft die tatsächlich angewählte Adresse (nach DNS).
func relayTargetAllowed(policy string, ip net.IP) bool {
	switch policy {
	case "any":
		return true
	case "off":
		return false
	}
	if ip == nil {
		return false
	}
	return ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast() || cgnat.Contains(ip)
}

func relayTransports(policy string) map[bool]*http.Transport {
	relayMu.Lock()
	defer relayMu.Unlock()
	if t, ok := relayTrans[policy]; ok {
		return t
	}
	{
		dialer := &net.Dialer{
			Timeout:   10 * time.Second,
			KeepAlive: 30 * time.Second,
			// Control sieht die aufgelöste Adresse: auch ein Name, der auf eine
			// öffentliche IP zeigt, kommt so nicht durch.
			Control: func(network, address string, _ syscall.RawConn) error {
				host, _, err := net.SplitHostPort(address)
				if err != nil {
					return err
				}
				if !relayTargetAllowed(policy, net.ParseIP(host)) {
					return fmt.Errorf("%w: %s liegt nicht im lokalen Netz (Einstellung relay=%s)", errRelayDenied, host, policy)
				}
				return nil
			},
		}
		mk := func(insecure bool) *http.Transport {
			return &http.Transport{
				Proxy:               nil, // Ziele im eigenen Netz — nie über einen Proxy
				DialContext:         dialer.DialContext,
				TLSClientConfig:     &tls.Config{InsecureSkipVerify: insecure}, // #nosec G402 — nur auf Wunsch des Servers (Selbstzertifikat des NAS)
				MaxIdleConnsPerHost: relayParallel,
				IdleConnTimeout:     60 * time.Second,
				TLSHandshakeTimeout: 15 * time.Second,
			}
		}
		relayTrans[policy] = map[bool]*http.Transport{false: mk(false), true: mk(true)}
	}
	return relayTrans[policy]
}

// relayErrKind übersetzt einen Fehler in eine Art, die der Server versteht.
func relayErrKind(err error) string {
	var ne net.Error
	var unk x509.UnknownAuthorityError
	var inv x509.CertificateInvalidError
	var hn x509.HostnameError
	var cve *tls.CertificateVerificationError
	switch {
	case errors.Is(err, errRelayDenied):
		return "denied"
	case errors.As(err, &unk), errors.As(err, &inv), errors.As(err, &hn), errors.As(err, &cve):
		return "tls"
	case errors.Is(err, context.DeadlineExceeded), errors.As(err, &ne) && ne.Timeout():
		return "timeout"
	case strings.Contains(err.Error(), "tls:"):
		return "tls"
	default:
		return "connect"
	}
}

// executeRelay stellt eine Anfrage im lokalen Netz.
func executeRelay(policy string, r RelayRequest) RelayResult {
	start := time.Now()
	res := RelayResult{ID: r.ID}
	fail := func(kind string, err error) RelayResult {
		// Ohne das 'Get "https://…": ' davor – die Adresse kennt der Server selbst.
		var ue *url.Error
		if errors.As(err, &ue) && ue.Err != nil {
			err = ue.Err
		}
		res.ErrorKind, res.Error, res.Ms = kind, err.Error(), time.Since(start).Milliseconds()
		return res
	}
	if policy == "off" {
		return fail("denied", errors.New("Weiterleitung ist auf diesem Agent abgeschaltet (relay=off)"))
	}
	u, err := url.Parse(r.URL)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return fail("other", fmt.Errorf("ungültige Adresse: %q", r.URL))
	}
	var body io.Reader
	if r.Body != "" {
		raw, err := base64.StdEncoding.DecodeString(r.Body)
		if err != nil {
			return fail("other", fmt.Errorf("Anfrage unlesbar: %w", err))
		}
		body = strings.NewReader(string(raw))
	}
	timeout := r.Timeout
	if timeout <= 0 {
		timeout = relayDefaultTime
	}
	if timeout > relayMaxTimeout {
		timeout = relayMaxTimeout
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeout)*time.Second)
	defer cancel()
	method := strings.ToUpper(r.Method)
	if method == "" {
		method = http.MethodGet
	}
	req, err := http.NewRequestWithContext(ctx, method, u.String(), body)
	if err != nil {
		return fail("other", err)
	}
	for k, v := range r.Headers {
		switch strings.ToLower(k) {
		case "host", "content-length", "connection", "transfer-encoding", "accept-encoding":
			continue
		}
		req.Header.Set(k, v)
	}
	if req.Header.Get("User-Agent") == "" {
		req.Header.Set("User-Agent", "rimperio-agent/"+Version)
	}
	client := &http.Client{
		Transport: relayTransports(policy)[r.Insecure],
		// Weiterleitungen folgt der Server selbst — hier die Antwort so, wie sie ist.
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	resp, err := client.Do(req)
	if err != nil {
		return fail(relayErrKind(err), err)
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, relayMaxBody+1))
	if err != nil {
		return fail(relayErrKind(err), err)
	}
	if len(data) > relayMaxBody {
		return fail("other", fmt.Errorf("Antwort größer als %d MB", relayMaxBody>>20))
	}
	res.Status = resp.StatusCode
	res.Headers = map[string][]string(resp.Header)
	if len(data) > 0 {
		res.Body = base64.StdEncoding.EncodeToString(data)
	}
	if r.Cert && resp.TLS != nil && len(resp.TLS.PeerCertificates) > 0 {
		res.Cert = base64.StdEncoding.EncodeToString(resp.TLS.PeerCertificates[0].Raw)
	}
	res.Ms = time.Since(start).Milliseconds()
	return res
}

// handleRelay führt eine Anfrage nebenher aus und meldet das Ergebnis.
func (p *program) handleRelay(client *Client, r RelayRequest) {
	go func() {
		relaySem <- struct{}{} // höchstens relayParallel gleichzeitig
		defer func() { <-relaySem }()
		res := executeRelay(RelayPolicy(p.cfg), r)
		for attempt := 0; attempt < 3; attempt++ {
			err := client.SendRelayResults([]RelayResult{res})
			if err == nil {
				return
			}
			if attempt == 2 {
				logf("Weiterleitung %s: Antwort nicht zugestellt: %v", r.ID, err)
				return
			}
			time.Sleep(2 * time.Second)
		}
	}()
}
