// ---------------------------------------------------------------------------
// Datei:        docker_registry.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.07.12.00.00
//
// Beschreibung: Fragt eine Container-Registry, welchen Abdruck (Digest) sie für
//               einen Tag führt — ohne etwas herunterzuladen. Dazu bei Bedarf
//               die Versionsangabe (Label org.opencontainers.image.version) des
//               neuen Images, damit Freigabe und KI sehen, ob aus 1.25.3 eine
//               1.25.4 oder eine 2.0 wird. Zugangsdaten kommen, falls
//               vorhanden, aus der Docker-Konfiguration des Hosts
//               (~/.docker/config.json, Feld "auths").
// ---------------------------------------------------------------------------

package core

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"
)

type imageRef struct {
	Registry string // registry-1.docker.io, ghcr.io, …
	Repo     string // library/nginx
	Tag      string
	Digest   string // gesetzt, wenn das Image auf einen Abdruck genagelt ist
	PullName string // so, wie docker pull es erwartet (ohne Tag): nginx, ghcr.io/x/y
}

// parseImageRef zerlegt "ghcr.io/foo/bar:1" — dieselbe Logik wie im Backend.
func parseImageRef(ref string) (imageRef, bool) {
	ref = strings.TrimSpace(ref)
	if ref == "" || strings.HasPrefix(ref, "sha256:") {
		return imageRef{}, false
	}
	out := imageRef{}
	if i := strings.Index(ref, "@"); i >= 0 {
		out.Digest = ref[i+1:]
		ref = ref[:i]
	}
	remainder, tag := ref, "latest"
	if i := strings.LastIndex(ref, ":"); i > 0 && !strings.Contains(ref[i+1:], "/") {
		remainder, tag = ref[:i], ref[i+1:]
	}
	out.PullName, out.Tag = remainder, tag
	parts := strings.Split(remainder, "/")
	if len(parts) > 1 && (strings.ContainsAny(parts[0], ".:") || parts[0] == "localhost") {
		out.Registry, out.Repo = parts[0], strings.Join(parts[1:], "/")
	} else {
		out.Registry, out.Repo = "registry-1.docker.io", remainder
		if !strings.Contains(out.Repo, "/") {
			out.Repo = "library/" + out.Repo
		}
	}
	if out.Registry == "docker.io" || out.Registry == "index.docker.io" {
		out.Registry = "registry-1.docker.io"
	}
	return out, true
}

// --- Zugangsdaten -------------------------------------------------------------

type dockerAuth struct{ user, pass string }

var (
	authOnce  sync.Once
	authCache map[string]dockerAuth
)

func loadDockerAuths() map[string]dockerAuth {
	authOnce.Do(func() {
		authCache = map[string]dockerAuth{}
		candidates := []string{}
		if d := os.Getenv("DOCKER_CONFIG"); d != "" {
			candidates = append(candidates, filepath.Join(d, "config.json"))
		}
		if h, err := os.UserHomeDir(); err == nil {
			candidates = append(candidates, filepath.Join(h, ".docker", "config.json"))
		}
		candidates = append(candidates, "/root/.docker/config.json")
		for _, p := range candidates {
			raw, err := os.ReadFile(p)
			if err != nil {
				continue
			}
			var cfg struct {
				Auths map[string]struct {
					Auth     string `json:"auth"`
					Username string `json:"username"`
					Password string `json:"password"`
				} `json:"auths"`
			}
			if json.Unmarshal(raw, &cfg) != nil {
				continue
			}
			for host, a := range cfg.Auths {
				user, pass := a.Username, a.Password
				if a.Auth != "" {
					if dec, err := base64.StdEncoding.DecodeString(a.Auth); err == nil {
						if u, p, ok := strings.Cut(string(dec), ":"); ok {
							user, pass = u, p
						}
					}
				}
				if user == "" {
					continue
				}
				key := strings.TrimSuffix(strings.TrimPrefix(strings.TrimPrefix(host, "https://"), "http://"), "/v1/")
				key = strings.TrimSuffix(key, "/")
				if key == "index.docker.io" || key == "docker.io" {
					key = "registry-1.docker.io"
				}
				if _, seen := authCache[key]; !seen {
					authCache[key] = dockerAuth{user, pass}
				}
			}
		}
	})
	return authCache
}

func authFor(registry string) (dockerAuth, bool) {
	a, ok := loadDockerAuths()[registry]
	return a, ok
}

// registryAuthHeader baut X-Registry-Auth für /images/create.
func registryAuthHeader(registry string) string {
	a, ok := authFor(registry)
	if !ok {
		return ""
	}
	server := registry
	if registry == "registry-1.docker.io" {
		server = "https://index.docker.io/v1/"
	}
	b, _ := json.Marshal(map[string]string{"username": a.user, "password": a.pass, "serveraddress": server})
	return base64.URLEncoding.EncodeToString(b)
}

// --- Registry-Abfrage -----------------------------------------------------------

const manifestAccept = "application/vnd.oci.image.index.v1+json, " +
	"application/vnd.oci.image.manifest.v1+json, " +
	"application/vnd.docker.distribution.manifest.list.v2+json, " +
	"application/vnd.docker.distribution.manifest.v2+json"

var challengeParam = regexp.MustCompile(`(\w+)="([^"]*)"`)

type registryClient struct {
	http   *http.Client
	mu     sync.Mutex
	tokens map[string]string // registry|repo → Bearer-Token
}

func newRegistryClient() *registryClient {
	return &registryClient{
		http:   &http.Client{Timeout: 25 * time.Second, Transport: &http.Transport{Proxy: http.ProxyFromEnvironment}},
		tokens: map[string]string{},
	}
}

