<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Logs -> Log-Handling: Rauschen erkennen, Meldungen
                   sperren, Aufbewahrung nach Stufen, Dubletten, Themenbereiche.
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Log-Handling &amp; Rauschen</h1>
        <p class="page-subtitle">
          Info- und Hinweis-Zeilen machen fast den ganzen Bestand aus. Hier siehst du, was den Platz frisst,
          sperrst Unnötiges schon beim Empfang und legst fest, wie lange welche Stufe aufbewahrt wird.
        </p>
      </div>
      <button class="btn btn-secondary btn-sm" :disabled="loading" @click="loadAll">Aktualisieren</button>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>

    <section v-if="overview" class="card mb-4">
      <div class="card-body lh-kpis">
        <div v-for="(n, lv) in sortedLevels" :key="lv" class="lh-kpi">
          <span class="lh-kpi-value">{{ n.toLocaleString('de-DE') }}</span>
          <span class="lh-kpi-label">{{ LEVEL_LABEL[lv] || lv }} (24 h)</span>
        </div>
        <div v-if="noise" class="lh-kpi">
          <span class="lh-kpi-value">{{ noise.low_share_percent }} %</span>
          <span class="lh-kpi-label">Anteil Info/Hinweis/Debug</span>
        </div>
      </div>
    </section>

    <nav class="rmm-tabs">
      <button v-for="t in TABS" :key="t.key" :class="{ 'is-active': tab === t.key }" @click="tab = t.key">
        {{ t.label }}
        <span v-if="t.key === 'rules' && rules.length" class="badge badge-neutral" style="margin-left:.35rem">{{ rules.length }}</span>
      </button>
    </nav>

    <!-- Rauschen --------------------------------------------------------- -->
    <section v-if="tab === 'noise'" class="card">
      <div class="card-header">
        <h2 class="card-title">Was am meisten Platz frisst</h2>
        <label class="rmm-muted">Zeitraum
          <select v-model.number="hours" class="select" @change="loadNoise">
            <option :value="6">6 h</option><option :value="24">24 h</option><option :value="72">3 Tage</option><option :value="168">7 Tage</option>
          </select>
        </label>
      </div>
      <div class="card-body">
        <p class="rmm-muted mb-2">
          Stichprobe der jüngsten {{ noise?.sample_size?.toLocaleString('de-DE') || 0 }} Info/Hinweis/Debug-Zeilen, gruppiert nach
          Vorlage (Zahlen, IPs und IDs zählen nicht). „Sperren“ legt eine Regel an – künftige Zeilen dieser Art werden nicht
          mehr gespeichert, auf Wunsch auch die vorhandenen gelöscht. Warnungen und Fehler tauchen hier nie auf.
        </p>
        <div class="rmm-table-wrap">
          <table class="table">
            <thead><tr><th>Anteil</th><th>Was ist das?</th><th>Gerät / Programm</th><th>Einschätzung</th><th></th></tr></thead>
            <tbody>
              <tr v-if="!noise?.groups?.length"><td colspan="5" class="rmm-muted">Kein Rauschen gefunden.</td></tr>
              <tr v-for="g in noise?.groups || []" :key="g.hostname + g.source + g.template">
                <td><strong>{{ g.share_percent }} %</strong><br><span class="rmm-muted">≈ {{ g.estimated_rows.toLocaleString('de-DE') }} Zeilen</span></td>
                <td>
                  <span v-if="g.category !== 'other'" class="badge badge-neutral">{{ g.category_label }}</span>
                  {{ g.title || '—' }}
                  <div class="rmm-code lh-sample">{{ g.sample }}</div>
                </td>
                <td>{{ g.hostname }}<br><span class="rmm-muted">{{ g.source }} · {{ LEVEL_LABEL[g.level] || g.level }}</span></td>
                <td>
                  <span v-if="g.blocked" class="badge badge-success">gesperrt</span>
                  <span v-else-if="g.suggestion === 'sperren'" class="badge badge-warning">sperren empfohlen</span>
                  <span v-else-if="g.suggestion === 'beobachten'" class="badge badge-neutral">beobachten</span>
                  <span v-if="g.known_noise" class="badge badge-neutral" title="Typisches Grundrauschen">Grundrauschen</span>
                </td>
                <td class="lh-actions">
                  <template v-if="auth.isAdmin && !g.blocked">
                    <button class="btn btn-primary btn-sm" @click="block(g, 'host')">Sperren ({{ g.hostname }})</button>
                    <button class="btn btn-ghost btn-sm" @click="block(g, 'all')">für alle Geräte</button>
                    <button class="btn btn-ghost btn-sm" title="Weiter speichern, aber nur als Debug (kürzeste Frist)" @click="block(g, 'host', 'downgrade')">nur kurz behalten</button>
                  </template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- Sperrregeln ------------------------------------------------------ -->
    <section v-if="tab === 'rules'" class="card">
      <div class="card-header">
        <h2 class="card-title">Sperrregeln</h2>
        <button v-if="auth.isAdmin" class="btn btn-primary btn-sm" @click="form = blankRule()">Eigene Regel</button>
      </div>
      <div class="card-body">
        <div v-if="form" class="card mb-4"><div class="card-body rmm-form">
          <label>Name<input v-model="form.name" class="input" /></label>
          <label>Aktion
            <select v-model="form.action" class="select"><option value="drop">nicht speichern</option><option value="downgrade">nur kurz (als Debug)</option></select>
          </label>
          <label>Vergleich
            <select v-model="form.match" class="select"><option value="contains">enthält</option><option value="regex">regulärer Ausdruck</option><option value="template">Vorlage</option></select>
          </label>
          <label class="is-wide">Muster<input v-model="form.pattern" class="input" placeholder="z. B. pam_unix(cron:session)" /></label>
          <label>Gerät (leer = alle, * erlaubt)<input v-model="form.hostname" class="input" /></label>
          <label>Programm (leer = alle)<input v-model="form.source" class="input" /></label>
          <label class="is-wide">Stufen
            <span class="lh-levels"><label v-for="lv in ['debug','info','notice','warning']" :key="lv"><input v-model="form.levels" type="checkbox" :value="lv" /> {{ LEVEL_LABEL[lv] }}</label></span>
          </label>
          <label class="is-wide"><span><input v-model="form.purge_existing" type="checkbox" /> vorhandene passende Zeilen auch löschen</span></label>
          <div class="is-wide rmm-actions">
            <button class="btn btn-secondary btn-sm" @click="previewRule">Vorschau (24 h)</button>
            <span v-if="preview !== null" class="rmm-muted">trifft {{ preview }} Zeilen der Stichprobe</span>
            <button class="btn btn-primary btn-sm" @click="saveRule">Speichern</button>
            <button class="btn btn-ghost btn-sm" @click="form = null; preview = null">Abbrechen</button>
          </div>
        </div></div>
        <div class="rmm-table-wrap">
          <table class="table">
            <thead><tr><th>An</th><th>Regel</th><th>Gilt für</th><th>Aktion</th><th>Treffer</th><th></th></tr></thead>
            <tbody>
              <tr v-if="!rules.length"><td colspan="6" class="rmm-muted">Noch keine Regeln – im Reiter „Rauschen“ mit einem Klick anlegen.</td></tr>
              <tr v-for="r in rules" :key="r.id">
                <td><input type="checkbox" :checked="r.enabled" :disabled="!auth.isAdmin" @change="toggleRule(r)" /></td>
                <td>{{ r.name }}<div class="rmm-code lh-sample">{{ r.match }}: {{ r.pattern }}</div></td>
                <td>{{ r.hostname || 'alle Geräte' }}<br><span class="rmm-muted">{{ r.source || 'alle Programme' }} · {{ (r.levels || []).map(l => LEVEL_LABEL[l] || l).join(', ') }}</span></td>
                <td><span class="badge" :class="r.action === 'drop' ? 'badge-warning' : 'badge-neutral'">{{ r.action === 'drop' ? 'nicht speichern' : 'nur kurz' }}</span></td>
                <td>{{ (r.hits || 0).toLocaleString('de-DE') }}<br><span class="rmm-muted">{{ r.last_hit_at ? fmtAgo(r.last_hit_at) : 'noch nie' }}</span></td>
                <td class="lh-actions">
                  <template v-if="auth.isAdmin">
                    <button class="btn btn-ghost btn-sm" @click="purge(r)">Vorhandene löschen</button>
                    <button class="btn btn-danger btn-sm" @click="removeRule(r)">Löschen</button>
                  </template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- Aufbewahrung ------------------------------------------------------ -->
    <section v-if="tab === 'retention' && overview" class="card">
      <div class="card-header"><h2 class="card-title">Aufbewahrung nach Stufen</h2></div>
      <div class="card-body">
        <p class="rmm-muted mb-2">
          Je unwichtiger, desto kürzer. Die allgemeine Aufbewahrung ({{ overview.global_days }} Tage,
          <router-link to="/settings/retention" style="text-decoration:underline">Einstellungen</router-link>) bleibt die Obergrenze.
          0 = wie allgemein. Durchgesetzt wird stündlich.
        </p>
        <div class="rmm-form">
          <label v-for="lv in TIER_ORDER" :key="lv">{{ LEVEL_LABEL[lv] }} (Tage)
            <input v-model.number="tiers[lv]" type="number" min="0" max="3650" class="input" :disabled="!auth.isAdmin" />
          </label>
        </div>
        <div class="rmm-actions mt-4">
          <button v-if="auth.isAdmin" class="btn btn-primary btn-sm" @click="saveTiers">Fristen speichern</button>
          <button v-if="auth.isAdmin" class="btn btn-ghost btn-sm" @click="tiers = { ...overview.default_tiers }">Empfehlung übernehmen</button>
        </div>
        <h3 class="card-title mt-4 mb-2">Dubletten zusammenfassen</h3>
        <p class="rmm-muted mb-2">
          Dieselbe Info/Hinweis/Debug-Meldung (bis auf Zahlen, IPs, IDs) innerhalb des Fensters wird keine neue Zeile,
          sondern zählt die erste hoch („×1.200“). Warnungen und Fehler bleiben immer einzeln.
        </p>
        <div class="rmm-form">
          <label><span><input v-model="dedup.enabled" type="checkbox" :disabled="!auth.isAdmin" /> eingeschaltet</span></label>
          <label>Fenster (Sekunden)<input v-model.number="dedup.window_seconds" type="number" min="0" max="3600" class="input" :disabled="!auth.isAdmin" /></label>
        </div>
        <button v-if="auth.isAdmin" class="btn btn-primary btn-sm mt-4" @click="saveDedup">Dubletten-Einstellung speichern</button>
      </div>
    </section>

    <!-- Themen ------------------------------------------------------------ -->
    <section v-if="tab === 'topics'" class="card">
      <div class="card-header">
        <h2 class="card-title">Themenbereiche (Warnungen und schlimmer, 24 h)</h2>
        <label class="rmm-muted"><input v-model="topicsAll" type="checkbox" @change="loadTopics" /> auch Info/Hinweis</label>
      </div>
      <div class="card-body">
        <div class="lh-topics">
          <button v-for="c in topics?.items || []" :key="c.key" class="card lh-topic" :class="{ 'is-active': topic?.key === c.key }" @click="openTopic(c.key)">
            <strong>{{ c.label }}</strong>
            <span class="lh-kpi-value">{{ c.count }}</span>
            <span class="rmm-muted">{{ c.latest?.title }}</span>
          </button>
          <p v-if="!topics?.items?.length" class="rmm-muted">Keine Zeilen in diesem Zeitraum.</p>
        </div>
        <div v-if="topic" class="rmm-table-wrap mt-4">
          <table class="table">
            <thead><tr><th>Zeit</th><th>Gerät</th><th>Was ist passiert?</th></tr></thead>
            <tbody>
              <tr v-for="it in topic.items" :key="it.id">
                <td>{{ fmtTime(it.ts) }}</td>
                <td>{{ it.host }}<br><span class="rmm-muted">{{ it.source }}</span></td>
                <td>
                  {{ it.summary }}<span v-if="it.repeat > 1" class="badge badge-warning" style="margin-left:.35rem">×{{ it.repeat }}</span>
                  <div v-if="it.hint" class="rmm-muted">💡 {{ it.hint }}</div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import { fmtAgo, fmtTime } from '../utils/rmm'

