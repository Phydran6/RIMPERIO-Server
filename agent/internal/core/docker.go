// ---------------------------------------------------------------------------
// Datei:        docker.go
// Projekt:      RIMPERIO — Agent
// Paket:        core
// Autor:        Phydran6
// Version:      2026.11.07.12.00.00
//
// Beschreibung: Container-Updates auf dem Host — das, was Watchtower und
//               Tugtainer tun, gesteuert vom Server:
//
//                 docker_scan    Alle Container, ihr Image, ob die Registry
//                                für den Tag ein neueres Image führt, und
//                                welche Version es trägt. Nichts wird geholt.
//                 docker_update  Für die genannten Container: neues Image
//                                holen, Container mit derselben Konfiguration
//                                neu erzeugen, auf „gesund“ warten. Klappt das
//                                nicht, läuft der alte Container wieder.
//
//               Compose-Container (Labels com.docker.compose.*) werden, wenn
//               „docker compose“ vorhanden ist, über Compose selbst erneuert;
//               alle anderen über die Docker-API (Konfiguration übernehmen wie
//               Watchtower, ohne die Vorgaben des alten Images festzuschreiben).
//
//               Ausgenommen sind Container mit
//                 com.centurylinklabs.watchtower.enable=false
//                 de.rimperio.update=off|none|source
//               und mit ...watchtower.monitor-only=true nur beobachtet.
//
//               Angesprochen wird der Docker-Socket (/var/run/docker.sock)
//               bzw. DOCKER_HOST=unix://… oder tcp://….
// ---------------------------------------------------------------------------

package core

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"
)

// --- Docker-API ------------------------------------------------------------------

type dockerClient struct {
	http *http.Client
	base string
}

func newDockerClient() (*dockerClient, error) {
	host := strings.TrimSpace(os.Getenv("DOCKER_HOST"))
	tr := &http.Transport{}
	base := "http://docker"
	switch {
	case host == "" || strings.HasPrefix(host, "unix://"):
		if host == "" && runtime.GOOS == "windows" {
			return nil, fmt.Errorf("Docker unter Windows wird nur über DOCKER_HOST=tcp://… unterstützt")
		}
		path := "/var/run/docker.sock"
		if host != "" {
			path = strings.TrimPrefix(host, "unix://")
		}
		if _, err := os.Stat(path); err != nil {
			return nil, fmt.Errorf("kein Docker auf diesem Rechner (%s fehlt)", path)
		}
		tr.DialContext = func(ctx context.Context, _, _ string) (net.Conn, error) {
			var d net.Dialer
			return d.DialContext(ctx, "unix", path)
		}
	case strings.HasPrefix(host, "tcp://"):
		base = "http://" + strings.TrimPrefix(host, "tcp://")
		tr.Proxy = nil
	default:
		return nil, fmt.Errorf("DOCKER_HOST %q wird nicht unterstützt", host)
	}
	return &dockerClient{http: &http.Client{Transport: tr}, base: base}, nil
}

func (d *dockerClient) do(method, path string, body any, hdr map[string]string, timeout time.Duration) (int, []byte, error) {
	var reader io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			return 0, nil, err
		}
		reader = bytes.NewReader(raw)
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, method, d.base+path, reader)
	if err != nil {
		return 0, nil, err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	resp, err := d.http.Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	return resp.StatusCode, data, nil
}

func dockerErr(status int, data []byte) error {
	var m struct {
		Message string `json:"message"`
	}
	if json.Unmarshal(data, &m) == nil && m.Message != "" {
		return fmt.Errorf("Docker: %s", m.Message)
	}
	return fmt.Errorf("Docker antwortet HTTP %d", status)
}

type dockerListItem struct {
	ID      string            `json:"Id"`
	Names   []string          `json:"Names"`
	Image   string            `json:"Image"`
	ImageID string            `json:"ImageID"`
	State   string            `json:"State"`
	Status  string            `json:"Status"`
	Labels  map[string]string `json:"Labels"`
	Created int64             `json:"Created"`
}

func (c dockerListItem) name() string {
	if len(c.Names) > 0 {
		return strings.TrimPrefix(c.Names[0], "/")
	}
	if len(c.ID) >= 12 {
		return c.ID[:12]
	}
	return c.ID
}