func (rc *registryClient) do(ref imageRef, method, path, accept string) (*http.Response, error) {
	u := "https://" + ref.Registry + "/v2/" + ref.Repo + path
	key := ref.Registry + "|" + ref.Repo
	send := func(auth string) (*http.Response, error) {
		req, err := http.NewRequest(method, u, nil)
		if err != nil {
			return nil, err
		}
		if accept != "" {
			req.Header.Set("Accept", accept)
		}
		req.Header.Set("User-Agent", "rimperio-agent/"+Version)
		if auth != "" {
			req.Header.Set("Authorization", auth)
		}
		return rc.http.Do(req)
	}
	rc.mu.Lock()
	tok := rc.tokens[key]
	rc.mu.Unlock()
	auth := ""
	if tok != "" {
		auth = "Bearer " + tok
	}
	resp, err := send(auth)
	if err != nil || resp.StatusCode != http.StatusUnauthorized {
		return resp, err
	}
	challenge := resp.Header.Get("WWW-Authenticate")
	resp.Body.Close()
	creds, hasCreds := authFor(ref.Registry)
	if strings.HasPrefix(strings.ToLower(challenge), "basic") {
		if !hasCreds {
			return nil, fmt.Errorf("Registry verlangt eine Anmeldung (docker login auf dem Host)")
		}
		return send("Basic " + base64.StdEncoding.EncodeToString([]byte(creds.user+":"+creds.pass)))
	}
	params := map[string]string{}
	for _, m := range challengeParam.FindAllStringSubmatch(challenge, -1) {
		params[strings.ToLower(m[1])] = m[2]
	}
	realm := params["realm"]
	if realm == "" {
		return nil, fmt.Errorf("Registry nennt keinen Anmeldeweg")
	}
	q := url.Values{}
	if s := params["service"]; s != "" {
		q.Set("service", s)
	}
	q.Set("scope", "repository:"+ref.Repo+":pull")
	treq, err := http.NewRequest(http.MethodGet, realm+"?"+q.Encode(), nil)
	if err != nil {
		return nil, err
	}
	if hasCreds {
		treq.SetBasicAuth(creds.user, creds.pass)
	}
	tresp, err := rc.http.Do(treq)
	if err != nil {
		return nil, err
	}
	defer tresp.Body.Close()
	if tresp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("kein Lesezugriff auf %s (HTTP %d)", ref.Repo, tresp.StatusCode)
	}
	var tb struct {
		Token       string `json:"token"`
		AccessToken string `json:"access_token"`
	}
	if err := json.NewDecoder(io.LimitReader(tresp.Body, 1<<20)).Decode(&tb); err != nil {
		return nil, err
	}
	tok = tb.Token
	if tok == "" {
		tok = tb.AccessToken
	}
	rc.mu.Lock()
	rc.tokens[key] = tok
	rc.mu.Unlock()
	return send("Bearer " + tok)
}

// remoteDigest: Welchen Abdruck führt die Registry für den Tag?
func (rc *registryClient) remoteDigest(ref imageRef) (string, error) {
	resp, err := rc.do(ref, http.MethodHead, "/manifests/"+ref.Tag, manifestAccept)
	if err != nil {
		return "", err
	}
	resp.Body.Close()
	if resp.StatusCode == http.StatusNotFound {
		return "", fmt.Errorf("die Registry kennt %s:%s nicht", ref.PullName, ref.Tag)
	}
	if resp.StatusCode >= 400 {
		return "", fmt.Errorf("Registry antwortet HTTP %d", resp.StatusCode)
	}
	d := resp.Header.Get("Docker-Content-Digest")
	if d == "" {
		resp, err = rc.do(ref, http.MethodGet, "/manifests/"+ref.Tag, manifestAccept)
		if err != nil {
			return "", err
		}
		resp.Body.Close()
		d = resp.Header.Get("Docker-Content-Digest")
	}
	if d == "" {
		return "", fmt.Errorf("die Registry nennt keinen Abdruck")
	}
	return d, nil
}

func (rc *registryClient) getJSON(ref imageRef, path, accept string, out any) error {
	resp, err := rc.do(ref, http.MethodGet, path, accept)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	return json.NewDecoder(io.LimitReader(resp.Body, 8<<20)).Decode(out)
}

// remoteVersion liest die Versionsangabe des neuen Images (falls gepflegt).
func (rc *registryClient) remoteVersion(ref imageRef) string {
	var m struct {
		Manifests []struct {
			Digest   string `json:"digest"`
			Platform struct {
				OS           string `json:"os"`
				Architecture string `json:"architecture"`
			} `json:"platform"`
		} `json:"manifests"`
		Config struct {
			Digest string `json:"digest"`
		} `json:"config"`
	}
	if rc.getJSON(ref, "/manifests/"+ref.Tag, manifestAccept, &m) != nil {
		return ""
	}
	if len(m.Manifests) > 0 {
		digest := ""
		for _, x := range m.Manifests {
			if x.Platform.OS == "linux" && x.Platform.Architecture == runtime.GOARCH {
				digest = x.Digest
				break
			}
		}
		if digest == "" {
			digest = m.Manifests[0].Digest
		}
		m.Manifests = nil
		if rc.getJSON(ref, "/manifests/"+digest, manifestAccept, &m) != nil {
			return ""
		}
	}
	if m.Config.Digest == "" {
		return ""
	}
	var blob struct {
		Config struct {
			Labels map[string]string `json:"Labels"`
		} `json:"config"`
	}
	if rc.getJSON(ref, "/blobs/"+m.Config.Digest, "", &blob) != nil {
		return ""
	}
	return blob.Config.Labels["org.opencontainers.image.version"]
}
