// ---------------------------------------------------------------------------
// Datei:        snmp.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.07.12.00.00
//
// Beschreibung: SNMP-Poller. Der Server kann einem Agent Netzwerkgeräte
//               zuweisen (Switch, Drucker, USV, NAS …), die der Server selbst
//               nicht erreicht — etwa an einem anderen Standort. Der Agent
//               fragt sie im vorgegebenen Takt ab (SNMP v1, v2c oder v3 mit
//               MD5/SHA/SHA-2 und DES/AES) und schickt die Rohwerte zurück.
//               Was daraus wird (Metriken, Schwellen, Alarme), entscheidet der
//               Server anhand der Vorlage des Geräts. Geschrieben wird nie.
// ---------------------------------------------------------------------------

package core

import (
	"encoding/hex"
	"fmt"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/gosnmp/gosnmp"
)

// SnmpTask ist ein Gerät, das dieser Agent abfragen soll.
type SnmpTask struct {
	DeviceID  int      `json:"device_id"`
	Host      string   `json:"host"`
	Port      int      `json:"port"`
	Version   string   `json:"version"` // 1 | 2c | 3
	Community string   `json:"community"`
	User      string   `json:"user"`
	AuthProto string   `json:"auth_proto"` // none | md5 | sha | sha224 | sha256 | sha384 | sha512
	AuthPass  string   `json:"auth_pass"`
	PrivProto string   `json:"priv_proto"` // none | des | aes | aes192 | aes256 | aes192c | aes256c
	PrivPass  string   `json:"priv_pass"`
	Context   string   `json:"context"`
	Timeout   int      `json:"timeout"` // Sekunden je Anfrage
	Retries   int      `json:"retries"`
	Interval  int      `json:"interval"` // Sekunden zwischen zwei Abfragen
	Gets      []string `json:"gets"`
	Walks     []string `json:"walks"`
	MaxRows   int      `json:"max_rows"`
	Revision  string   `json:"revision"` // ändert sich mit der Konfiguration → sofort abfragen
}

// SnmpResult sind die Rohwerte einer Abfrage.
type SnmpResult struct {
	DeviceID int                `json:"device_id"`
	OK       bool               `json:"ok"`
	Error    string             `json:"error,omitempty"`
	RTTms    int                `json:"rtt_ms"`
	PolledAt time.Time          `json:"polled_at"`
	Values   map[string]any     `json:"values"`
	Walks    map[string][][]any `json:"walks"`
}

var authProtos = map[string]gosnmp.SnmpV3AuthProtocol{
	"md5": gosnmp.MD5, "sha": gosnmp.SHA, "sha1": gosnmp.SHA, "sha224": gosnmp.SHA224,
	"sha256": gosnmp.SHA256, "sha384": gosnmp.SHA384, "sha512": gosnmp.SHA512,
}

var privProtos = map[string]gosnmp.SnmpV3PrivProtocol{
	"des": gosnmp.DES, "aes": gosnmp.AES, "aes128": gosnmp.AES, "aes192": gosnmp.AES192,
	"aes256": gosnmp.AES256, "aes192c": gosnmp.AES192C, "aes256c": gosnmp.AES256C,
}

func snmpSession(t SnmpTask) (*gosnmp.GoSNMP, error) {
	timeout := t.Timeout
	if timeout <= 0 {
		timeout = 3
	}
	port := t.Port
	if port <= 0 {
		port = 161
	}
	g := &gosnmp.GoSNMP{
		Target: t.Host, Port: uint16(port), Timeout: time.Duration(timeout) * time.Second,
		Retries: t.Retries, MaxOids: 40, MaxRepetitions: 25, ExponentialTimeout: false,
	}
	switch t.Version {
	case "1":
		g.Version, g.Community = gosnmp.Version1, t.Community
	case "3":
		g.Version = gosnmp.Version3
		g.SecurityModel = gosnmp.UserSecurityModel
		g.ContextName = t.Context
		usm := &gosnmp.UsmSecurityParameters{UserName: t.User,
			AuthenticationProtocol: gosnmp.NoAuth, PrivacyProtocol: gosnmp.NoPriv}
		g.MsgFlags = gosnmp.NoAuthNoPriv
		if p, ok := authProtos[strings.ToLower(t.AuthProto)]; ok {
			usm.AuthenticationProtocol, usm.AuthenticationPassphrase = p, t.AuthPass
			g.MsgFlags = gosnmp.AuthNoPriv
			if q, ok := privProtos[strings.ToLower(t.PrivProto)]; ok {
				usm.PrivacyProtocol, usm.PrivacyPassphrase = q, t.PrivPass
				g.MsgFlags = gosnmp.AuthPriv
			}
		}
		g.SecurityParameters = usm
	default:
		g.Version, g.Community = gosnmp.Version2c, t.Community
	}
	if err := g.Connect(); err != nil {
		return nil, err
	}
	return g, nil
}

