<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - SNMP-Gerät: Messwerte, Verlauf, Schwellen, Einstellungen, Alarme
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <router-link to="/rmm/snmp" class="rmm-muted">← SNMP-Geräte</router-link>
        <h1 class="page-title">{{ d?.name || '…' }}</h1>
        <p v-if="d" class="page-subtitle">{{ d.host }} · {{ d.system?.sys_descr }}</p>
      </div>
      <div v-if="d && auth.isAdmin" class="rmm-actions">
        <button class="btn btn-secondary btn-sm" :disabled="busy" @click="pollNow">Jetzt abfragen</button>
        <button class="btn btn-ghost btn-sm" @click="edit = edit ? null : JSON.parse(JSON.stringify(d))">Einstellungen</button>
        <button class="btn btn-danger btn-sm" @click="remove">Entfernen</button>
      </div>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>

    <template v-if="d">
      <section class="card mb-4">
        <div class="card-body">
          <dl class="rmm-grid">
            <div><dt>Status</dt><dd><span class="badge" :class="SNMP_STATUS[d.status]?.[0]">{{ SNMP_STATUS[d.status]?.[1] }}</span></dd></div>
            <div><dt>sysName</dt><dd>{{ d.system?.sys_name || '–' }}</dd></div>
            <div><dt>Standort (Gerät)</dt><dd>{{ d.system?.sys_location || '–' }}</dd></div>
            <div><dt>Laufzeit</dt><dd>{{ fmtUptime(d.system?.uptime_seconds) }}</dd></div>
            <div><dt>Letzte Antwort</dt><dd>{{ fmtAgo(d.last_ok_at) }}<span v-if="d.rtt_ms"> · {{ d.rtt_ms }} ms</span></dd></div>
            <div><dt>Abfrage</dt><dd>{{ d.poller === 'server' ? 'Server' : 'Agent' }} · alle {{ d.interval_sec }} s</dd></div>
            <div><dt>sysObjectID</dt><dd class="rmm-code">{{ d.system?.sys_object_id || '–' }}</dd></div>
          </dl>
          <p v-if="d.last_error" class="rmm-note is-error mt-4">{{ d.last_error }}</p>
        </div>
      </section>

      <section v-if="edit" class="card mb-4">
        <div class="card-header"><h2 class="card-title">Einstellungen</h2></div>
        <div class="card-body rmm-form">
          <label>Name<input v-model="edit.name" class="input" /></label>
          <label>Adresse<input v-model="edit.host" class="input" /></label>
          <label>Port (leer = Zugangsdaten)<input v-model.number="edit.port" type="number" class="input" /></label>
          <label>Zugangsdaten
            <select v-model="edit.credential_id" class="select"><option :value="null">v2c „public“</option>
              <option v-for="c in credentials" :key="c.id" :value="c.id">{{ c.name }}</option></select>
          </label>
          <label>Abfrage durch
            <select v-model="edit.poller" class="select"><option value="server">Server</option>
              <option v-for="a in agents" :key="a.agent_uid" :value="a.agent_uid">Agent {{ a.hostname }}</option></select>
          </label>
          <label>Standort<input v-model="edit.site" class="input" /></label>
          <label>Abfrage alle (Sekunden)<input v-model.number="edit.interval_sec" type="number" min="30" class="input" /></label>
          <label class="pol-check"><input v-model="edit.enabled" type="checkbox" /> aktiv</label>
          <label class="pol-check is-wide">Vorlagen:
            <span v-for="t in templates" :key="t.key"><input v-model="edit.templates" type="checkbox" :value="t.key" /> {{ t.name }}</span>
          </label>
          <label class="is-wide">Eigene Werte (JSON-Liste, z. B. <span class="rmm-code">[{"key":"temp","label":"Temperatur","oid":"1.3.6.1.4.1.…","type":"gauge","unit":"°C","warn":60,"crit":70}]</span>)
            <textarea v-model="customText" class="input rmm-code" rows="5" /></label>
          <div class="is-wide rmm-actions">
            <button class="btn btn-primary btn-sm" :disabled="busy" @click="save">Speichern</button>
            <button class="btn btn-ghost btn-sm" @click="edit = null">Abbrechen</button>
          </div>
        </div>
      </section>

      <section v-for="(rows, group) in groups" :key="group" class="card mb-4">
        <div class="card-header"><h2 class="card-title">{{ group || 'Werte' }}</h2></div>
        <div class="rmm-table-wrap">
          <table class="table">
            <thead><tr><th>Wert</th><th>Stand</th><th>Status</th><th v-if="auth.isAdmin">Schwelle (Warnung / kritisch)</th><th></th></tr></thead>
            <tbody>
              <template v-for="m in rows" :key="m.key">
                <tr>
                  <td>{{ m.label }}</td>
                  <td><strong>{{ m.text ?? fmtRate(m.value, m.unit) }}</strong></td>
                  <td><span class="badge" :class="SNMP_STATUS[m.status]?.[0]">{{ SNMP_STATUS[m.status]?.[1] || m.status }}</span></td>
                  <td v-if="auth.isAdmin" class="rmm-actions">
                    <template v-if="m.text === null || m.text === undefined">
                      <input v-model="ov(m).warn" class="input snmp-th" placeholder="Std." />
                      <input v-model="ov(m).crit" class="input snmp-th" placeholder="Std." />
                      <label class="rmm-muted"><input v-model="ov(m).alert" type="checkbox" :true-value="false" :false-value="undefined" /> stumm</label>
                    </template>
                  </td>
                  <td><button v-if="m.value !== null" class="btn btn-ghost btn-sm" @click="toggleChart(m)">{{ chartKey === m.key ? 'schließen' : 'Verlauf' }}</button></td>
                </tr>
                <tr v-if="chartKey === m.key">
                  <td :colspan="auth.isAdmin ? 5 : 4">
                    <div class="rmm-actions mb-2"><strong>{{ m.label }}</strong> — letzte
                      <select v-model.number="hours" class="select" style="max-width:8rem" @change="loadChart(m)"><option :value="6">6 h</option><option :value="24">24 h</option><option :value="168">7 Tage</option><option :value="336">14 Tage</option></select></div>
                    <MetricChart :points="chart" :unit="m.unit" :label="m.label" />
                    <p v-if="!chart.length" class="rmm-muted">Verlauf wird nur für ausgewählte Werte gespeichert (Datenrate, CPU, Speicher, Temperaturen, USV).</p>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
        <div v-if="auth.isAdmin && dirtyOverrides" class="card-body"><button class="btn btn-primary btn-sm" @click="saveOverrides">Schwellen speichern</button>
          <span class="rmm-muted"> Gilt je Wert für alle Zeilen (z. B. alle Ports).</span></div>
      </section>

      <section class="card">
        <div class="card-header"><h2 class="card-title">Alarme</h2></div>
        <div class="rmm-table-wrap">
          <table class="table">
            <thead><tr><th>Seit</th><th>Meldung</th><th>Schwere</th><th>Zustand</th></tr></thead>
            <tbody>
              <tr v-if="!d.alerts.length"><td colspan="4" class="rmm-muted">Keine Alarme.</td></tr>
              <tr v-for="a in d.alerts" :key="a.id"><td>{{ fmtTime(a.created_at) }}</td><td>{{ a.message }}</td>
                <td><span class="badge" :class="a.severity === 'critical' ? 'badge-danger' : 'badge-warning'">{{ a.severity }}</span></td>
                <td>{{ a.active ? 'aktiv' : `behoben ${fmtTime(a.resolved_at)}` }}</td></tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import MetricChart from '../components/MetricChart.vue'
