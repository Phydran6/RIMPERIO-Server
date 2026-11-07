<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Update-Quellen: Feeds, CISA KEV, GitHub-Releases, Websuche, Meldungen
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Update-Quellen</h1>
        <p class="page-subtitle">
          Woher die Einschätzung kommt: Sicherheitsmeldungen, bekannte Probleme, Release-Notizen. Jede Meldung wird
          den passenden Updates zugeordnet (KB, CVE, Paketname) und geht mit in die KI-Prüfung.
        </p>
      </div>
      <button v-if="auth.isAdmin" class="btn btn-primary btn-sm" @click="form = blank()"><AppIcon name="plus" :size="16" /> Eigene Quelle</button>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>

    <section v-if="form" class="card mb-4">
      <div class="card-header"><h2 class="card-title">Eigene Quelle</h2></div>
      <div class="card-body rmm-form">
        <label>Name<input v-model="form.name" class="input" /></label>
        <label>Art
          <select v-model="form.kind" class="select">
            <option value="rss">RSS / Atom / RDF</option><option value="github">GitHub-Releases (Atom)</option><option value="kev">CISA-KEV-Format (JSON)</option>
          </select>
        </label>
        <label class="is-wide">Adresse<input v-model="form.url" class="input" placeholder="https://… /feed oder https://github.com/owner/repo/releases.atom" /></label>
        <label class="pol-check is-wide">Gilt für:
          <span v-for="pl in ['windows','linux','docker']" :key="pl"><input v-model="form.platforms" type="checkbox" :value="pl" /> {{ pl }}</span>
        </label>
        <label>Abruf alle (Minuten)<input v-model.number="form.interval_minutes" type="number" min="15" class="input" /></label>
        <label class="is-wide">Notiz<input v-model="form.note" class="input" /></label>
        <div class="is-wide rmm-actions">
          <button class="btn btn-primary btn-sm" :disabled="busy || !form.name || !form.url" @click="create">Anlegen und abrufen</button>
          <button class="btn btn-ghost btn-sm" @click="form = null">Abbrechen</button>
        </div>
      </div>
    </section>

    <section class="card mb-4">
      <div class="card-header"><h2 class="card-title">Websuche</h2></div>
      <div class="card-body rmm-form">
        <label>Anbieter
          <select v-model="cfg.websearch.provider" class="select" :disabled="!auth.isAdmin">
            <option value="ai">über die KI (Claude-Websuche)</option>
            <option value="searxng">SearXNG (eigene Instanz)</option>
            <option value="brave">Brave Search API</option>
            <option value="off">aus</option>
          </select>
        </label>
        <label v-if="cfg.websearch.provider === 'searxng'">SearXNG-Adresse<input v-model="cfg.websearch.url" class="input" placeholder="https://searx.example" /></label>
        <label v-if="cfg.websearch.provider === 'brave'">API-Schlüssel<input v-model="braveKey" type="password" class="input" :placeholder="cfg.websearch.api_key_set ? 'hinterlegt — leer lassen = unverändert' : ''" /></label>
        <label>Treffer je Suche<input v-model.number="cfg.websearch.max_results" type="number" min="1" max="10" class="input" /></label>
        <label class="pol-check"><input v-model="cfg.github_auto" type="checkbox" /> Release-Notizen für Container-Images automatisch anlegen</label>
        <div class="is-wide rmm-muted">
          „Über die KI“ nutzt die eingebaute Websuche von Claude (bei Anbieter Anthropic unter <router-link to="/ai">KI-Auswertung</router-link>).
          Bei anderen KI-Anbietern bitte SearXNG oder Brave wählen.
        </div>
        <div v-if="auth.isAdmin" class="is-wide rmm-actions">
          <button class="btn btn-primary btn-sm" :disabled="busy" @click="saveCfg">Speichern</button>
          <input v-model="testQuery" class="input" style="max-width:18rem" placeholder="Testsuche, z. B. KB5070000 known issues" />
          <button class="btn btn-secondary btn-sm" :disabled="busy || !testQuery" @click="testSearch">Testen</button>
        </div>
        <ul v-if="testResults.length" class="is-wide src-list">
          <li v-for="r in testResults" :key="r.url"><a :href="r.url" target="_blank" rel="noopener">{{ r.title }}</a> <span class="rmm-muted">{{ r.snippet }}</span></li>
        </ul>
      </div>
    </section>

    <section class="card mb-4">
      <div class="card-header"><h2 class="card-title">Quellen</h2></div>
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th></th><th>Quelle</th><th>Gilt für</th><th>Abruf</th><th>Stand</th><th>Meldungen</th><th></th></tr></thead>
          <tbody>
            <tr v-for="src in sources" :key="src.id" :style="src.enabled ? '' : 'opacity:.55'">
              <td><input type="checkbox" :checked="src.enabled" :disabled="!auth.isAdmin" @change="toggle(src)" /></td>
              <td><strong>{{ src.name }}</strong><span v-if="src.builtin" class="badge badge-neutral" style="margin-left:.35rem">mitgeliefert</span>
                <div class="rmm-muted"><a :href="src.url" target="_blank" rel="noopener">{{ src.url }}</a></div>
                <div v-if="src.note" class="rmm-muted">{{ src.note }}</div></td>
              <td>{{ src.platforms.join(', ') }}</td>
              <td>{{ src.interval_minutes }} min</td>
              <td class="rmm-muted">{{ fmtAgo(src.last_fetch_at) }}<div :style="src.last_status.startsWith('Fehler') ? 'color:var(--color-danger)' : ''">{{ src.last_status }}</div></td>
              <td><a href="#" @click.prevent="showItems(src)">{{ src.item_count }}</a></td>
              <td class="rmm-actions" style="justify-content:flex-end">
                <button v-if="auth.isAdmin" class="btn btn-secondary btn-sm" :disabled="busy" @click="refresh(src)">Abrufen</button>
                <button v-if="auth.isAdmin && !src.builtin" class="btn btn-danger btn-sm" @click="remove(src)">Löschen</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="card">
      <div class="card-header">
        <h2 class="card-title">Meldungen <span v-if="itemsSource" class="rmm-muted">— {{ itemsSource.name }}</span></h2>
        <div class="rmm-actions">
          <input v-model="q" class="input" placeholder="Suchen (KB, CVE, Paket …)" style="max-width:16rem" @keyup.enter="loadAdvisories" />
          <button class="btn btn-ghost btn-sm" @click="itemsSource = null; loadAdvisories()">alle Quellen</button>
        </div>
      </div>
      <ul class="src-list card-body">
        <li v-for="a in advisories" :key="a.id">
          <a :href="a.link" target="_blank" rel="noopener"><strong>{{ a.title }}</strong></a>
          <span class="rmm-muted"> — {{ a.source }}, {{ fmtTime(a.published_at) }}</span>
          <div class="rmm-muted">{{ a.summary }}</div>
        </li>
        <li v-if="!advisories.length" class="rmm-muted">Keine Meldungen.</li>
      </ul>
    </section>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import { fmtAgo, fmtTime } from '../utils/rmm'