func (d *dockerClient) list(all bool) ([]dockerListItem, error) {
	path := "/containers/json"
	if all {
		path += "?all=1"
	}
	st, data, err := d.do(http.MethodGet, path, nil, nil, 30*time.Second)
	if err != nil {
		return nil, err
	}
	if st != http.StatusOK {
		return nil, dockerErr(st, data)
	}
	var out []dockerListItem
	return out, json.Unmarshal(data, &out)
}

func (d *dockerClient) inspect(kind, id string) (map[string]any, error) {
	st, data, err := d.do(http.MethodGet, "/"+kind+"/"+url.PathEscape(id)+"/json", nil, nil, 30*time.Second)
	if err != nil {
		return nil, err
	}
	if st != http.StatusOK {
		return nil, dockerErr(st, data)
	}
	var out map[string]any
	return out, json.Unmarshal(data, &out)
}

func (d *dockerClient) post(path string, body any, ok ...int) ([]byte, error) {
	st, data, err := d.do(http.MethodPost, path, body, nil, 5*time.Minute)
	if err != nil {
		return nil, err
	}
	if st >= 200 && st < 300 {
		return data, nil
	}
	for _, o := range ok {
		if st == o {
			return data, nil
		}
	}
	return nil, dockerErr(st, data)
}

// pull holt ein Image. Die Antwort ist ein Strom von JSON-Zeilen; ein Fehler
// steht mitten darin, nicht im Statuscode.
func (d *dockerClient) pull(ref imageRef) error {
	q := url.Values{}
	q.Set("fromImage", ref.PullName)
	q.Set("tag", ref.Tag)
	hdr := map[string]string{}
	if a := registryAuthHeader(ref.Registry); a != "" {
		hdr["X-Registry-Auth"] = a
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, d.base+"/images/create?"+q.Encode(), nil)
	if err != nil {
		return err
	}
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	resp, err := d.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		data, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
		return dockerErr(resp.StatusCode, data)
	}
	sc := bufio.NewScanner(resp.Body)
	sc.Buffer(make([]byte, 0, 64*1024), 4<<20)
	for sc.Scan() {
		var line struct {
			Error string `json:"error"`
		}
		if json.Unmarshal(sc.Bytes(), &line) == nil && line.Error != "" {
			return fmt.Errorf("Image holen: %s", line.Error)
		}
	}
	return sc.Err()
}

// --- kleine Helfer für die generischen JSON-Strukturen ----------------------------

func str(v any) string {
	s, _ := v.(string)
	return s
}

func asMap(v any) map[string]any {
	m, _ := v.(map[string]any)
	if m == nil {
		return map[string]any{}
	}
	return m
}

func asSlice(v any) []any {
	s, _ := v.([]any)
	return s
}

func jsonEqual(a, b any) bool {
	x, _ := json.Marshal(a)
	y, _ := json.Marshal(b)
	return bytes.Equal(x, y)
}

func containsAny(s string, subs ...string) bool {
	for _, x := range subs {
		if strings.Contains(s, x) {
			return true
		}
	}
	return false
}

func stringLabels(v any) map[string]string {
	out := map[string]string{}
	for k, x := range asMap(v) {
		out[k] = str(x)
	}
	return out
}

// exclusion sagt, ob ein Container ausgenommen ist bzw. nur beobachtet wird.
func exclusion(labels map[string]string) (excluded bool, reason string, monitorOnly bool) {
	if strings.EqualFold(labels["com.centurylinklabs.watchtower.enable"], "false") {
		return true, "Label com.centurylinklabs.watchtower.enable=false", false
	}
	switch strings.ToLower(labels["de.rimperio.update"]) {
	case "off", "none", "false":
		return true, "Label de.rimperio.update=" + labels["de.rimperio.update"], false
	case "source":
		return true, "RIMPERIO-eigener Container (wird aus dem Quellcode gebaut)", false
	}
	if strings.EqualFold(labels["com.centurylinklabs.watchtower.monitor-only"], "true") ||
		strings.EqualFold(labels["de.rimperio.update"], "monitor") {
		return false, "", true
	}
	return false, "", false
}

