// ---------------------------------------------------------------------------
// Datei:        patch_test.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.07.12.00.00
//
// Beschreibung: Tests für die Paketlisten-Parser, die Image-Zerlegung, die
//               Ausnahme-Labels und den Neuaufbau einer Container-Konfiguration.
// ---------------------------------------------------------------------------

package core

import (
	"encoding/json"
	"testing"
)

func TestParseApt(t *testing.T) {
	out := `Listing...
openssl/jammy-updates,jammy-security 3.0.2-0ubuntu1.15 amd64 [upgradable from: 3.0.2-0ubuntu1.14]
linux-image-generic/jammy-updates 5.15.0.125.125 amd64 [upgradable from: 5.15.0.124.124]
curl/stable 7.88.1-10+deb12u8 amd64 [upgradable from: 7.88.1-10+deb12u7]
`
	ups := parseApt(out)
	if len(ups) != 3 {
		t.Fatalf("3 Updates erwartet, %d bekommen", len(ups))
	}
	if !ups[0].Security || ups[0].Category != "security" || ups[0].Current != "3.0.2-0ubuntu1.14" || ups[0].Available != "3.0.2-0ubuntu1.15" {
		t.Errorf("openssl falsch zerlegt: %+v", ups[0])
	}
	if ups[1].Kind != "kernel" || !ups[1].Reboot {
		t.Errorf("Kernel nicht erkannt: %+v", ups[1])
	}
	if ups[2].Security {
		t.Errorf("curl ist kein Sicherheitsupdate: %+v", ups[2])
	}
}

func TestParseDnfWithUpdateinfo(t *testing.T) {
	sec := parseUpdateinfo("RHSA-2024:1234 Important/Sec. openssl-libs-1:3.0.7-25.el9.x86_64\nRHBA-2024:1 bugfix vim-minimal-2:9.0-1.el9.x86_64\n")
	ups := parseDnf(`Last metadata expiration check: 0:01:00 ago.
openssl-libs.x86_64    1:3.0.7-25.el9    baseos
vim-minimal.x86_64     2:9.0-1.el9       baseos
kernel.x86_64          5.14.0-427.el9    baseos
`, sec)
	if len(ups) != 3 {
		t.Fatalf("3 Updates erwartet, %d bekommen", len(ups))
	}
	if !ups[0].Security || ups[0].Severity != "Important" || ups[0].KB != "RHSA-2024:1234" || ups[0].Package != "openssl-libs" {
		t.Errorf("openssl-libs falsch: %+v", ups[0])
	}
	if ups[1].Security {
		t.Errorf("vim ist kein Sicherheitsupdate: %+v", ups[1])
	}
	if ups[2].Kind != "kernel" {
		t.Errorf("Kernel nicht erkannt: %+v", ups[2])
	}
}

func TestParseImageRef(t *testing.T) {
	cases := map[string]imageRef{
		"nginx":                            {Registry: "registry-1.docker.io", Repo: "library/nginx", Tag: "latest", PullName: "nginx"},
		"postgres:16-alpine":               {Registry: "registry-1.docker.io", Repo: "library/postgres", Tag: "16-alpine", PullName: "postgres"},
		"ghcr.io/foo/bar:1.2":              {Registry: "ghcr.io", Repo: "foo/bar", Tag: "1.2", PullName: "ghcr.io/foo/bar"},
		"localhost:5000/app":               {Registry: "localhost:5000", Repo: "app", Tag: "latest", PullName: "localhost:5000/app"},
		"docker.io/louislam/uptime-kuma:1": {Registry: "registry-1.docker.io", Repo: "louislam/uptime-kuma", Tag: "1", PullName: "docker.io/louislam/uptime-kuma"},
	}
	for in, want := range cases {
		got, ok := parseImageRef(in)
		if !ok || got != want {
			t.Errorf("%s: %+v, erwartet %+v", in, got, want)
		}
	}
	if r, _ := parseImageRef("nginx@sha256:abc"); r.Digest != "sha256:abc" {
		t.Errorf("Abdruck nicht erkannt: %+v", r)
	}
}