const auth = useAuthStore()
const TABS = [
  { key: 'noise', label: 'Rauschen' }, { key: 'rules', label: 'Sperrregeln' },
  { key: 'retention', label: 'Aufbewahrung & Dubletten' }, { key: 'topics', label: 'Themenbereiche' },
]
const LEVEL_LABEL = {
  emergency: 'Notfall', alert: 'Alarm', critical: 'Kritisch', error: 'Fehler', warning: 'Warnung',
  notice: 'Hinweis', info: 'Info', informational: 'Info', debug: 'Debug',
}
const TIER_ORDER = ['debug', 'info', 'notice', 'warning', 'error', 'critical']
const LEVEL_SORT = ['emergency', 'alert', 'critical', 'error', 'warning', 'notice', 'info', 'informational', 'debug']

const tab = ref('noise')
const loading = ref(false)
const error = ref('')
const notice = ref('')
const overview = ref(null)
const noise = ref(null)
const rules = ref([])
const tiers = ref({})
const dedup = ref({ enabled: true, window_seconds: 300 })
const hours = ref(24)
const form = ref(null)
const preview = ref(null)
const topics = ref(null)
const topicsAll = ref(false)
const topic = ref(null)

const sortedLevels = computed(() => {
  const by = overview.value?.last_24h || {}
  const out = {}
  for (const k of LEVEL_SORT) if (by[k]) out[k] = by[k]
  return out
})

