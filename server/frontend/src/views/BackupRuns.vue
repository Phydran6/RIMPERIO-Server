<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Backup -> Verlauf & Wiederherstellen: jeder Lauf mit
                   Ort, Groesse, Pruefsumme, Protokoll. Herunterladen (entschluesselt
                   oder wie gespeichert), Probe-Wiederherstellung, RIMPERIO zurueckspielen.
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Backup – Verlauf & Wiederherstellen</h1>
        <p class="page-subtitle">„Prüfen“ holt eine Sicherung vom Ort, vergleicht die Prüfsumme und entschlüsselt sie zur Probe –
          ohne etwas zu ersetzen. Ohne RIMPERIO entschlüsseln: <span class="rmm-code">docs/backup/rimperio-decrypt.py</span> mit dem
          Passwort aus Vaultwarden.</p>
      </div>
      <div class="rmm-actions">
        <select v-model="jobFilter" class="select" @change="load">
          <option :value="''">alle Aufträge</option>
          <option v-for="j in jobs" :key="j.id" :value="String(j.id)">{{ j.name }}</option>
        </select>
        <button class="btn btn-secondary btn-sm" @click="load"><AppIcon name="refresh" :size="15" /></button>
      </div>
    </div>
    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }} <router-link v-if="restoreName" to="/backup">Zur Sicherung → Zurückspielen</router-link></p>

    <section class="card">
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Wann</th><th>Auftrag</th><th>Status</th><th>Ort</th><th>Größe</th><th></th></tr></thead>
          <tbody>
            <tr v-if="!runs.length"><td colspan="6" class="rmm-muted">Noch keine Läufe.</td></tr>
            <template v-for="r in runs" :key="r.id">
              <tr>
                <td>{{ fmtTime(r.started_at) }}<div class="rmm-muted">{{ r.trigger === 'manual' ? r.created_by : 'Zeitplan' }}{{ r.duration_s != null ? ' · ' + fmtDur(r.duration_s) : '' }}</div></td>
                <td>{{ r.job_name }}</td>
                <td><span class="badge" :class="RUN[r.status]?.[0]">{{ RUN[r.status]?.[1] || r.status }}</span>
                  <span v-if="r.encrypted" class="badge badge-success">verschlüsselt</span>
                  <span v-if="r.detail?.verified" class="badge badge-primary" :title="'geprüft ' + fmtTime(r.detail.verified.checked_at)">geprüft</span>
                  <div v-if="r.error" class="rmm-muted br-err">{{ r.error }}</div></td>
                <td class="rmm-code br-loc">{{ r.location || '–' }}</td>
                <td>{{ fmtBytes(r.size_bytes) }}<div v-if="r.plain_bytes && r.plain_bytes !== r.size_bytes" class="rmm-muted">Inhalt {{ fmtBytes(r.plain_bytes) }}</div></td>
                <td class="rmm-actions" style="justify-content:flex-end">
                  <button class="btn btn-ghost btn-sm" @click="open[r.id] = !open[r.id]">Protokoll</button>
                  <template v-if="auth.isAdmin && r.status === 'done' && r.file_name && r.detail?.target_id">
                    <button class="btn btn-secondary btn-sm" :disabled="busy" @click="download(r, true)">Herunterladen</button>
                    <button v-if="r.encrypted" class="btn btn-ghost btn-sm" :disabled="busy" title="verschlüsselt, wie am Ort gespeichert" @click="download(r, false)">.rimpbak</button>
                    <button class="btn btn-ghost btn-sm" :disabled="busy" @click="verify(r)">Prüfen</button>
                    <button v-if="r.detail?.content === 'rimperio-backup'" class="btn btn-primary btn-sm" :disabled="busy" @click="toRestore(r)">Zurückspielen …</button>
                    <button class="btn btn-ghost btn-sm" :disabled="busy" @click="remove(r)">Löschen</button>
                  </template>
                </td>
              </tr>
              <tr v-if="open[r.id]" class="br-log-row">
                <td colspan="6">
                  <pre class="rmm-pre">{{ (r.log || []).map(l => (l.at || '').slice(11, 19) + '  ' + l.text).join('\n') || '–' }}</pre>
                  <div v-if="r.sha256" class="rmm-muted rmm-code">SHA-256: {{ r.sha256 }}</div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import { fmtTime } from '../utils/rmm'

const auth = useAuthStore()
const route = useRoute()
const runs = ref([])
const jobs = ref([])
const jobFilter = ref(route.query.job ? String(route.query.job) : '')
const open = ref({})
const error = ref('')
const notice = ref('')
const restoreName = ref('')
const busy = ref(false)
let timer = null

const RUN = { done: ['badge-success', 'ok'], error: ['badge-danger', 'Fehler'], running: ['badge-primary', 'läuft'],
  waiting: ['badge-primary', 'wartet auf Rechner'], deleted: ['badge-neutral', 'entfernt (Aufbewahrung)'] }

function fmtBytes(v) {
  if (!v) return '–'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0; let x = Number(v)
  while (x >= 1024 && i < u.length - 1) { x /= 1024; i++ }
  return (i >= 2 ? x.toFixed(1) : Math.round(x)) + ' ' + u[i]
}
const fmtDur = s => (s < 90 ? `${s} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`)

async function load() {
  try {
    const q = jobFilter.value ? `?job_id=${jobFilter.value}` : ''
    const [r, j] = await Promise.all([auth.api('/api/backups/runs' + q), auth.api('/api/backups/jobs')])
    runs.value = r; jobs.value = j; error.value = ''
    clearTimeout(timer)
    if (r.some(x => ['running', 'waiting'].includes(x.status))) timer = setTimeout(load, 4000)
  } catch (e) { error.value = e.message }
}
async function download(r, decrypt) {
  try {
    const { ticket } = await auth.api('/api/auth/ticket', { method: 'POST', body: { purpose: 'backup-download' } })
    window.location.href = `/api/backups/runs/${r.id}/download?decrypt=${decrypt}&token=${encodeURIComponent(ticket)}`
  } catch (e) { error.value = e.message }
}
async function verify(r) {
  busy.value = true
  try {
    const res = await auth.api(`/api/backups/runs/${r.id}/verify`, { method: 'POST' })
    if (res.ok) { notice.value = `Geprüft: ${res.name} lässt sich vollständig zurückholen (${res.entries ?? '?'} Einträge).`; restoreName.value = '' } else { error.value = res.message }
    await load()
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function toRestore(r) {
  if (!confirm('Diese RIMPERIO-Sicherung entschlüsseln und unter „Sicherung“ bereitlegen? Zurückgespielt wird erst dort, nach Auswahl der Bereiche.')) return
  busy.value = true
  try {
    const res = await auth.api(`/api/backups/runs/${r.id}/to-restore`, { method: 'POST' })
    restoreName.value = res.name
    notice.value = `Bereitgelegt als ${res.name}.`
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function remove(r) {
  if (!confirm('Diese Sicherung am Ort löschen?')) return
  try { await auth.api(`/api/backups/runs/${r.id}`, { method: 'DELETE' }); await load() } catch (e) { error.value = e.message }
}
onMounted(load)
onBeforeUnmount(() => clearTimeout(timer))
</script>

<style scoped>
.br-err { color: var(--color-danger); max-width: 22rem; overflow-wrap: anywhere; }
.br-loc { max-width: 22rem; overflow-wrap: anywhere; font-size: .78rem; }
.br-log-row td { background: var(--color-surface-alt, transparent); }
td .badge + .badge { margin-left: .25rem; }
</style>