func TestExclusion(t *testing.T) {
	if ex, _, _ := exclusion(map[string]string{"com.centurylinklabs.watchtower.enable": "false"}); !ex {
		t.Error("Watchtower-Ausnahme nicht erkannt")
	}
	if _, _, mon := exclusion(map[string]string{"com.centurylinklabs.watchtower.monitor-only": "true"}); !mon {
		t.Error("monitor-only nicht erkannt")
	}
	if ex, _, mon := exclusion(map[string]string{}); ex || mon {
		t.Error("ohne Labels darf nichts ausgenommen sein")
	}
}

func TestCreateConfigDropsImageDefaults(t *testing.T) {
	var old, img map[string]any
	_ = json.Unmarshal([]byte(`{
	  "Id": "0123456789abcdef0000",
	  "Name": "/web",
	  "Config": {"Hostname": "0123456789ab", "Image": "nginx:latest",
	    "Env": ["PATH=/usr/bin", "NGINX_VERSION=1.25.3", "MY_SETTING=1"],
	    "Cmd": ["nginx", "-g", "daemon off;"], "Labels": {"maintainer": "x", "app": "web"},
	    "ExposedPorts": {"80/tcp": {}, "8080/tcp": {}}},
	  "HostConfig": {"NetworkMode": "appnet", "Binds": ["/srv/web:/usr/share/nginx/html:ro"]},
	  "Mounts": [{"Type": "volume", "Name": "abc123", "Destination": "/cache", "RW": true},
	             {"Type": "bind", "Source": "/srv/web", "Destination": "/usr/share/nginx/html", "RW": false}],
	  "NetworkSettings": {"Networks": {
	    "appnet": {"Aliases": ["web", "0123456789ab"]},
	    "monitoring": {"Aliases": ["web"]}}}
	}`), &old)
	_ = json.Unmarshal([]byte(`{"Config": {"Env": ["PATH=/usr/bin", "NGINX_VERSION=1.25.3"],
	  "Cmd": ["nginx", "-g", "daemon off;"], "Labels": {"maintainer": "x"}, "ExposedPorts": {"80/tcp": {}}}}`), &img)

	body, extra := createConfig(old, img, "nginx:latest")
	env := asSlice(body["Env"])
	if len(env) != 1 || str(env[0]) != "MY_SETTING=1" {
		t.Errorf("Env sollte nur die eigene Einstellung behalten: %v", env)
	}
	if _, ok := body["Cmd"]; ok {
		t.Error("Cmd des Images darf nicht festgeschrieben werden")
	}
	if _, ok := body["Hostname"]; ok {
		t.Error("automatischer Hostname darf nicht übernommen werden")
	}
	if labels := asMap(body["Labels"]); len(labels) != 1 || str(labels["app"]) != "web" {
		t.Errorf("Labels falsch: %v", labels)
	}
	if ports := asMap(body["ExposedPorts"]); len(ports) != 1 {
		t.Errorf("Ports falsch: %v", ports)
	}
	binds := asSlice(asMap(body["HostConfig"])["Binds"])
	if len(binds) != 2 || str(binds[1]) != "abc123:/cache" {
		t.Errorf("anonymes Volume nicht mitgenommen: %v", binds)
	}
	ep := asMap(asMap(asMap(body["NetworkingConfig"])["EndpointsConfig"])["appnet"])
	if aliases := asSlice(ep["Aliases"]); len(aliases) != 1 || str(aliases[0]) != "web" {
		t.Errorf("Aliase falsch: %v", aliases)
	}
	if _, ok := extra["monitoring"]; !ok || len(extra) != 1 {
		t.Errorf("zweites Netz fehlt: %v", extra)
	}
}

func TestSanitizeIDs(t *testing.T) {
	got := sanitizeIDs([]string{"openssl", "linux-image-6.1.0-27-amd64", "a; rm -rf /", "8f1c2e3a-1b2c-4d5e-8f90-123456789abc", "openssl-libs.x86_64"})
	if len(got) != 4 {
		t.Errorf("Unsichere ID durchgelassen oder sichere verworfen: %v", got)
	}
}
