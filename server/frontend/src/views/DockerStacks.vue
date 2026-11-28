<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Geraete -> Docker-Stacks: Compose-Projekte auf den
                   Rechnern mit Agent. Compose-Datei (YAML) und .env im Browser
                   bearbeiten, Image-Tags umstellen, pruefen, ausrollen - wie die
                   Stacks in Portainer. Vor dem Schreiben prueft der Rechner die
                   Datei mit "docker compose config"; die alte Datei bleibt als
                   .rimperio-<zeit>.bak liegen, ein fehlgeschlagenes Ausrollen holt
                   den alten Stand automatisch zurueck (app/compose.py).
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Docker-Stacks</h1>
        <p class="page-subtitle">Compose-Projekte auf den Rechnern: YAML und <span class="rmm-code">.env</span> im Browser bearbeiten,
          Image-Tags umstellen und neu ausrollen. Gespeichert wird erst, wenn <span class="rmm-code">docker compose config</span>
          auf dem Rechner zufrieden ist; die alte Datei bleibt als Sicherung daneben liegen.</p>
      </div>
      <button class="btn btn-secondary btn-sm" :disabled="busy" @click="loadHosts"><AppIcon name="refresh" :size="15" /> Neu laden</button>
    </div>
    <p v-if="error" class="rmm-note is-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>

    <div class="ds-layout">
      <!-- Rechner -->
      <section class="card ds-hosts">
        <div class="card-header"><h2 class="card-title">Rechner mit Docker</h2></div>
        <ul class="ds-list">
          <li v-if="!hosts.length" class="rmm-muted ds-empty">Noch kein Rechner mit Docker gemeldet (Docker-Scan im Patch-Center).</li>
          <li v-for="h in hosts" :key="h.agent_uid">
            <button class="ds-item" :class="{ 'is-active': host?.agent_uid === h.agent_uid }" @click="pickHost(h)">
              <strong>{{ h.hostname }}</strong>
              <span class="rmm-muted">{{ h.projects.length }} Stacks · {{ h.containers }} Container</span>
              <span v-if="h.updates" class="badge badge-warning">{{ h.updates }} Updates</span>
              <span v-if="!h.compose" class="badge badge-neutral">ohne compose</span>
            </button>
          </li>
        </ul>
      </section>

      <!-- Stacks -->
      <section class="card ds-stacks">
        <div class="card-header"><h2 class="card-title">{{ host ? 'Stacks auf ' + host.hostname : 'Stacks' }}</h2></div>
        <div class="card-body">
          <p v-if="!host" class="rmm-muted">Links einen Rechner wählen.</p>
          <p v-else-if="loadingStacks" class="rmm-muted">Frage den Rechner …</p>
          <p v-else-if="!stacks.length" class="rmm-muted">Keine Compose-Projekte gefunden.</p>
          <table v-else class="table">
            <thead><tr><th>Stack</th><th>Zustand</th><th>Datei</th><th></th></tr></thead>
            <tbody>
              <tr v-for="s in stacks" :key="s.name">
                <td><strong>{{ s.name }}</strong></td>
                <td><span class="badge" :class="s.status.startsWith('running') ? 'badge-success' : 'badge-neutral'">{{ s.status }}</span></td>
                <td class="rmm-code ds-path">{{ s.files.join(', ') }}</td>
                <td class="ds-actions">
                  <button class="btn btn-primary btn-sm" @click="openStack(s)">Bearbeiten</button>
                  <button class="btn btn-ghost btn-sm" :disabled="busy" @click="action(s, 'pull_up')">Pull + neu starten</button>
                  <button class="btn btn-ghost btn-sm" :disabled="busy" @click="action(s, 'restart')">Neustart</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>

    <!-- Editor -->
    <div v-if="edit" class="modal-backdrop" @click.self="closeEdit">
      <div class="modal ds-modal">
        <div class="card-header">
          <h2 class="card-title">{{ edit.name }} <span class="rmm-muted">auf {{ host.hostname }}</span></h2>
          <button class="btn-icon" aria-label="Schließen" @click="closeEdit"><AppIcon name="close" :size="18" /></button>
        </div>
        <div class="card-body ds-edit">
          <p v-if="edit.loading" class="rmm-muted">Lese die Dateien …</p>
          <template v-else>
            <nav class="rmm-tabs">
              <button v-for="(f, i) in edit.files" :key="f.path" :class="{ 'is-active': edit.current === i }" @click="edit.current = i">
                {{ f.name }}<span v-if="f.content !== f.original" class="ds-dirty">●</span>
              </button>
            </nav>
            <template v-if="file">
              <p v-if="!file.readable" class="rmm-note is-error">Der Agent darf {{ file.path }} nicht lesen (Rechte). Datei für die Gruppe
                <span class="rmm-code">docker</span> lesbar machen oder dem Dienstkonto Leserechte geben.</p>
              <template v-else>
                <!-- Image-Tags -->
                <div v-if="file.kind === 'compose' && images.length" class="ds-images">
                  <h3 class="ds-h3">Images und Tags</h3>
                  <table class="table">
                    <thead><tr><th>Dienst</th><th>Image</th><th>Tag</th><th>Status</th></tr></thead>
                    <tbody>
                      <tr v-for="img in images" :key="img.line">
                        <td>{{ img.service || '–' }}</td>
                        <td class="rmm-code">{{ img.repo }}</td>
                        <td><input class="input ds-tag" :value="img.tag" :placeholder="img.digest ? '(Digest)' : 'latest'" @change="setTag(img, $event.target.value)" /></td>
                        <td>
                          <span v-if="svc(img.service)?.update_available" class="badge badge-warning">Update verfügbar{{ svc(img.service)?.remote_version ? ': ' + svc(img.service).remote_version : '' }}</span>
                          <span v-else-if="svc(img.service)" class="rmm-muted">{{ svc(img.service).state }}</span>
                          <span v-if="isDb(img.repo)" class="badge badge-danger" title="Datenbank-Images besser über Patchmanagement → DB-Updates (mit Sicherung und Probelauf)">Datenbank</span>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                  <p v-if="dbTagChanged" class="rmm-note is-error">Achtung: Du änderst das Image einer Datenbank. Ein Sprung der Hauptversion
                    (z. B. postgres:16 → 17) startet nicht auf den alten Daten. Sicherer geht es unter
                    <router-link to="/rmm/db-updates">Patchmanagement → DB-Updates</router-link> mit Sicherung und Probelauf.</p>
                </div>

                <div class="ds-editor">
                  <pre class="ds-lines" aria-hidden="true">{{ lineNumbers }}</pre>
                  <textarea ref="area" v-model="file.content" class="ds-text" spellcheck="false" wrap="off"
                    @keydown.tab.prevent="insertTab" @scroll="syncScroll" />
                </div>
                <p v-if="yamlError" class="rmm-note is-error">YAML: {{ yamlError }}</p>
                <p v-else-if="file.kind === 'compose'" class="rmm-muted ds-small">YAML sieht gut aus. Der Rechner prüft beim Speichern zusätzlich mit docker compose config.</p>
              </template>
            </template>

            <div v-if="result" class="ds-result" :class="result.ok ? 'is-ok' : 'is-error'">
              <strong>{{ result.message }}</strong>
              <span v-if="result.backup" class="rmm-muted"> Sicherung: <span class="rmm-code">{{ result.backup }}</span></span>
              <span v-if="result.rolled_back" class="rmm-muted"> – der alte Stand wurde zurückgeholt und wieder gestartet.</span>
              <pre v-if="(result.output || []).length" class="ds-out">{{ result.output.join('\n') }}</pre>
            </div>

            <div class="rmm-actions ds-buttons">
              <label class="pol-check"><input v-model="opts.pull" type="checkbox" /> vorher Images holen (pull)</label>
              <label class="pol-check"><input v-model="opts.rollback" type="checkbox" /> bei Fehler alten Stand zurückholen</label>
              <span class="ds-grow" />
              <button class="btn btn-secondary btn-sm" :disabled="busy || !canSave" @click="save(false)">Nur speichern</button>
              <button class="btn btn-primary btn-sm" :disabled="busy || !canSave" @click="save(true)">{{ busy ? 'Läuft …' : 'Speichern und ausrollen' }}</button>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import yaml from 'js-yaml'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const hosts = ref([])