// --- Scan ------------------------------------------------------------------------

type DockerContainer struct {
	ID              string            `json:"id"`
	Name            string            `json:"name"`
	Image           string            `json:"image"`
	ImageID         string            `json:"image_id"`
	State           string            `json:"state"`
	Status          string            `json:"status"`
	Health          string            `json:"health,omitempty"`
	ComposeProject  string            `json:"compose_project,omitempty"`
	ComposeService  string            `json:"compose_service,omitempty"`
	LocalDigest     string            `json:"local_digest,omitempty"`
	RemoteDigest    string            `json:"remote_digest,omitempty"`
	LocalVersion    string            `json:"local_version,omitempty"`
	RemoteVersion   string            `json:"remote_version,omitempty"`
	UpdateAvailable bool              `json:"update_available"`
	Pinned          bool              `json:"pinned,omitempty"`
	Excluded        bool              `json:"excluded,omitempty"`
	ExcludeReason   string            `json:"exclude_reason,omitempty"`
	MonitorOnly     bool              `json:"monitor_only,omitempty"`
	Error           string            `json:"error,omitempty"`
	Created         int64             `json:"created"`
	Labels          map[string]string `json:"labels,omitempty"`
}

type DockerScanResult struct {
	Available     bool              `json:"available"`
	EngineVersion string            `json:"engine_version,omitempty"`
	Compose       bool              `json:"compose"`
	Containers    []DockerContainer `json:"containers"`
	Error         string            `json:"error,omitempty"`
}

func relevantLabels(l map[string]string) map[string]string {
	out := map[string]string{}
	for k, v := range l {
		if strings.HasPrefix(k, "de.rimperio.") || strings.HasPrefix(k, "com.centurylinklabs.watchtower.") ||
			k == "org.opencontainers.image.source" || k == "org.opencontainers.image.version" {
			out[k] = v
		}
	}
	return out
}

func composeAvailable() bool {
	if !hasCmd("docker") {
		return false
	}
	return exec.Command("docker", "compose", "version").Run() == nil
}

