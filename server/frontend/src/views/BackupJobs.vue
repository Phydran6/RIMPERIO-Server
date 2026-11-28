<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Backup -> Auftraege: WAS gesichert wird, WANN und WOHIN.
                   RIMPERIO selbst, Ordner auf Rechnern (auch Raspberry Pi),
                   NAS-Konfiguration, Hyper Backup, Proxmox-VMs.
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Backup – Aufträge</h1>
        <p class="page-subtitle">
          Was wird gesichert, wann und wohin. Alles, was RIMPERIO selbst ablegt, ist verschlüsselt (AES-256-GCM);
          das Passwort liegt {{ ov.key?.backend === 'vaultwarden' ? 'in Vaultwarden' : 'lokal verschlüsselt' }} als „{{ ov.key?.name || 'Backup-Schlüssel' }}“.
          Orte unter <router-link to="/backups/targets">Orte</router-link>, Ergebnisse unter <router-link to="/backups/runs">Verlauf</router-link>.
        </p>
      </div>
      <button v-if="auth.isAdmin" class="btn btn-primary btn-sm" :disabled="!targets.length && false" @click="openForm()"><AppIcon name="plus" :size="16" /> Auftrag anlegen</button>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>
    <p v-if="auth.isAdmin && !targets.length" class="rmm-note is-info">Noch kein Ort angelegt – zuerst unter
      <router-link to="/backups/targets">Orte</router-link> festlegen, wohin gesichert wird (z. B. eine SMB-Freigabe auf dem NAS).</p>
    <p v-if="auth.isAdmin && !ov.vault_active" class="rmm-note is-info">Tipp: Mit <router-link to="/integrations/vault">Vaultwarden</router-link>
      liegen Backup-Passwort und alle Zugänge zentral in deinem Tresor statt nur auf diesem Server.</p>

    <div class="bj-tiles">
      <div class="card bj-tile"><span class="rmm-muted">Aufträge</span><strong>{{ ov.jobs || 0 }}</strong><span class="rmm-muted">{{ ov.enabled || 0 }} aktiv</span></div>
      <div class="card bj-tile"><span class="rmm-muted">Zuletzt erfolgreich</span><strong class="is-ok">{{ ov.ok || 0 }}</strong></div>
      <div class="card bj-tile"><span class="rmm-muted">Fehlgeschlagen</span><strong :class="ov.failed ? 'is-bad' : ''">{{ ov.failed || 0 }}</strong></div>
      <div class="card bj-tile"><span class="rmm-muted">Überfällig</span><strong :class="ov.overdue ? 'is-warn' : ''">{{ ov.overdue || 0 }}</strong></div>
      <div class="card bj-tile"><span class="rmm-muted">Belegt an den Orten</span><strong>{{ fmtBytes(ov.stored_bytes) }}</strong></div>
    </div>

    <section class="card">
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Auftrag</th><th>Was</th><th>Wohin</th><th>Wann</th><th>Letzter Lauf</th><th></th></tr></thead>
          <tbody>
            <tr v-if="!jobs.length"><td colspan="6" class="rmm-muted">Noch keine Aufträge.</td></tr>
            <tr v-for="j in jobs" :key="j.id" :style="j.enabled ? '' : 'opacity:.6'">
              <td><strong>{{ j.name }}</strong>
                <div><span class="badge badge-neutral">{{ j.kind_label }}</span>
                  <span v-if="j.encrypt" class="badge badge-success" title="AES-256-GCM">verschlüsselt</span>
                  <span v-if="j.overdue" class="badge badge-warning">überfällig</span></div></td>
              <td class="bj-src">{{ j.source_label }}</td>
              <td>{{ j.target ? j.target.name : (j.kind === 'nas_task' ? 'Ziel der Hyper-Backup-Aufgabe' : j.kind === 'pve' ? 'Proxmox-Speicher' : '–') }}</td>
              <td>{{ fmtSchedule(j.schedule) }}<div class="rmm-muted">nächster: {{ j.next_run_at ? fmtTime(j.next_run_at) : '–' }}</div>
                <div v-if="!['nas_task', 'pve'].includes(j.kind)" class="rmm-muted">behalten: {{ j.keep_last ? j.keep_last : '' }}{{ j.keep_days ? `${j.keep_last ? ', mind. ' : ''}${j.keep_days} Tage` : '' }}</div></td>
              <td>
                <template v-if="j.last_run">
                  <span class="badge" :class="RUN[j.last_run.status]?.[0]">{{ RUN[j.last_run.status]?.[1] || j.last_run.status }}</span>
                  {{ fmtAgo(j.last_run.started_at) }}
                  <div class="rmm-muted">{{ fmtBytes(j.last_run.size_bytes) }}</div>
                  <div v-if="j.last_run.error" class="rmm-muted bj-err">{{ j.last_run.error }}</div>
                </template>
                <span v-else class="rmm-muted">noch nie</span>
              </td>
              <td class="rmm-actions" style="justify-content:flex-end">
                <template v-if="auth.isAdmin">
                  <button class="btn btn-primary btn-sm" :disabled="busy || j.running" @click="run(j)">{{ j.running ? 'läuft …' : 'Jetzt sichern' }}</button>
                  <router-link class="btn btn-ghost btn-sm" :to="{ path: '/backups/runs', query: { job: j.id } }">Verlauf</router-link>
                  <button class="btn btn-ghost btn-sm" @click="openForm(j)">Bearbeiten</button>
                  <button class="btn btn-ghost btn-sm" @click="remove(j)">Entfernen</button>
                </template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Formular ------------------------------------------------------------ -->
    <div v-if="form" class="modal-backdrop" @click.self="form = null">
      <div class="modal max-w-3xl">
        <div class="card-header"><h2 class="card-title">{{ form.id ? form.name : 'Backup-Auftrag anlegen' }}</h2>
          <button class="btn-icon" aria-label="Schließen" @click="form = null"><AppIcon name="close" :size="18" /></button></div>
        <div class="card-body rmm-form overflow-y-auto">
          <label class="is-wide">Was soll gesichert werden?
            <select v-model="form.kind" class="select" :disabled="!!form.id">
              <option v-for="(label, k) in KINDS" :key="k" :value="k">{{ label }}</option>
            </select></label>
          <label class="is-wide">Name<input v-model="form.name" class="input" :placeholder="suggestName" /></label>

          <!-- Quelle -->
          <template v-if="form.kind === 'rimperio'">
            <div class="is-wide bj-scopes">
              <span class="rmm-muted">Bereiche (leer = Standard):</span>
              <label v-for="s in scopes" :key="s.id" class="pol-check" :title="s.hint"><input v-model="form.source.scopes" type="checkbox" :value="s.id" /> {{ s.label }}</label>
            </div>
          </template>
          <template v-if="form.kind === 'files'">
            <label class="is-wide">Rechner
              <select v-model="form.source.agent_uid" class="select">
                <option value="">— wählen —</option>
                <option v-for="a in agents" :key="a.agent_uid" :value="a.agent_uid">{{ a.hostname }} ({{ a.platform }}{{ a.online ? '' : ', offline' }})</option>
              </select></label>
            <label class="is-wide">Ordner (einer je Zeile)
              <textarea v-model="pathsText" class="input" rows="3" :placeholder="selectedAgent?.platform === 'windows' ? 'C:\\Daten\nD:\\Projekte' : '/etc\n/home\n/opt/homeassistant'" /></label>
            <label class="is-wide">Ausschließen (Muster, einer je Zeile, optional)
              <textarea v-model="excludesText" class="input" rows="2" placeholder="*.tmp&#10;node_modules" /></label>
            <p class="is-wide rmm-muted">Der Agent packt die Ordner (tar.gz) und schickt sie per HTTPS an RIMPERIO; verschlüsselt wird beim Empfang,
              der Rechner bekommt nie Zugang zum Ort. Linux: mit root-Rechten über das Hilfsskript des Agents (Agent ab diesem Stand),
              sonst nur, was das Dienstkonto lesen darf. Windows: tar.exe (ab Windows 10).</p>
          </template>
          <template v-if="form.kind === 'nas_config' || form.kind === 'nas_task'">
            <label class="is-wide">NAS
              <select v-model.number="form.source.nas_id" class="select" @change="loadTasks">
                <option :value="null">— wählen —</option>
                <option v-for="n in nasList.filter(x => x.vendor === 'synology')" :key="n.id" :value="n.id">{{ n.name }} ({{ n.host }})</option>
              </select></label>
            <label v-if="form.kind === 'nas_task'" class="is-wide">Hyper-Backup-Aufgabe
              <select v-model="form.source.task_id" class="select" @change="taskName">
                <option :value="null">— wählen —</option>
                <option v-for="t in tasks" :key="t.id" :value="t.id">{{ t.name }} (#{{ t.id }})</option>
              </select></label>
            <p v-if="form.kind === 'nas_config'" class="is-wide rmm-muted">Die DSM-Konfiguration (.dss: Benutzer, Freigaben, Einstellungen) – klein und schnell, ideal vor jedem Update.</p>
            <p v-else class="is-wide rmm-muted">RIMPERIO startet die Aufgabe auf dem NAS und wartet auf das Ergebnis. Ziel, Versionierung und Verschlüsselung
              stellt Hyper Backup selbst ein.</p>
          </template>
          <template v-if="form.kind === 'pve'">
            <label>Proxmox-Host
              <select v-model.number="form.source.host_id" class="select">
                <option :value="null">— wählen —</option>
                <option v-for="h in pveHosts" :key="h.id" :value="h.id">{{ h.name }}</option>
              </select></label>
            <label>VM / Container
              <select v-model="guestKey" class="select">
                <option value="">— wählen —</option>
                <option v-for="g in guests" :key="g.key" :value="g.key">{{ g.vmid }} · {{ g.name }} ({{ g.type === 'lxc' ? 'CT' : 'VM' }}, {{ g.node }})</option>
              </select></label>
            <label class="is-wide">Speicher in Proxmox (leer = Standard des Hosts)<input v-model="form.source.storage" class="input" placeholder="local / pbs" /></label>
          </template>

          <!-- Ziel -->
          <label v-if="!['nas_task', 'pve'].includes(form.kind)" class="is-wide">Wohin
            <select v-model.number="form.target_id" class="select">
              <option :value="null">— Ort wählen —</option>
              <option v-for="t in targets" :key="t.id" :value="t.id">{{ t.name }} – {{ t.kind_label }}</option>
            </select></label>
          <template v-if="!['nas_task', 'pve'].includes(form.kind)">
            <label class="pol-check"><input v-model="form.encrypt" type="checkbox" /> verschlüsseln (empfohlen)</label>
            <label v-if="form.encrypt" class="pol-check"><input v-model="form.own_key" type="checkbox" /> eigener Schlüssel für diesen Auftrag</label>
          </template>

          <!-- Zeitplan -->
          <label>Zeitplan
            <select v-model="form.schedule.type" class="select">
              <option value="daily">täglich</option><option value="weekly">an bestimmten Tagen</option>
              <option value="hourly">alle paar Stunden</option><option value="manual">nur von Hand</option>
            </select></label>
          <label v-if="['daily', 'weekly'].includes(form.schedule.type)">Uhrzeit<input v-model="form.schedule.time" type="time" class="input" /></label>
          <label v-if="form.schedule.type === 'hourly'">alle … Stunden<input v-model.number="form.schedule.every_hours" type="number" min="1" max="168" class="input" /></label>
          <div v-if="form.schedule.type === 'weekly'" class="is-wide bj-days">
            <label v-for="(dn, i) in DAYS" :key="i" class="pol-check"><input v-model="form.schedule.days" type="checkbox" :value="i" /> {{ dn }}</label>
          </div>
          <label v-if="!['nas_task', 'pve'].includes(form.kind)">Behalten (letzte n, 0 = nur nach Tagen)<input v-model.number="form.keep_last" type="number" min="0" class="input" /></label>
          <label v-if="!['nas_task', 'pve'].includes(form.kind)">… aber mindestens (Tage)<input v-model.number="form.keep_days" type="number" min="0" class="input" /></label>
          <label>Melden, wenn älter als (Stunden, 0 = aus Zeitplan)<input v-model.number="form.max_age_hours" type="number" min="0" class="input" /></label>
          <label class="pol-check"><input v-model="form.enabled" type="checkbox" /> aktiv</label>
          <label class="is-wide">Notiz<input v-model="form.note" class="input" /></label>
          <p v-if="formError" class="is-wide rmm-note is-error">{{ formError }}</p>
          <div class="is-wide rmm-actions">
            <button class="btn btn-primary btn-sm" :disabled="busy" @click="save">Speichern</button>
            <button class="btn btn-ghost btn-sm" @click="form = null">Abbrechen</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import { fmtAgo, fmtTime } from '../utils/rmm'

const auth = useAuthStore()
const route = useRoute()
const jobs = ref([])
const targets = ref([])
const ov = ref({})
const agents = ref([])
const nasList = ref([])
const pveHosts = ref([])
const scopes = ref([])
const tasks = ref([])
const form = ref(null)
const formError = ref('')
const error = ref('')
const notice = ref('')
const busy = ref(false)
const pathsText = ref('')
const excludesText = ref('')
const guestKey = ref('')
let timer = null

const KINDS = { rimperio: 'RIMPERIO-Server (Datenbank, Einstellungen)', files: 'Ordner auf einem Rechner (Linux, Windows, Raspberry Pi)',
  nas_config: 'NAS-Konfiguration (Synology .dss)', nas_task: 'Hyper Backup auf dem NAS anstoßen und überwachen', pve: 'Proxmox-VM/-Container (vzdump)' }
const RUN = { done: ['badge-success', 'ok'], error: ['badge-danger', 'Fehler'], running: ['badge-primary', 'läuft'],
  waiting: ['badge-primary', 'wartet auf Rechner'], deleted: ['badge-neutral', 'entfernt'] }
const DAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']

const selectedAgent = computed(() => agents.value.find(a => a.agent_uid === form.value?.source?.agent_uid))
const guests = computed(() => {
  const h = pveHosts.value.find(x => x.id === form.value?.source?.host_id)
  return (h?.guests || []).map(g => ({ ...g, key: `${g.node}|${g.vmid}|${g.type || 'qemu'}` }))
})
const suggestName = computed(() => {
  const f = form.value
  if (!f) return ''
  if (f.kind === 'files') return (selectedAgent.value?.hostname || 'Rechner') + ' – Ordner'
  if (f.kind === 'nas_config') return (nasList.value.find(n => n.id === f.source.nas_id)?.name || 'NAS') + ' – Konfiguration'
  if (f.kind === 'nas_task') return (nasList.value.find(n => n.id === f.source.nas_id)?.name || 'NAS') + ' – ' + (f.source.task_name || 'Hyper Backup')
  if (f.kind === 'pve') return 'Proxmox ' + (f.source.vmid || '')
  return 'RIMPERIO-Server'
})
watch(guestKey, k => {
  if (!form.value || !k) return
  const [node, vmid, type] = k.split('|')
  Object.assign(form.value.source, { node, vmid: Number(vmid), guest_type: type })
})

function fmtBytes(v) {
  if (v == null || isNaN(v) || !v) return '–'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0; let x = Number(v)
  while (x >= 1024 && i < u.length - 1) { x /= 1024; i++ }
  return (i >= 2 ? x.toFixed(1) : Math.round(x)) + ' ' + u[i]
}
function fmtSchedule(s) {
  if (!s || s.type === 'manual') return 'nur von Hand'
  if (s.type === 'hourly') return `alle ${s.every_hours || 6} h`
  if (s.type === 'weekly') return (s.days || []).map(d => DAYS[d]).join(', ') + ' ' + (s.time || '')
  return 'täglich ' + (s.time || '02:30')
}

async function load() {
  try {
    const [j, t, o] = await Promise.all([auth.api('/api/backups/jobs'), auth.api('/api/backups/targets'), auth.api('/api/backups/overview')])
    jobs.value = j; targets.value = t; ov.value = o
    error.value = ''
    clearTimeout(timer)
    if (j.some(x => x.running || ['running', 'waiting'].includes(x.last_run?.status))) timer = setTimeout(load, 4000)
  } catch (e) { error.value = e.message }
}
async function loadRefs() {
  const tasksList = [auth.api('/api/rmm/agents').catch(() => []), auth.api('/api/nas').catch(() => []),
    auth.api('/api/patching/pve/hosts').catch(() => []), auth.api('/api/backup/overview').catch(() => ({ scopes: [] }))]
  const [a, n, p, b] = await Promise.all(tasksList)
  agents.value = a; nasList.value = n; pveHosts.value = p; scopes.value = b.scopes || []
}
async function loadTasks() {
  tasks.value = []
  if (form.value?.kind === 'nas_task' && form.value.source.nas_id) {
    try { tasks.value = await auth.api(`/api/nas/${form.value.source.nas_id}/backup-tasks`) } catch { /* leer */ }
  }
}
function taskName() {
  const t = tasks.value.find(x => x.id === form.value.source.task_id)
  form.value.source.task_name = t?.name || ''
}

function blank(kind = 'rimperio') {
  return { name: '', kind, source: { scopes: [], paths: [], excludes: [], agent_uid: '', nas_id: null, task_id: null, host_id: null, storage: '' },
    target_id: targets.value[0]?.id ?? null, encrypt: true, own_key: false,
    schedule: { type: 'daily', time: '02:30', days: [6], every_hours: 6, tz: 'Europe/Berlin' },
    keep_last: 14, keep_days: 0, max_age_hours: 0, enabled: true, note: '' }
}
async function openForm(j, preset) {
  formError.value = ''
  await loadRefs()
  if (j) {
    form.value = { ...blank(j.kind), ...JSON.parse(JSON.stringify(j)), own_key: !!j.key_secret_id }
    form.value.source = { ...blank(j.kind).source, ...j.source }
    form.value.schedule = { ...blank().schedule, ...j.schedule }
  } else {
    form.value = blank(preset?.kind)
    if (preset?.nas) form.value.source.nas_id = Number(preset.nas)
    if (preset?.task) { form.value.source.task_id = isNaN(Number(preset.task)) ? preset.task : Number(preset.task); form.value.source.task_name = preset.task_name || '' }
    if (preset?.kind === 'nas_config') form.value.schedule = { ...form.value.schedule, type: 'weekly', days: [6], time: '02:00' }
  }
  pathsText.value = (form.value.source.paths || []).join('\n')
  excludesText.value = (form.value.source.excludes || []).join('\n')
  guestKey.value = form.value.source.vmid ? `${form.value.source.node}|${form.value.source.vmid}|${form.value.source.guest_type || 'qemu'}` : ''
  await loadTasks()
}
async function save() {
  formError.value = ''
  const f = form.value
  const body = { ...f, name: (f.name || '').trim() || suggestName.value, source: { ...f.source } }
  delete body.id; delete body.target; delete body.last_run; delete body.running
  if (f.kind === 'files') {
    body.source.paths = pathsText.value.split('\n').map(s => s.trim()).filter(Boolean)
    body.source.excludes = excludesText.value.split('\n').map(s => s.trim()).filter(Boolean)
  }
  if (['nas_task', 'pve'].includes(f.kind)) { body.target_id = null; body.encrypt = false }
  else {
    body.keep_last = Math.max(0, Number(body.keep_last) || 0)
    body.keep_days = Math.max(0, Number(body.keep_days) || 0)
    if (!body.keep_last && !body.keep_days) { formError.value = 'Bitte angeben, wie viele Sicherungen (oder wie viele Tage) behalten werden.'; return }
  }
  busy.value = true
  try {
    await auth.api(f.id ? `/api/backups/jobs/${f.id}` : '/api/backups/jobs', { method: f.id ? 'PUT' : 'POST', body })
    form.value = null
    notice.value = 'Gespeichert.'
    await load()
  } catch (e) { formError.value = e.message } finally { busy.value = false }
}
async function run(j) {
  busy.value = true
  try { await auth.api(`/api/backups/jobs/${j.id}/run`, { method: 'POST' }); notice.value = `„${j.name}“ gestartet.`; await load() } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function remove(j) {
  if (!confirm(`Auftrag „${j.name}“ entfernen?`)) return
  const files = confirm('Auch die gespeicherten Sicherungen dieses Auftrags am Ort löschen?\n\nOK = löschen, Abbrechen = Dateien behalten')
  try { await auth.api(`/api/backups/jobs/${j.id}?delete_files=${files}`, { method: 'DELETE' }); await load() } catch (e) { error.value = e.message }
}

function onKey(e) { if (e.key === 'Escape' && form.value) form.value = null }
onMounted(async () => {
  window.addEventListener('keydown', onKey)
  await load()
  if (route.query.new && auth.isAdmin) openForm(null, { kind: String(route.query.new), nas: route.query.nas, task: route.query.task, task_name: route.query.task_name })
})
onBeforeUnmount(() => { window.removeEventListener('keydown', onKey); clearTimeout(timer) })
</script>

<style scoped>
.pol-check { flex-direction: row !important; align-items: center; gap: .4rem !important; }
.bj-tiles { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); }
.bj-tile { padding: .75rem 1rem; display: flex; flex-direction: column; gap: .15rem; }
.bj-tile strong { font-size: 1.4rem; }
.bj-tile .is-ok { color: var(--color-success); }
.bj-tile .is-bad { color: var(--color-danger); }
.bj-tile .is-warn { color: var(--color-warning); }
.bj-src { max-width: 22rem; overflow-wrap: anywhere; }
.bj-err { color: var(--color-danger); max-width: 18rem; overflow-wrap: anywhere; }
.bj-scopes, .bj-days { display: flex; flex-wrap: wrap; gap: .4rem 1rem; align-items: center; }
td .badge + .badge { margin-left: .25rem; }
</style>