const host = ref(null)
const stacks = ref([])
const loadingStacks = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const edit = ref(null)
const result = ref(null)
const area = ref(null)
const opts = ref({ pull: true, rollback: true })

const DB_RE = /(^|\/)(postgres|postgis|timescaledb|mariadb|mysql|percona|mongo|redis|valkey)(:|@|$)/i
const isDb = repo => DB_RE.test(repo + ':')

async function loadHosts() {
  busy.value = true
  try {
    hosts.value = await auth.api('/api/rmm/docker-hosts')
    error.value = ''
    const want = route.query.agent || host.value?.agent_uid
    const h = hosts.value.find(x => x.agent_uid === want) || (hosts.value.length === 1 ? hosts.value[0] : null)
    if (h) await pickHost(h)
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function pickHost(h) {
  host.value = h
  router.replace({ query: { ...route.query, agent: h.agent_uid } })
  loadingStacks.value = true
  stacks.value = []
  try { stacks.value = await auth.api(`/api/rmm/agents/${h.agent_uid}/stacks`); error.value = '' } catch (e) { error.value = e.message } finally { loadingStacks.value = false }
}

async function openStack(s) {
  result.value = null
  edit.value = { name: s.name, loading: true, files: [], current: 0, services: [] }
  try {
    const d = await auth.api(`/api/rmm/agents/${host.value.agent_uid}/stacks/${encodeURIComponent(s.name)}`)
    const files = d.files_content.map(f => ({ ...f, original: f.content }))
    // Eine .env, die es noch nicht gibt, laesst sich anlegen.
    if (!files.some(f => f.kind === 'env')) files.push({ path: d.dir + '/.env', name: '.env (neu)', kind: 'env', readable: true, content: '', original: '' })
    edit.value = { name: s.name, loading: false, files, current: 0, services: d.services || [] }
  } catch (e) { edit.value = null; error.value = e.message }
}
function closeEdit() {
  if (edit.value && edit.value.files.some(f => f.content !== f.original) && !confirm('Ungespeicherte Änderungen verwerfen?')) return
  edit.value = null
}
const file = computed(() => edit.value?.files[edit.value.current])
const lineNumbers = computed(() => {
  const n = (file.value?.content || '').split('\n').length
  return Array.from({ length: n }, (_, i) => i + 1).join('\n')
})
const yamlError = computed(() => {
  if (!file.value || file.value.kind !== 'compose') return ''
  try { yaml.load(file.value.content); return '' } catch (e) { return e.reason ? `${e.reason} (Zeile ${(e.mark?.line ?? 0) + 1})` : e.message }
})
const canSave = computed(() => file.value && file.value.readable && !yamlError.value && file.value.content !== file.value.original)

// Image-Zeilen in der Compose-Datei finden - Kommentare und Formatierung bleiben,
// geaendert wird nur der Tag in genau dieser Zeile.
const IMG_LINE = /^(\s*)image:\s*(["']?)([^"'\s#]+)\2(.*)$/
const images = computed(() => {
  if (!file.value || file.value.kind !== 'compose') return []
  const lines = file.value.content.split('\n')
  const out = []
  lines.forEach((line, i) => {
    const m = line.match(IMG_LINE)
    if (!m) return
    const indent = m[1].length
    let service = ''
    for (let j = i - 1; j >= 0; j--) {
      const k = lines[j].match(/^(\s*)([A-Za-z0-9_.-]+):\s*(#.*)?$/)
      if (k && k[1].length < indent) { service = k[2]; break }
    }
    const ref = m[3]
    const at = ref.indexOf('@')
    const base = at >= 0 ? ref.slice(0, at) : ref
    const slash = base.lastIndexOf('/')
    const colon = base.lastIndexOf(':')
    const hasTag = colon > slash
    out.push({ line: i, service, repo: hasTag ? base.slice(0, colon) : base, tag: hasTag ? base.slice(colon + 1) : '',
      digest: at >= 0 ? ref.slice(at) : '', quote: m[2], indent: m[1], rest: m[4], original: ref })
  })
  return out
})
const dbTagChanged = computed(() => {
  if (!file.value || file.value.kind !== 'compose') return false
  const before = file.value.original.split('\n')
  return images.value.some(img => isDb(img.repo) && (before[img.line] || '') !== file.value.content.split('\n')[img.line])
})
function svc(name) { return (edit.value?.services || []).find(s => s.service === name) }
function setTag(img, tag) {
  tag = String(tag || '').trim()
  if (tag && !/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(tag)) { error.value = `Ungültiger Tag: ${tag}`; return }
  const lines = file.value.content.split('\n')
  // Ein neuer Tag ersetzt einen festgenagelten Digest - sonst gilt weiter der Digest.
  const ref = img.repo + (tag ? ':' + tag : '')
  lines[img.line] = `${img.indent}image: ${img.quote}${ref}${img.quote}${img.rest}`
  file.value.content = lines.join('\n')
  error.value = ''
}
function insertTab() {
  const el = area.value
  if (!el) return
  const s = el.selectionStart
  const e = el.selectionEnd
  file.value.content = file.value.content.slice(0, s) + '  ' + file.value.content.slice(e)
  nextTick(() => { el.selectionStart = el.selectionEnd = s + 2 })
}
function syncScroll(ev) {
  const lines = ev.target.previousElementSibling
  if (lines) lines.scrollTop = ev.target.scrollTop
}

async function save(deploy) {
  if (deploy && !confirm(`${edit.value.name} auf ${host.value.hostname} speichern und neu ausrollen?`)) return
  busy.value = true
  result.value = null
  const f = file.value
  try {
    const r = await auth.api(`/api/rmm/agents/${host.value.agent_uid}/stacks/${encodeURIComponent(edit.value.name)}`, {
      method: 'PUT', body: { path: f.path, content: f.content, deploy, pull: deploy && opts.value.pull, rollback: opts.value.rollback } })
    result.value = r
    if (r.saved && !r.rolled_back) { f.original = f.content; if (f.name === '.env (neu)') f.name = '.env' }
  } catch (e) { result.value = { ok: false, message: e.message, output: [] } } finally { busy.value = false }
}
async function action(s, what) {
  if (!confirm(`${s.name}: ${what === 'pull_up' ? 'Images holen und neu starten' : 'neu starten'}?`)) return
  busy.value = true
  try {
    const r = await auth.api(`/api/rmm/agents/${host.value.agent_uid}/stacks/${encodeURIComponent(s.name)}/action`, { method: 'POST', body: { action: what } })
    if (r.ok) { notice.value = `${s.name}: ${r.message}`; error.value = '' } else { error.value = `${s.name}: ${r.message}\n${(r.output || []).join('\n')}` }
    await pickHost(host.value)
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
onMounted(loadHosts)
</script>

<style scoped>
.ds-layout { display: grid; gap: 1rem; grid-template-columns: minmax(14rem, 18rem) 1fr; align-items: start; }
@media (max-width: 900px) { .ds-layout { grid-template-columns: 1fr; } }
.ds-list { list-style: none; margin: 0; padding: .5rem; display: flex; flex-direction: column; gap: .25rem; }
.ds-empty { padding: .5rem; }
.ds-item { width: 100%; text-align: left; display: flex; flex-direction: column; gap: .15rem; padding: .55rem .7rem; border-radius: .5rem; border: 1px solid transparent; background: none; cursor: pointer; color: inherit; }
.ds-item:hover { background: var(--color-surface-2, rgba(0,0,0,.04)); }
.ds-item.is-active { border-color: var(--color-primary); background: var(--color-surface-2, rgba(0,0,0,.04)); }
.ds-path { font-size: .75rem; overflow-wrap: anywhere; }
.ds-actions { display: flex; gap: .3rem; flex-wrap: wrap; justify-content: flex-end; }
.ds-modal { width: min(1100px, 96vw); max-width: 96vw; }
.ds-edit { display: flex; flex-direction: column; gap: .75rem; }
.ds-dirty { color: var(--color-warning); margin-left: .3rem; }
.ds-h3 { font-size: .85rem; font-weight: 600; margin: 0 0 .4rem; }
.ds-tag { min-width: 9rem; padding: .25rem .5rem; }
.ds-editor { display: flex; border: 1px solid var(--color-border); border-radius: .5rem; overflow: hidden; height: 50vh; min-height: 18rem; }
.ds-lines { margin: 0; padding: .6rem .5rem; text-align: right; color: var(--color-text-muted); background: var(--color-surface-2, rgba(0,0,0,.04)); font: 12.5px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; overflow: hidden; user-select: none; min-width: 2.8rem; }
.ds-text { flex: 1; margin: 0; padding: .6rem .75rem; border: 0; outline: none; resize: none; background: var(--color-surface, transparent); color: var(--color-text); font: 12.5px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: pre; overflow: auto; tab-size: 2; }
.ds-small { font-size: .8rem; }
.ds-result { border-radius: .5rem; padding: .6rem .8rem; border: 1px solid var(--color-border); }
.ds-result.is-ok { border-color: var(--color-success); }
.ds-result.is-error { border-color: var(--color-danger); }
.ds-out { white-space: pre-wrap; font-size: .75rem; max-height: 14rem; overflow: auto; margin: .5rem 0 0; }
.ds-buttons { align-items: center; flex-wrap: wrap; }
.ds-grow { flex: 1; }
.pol-check { display: flex; align-items: center; gap: .4rem; font-size: .85rem; }
</style>