func dockerScan() (DockerScanResult, error) {
	res := DockerScanResult{Containers: []DockerContainer{}}
	dc, err := newDockerClient()
	if err != nil {
		res.Error = err.Error()
		return res, nil // kein Fehler des Auftrags: der Rechner hat schlicht kein Docker
	}
	if st, data, err := dc.do(http.MethodGet, "/version", nil, nil, 15*time.Second); err == nil && st == 200 {
		var v struct {
			Version string `json:"Version"`
		}
		_ = json.Unmarshal(data, &v)
		res.EngineVersion = v.Version
	} else {
		res.Error = "Docker ist nicht erreichbar"
		if err != nil {
			res.Error += ": " + err.Error()
		}
		return res, nil
	}
	res.Available = true
	res.Compose = composeAvailable()

	items, err := dc.list(true)
	if err != nil {
		return res, err
	}
	rc := newRegistryClient()
	type check struct {
		digest, version, err string
	}
	cache := map[string]*check{}
	var mu sync.Mutex
	gate := make(chan struct{}, 4)
	var wg sync.WaitGroup

	out := make([]DockerContainer, len(items))
	for i, it := range items {
		c := DockerContainer{ID: it.ID, Name: it.name(), ImageID: it.ImageID, State: it.State,
			Status: it.Status, Created: it.Created, Labels: relevantLabels(it.Labels),
			ComposeProject: it.Labels["com.docker.compose.project"],
			ComposeService: it.Labels["com.docker.compose.service"]}
		c.Excluded, c.ExcludeReason, c.MonitorOnly = exclusion(it.Labels)
		if strings.EqualFold(it.Labels["de.rimperio.origin"], "rimperio") && !c.Excluded {
			c.Excluded, c.ExcludeReason = true, "RIMPERIO-eigener Container (wird aus dem Quellcode gebaut)"
		}
		full, ierr := dc.inspect("containers", it.ID)
		if ierr == nil {
			c.Image = str(asMap(full["Config"])["Image"])
			c.Health = str(asMap(asMap(full["State"])["Health"])["Status"])
		}
		if c.Image == "" {
			c.Image = it.Image
		}
		img, _ := dc.inspect("images", it.ImageID)
		c.LocalVersion = stringLabels(asMap(img["Config"])["Labels"])["org.opencontainers.image.version"]
		ref, ok := parseImageRef(c.Image)
		if !ok {
			c.Error = "Image ohne Namen — nicht prüfbar"
			out[i] = c
			continue
		}
		if ref.Digest != "" {
			c.Pinned = true
			c.Error = "auf einen festen Abdruck genagelt"
			out[i] = c
			continue
		}
		for _, rd := range asSlice(img["RepoDigests"]) {
			s := str(rd)
			if at := strings.Index(s, "@"); at > 0 {
				if c.LocalDigest == "" || strings.HasSuffix(ref.PullName, s[:at]) || strings.HasSuffix(s[:at], ref.Repo) {
					c.LocalDigest = s[at+1:]
				}
			}
		}
		if c.LocalDigest == "" {
			c.Error = "lokal gebautes Image ohne Registry-Abdruck — nicht prüfbar"
			out[i] = c
			continue
		}
		out[i] = c
		wg.Add(1)
		go func(i int, ref imageRef, image string) {
			defer wg.Done()
			gate <- struct{}{}
			defer func() { <-gate }()
			mu.Lock()
			ch, seen := cache[image]
			if !seen {
				ch = &check{}
				cache[image] = ch
			}
			mu.Unlock()
			if !seen {
				if d, err := rc.remoteDigest(ref); err != nil {
					ch.err = err.Error()
				} else {
					ch.digest = d
				}
			}
			mu.Lock()
			defer mu.Unlock()
			c := &out[i]
			c.RemoteDigest, c.Error = ch.digest, ch.err
			if ch.digest == "" {
				return
			}
			repoDigests := []string{}
			if img, err := dc.inspect("images", c.ImageID); err == nil {
				for _, rd := range asSlice(img["RepoDigests"]) {
					repoDigests = append(repoDigests, str(rd))
				}
			}
			found := false
			for _, rd := range repoDigests {
				if strings.HasSuffix(rd, "@"+ch.digest) {
					found = true
				}
			}
			c.UpdateAvailable = !found
		}(i, ref, c.Image)
	}
	wg.Wait()

	// Versionen nur für Container mit Update nachschlagen (kostet 2–3 Abfragen).
	versions := map[string]string{}
	for i := range out {
		c := &out[i]
		if !c.UpdateAvailable {
			continue
		}
		if v, ok := versions[c.Image]; ok {
			c.RemoteVersion = v
			continue
		}
		if ref, ok := parseImageRef(c.Image); ok {
			versions[c.Image] = rc.remoteVersion(ref)
			c.RemoteVersion = versions[c.Image]
		}
	}
	sort.Slice(out, func(a, b int) bool { return out[a].Name < out[b].Name })
	res.Containers = out
	return res, nil
}

// --- Update ----------------------------------------------------------------------

type DockerUpdateRequest struct {
	Containers    []string `json:"containers"`     // Containernamen
	Cleanup       bool     `json:"cleanup"`        // altes Image danach entfernen
	HealthTimeout int      `json:"health_timeout"` // Sekunden
	Rollback      bool     `json:"rollback"`       // bei Fehlschlag alten Container zurück
	PreferCompose bool     `json:"prefer_compose"`
	StopTimeout   int      `json:"stop_timeout"`
}

type DockerUpdateOutcome struct {
	Name       string `json:"name"`
	Status     string `json:"status"` // updated | unchanged | failed | rolled_back | skipped
	Method     string `json:"method,omitempty"`
	Message    string `json:"message"`
	OldImageID string `json:"old_image_id,omitempty"`
	NewImageID string `json:"new_image_id,omitempty"`
	OldVersion string `json:"old_version,omitempty"`
	NewVersion string `json:"new_version,omitempty"`
}

type DockerUpdateResult struct {
	Results []DockerUpdateOutcome `json:"results"`
	Message string                `json:"message"`
}