// snmpValue bringt einen Wert in eine JSON-taugliche, eindeutige Form:
// Zahlen bleiben Zahlen, lesbarer Text bleibt Text, alles andere wird "0x…".
func snmpValue(p gosnmp.SnmpPDU) (any, bool) {
	switch p.Type {
	case gosnmp.NoSuchObject, gosnmp.NoSuchInstance, gosnmp.EndOfMibView, gosnmp.Null:
		return nil, false
	case gosnmp.OctetString, gosnmp.Opaque:
		b, _ := p.Value.([]byte)
		return octets(b), true
	case gosnmp.ObjectIdentifier, gosnmp.IPAddress:
		return strings.TrimPrefix(fmt.Sprint(p.Value), "."), true
	case gosnmp.Counter64:
		return gosnmp.ToBigInt(p.Value).Uint64(), true
	default:
		switch v := p.Value.(type) {
		case int, int64, uint, uint32, uint64, float32, float64:
			return v, true
		case []byte:
			return octets(v), true
		default:
			return fmt.Sprint(v), true
		}
	}
}

func octets(b []byte) string {
	printable := utf8.Valid(b)
	for _, c := range b {
		if c < 0x20 && c != '\t' && c != '\n' && c != '\r' && c != 0 {
			printable = false
			break
		}
	}
	if printable {
		return strings.TrimRight(string(b), "\x00")
	}
	return "0x" + hex.EncodeToString(b)
}

func pollSnmp(t SnmpTask) SnmpResult {
	res := SnmpResult{DeviceID: t.DeviceID, PolledAt: time.Now().UTC(),
		Values: map[string]any{}, Walks: map[string][][]any{}}
	started := time.Now()
	g, err := snmpSession(t)
	if err != nil {
		res.Error = err.Error()
		return res
	}
	defer g.Conn.Close()

	for i := 0; i < len(t.Gets); i += 30 {
		end := i + 30
		if end > len(t.Gets) {
			end = len(t.Gets)
		}
		pkt, err := g.Get(t.Gets[i:end])
		if err != nil {
			res.Error = err.Error()
			res.RTTms = int(time.Since(started).Milliseconds())
			return res
		}
		for _, v := range pkt.Variables {
			if val, ok := snmpValue(v); ok {
				res.Values[strings.TrimPrefix(v.Name, ".")] = val
			}
		}
	}
	maxRows := t.MaxRows
	if maxRows <= 0 {
		maxRows = 2000
	}
	for _, base := range t.Walks {
		base = strings.TrimPrefix(base, ".")
		rows := [][]any{}
		walk := g.BulkWalk
		if t.Version == "1" {
			walk = g.Walk
		}
		werr := walk(base, func(p gosnmp.SnmpPDU) error {
			if len(rows) >= maxRows {
				return fmt.Errorf("zu viele Zeilen")
			}
			if val, ok := snmpValue(p); ok {
				suffix := strings.TrimPrefix(strings.TrimPrefix(p.Name, "."), base+".")
				rows = append(rows, []any{suffix, val})
			}
			return nil
		})
		if werr != nil && len(rows) == 0 && len(res.Values) == 0 && res.Error == "" {
			res.Error = werr.Error()
		}
		res.Walks[base] = rows
	}
	res.RTTms = int(time.Since(started).Milliseconds())
	res.OK = res.Error == "" || len(res.Values) > 0
	return res
}

// snmpScheduler merkt sich, wann welches Gerät zuletzt abgefragt wurde.
type snmpScheduler struct {
	mu   sync.Mutex
	last map[int]time.Time
	rev  map[int]string
	busy bool
}

func newSnmpScheduler() *snmpScheduler {
	return &snmpScheduler{last: map[int]time.Time{}, rev: map[int]string{}}
}

// due wählt die fälligen Geräte aus. Läuft noch eine Runde, kommt nichts.
func (s *snmpScheduler) due(tasks []SnmpTask) []SnmpTask {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.busy || len(tasks) == 0 {
		return nil
	}
	now := time.Now()
	out := []SnmpTask{}
	for _, t := range tasks {
		interval := time.Duration(t.Interval) * time.Second
		if interval < 30*time.Second {
			interval = 60 * time.Second
		}
		if last, ok := s.last[t.DeviceID]; ok && now.Sub(last) < interval && s.rev[t.DeviceID] == t.Revision {
			continue
		}
		s.last[t.DeviceID], s.rev[t.DeviceID] = now, t.Revision
		out = append(out, t)
	}
	if len(out) > 0 {
		s.busy = true
	}
	return out
}

// run fragt die Geräte nebenläufig ab (höchstens acht zugleich) und meldet.
func (s *snmpScheduler) run(client *Client, tasks []SnmpTask) {
	defer func() {
		s.mu.Lock()
		s.busy = false
		s.mu.Unlock()
	}()
	gate := make(chan struct{}, 8)
	results := make([]SnmpResult, len(tasks))
	var wg sync.WaitGroup
	for i, t := range tasks {
		wg.Add(1)
		go func(i int, t SnmpTask) {
			defer wg.Done()
			gate <- struct{}{}
			defer func() { <-gate }()
			results[i] = pollSnmp(t)
		}(i, t)
	}
	wg.Wait()
	if err := client.SendSnmpResults(results); err != nil {
		logf("SNMP-Ergebnisse senden fehlgeschlagen: %v", err)
	}
}