const auth = useAuthStore()
const sources = ref([])
const cfg = ref({ websearch: { provider: 'ai', url: '', max_results: 5 }, github_auto: true })
const advisories = ref([])
const itemsSource = ref(null)
const form = ref(null)
const q = ref('')
const braveKey = ref('')
const testQuery = ref('')
const testResults = ref([])
const error = ref('')
const notice = ref('')
const busy = ref(false)

const blank = () => ({ name: '', kind: 'rss', url: '', platforms: ['windows', 'linux', 'docker'], interval_minutes: 360, note: '', language: 'de' })

async function guard(fn) {
  busy.value = true
  try { await fn(); error.value = '' } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function load() {
  await guard(async () => {
    const data = await auth.api('/api/patching/sources')
    sources.value = data.sources
    cfg.value = data.config
    await loadAdvisories()
  })
}
async function loadAdvisories() {
  const p = new URLSearchParams({ limit: '150' })
  if (itemsSource.value) p.set('source_id', itemsSource.value.id)
  if (q.value) p.set('q', q.value)
  advisories.value = await auth.api(`/api/patching/advisories?${p}`)
}
function showItems(src) { itemsSource.value = src; loadAdvisories() }
async function toggle(src) {
  await guard(async () => { await auth.api(`/api/patching/sources/${src.id}`, { method: 'PATCH', body: { enabled: !src.enabled } }); await load() })
}
async function refresh(src) {
  await guard(async () => { const r = await auth.api(`/api/patching/sources/${src.id}/refresh`, { method: 'POST' }); notice.value = `${src.name}: ${r.added} neue Meldung(en).`; await load() })
}
async function remove(src) {
  if (!confirm(`Quelle „${src.name}“ samt Meldungen löschen?`)) return
  await guard(async () => { await auth.api(`/api/patching/sources/${src.id}`, { method: 'DELETE' }); await load() })
}
async function create() {
  await guard(async () => { const s = await auth.api('/api/patching/sources', { method: 'POST', body: form.value }); notice.value = `„${s.name}“ angelegt: ${s.last_status}`; form.value = null; await load() })
}
async function saveCfg() {
  await guard(async () => {
    const body = { websearch: { ...cfg.value.websearch }, github_auto: cfg.value.github_auto }
    delete body.websearch.api_key_set
    if (braveKey.value) body.websearch.api_key = braveKey.value
    cfg.value = await auth.api('/api/patching/sources/config', { method: 'PUT', body })
    braveKey.value = ''
    notice.value = 'Gespeichert.'
  })
}
async function testSearch() {
  await guard(async () => {
    const r = await auth.api('/api/patching/sources/websearch-test', { method: 'POST', body: { query: testQuery.value } })
    testResults.value = r.results
    notice.value = r.note || `${r.results.length} Treffer.`
  })
}

onMounted(load)
</script>

<style scoped>
.pol-check { flex-direction: row !important; align-items: center; gap: .5rem !important; flex-wrap: wrap; }
.pol-check span { display: inline-flex; gap: .25rem; align-items: center; margin-right: .5rem; }
.src-list { list-style: none; margin: 0; padding: 0 1rem 1rem; display: flex; flex-direction: column; gap: .6rem; font-size: .875rem; }
.src-list .rmm-muted { overflow-wrap: anywhere; }
</style>