func dockerUpdate(req DockerUpdateRequest) (*DockerUpdateResult, error) {
	dc, err := newDockerClient()
	if err != nil {
		return nil, err
	}
	if req.HealthTimeout <= 0 {
		req.HealthTimeout = 120
	}
	if req.StopTimeout <= 0 {
		req.StopTimeout = 30
	}
	useCompose := req.PreferCompose && composeAvailable()
	res := &DockerUpdateResult{Results: []DockerUpdateOutcome{}}
	failed := 0
	for _, name := range req.Containers {
		o := dc.updateOne(strings.TrimSpace(name), req, useCompose)
		if o.Status == "failed" || o.Status == "rolled_back" {
			failed++
		}
		logf("Docker-Update %s: %s — %s", o.Name, o.Status, o.Message)
		res.Results = append(res.Results, o)
	}
	res.Message = fmt.Sprintf("%d Container bearbeitet, %d mit Problem.", len(res.Results), failed)
	if failed > 0 && failed == len(res.Results) {
		return res, fmt.Errorf("kein Container erfolgreich aktualisiert")
	}
	return res, nil
}

func (d *dockerClient) updateOne(name string, req DockerUpdateRequest, useCompose bool) DockerUpdateOutcome {
	o := DockerUpdateOutcome{Name: name, Status: "failed"}
	old, err := d.inspect("containers", name)
	if err != nil {
		o.Message = "Container nicht gefunden: " + err.Error()
		return o
	}
	cfg := asMap(old["Config"])
	labels := stringLabels(cfg["Labels"])
	if ex, reason, mon := exclusion(labels); ex || mon || strings.EqualFold(labels["de.rimperio.origin"], "rimperio") {
		o.Status = "skipped"
		o.Message = "ausgenommen"
		if reason != "" {
			o.Message += ": " + reason
		}
		return o
	}
	image := str(cfg["Image"])
	ref, ok := parseImageRef(image)
	if !ok || ref.Digest != "" {
		o.Status, o.Message = "skipped", "Image ist festgenagelt oder ohne Namen"
		return o
	}
	o.OldImageID = str(old["Image"])
	oldImg, _ := d.inspect("images", o.OldImageID)
	o.OldVersion = stringLabels(asMap(oldImg["Config"])["Labels"])["org.opencontainers.image.version"]

	if err := d.pull(ref); err != nil {
		o.Message = err.Error()
		return o
	}
	newImg, err := d.inspect("images", image)
	if err != nil {
		o.Message = "neues Image nicht lesbar: " + err.Error()
		return o
	}
	o.NewImageID = str(newImg["Id"])
	o.NewVersion = stringLabels(asMap(newImg["Config"])["Labels"])["org.opencontainers.image.version"]
	if o.NewImageID == o.OldImageID {
		o.Status, o.Message = "unchanged", "Image war bereits aktuell"
		return o
	}

	project, service := labels["com.docker.compose.project"], labels["com.docker.compose.service"]
	workdir := labels["com.docker.compose.project.working_dir"]
	if useCompose && project != "" && service != "" && workdir != "" {
		if st, err := os.Stat(workdir); err == nil && st.IsDir() {
			return d.updateCompose(o, ref, labels, req)
		}
	}
	return d.updateAPI(o, old, oldImg, image, req)
}

// --- Weg 1: Compose ---------------------------------------------------------------

func composeArgs(labels map[string]string, more ...string) []string {
	args := []string{"compose", "--project-directory", labels["com.docker.compose.project.working_dir"],
		"-p", labels["com.docker.compose.project"]}
	for _, f := range strings.Split(labels["com.docker.compose.project.config_files"], ",") {
		f = strings.TrimSpace(f)
		if f == "" {
			continue
		}
		if _, err := os.Stat(f); err == nil {
			args = append(args, "-f", f)
		}
	}
	return append(args, more...)
}

func runDocker(timeout time.Duration, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, "docker", args...)
	out, err := cmd.CombinedOutput()
	return truncate(string(out), 20_000), err
}

func (d *dockerClient) composeContainer(labels map[string]string) (string, error) {
	items, err := d.list(true)
	if err != nil {
		return "", err
	}
	for _, it := range items {
		if it.Labels["com.docker.compose.project"] == labels["com.docker.compose.project"] &&
			it.Labels["com.docker.compose.service"] == labels["com.docker.compose.service"] {
			return it.ID, nil
		}
	}
	return "", fmt.Errorf("Compose hat keinen Container für den Dienst erzeugt")
}