import { fmtAgo, fmtRate, fmtTime, fmtUptime, SNMP_STATUS } from '../utils/rmm'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const d = ref(null)
const edit = ref(null)
const credentials = ref([])
const templates = ref([])
const agents = ref([])
const overrides = ref({})
const dirtyOverrides = ref(false)
const chartKey = ref('')
const chart = ref([])
const hours = ref(24)
const customText = ref('[]')
const error = ref('')
const notice = ref('')
const busy = ref(false)
let timer = null

const baseKey = key => key.split(':')[0]
const groups = computed(() => {
  const out = {}
  for (const m of d.value?.metrics || []) (out[m.group] ||= []).push(m)
  return out
})
function ov(m) {
  const k = baseKey(m.key)
  if (!overrides.value[k]) overrides.value[k] = {}
  return overrides.value[k]
}
watch(overrides, () => { dirtyOverrides.value = true }, { deep: true })

async function load() {
  try {
    d.value = await auth.api(`/api/snmp/devices/${route.params.id}`)
    if (!dirtyOverrides.value) {
      overrides.value = JSON.parse(JSON.stringify(d.value.overrides || {}))
      setTimeout(() => { dirtyOverrides.value = false })
    }
    error.value = ''
  } catch (e) { error.value = e.message }
}
async function loadMeta() {
  ;[credentials.value, templates.value, agents.value] = await Promise.all([
    auth.api('/api/snmp/credentials'), auth.api('/api/snmp/templates'), auth.api('/api/rmm/agents')])
}
async function toggleChart(m) {
  if (chartKey.value === m.key) { chartKey.value = ''; return }
  chartKey.value = m.key
  await loadChart(m)
}
async function loadChart(m) {
  chart.value = await auth.api(`/api/snmp/devices/${d.value.id}/history?key=${encodeURIComponent(m.key)}&hours=${hours.value}`)
}
function body(src) {
  const { id, status, system, last_poll_at, last_ok_at, last_error, rtt_ms, metrics, alerts, ...rest } = src
  return rest
}
async function save() {
  busy.value = true
  try {
    let custom
    try { custom = JSON.parse(customText.value || '[]') } catch { throw new Error('Eigene Werte: kein gültiges JSON.') }
    await auth.api(`/api/snmp/devices/${d.value.id}`, { method: 'PUT', body: { ...body(edit.value), custom, port: edit.value.port || null } })
    edit.value = null; notice.value = 'Gespeichert.'; await load()
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function saveOverrides() {
  const clean = {}
  for (const [k, v] of Object.entries(overrides.value)) {
    const o = {}
    if (v.warn !== undefined && v.warn !== '') o.warn = Number(v.warn)
    if (v.crit !== undefined && v.crit !== '') o.crit = Number(v.crit)
    if (v.alert === false) o.alert = false
    if (Object.keys(o).length) clean[k] = o
  }
  try {
    await auth.api(`/api/snmp/devices/${d.value.id}`, { method: 'PUT', body: { ...body(d.value), overrides: clean } })
    dirtyOverrides.value = false; notice.value = 'Schwellen gespeichert — gelten ab der nächsten Abfrage.'; await load()
  } catch (e) { error.value = e.message }
}
async function pollNow() {
  busy.value = true
  try { const r = await auth.api(`/api/snmp/devices/${d.value.id}/poll`, { method: 'POST' }); notice.value = r.message || (r.ok ? `Antwort in ${r.rtt_ms} ms.` : `Keine Antwort: ${r.error}`); await load() }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
async function remove() {
  if (!confirm(`${d.value.name} nicht mehr überwachen?`)) return
  try { await auth.api(`/api/snmp/devices/${d.value.id}`, { method: 'DELETE' }); router.push('/rmm/snmp') } catch (e) { error.value = e.message }
}
watch(edit, v => { if (v) customText.value = JSON.stringify(v.custom || [], null, 2) })

onMounted(async () => { await load(); await loadMeta().catch(() => {}); timer = setInterval(load, 30000) })
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.pol-check { flex-direction: row !important; align-items: center; gap: .5rem !important; flex-wrap: wrap; }
.pol-check > span { display: inline-flex; gap: .25rem; align-items: center; margin-right: .75rem; }
.snmp-th { max-width: 5rem; }
</style>
