package core

import (
	"encoding/base64"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRelayTargetAllowed(t *testing.T) {
	cases := []struct {
		policy, ip string
		want       bool
	}{
		{"lan", "127.0.0.1", true},
		{"lan", "192.168.1.20", true},
		{"lan", "10.1.2.3", true},
		{"lan", "172.16.5.5", true},
		{"lan", "100.100.1.1", true}, // CGNAT / Tailscale
		{"lan", "169.254.1.1", true},
		{"lan", "fd00::1", true},
		{"lan", "::1", true},
		{"lan", "8.8.8.8", false},
		{"lan", "2001:4860:4860::8888", false},
		{"any", "8.8.8.8", true},
		{"off", "127.0.0.1", false},
	}
	for _, c := range cases {
		if got := relayTargetAllowed(c.policy, net.ParseIP(c.ip)); got != c.want {
			t.Errorf("%s %s: %v, erwartet %v", c.policy, c.ip, got, c.want)
		}
	}
}

func TestRelayPolicy(t *testing.T) {
	t.Setenv("RIMPERIO_RELAY", "")
	if p := RelayPolicy(&Config{}); p != "lan" {
		t.Fatalf("Standard: %s", p)
	}
	if p := RelayPolicy(&Config{Relay: "OFF"}); p != "off" {
		t.Fatalf("off: %s", p)
	}
	if len(Capabilities(&Config{Relay: "off"})) != 0 {
		t.Fatal("relay=off darf nichts ankündigen")
	}
	t.Setenv("RIMPERIO_RELAY", "any")
	if p := RelayPolicy(&Config{Relay: "off"}); p != "any" {
		t.Fatalf("Umgebung gewinnt: %s", p)
	}
}

func TestExecuteRelayTLS(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if r.URL.Path == "/redirect" {
			http.Redirect(w, r, "/elsewhere", http.StatusFound)
			return
		}
		w.Header().Add("Set-Cookie", "a=1")
		w.Header().Add("Set-Cookie", "b=2")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"method":"` + r.Method + `","body":"` + string(body) + `","x":"` + r.Header.Get("X-SYNO-TOKEN") + `"}`))
	}))
	defer srv.Close()

	res := executeRelay("lan", RelayRequest{ID: "1", Method: "POST", URL: srv.URL + "/webapi/entry.cgi",
		Headers: map[string]string{"X-SYNO-TOKEN": "tok", "Host": "falsch"}, Body: base64.StdEncoding.EncodeToString([]byte("api=x")),
		Timeout: 5, Insecure: true, Cert: true})
	if res.Error != "" || res.Status != 200 {
		t.Fatalf("Fehler: %+v", res)
	}
	raw, _ := base64.StdEncoding.DecodeString(res.Body)
	if !strings.Contains(string(raw), `"method":"POST"`) || !strings.Contains(string(raw), `"body":"api=x"`) || !strings.Contains(string(raw), `"x":"tok"`) {
		t.Fatalf("Antwort: %s", raw)
	}
	if len(res.Headers["Set-Cookie"]) != 2 {
		t.Fatalf("mehrfache Kopfzeilen verloren: %v", res.Headers)
	}
	if res.Cert == "" {
		t.Fatal("Zertifikat fehlt")
	}

	// Ohne "insecure": Selbstzertifikat abgelehnt, als tls erkannt.
	res = executeRelay("lan", RelayRequest{ID: "2", Method: "GET", URL: srv.URL + "/", Timeout: 5})
	if res.ErrorKind != "tls" {
		t.Fatalf("erwartet tls, war %q (%s)", res.ErrorKind, res.Error)
	}

	// Weiterleitungen nicht selbst folgen.
	res = executeRelay("lan", RelayRequest{ID: "3", Method: "GET", URL: srv.URL + "/redirect", Timeout: 5, Insecure: true})
	if res.Status != http.StatusFound {
		t.Fatalf("Redirect: %+v", res)
	}
}

func TestExecuteRelayErrors(t *testing.T) {
	// Port, auf dem nichts lauscht.
	l, _ := net.Listen("tcp", "127.0.0.1:0")
	addr := l.Addr().String()
	l.Close()
	res := executeRelay("lan", RelayRequest{ID: "c", URL: "http://" + addr + "/", Timeout: 3})
	if res.ErrorKind != "connect" {
		t.Fatalf("connect erwartet: %+v", res)
	}
	res = executeRelay("lan", RelayRequest{ID: "d", URL: "http://8.8.8.8/", Timeout: 3})
	if res.ErrorKind != "denied" {
		t.Fatalf("denied erwartet: %+v", res)
	}
	res = executeRelay("off", RelayRequest{ID: "e", URL: "http://127.0.0.1/", Timeout: 3})
	if res.ErrorKind != "denied" {
		t.Fatalf("off: %+v", res)
	}
	res = executeRelay("lan", RelayRequest{ID: "f", URL: "file:///etc/passwd"})
	if res.ErrorKind != "other" {
		t.Fatalf("Schema: %+v", res)
	}
}

func TestDetectSynology(t *testing.T) {
	root := t.TempDir()
	must := func(p, s string) {
		if err := os.MkdirAll(filepath.Dir(filepath.Join(root, p)), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(root, p), []byte(s), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	if detectSynology(root) != nil {
		t.Fatal("ohne VERSION kein DSM")
	}
	must("etc.defaults/VERSION", "majorversion=\"7\"\nminorversion=\"2\"\nproductversion=\"7.2.1\"\nbuildnumber=\"69057\"\n")
	must("etc.defaults/synoinfo.conf", "upnpmodelname=\"DS920+\"\n")
	a := detectSynology(root)
	if a == nil || a.Model != "DS920+" || a.Version != "7.2.1" || a.Build != "69057" {
		t.Fatalf("%+v", a)
	}
	if a.OSName() != "Synology DSM 7.2.1-69057" {
		t.Fatal(a.OSName())
	}
}