func (d *dockerClient) updateCompose(o DockerUpdateOutcome, ref imageRef, labels map[string]string, req DockerUpdateRequest) DockerUpdateOutcome {
	o.Method = "compose"
	svc := labels["com.docker.compose.service"]
	if out, err := runDocker(45*time.Minute, composeArgs(labels, "up", "-d", "--no-deps", svc)...); err != nil {
		o.Message = "docker compose up: " + strings.TrimSpace(out)
		return d.composeRollback(o, ref, labels, req)
	}
	id, err := d.composeContainer(labels)
	if err != nil {
		o.Message = err.Error()
		return d.composeRollback(o, ref, labels, req)
	}
	if ok, why := d.waitHealthy(id, req.HealthTimeout); !ok {
		o.Message = why
		return d.composeRollback(o, ref, labels, req)
	}
	if req.Cleanup {
		d.removeImage(o.OldImageID)
	}
	o.Status, o.Message = "updated", "über docker compose neu erzeugt"
	return o
}

// composeRollback: das alte Image wieder unter dem Tag ablegen und den Dienst
// damit neu erzeugen lassen.
func (d *dockerClient) composeRollback(o DockerUpdateOutcome, ref imageRef, labels map[string]string, req DockerUpdateRequest) DockerUpdateOutcome {
	if !req.Rollback {
		return o
	}
	q := url.Values{}
	q.Set("repo", ref.PullName)
	q.Set("tag", ref.Tag)
	if _, err := d.post("/images/"+url.PathEscape(o.OldImageID)+"/tag?"+q.Encode(), nil); err != nil {
		o.Message += " | Zurückrollen nicht möglich: " + err.Error()
		return o
	}
	out, err := runDocker(30*time.Minute, composeArgs(labels, "up", "-d", "--no-deps", "--force-recreate",
		labels["com.docker.compose.service"])...)
	if err != nil {
		o.Message += " | Zurückrollen fehlgeschlagen: " + strings.TrimSpace(out)
		return o
	}
	o.Status = "rolled_back"
	o.Message += " | alter Stand wiederhergestellt"
	return o
}

// --- Weg 2: Docker-API -----------------------------------------------------------

func subtractList(have, image any) []any {
	drop := map[string]bool{}
	for _, x := range asSlice(image) {
		drop[str(x)] = true
	}
	out := []any{}
	for _, x := range asSlice(have) {
		if !drop[str(x)] {
			out = append(out, x)
		}
	}
	return out
}

func subtractKeys(have, image any) map[string]any {
	img := asMap(image)
	out := map[string]any{}
	for k, v := range asMap(have) {
		if _, inImage := img[k]; !inImage {
			out[k] = v
		}
	}
	return out
}

