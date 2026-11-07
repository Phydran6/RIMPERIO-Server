<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Fernwartung: ein Rechner (Status, Befehle, Skripte,
                   Patches, Checks, Inventur)
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <router-link to="/rmm" class="rmm-muted">← Rechner</router-link>
        <h1 class="page-title">
          <span class="rmm-dot" :class="agent.online ? 'is-online' : 'is-offline'"></span>
          {{ agent.hostname || '…' }}
        </h1>
        <p class="page-subtitle">
          {{ agent.online ? 'online' : 'offline' }} · zuletzt {{ fmtAgo(agent.last_seen) }} ·
          {{ agent.os || PLATFORM_LABEL[agent.platform] || '' }} · Agent {{ agent.version || '–' }}
        </p>
      </div>
      <div class="rmm-actions">
        <router-link v-if="agent.hostname" :to="`/devices/${encodeURIComponent(agent.hostname)}`" class="btn btn-secondary btn-sm">
          <AppIcon name="logs" :size="16" /> Logs
        </router-link>
        <button v-if="auth.isAdmin" class="btn btn-danger btn-sm" @click="showRemove = !showRemove">Agent entfernen</button>
      </div>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>
    <p v-if="agent.uninstall_pending" class="rmm-note is-info">
      Deinstallation angefordert.
      {{ agent.online
        ? 'Der Agent führt sie beim nächsten Check-in aus (spätestens in einer Minute) und verschwindet dann aus der Liste.'
        : 'Der Rechner ist offline – der Agent deinstalliert sich, sobald er sich wieder meldet.' }}
      Kommt er nicht wieder, löscht „Agent entfernen“ ohne Häkchen den Eintrag sofort.
    </p>

    <section v-if="showRemove && auth.isAdmin" class="card mb-4">
      <div class="card-header"><h2 class="card-title">Agent entfernen</h2></div>
      <div class="card-body">
        <label class="rmm-remove-option">
          <input v-model="removeUninstall" type="checkbox">
          <span>Agent auch auf dem Rechner deinstallieren</span>
        </label>
        <p class="rmm-muted rmm-remove-text">
          <template v-if="removeUninstall">
            Der Agent bekommt beim nächsten Check-in den Auftrag, sich zu deinstallieren, und meldet sich dabei hier ab.
            Ist der Rechner aus, passiert das, sobald er wieder läuft.
          </template>
          <template v-else>
            Nur der Eintrag hier wird gelöscht. Der Agent bleibt auf dem Rechner installiert und wird abgewiesen,
            bis man ihn dort deinstalliert.
          </template>
          Befehlsverlauf, Messwerte und Patch-Stand gehen verloren. Hat das Gerät Logs, bleibt es mit ihnen unter Geräte stehen.
        </p>
        <div class="rmm-actions">
          <button class="btn btn-danger btn-sm" :disabled="busy" @click="remove">
            {{ removeUninstall ? 'Deinstallieren und entfernen' : 'Nur Eintrag entfernen' }}
          </button>
          <button class="btn btn-secondary btn-sm" @click="showRemove = false">Abbrechen</button>
        </div>
      </div>
    </section>

    <section class="card mb-4">
      <div class="card-body">
        <dl class="rmm-grid">
          <div v-for="m in metrics" :key="m.label">
            <dt>{{ m.label }}</dt>
            <dd :class="loadClass(m.value)">{{ fmtPct(m.value) }}</dd>
            <div class="rmm-bar"><span :class="loadClass(m.value)" :style="{ width: (m.value || 0) + '%' }"></span></div>
          </div>
          <div><dt>Lokale IP</dt><dd>{{ agent.local_ip || '–' }}</dd></div>
          <div><dt>Öffentliche IP</dt><dd>{{ agent.public_ip || '–' }}</dd></div>
          <div><dt>Gestartet</dt><dd>{{ fmtTime(agent.boot_time) }}</dd></div>
          <div><dt>Angemeldet seit</dt><dd>{{ fmtTime(agent.enrolled_at) }}</dd></div>
          <div>
            <dt>Standort</dt>
            <dd v-if="!auth.isAdmin">{{ agent.site }}</dd>
            <input v-else v-model="edit.site" class="input" @change="save" />
          </div>
          <div>
            <dt>Beschreibung</dt>
            <dd v-if="!auth.isAdmin">{{ agent.description || '–' }}</dd>
            <input v-else v-model="edit.description" class="input" @change="save" />
          </div>
        </dl>
      </div>
    </section>

    <nav class="rmm-tabs">
      <button v-for="t in tabs" :key="t.key" :class="{ 'is-active': tab === t.key }" @click="tab = t.key">{{ t.label }}</button>
    </nav>

    <!-- Befehl ---------------------------------------------------------------->
    <section v-if="tab === 'run'" class="card">
      <div class="card-body">
        <div v-if="auth.isAdmin" class="rmm-form mb-4">
          <label>Shell
            <select v-model="cmd.shell" class="select"><option v-for="s in SHELLS" :key="s.value" :value="s.value">{{ s.label }}</option></select>
          </label>
          <label>Zeitlimit (s)<input v-model.number="cmd.timeout" type="number" min="1" class="input" /></label>
          <label>oder Skript aus der Bibliothek
            <select v-model="scriptId" class="select">
              <option :value="null">– eigener Befehl –</option>
              <option v-for="s in scripts" :key="s.id" :value="s.id">{{ s.category }} / {{ s.name }}</option>
            </select>
          </label>
          <label v-if="!scriptId" class="is-wide">Befehl
            <textarea v-model="cmd.command" class="textarea rmm-code" rows="4" placeholder="Get-Service | Where Status -eq Stopped"></textarea>
          </label>
          <div class="is-wide rmm-actions">
            <button class="btn btn-primary btn-sm" :disabled="busy || (!scriptId && !cmd.command.trim())" @click="run">Ausführen</button>
            <span class="rmm-muted">Der Agent holt den Auftrag beim nächsten Check-in ab (≤ 1 min).</span>
          </div>
        </div>
        <h3 class="card-title">Verlauf</h3>
        <p v-if="!commands.length" class="rmm-muted">Noch keine Befehle.</p>
        <details v-for="c in commands" :key="c.id" class="mb-2">
          <summary>
            <span class="badge" :class="STATUS_BADGE[c.status]">{{ STATUS_LABEL[c.status] || c.status }}</span>
            <span class="rmm-code"> {{ c.source || c.command.split('\n')[0].slice(0, 90) }}</span>
            <span class="rmm-muted"> · {{ c.created_by }} · {{ fmtTime(c.created_at) }}<template v-if="c.exit_code !== null"> · Exit {{ c.exit_code }}</template></span>
          </summary>
          <pre v-if="c.stdout" class="rmm-pre">{{ c.stdout }}</pre>
          <pre v-if="c.stderr" class="rmm-pre is-bad">{{ c.stderr }}</pre>
        </details>
      </div>
    </section>

    <!-- Patches ---------------------------------------------------------------->
    <section v-if="tab === 'patches'" class="card">
      <div class="card-body">
        <div class="rmm-actions mb-3">
          <strong>{{ patches.count }} ausstehende Updates</strong>
          <span class="rmm-muted">· geprüft {{ fmtTime(patches.scanned_at) }}</span>
          <template v-if="auth.isAdmin">
            <button class="btn btn-secondary btn-sm" :disabled="busy" @click="patch('scan')">Nach Updates suchen</button>
            <button class="btn btn-primary btn-sm" :disabled="busy || !patches.count" @click="patch('apply')">Updates installieren</button>
          </template>
        </div>
        <table v-if="patches.updates.length" class="table">
          <thead><tr><th>Update</th><th>KB / Paket</th><th>Schwere</th></tr></thead>
          <tbody>
            <tr v-for="(u, i) in patches.updates" :key="u.id || i">
              <td>{{ u.title || u.name || u.id }}</td><td class="rmm-code">{{ u.kb || u.version || '' }}</td><td>{{ u.severity || '' }}</td>
            </tr>
          </tbody>
        </table>
        <template v-if="patches.last_apply_output">
          <h3 class="card-title mt-4">Letzte Installation · {{ fmtTime(patches.last_apply_at) }}</h3>
          <pre class="rmm-pre">{{ patches.last_apply_output }}</pre>
        </template>
      </div>
    </section>

    <!-- Checks ----------------------------------------------------------------->
    <section v-if="tab === 'checks'" class="card">
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Check</th><th>Typ</th><th>Status</th><th>Wert</th><th>Zeit</th></tr></thead>
          <tbody>
            <tr v-if="!checks.length"><td colspan="5" class="rmm-muted">Keine Checks für diesen Rechner. <router-link to="/rmm/checks">Checks anlegen</router-link></td></tr>
            <tr v-for="r in checks" :key="r.check_id" :title="r.output || ''">
              <td>{{ r.name }}</td><td>{{ r.type }}</td>
              <td><span class="badge" :class="STATUS_BADGE[r.status]">{{ STATUS_LABEL[r.status] || r.status }}</span></td>
              <td>{{ r.value }}</td><td>{{ fmtTime(r.ts) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Inventur --------------------------------------------------------------->
    <section v-if="tab === 'inventory'" class="card">
      <div class="card-body">
        <p v-if="!agent.inventory" class="rmm-muted">Noch keine Inventur übermittelt.</p>
        <template v-else>
          <dl class="rmm-grid mb-4">
            <div v-for="(v, k) in simpleInventory" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></div>
          </dl>
          <details v-for="(v, k) in complexInventory" :key="k" class="mb-2">
            <summary><strong>{{ k }}</strong> <span class="rmm-muted">({{ Array.isArray(v) ? v.length + ' Einträge' : 'Details' }})</span></summary>
            <table v-if="Array.isArray(v) && v.length && typeof v[0] === 'object'" class="table">
              <thead><tr><th v-for="col in Object.keys(v[0]).slice(0, 6)" :key="col">{{ col }}</th></tr></thead>
              <tbody><tr v-for="(row, i) in v" :key="i"><td v-for="col in Object.keys(v[0]).slice(0, 6)" :key="col">{{ row[col] }}</td></tr></tbody>
            </table>
            <pre v-else class="rmm-pre">{{ JSON.stringify(v, null, 2) }}</pre>
          </details>
        </template>
      </div>
    </section>

    <!-- Prozesse --------------------------------------------------------------->
    <section v-if="tab === 'procs'" class="card">
      <div class="card-body">
        <p class="rmm-muted mb-2">Stand des letzten Check-ins · Platten und Prozesse mit der höchsten Last.</p>
        <table v-if="last.disk?.length" class="table mb-4">
          <thead><tr><th>Laufwerk</th><th>Belegt</th><th>Größe</th></tr></thead>
          <tbody><tr v-for="d in last.disk" :key="d.mount"><td>{{ d.mount }}</td><td :class="loadClass(d.percent)">{{ fmtPct(d.percent) }}</td><td>{{ d.total_gb?.toFixed(0) }} GB</td></tr></tbody>
        </table>
        <table v-if="last.top_procs?.length" class="table">
          <thead><tr><th>Prozess</th><th>CPU</th><th>RAM</th></tr></thead>
          <tbody><tr v-for="(p, i) in last.top_procs" :key="i"><td>{{ p.name }}</td><td>{{ fmtPct(p.cpu) }}</td><td>{{ fmtPct(p.mem) }}</td></tr></tbody>
        </table>
        <p class="rmm-muted mt-2">Laufzeit: {{ fmtUptime(last.uptime) }}</p>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import { fmtAgo, fmtPct, fmtTime, fmtUptime, loadClass, PLATFORM_LABEL, SHELLS, STATUS_BADGE, STATUS_LABEL } from '../utils/rmm'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const uid = route.params.uid
const base = `/api/rmm/agents/${uid}`

const agent = ref({})
const edit = reactive({ site: '', description: '' })
const commands = ref([])
const scripts = ref([])
const scriptId = ref(null)
const patches = ref({ count: 0, updates: [] })
const checks = ref([])
const last = ref({})
const error = ref('')
const notice = ref('')
const busy = ref(false)
const tab = ref('run')
const cmd = reactive({ shell: 'powershell', command: '', timeout: 300 })
const showRemove = ref(false)
const removeUninstall = ref(true)
let timer = null

const tabs = [
  { key: 'run', label: 'Befehle & Skripte' }, { key: 'patches', label: 'Patches' },
  { key: 'checks', label: 'Checks' }, { key: 'procs', label: 'Platten & Prozesse' },
  { key: 'inventory', label: 'Inventur' },
]
const metrics = computed(() => [
  { label: 'CPU', value: agent.value.last_cpu },
  { label: 'Arbeitsspeicher', value: agent.value.last_mem },
  { label: 'Platte (voll.)', value: agent.value.last_disk },
])
const simpleInventory = computed(() => Object.fromEntries(
  Object.entries(agent.value.inventory || {}).filter(([, v]) => v === null || typeof v !== 'object')))
const complexInventory = computed(() => Object.fromEntries(
  Object.entries(agent.value.inventory || {}).filter(([, v]) => v !== null && typeof v === 'object')))

async function guard(fn, ok = '') {
  busy.value = true
  error.value = ''
  try {
    await fn()
    notice.value = ok
    if (ok) setTimeout(() => { notice.value = '' }, 4000)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function load() {
  await guard(async () => {
    let a
    try {
      a = await auth.api(base)
    } catch (e) {
      // Nach einer angeforderten Deinstallation meldet sich der Agent ab und
      // verschwindet — dann zurück zur Liste statt einer Fehlermeldung.
      if (agent.value.uninstall_pending) { router.push('/rmm'); return }
      throw e
    }
    const [c, p, r, t] = await Promise.all([
      auth.api(`${base}/commands`), auth.api(`${base}/patches`),
      auth.api(`/api/rmm/checks/results?uid=${uid}`), auth.api(`${base}/telemetry?hours=1`),
    ])
    agent.value = a
    if (document.activeElement?.tagName !== 'INPUT') Object.assign(edit, { site: a.site, description: a.description })
    commands.value = c
    patches.value = p
    checks.value = r
    last.value = t.length ? t[t.length - 1] : {}
    if (!cmd.command && a.platform === 'linux') cmd.shell = 'bash'
  })
}

const save = () => guard(() => auth.api(base, { method: 'PATCH', body: { ...edit } }), 'Gespeichert.')

const run = () => guard(async () => {
  if (scriptId.value) {
    await auth.api(`${base}/run-script/${scriptId.value}`, { method: 'POST' })
  } else {
    await auth.api(`${base}/commands`, { method: 'POST', body: { ...cmd } })
    cmd.command = ''
  }
  await load()
}, 'Auftrag eingereiht.')

const patch = (action) => guard(async () => {
  if (action === 'apply' && !confirm(`${patches.value.count} Updates auf ${agent.value.hostname} installieren? Ein Neustart kann nötig sein.`)) return
  await auth.api(`${base}/patches/${action}`, { method: 'POST' })
  await load()
}, action === 'scan' ? 'Suche nach Updates eingereiht.' : 'Installation eingereiht.')

const remove = () => guard(async () => {
  if (removeUninstall.value) {
    await auth.api(`${base}?uninstall=true`, { method: 'DELETE' })
    showRemove.value = false
    await load()
    return
  }
  if (!confirm(`Eintrag von ${agent.value.hostname} entfernen? Der Agent auf dem Rechner bleibt installiert.`)) return
  await auth.api(base, { method: 'DELETE' })
  router.push('/rmm')
}, removeUninstall.value ? 'Deinstallation eingereiht.' : '')

onMounted(async () => {
  await load()
  try { scripts.value = await auth.api('/api/rmm/scripts') } catch { /* nur Komfort */ }
  timer = setInterval(load, 15000)
})
onUnmounted(() => clearInterval(timer))
</script>