function blankRule() {
  return { name: '', action: 'drop', match: 'contains', pattern: '', hostname: '', source: '',
    levels: ['info', 'notice', 'debug'], purge_existing: false }
}

async function run(fn, ok = '') {
  error.value = ''
  notice.value = ''
  try {
    await fn()
    if (ok) notice.value = ok
  } catch (e) {
    error.value = e.message
  }
}

async function loadOverview() {
  overview.value = await auth.api('/api/log-handling/overview')
  rules.value = overview.value.rules
  tiers.value = { ...overview.value.tiers }
  dedup.value = { ...overview.value.dedup }
}
async function loadNoise() {
  noise.value = await auth.api(`/api/log-handling/noise?hours=${hours.value}`)
}
async function loadTopics() {
  topics.value = await auth.api(`/api/log-handling/categories?hours=24&problems_only=${!topicsAll.value}`)
  topic.value = null
}
async function openTopic(key) {
  await run(async () => {
    topic.value = await auth.api(`/api/log-handling/category/${key}?hours=72&problems_only=${!topicsAll.value}`)
  })
}
async function loadAll() {
  loading.value = true
  await run(async () => { await Promise.all([loadOverview(), loadNoise(), loadTopics()]) })
  loading.value = false
}

async function block(g, scope, action = 'drop') {
  const where = scope === 'host' ? `auf ${g.hostname}` : 'auf allen Geräten'
  const purge = confirm(`„${g.title || g.template}“ ${where} ${action === 'drop' ? 'nicht mehr speichern' : 'nur noch kurz behalten'}.\n\nVorhandene Zeilen dieser Art gleich löschen?`)
  await run(async () => {
    const r = await auth.api('/api/log-handling/rules/from-log', {
      method: 'POST', body: { log_id: g.sample_id, scope, action, purge_existing: purge },
    })
    await Promise.all([loadOverview(), loadNoise()])
    notice.value = `Regel „${r.name}“ angelegt` + (r.purged ? `, ${r.purged.toLocaleString('de-DE')} vorhandene Zeilen gelöscht.` : '.')
  })
}
async function previewRule() {
  await run(async () => {
    const r = await auth.api('/api/log-handling/rules/preview', { method: 'POST', body: form.value })
    preview.value = r.hits_in_sample
  })
}
async function saveRule() {
  await run(async () => {
    await auth.api('/api/log-handling/rules', { method: 'POST', body: form.value })
    form.value = null
    preview.value = null
    await loadOverview()
  }, 'Regel gespeichert – greift ab sofort beim Empfang.')
}
async function toggleRule(r) {
  await run(async () => {
    await auth.api(`/api/log-handling/rules/${r.id}`, { method: 'PUT', body: { ...r, enabled: !r.enabled } })
    await loadOverview()
  })
}
async function removeRule(r) {
  if (!confirm(`Regel „${r.name}“ löschen? Solche Zeilen werden dann wieder gespeichert.`)) return
  await run(async () => {
    await auth.api(`/api/log-handling/rules/${r.id}`, { method: 'DELETE' })
    await loadOverview()
  }, 'Regel gelöscht.')
}
async function purge(r) {
  if (!confirm(`Alle vorhandenen Zeilen löschen, die „${r.name}“ trifft?`)) return
  await run(async () => {
    const res = await auth.api(`/api/log-handling/rules/${r.id}/purge`, { method: 'POST' })
    notice.value = `${res.deleted.toLocaleString('de-DE')} Zeilen gelöscht.`
  })
}
async function saveTiers() {
  await run(async () => {
    const r = await auth.api('/api/log-handling/tiers', { method: 'PUT', body: { tiers: tiers.value } })
    tiers.value = { ...r.tiers }
  }, 'Fristen gespeichert – greifen beim nächsten stündlichen Lauf.')
}
async function saveDedup() {
  await run(async () => {
    dedup.value = await auth.api('/api/log-handling/dedup', { method: 'PUT', body: dedup.value })
  }, 'Gespeichert.')
}

onMounted(loadAll)
</script>

<style scoped>
.lh-kpis { display: flex; flex-wrap: wrap; gap: 1.5rem; }
.lh-kpi { display: flex; flex-direction: column; }
.lh-kpi-value { font-size: 1.375rem; font-weight: 700; color: var(--color-text-primary); }
.lh-kpi-label { font-size: .75rem; color: var(--color-text-muted); }
.lh-sample { font-size: .75rem; margin-top: .25rem; white-space: pre-wrap; word-break: break-all; max-width: 42rem; }
.lh-actions { white-space: nowrap; }
.lh-actions .btn { margin: 0 .25rem .25rem 0; }
.lh-levels { display: flex; gap: 1rem; flex-wrap: wrap; }
.lh-levels label { flex-direction: row; align-items: center; gap: .35rem; }
.lh-topics { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr)); }
.lh-topic { display: flex; flex-direction: column; gap: .25rem; padding: .75rem 1rem; text-align: left; cursor: pointer; }
.lh-topic.is-active { outline: 2px solid var(--color-primary); }
</style>