// createConfig baut aus der alten Konfiguration die neue — ohne die Vorgaben
// des alten Images festzuschreiben (sonst bliebe etwa VERSION=1.2 aus dem alten
// Image ewig stehen).
func createConfig(old, oldImg map[string]any, image string) (body map[string]any, extraNets map[string]map[string]any) {
	id := str(old["Id"])
	cfg := map[string]any{}
	for k, v := range asMap(old["Config"]) {
		cfg[k] = v
	}
	imgCfg := asMap(oldImg["Config"])
	cfg["Image"] = image
	cfg["Env"] = subtractList(cfg["Env"], imgCfg["Env"])
	for _, k := range []string{"Cmd", "Entrypoint", "WorkingDir", "User", "StopSignal", "Healthcheck", "OnBuild", "Shell"} {
		if v, ok := cfg[k]; ok && jsonEqual(v, imgCfg[k]) {
			delete(cfg, k)
		}
	}
	cfg["ExposedPorts"] = subtractKeys(cfg["ExposedPorts"], imgCfg["ExposedPorts"])
	cfg["Volumes"] = subtractKeys(cfg["Volumes"], imgCfg["Volumes"])
	imgLabels := asMap(imgCfg["Labels"])
	labels := map[string]any{}
	for k, v := range asMap(cfg["Labels"]) {
		if iv, ok := imgLabels[k]; !ok || str(iv) != str(v) {
			labels[k] = v
		}
	}
	cfg["Labels"] = labels
	if len(id) >= 12 && str(cfg["Hostname"]) == id[:12] {
		delete(cfg, "Hostname")
	}

	host := map[string]any{}
	for k, v := range asMap(old["HostConfig"]) {
		host[k] = v
	}
	// Anonyme Volumes mitnehmen — sonst bekäme der neue Container leere.
	covered := map[string]bool{}
	for _, b := range asSlice(host["Binds"]) {
		parts := strings.Split(str(b), ":")
		if len(parts) >= 2 {
			covered[parts[1]] = true
		}
	}
	for _, m := range asSlice(host["Mounts"]) {
		covered[str(asMap(m)["Target"])] = true
	}
	binds := asSlice(host["Binds"])
	for _, m := range asSlice(old["Mounts"]) {
		mm := asMap(m)
		dst := str(mm["Destination"])
		if str(mm["Type"]) == "volume" && str(mm["Name"]) != "" && !covered[dst] {
			b := str(mm["Name"]) + ":" + dst
			if rw, ok := mm["RW"].(bool); ok && !rw {
				b += ":ro"
			}
			binds = append(binds, b)
		}
	}
	if len(binds) > 0 {
		host["Binds"] = binds
	}

	body = cfg
	body["HostConfig"] = host
	extraNets = map[string]map[string]any{}
	mode := str(host["NetworkMode"])
	if mode == "host" || mode == "none" || strings.HasPrefix(mode, "container:") {
		return body, extraNets
	}
	nets := asMap(asMap(old["NetworkSettings"])["Networks"])
	names := make([]string, 0, len(nets))
	for n := range nets {
		names = append(names, n)
	}
	sort.Strings(names)
	first := ""
	if _, ok := nets[mode]; ok {
		first = mode
	} else if len(names) > 0 {
		first = names[0]
	}
	endpoint := func(n string) map[string]any {
		ep := asMap(nets[n])
		out := map[string]any{}
		if v := ep["IPAMConfig"]; v != nil {
			out["IPAMConfig"] = v
		}
		if v := ep["Links"]; v != nil {
			out["Links"] = v
		}
		if v := ep["DriverOpts"]; v != nil {
			out["DriverOpts"] = v
		}
		if n != "bridge" {
			aliases := []any{}
			for _, a := range asSlice(ep["Aliases"]) {
				if len(id) >= 12 && str(a) == id[:12] {
					continue
				}
				aliases = append(aliases, a)
			}
			if len(aliases) > 0 {
				out["Aliases"] = aliases
			}
		}
		return out
	}
	if first != "" {
		body["NetworkingConfig"] = map[string]any{"EndpointsConfig": map[string]any{first: endpoint(first)}}
	}
	for _, n := range names {
		if n != first {
			extraNets[n] = endpoint(n)
		}
	}
	return body, extraNets
}

func (d *dockerClient) updateAPI(o DockerUpdateOutcome, old, oldImg map[string]any, image string, req DockerUpdateRequest) DockerUpdateOutcome {
	o.Method = "api"
	id := str(old["Id"])
	name := strings.TrimPrefix(str(old["Name"]), "/")
	wasRunning, _ := asMap(old["State"])["Running"].(bool)
	body, extra := createConfig(old, oldImg, image)
	backup := name + "-rimperio-old"

	// Ein Überbleibsel eines abgebrochenen Laufs zuerst wegräumen.
	if leftover, err := d.inspect("containers", backup); err == nil {
		running, _ := asMap(leftover["State"])["Running"].(bool)
		if running {
			o.Message = "ein laufender Container " + backup + " steht im Weg"
			return o
		}
		_, _, _ = d.do(http.MethodDelete, "/containers/"+url.PathEscape(backup)+"?force=1", nil, nil, time.Minute)
	}

	restore := func(why string) DockerUpdateOutcome {
		_, _ = d.post("/containers/"+url.PathEscape(id)+"/rename?name="+url.QueryEscape(name), nil)
		if wasRunning {
			_, _ = d.post("/containers/"+url.PathEscape(id)+"/start", nil, http.StatusNotModified)
		}
		o.Message = why
		if req.Rollback {
			o.Status = "rolled_back"
			o.Message += " | alter Container läuft wieder"
		}
		return o
	}

	if wasRunning {
		if _, err := d.post(fmt.Sprintf("/containers/%s/stop?t=%d", url.PathEscape(id), req.StopTimeout), nil, http.StatusNotModified); err != nil {
			o.Message = "alter Container lässt sich nicht stoppen: " + err.Error()
			return o
		}
	}
	if _, err := d.post("/containers/"+url.PathEscape(id)+"/rename?name="+url.QueryEscape(backup), nil); err != nil {
		if wasRunning {
			_, _ = d.post("/containers/"+url.PathEscape(id)+"/start", nil, http.StatusNotModified)
		}
		o.Message = "Umbenennen fehlgeschlagen: " + err.Error()
		return o
	}
	data, err := d.post("/containers/create?name="+url.QueryEscape(name), body)
	if err != nil {
		return restore("neuer Container nicht angelegt: " + err.Error())
	}
	var created struct {
		ID string `json:"Id"`
	}
	_ = json.Unmarshal(data, &created)
	dropNew := func() {
		_, _, _ = d.do(http.MethodDelete, "/containers/"+url.PathEscape(created.ID)+"?force=1", nil, nil, time.Minute)
	}
	for n, ep := range extra {
		if _, err := d.post("/networks/"+url.PathEscape(n)+"/connect", map[string]any{"Container": created.ID, "EndpointConfig": ep}); err != nil {
			dropNew()
			return restore("Netz " + n + " nicht verbunden: " + err.Error())
		}
	}
	if wasRunning {
		if _, err := d.post("/containers/"+url.PathEscape(created.ID)+"/start", nil); err != nil {
			dropNew()
			return restore("neuer Container startet nicht: " + err.Error())
		}
		if ok, why := d.waitHealthy(created.ID, req.HealthTimeout); !ok {
			if req.Rollback {
				dropNew()
				return restore(why)
			}
			o.Message = why + " (ohne Zurückrollen, alter Container bleibt als " + backup + " liegen)"
			return o
		}
	}
	_, _, _ = d.do(http.MethodDelete, "/containers/"+url.PathEscape(id)+"?force=1", nil, nil, time.Minute)
	if req.Cleanup {
		d.removeImage(o.OldImageID)
	}
	o.Status, o.Message = "updated", "mit neuem Image neu erzeugt"
	return o
}

func (d *dockerClient) removeImage(id string) {
	if id == "" {
		return
	}
	// 409 = noch von einem anderen Container benutzt — dann bleibt es.
	_, _, _ = d.do(http.MethodDelete, "/images/"+url.PathEscape(id), nil, nil, 2*time.Minute)
}

// waitHealthy wartet, bis der Container gesund ist. Ohne eigenen Healthcheck
// gilt: läuft nach 15 s noch und startet nicht ständig neu.
func (d *dockerClient) waitHealthy(id string, timeout int) (bool, string) {
	deadline := time.Now().Add(time.Duration(timeout) * time.Second)
	started := time.Now()
	restarts := -1.0
	for {
		c, err := d.inspect("containers", id)
		if err != nil {
			return false, "Container verschwunden: " + err.Error()
		}
		st := asMap(c["State"])
		running, _ := st["Running"].(bool)
		restarting, _ := st["Restarting"].(bool)
		rc, _ := c["RestartCount"].(float64)
		if restarts < 0 {
			restarts = rc
		}
		health := str(asMap(st["Health"])["Status"])
		switch {
		case !running && !restarting:
			return false, fmt.Sprintf("neuer Container beendet sich (Exit %v)", st["ExitCode"])
		case health == "healthy":
			return true, ""
		case health == "unhealthy":
			return false, "neuer Container meldet „unhealthy“"
		case health == "" && rc > restarts:
			return false, "neuer Container startet ständig neu"
		case health == "" && time.Since(started) > 15*time.Second && running:
			return true, ""
		}
		if time.Now().After(deadline) {
			if health == "starting" {
				return false, "Healthcheck nach " + fmt.Sprint(timeout) + " s noch nicht grün"
			}
			return running, "Zeit abgelaufen"
		}
		time.Sleep(3 * time.Second)
	}
}
