<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Geraete -> Proxmox VE -> Knoten patchen & Konsole:
                   Updates der Proxmox-Knoten selbst (apt ueber die API, Upgrade ueber
                   termproxy), rollierender Patch-Lauf ueber alle Knoten, Zeitplan
                   (Wartungsfenster) je Host, Neustart, Shell auf dem Knoten.
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Proxmox: Knoten patchen &amp; Konsole</h1>
        <p class="page-subtitle">
          Updates der Proxmox-Knoten selbst – so wie „Updates → Upgrade“ in Proxmox: Paketliste über die API,
          Installation über die Knoten-Konsole (pveupgrade) mit automatischer Bestätigung und mitgeschnittener Ausgabe.
          „Alle Knoten patchen“ geht einen Cluster Knoten für Knoten durch, mit Neustart nach neuem Kernel – von Hand
          oder im Wartungsfenster. Die Gäste (VMs, LXC) patcht der RIMPERIO-Agent im Patch-Center.
        </p>
      </div>
      <button class="btn btn-secondary btn-sm" :disabled="loading" @click="load(true)">Neu abfragen</button>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>
    <p v-if="!loading && !data?.hosts?.length" class="rmm-note is-info">
      Noch kein Proxmox-Host verbunden – unter <router-link to="/devices/proxmox" style="text-decoration:underline">Proxmox VE → Hosts &amp; Gäste</router-link>.
    </p>

    <section v-for="h in data?.hosts || []" :key="h.host_id" class="card mb-4">
      <div class="card-header pve-host-head">
        <h2 class="card-title">{{ h.host }}</h2>
        <span v-if="h.error" class="badge badge-danger">{{ h.error }}</span>
        <template v-else>
          <span class="badge" :class="hostCount(h) ? 'badge-warning' : 'badge-success'">
            {{ hostCount(h) ? hostCount(h) + ' Updates' : 'alle Knoten aktuell' }}</span>
          <span v-if="plan(h)?.config.enabled" class="badge badge-primary"
                :title="'Nächstes Wartungsfenster: ' + fmtTime(plan(h).next_window)">
            Zeitplan: {{ scheduleText(plan(h).config) }}</span>
          <span v-else class="badge badge-neutral">kein Zeitplan</span>
        </template>
        <span class="pve-node-actions" v-if="auth.isAdmin && !h.error">
          <button class="btn btn-primary btn-sm" :disabled="!!runFor(h)" @click="patchAll(h)">
            {{ runFor(h) ? 'Patch-Lauf läuft …' : 'Alle Knoten patchen' }}</button>
          <button class="btn btn-ghost btn-sm" @click="toggleSchedule(h)">{{ editing[h.host_id] ? 'Zeitplan schließen' : 'Zeitplan' }}</button>
        </span>
      </div>
      <div class="card-body">
        <!-- Zeitplan ---------------------------------------------------------->
        <form v-if="editing[h.host_id]" class="pve-plan" @submit.prevent="saveSchedule(h)">
          <label class="pve-check"><input v-model="editing[h.host_id].enabled" type="checkbox">
            Im Wartungsfenster automatisch patchen</label>
          <div class="pve-days">
            <label v-for="(d, i) in DAYS" :key="i" class="pve-check">
              <input type="checkbox" :checked="editing[h.host_id].days.includes(i)" @change="toggleDay(h, i)"> {{ d }}</label>
          </div>
          <div class="pve-grid">
            <label>Beginn <input v-model="editing[h.host_id].start" type="time" class="input" required></label>
            <label>Dauer (Minuten) <input v-model.number="editing[h.host_id].duration_minutes" type="number" min="30" max="1440" class="input"></label>
            <label>Zeitzone <input v-model.trim="editing[h.host_id].timezone" type="text" class="input"></label>
            <label>Neustart
              <select v-model="editing[h.host_id].reboot" class="input">
                <option value="never">nie (nur empfehlen)</option>
                <option value="kernel">bei neuem Kernel</option>
                <option value="always">nach jedem Upgrade</option>
              </select></label>
            <label>Warten auf Neustart (Minuten) <input v-model.number="editing[h.host_id].reboot_timeout_minutes" type="number" min="5" max="120" class="input"></label>
            <label>Nur diese Knoten <input v-model="nodesText[h.host_id]" type="text" class="input" placeholder="leer = alle"></label>
          </div>
          <label class="pve-check"><input v-model="editing[h.host_id].refresh" type="checkbox"> Vorher Paketlisten auffrischen (apt update)</label>
          <label class="pve-check"><input v-model="editing[h.host_id].only_security" type="checkbox"> Nur patchen, wenn Sicherheitsupdates dabei sind</label>
          <p class="rmm-muted">Knoten für Knoten, nie zwei gleichzeitig. Vor jedem Neustart in einem Cluster wird das Quorum geprüft,
            der nächste Knoten kommt erst dran, wenn der vorige wieder online ist. Schlägt etwas fehl, hält der Lauf an.
            Beim Neustart fährt Proxmox die Gäste nach ihrer Einstellung herunter bzw. verschiebt sie per HA.</p>
          <div><button class="btn btn-primary btn-sm" type="submit">Zeitplan speichern</button></div>
        </form>

        <!-- Laufender bzw. letzter Patch-Lauf -------------------------------->
        <div v-for="r in runsFor(h).slice(0, 1)" :key="r.id" class="pve-run">
          <p>
            <strong>Patch-Lauf</strong>
            <span class="badge" :class="RUN_BADGE[r.status]">{{ RUN_TEXT[r.status] }}</span>
            <span class="rmm-muted"> {{ r.trigger === 'schedule' ? 'Zeitplan' : r.user }} · {{ fmtTime(r.started_at) }}</span>
          </p>
          <ul class="pve-run-nodes">
            <li v-for="n in r.nodes" :key="n.node">
              <span class="badge" :class="NODE_BADGE[n.status]">{{ NODE_TEXT[n.status] || n.status }}</span>
              <strong>{{ n.node }}</strong>
              <span class="rmm-muted">{{ n.count ? n.count + ' Paket(e)' : '' }} {{ n.rebooted ? '· neu gestartet' : '' }} {{ n.note ? '· ' + n.note : '' }}</span>
            </li>
          </ul>
          <p v-if="r.error" class="rmm-note is-error">{{ r.error }}</p>
          <details><summary class="rmm-muted">Ablauf</summary>
            <pre class="rmm-pre">{{ (r.log || []).map(l => fmtTime(l.ts) + '  ' + l.text).join('\n') }}</pre></details>
        </div>

        <!-- Knoten ------------------------------------------------------------>
        <div v-for="n in h.nodes" :key="n.node" class="pve-node">
          <div class="pve-node-head">
            <strong>{{ n.node }}</strong>
            <span v-if="!n.online" class="badge badge-neutral">offline</span>
            <span v-else-if="n.error" class="badge badge-danger">{{ n.error }}</span>
            <template v-else>
              <span class="badge" :class="n.count ? 'badge-warning' : 'badge-success'">{{ n.count ? n.count + ' Updates' : 'aktuell' }}</span>
              <span v-if="n.items?.some(p => p.security)" class="badge badge-danger">Sicherheit</span>
              <span v-if="n.kernel_update" class="badge badge-warning">neuer Kernel – Neustart nötig</span>
              <span class="rmm-muted">{{ n.pve_version ? 'pve-manager ' + n.pve_version : '' }} {{ n.kversion }}</span>
            </template>
            <span class="pve-node-actions" v-if="auth.isAdmin && n.online">
              <button class="btn btn-ghost btn-sm" @click="refresh(h, n)">Paketlisten auffrischen</button>
              <button class="btn btn-secondary btn-sm" :disabled="!n.count || running(h, n) || !!runFor(h)" @click="upgrade(h, n)">{{ running(h, n) ? 'läuft …' : 'Nur diesen Knoten' }}</button>
              <button class="btn btn-ghost btn-sm" @click="reboot(h, n)">Neustart</button>
              <router-link class="btn btn-ghost btn-sm" :to="{ path: '/terminal', query: { pve: h.host_id, node: n.node, kind: 'node' } }">Shell</router-link>
            </span>
          </div>
          <div v-if="n.count" class="rmm-table-wrap">
            <table class="table">
              <thead><tr><th>Paket</th><th>Version</th><th>Herkunft</th></tr></thead>
              <tbody>
                <tr v-for="p in n.items" :key="p.package">
                  <td><strong>{{ p.package }}</strong> <span v-if="p.kernel" class="badge badge-warning">Kernel</span>
                    <span v-if="p.security" class="badge badge-danger">Sicherheit</span><br><span class="rmm-muted">{{ p.title }}</span></td>
                  <td class="rmm-code">{{ p.from }} → {{ p.to }}</td>
                  <td class="rmm-muted">{{ p.origin }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>

    <section v-if="jobs.length" class="card mb-4">
      <div class="card-header"><h2 class="card-title">Upgrades (Ausgabe je Knoten)</h2></div>
      <div class="card-body">
        <div v-for="j in jobs" :key="j.id" class="mb-4">
          <p>
            <strong>{{ j.host }} / {{ j.node }}</strong>
            <span class="badge" :class="{ running: 'badge-primary', succeeded: 'badge-success', failed: 'badge-danger' }[j.status]">{{ { running: 'läuft', succeeded: 'erfolgreich', failed: 'fehlgeschlagen' }[j.status] }}</span>
            <span v-if="j.reboot_recommended" class="badge badge-warning">Neustart empfohlen</span>
            <span class="rmm-muted"> {{ fmtTime(j.started_at) }} · {{ j.user }}</span>
            <button class="btn btn-ghost btn-sm" @click="toggle(j)">{{ outputs[j.id] !== undefined ? 'Ausgabe ausblenden' : 'Ausgabe' }}</button>
          </p>
          <p v-if="j.error" class="rmm-note is-error">{{ j.error }}</p>
          <pre v-if="outputs[j.id] !== undefined" class="rmm-pre">{{ outputs[j.id] || '…' }}</pre>
        </div>
      </div>
    </section>

    <section v-if="patching?.history?.length" class="card">
      <div class="card-header"><h2 class="card-title">Verlauf der Patch-Läufe</h2></div>
      <div class="card-body rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Beginn</th><th>Host</th><th>Auslöser</th><th>Ergebnis</th><th>Knoten</th></tr></thead>
          <tbody>
            <tr v-for="r in patching.history" :key="r.id">
              <td class="rmm-muted">{{ fmtTime(r.started_at) }}</td>
              <td>{{ r.host }}</td>
              <td>{{ r.trigger === 'schedule' ? 'Zeitplan' : r.user }}</td>
              <td><span class="badge" :class="RUN_BADGE[r.status]">{{ RUN_TEXT[r.status] }}</span>
                <span v-if="r.error" class="rmm-muted"> {{ r.error }}</span></td>
              <td class="rmm-muted">{{ (r.nodes || []).map(n => n.node + ': ' + (NODE_TEXT[n.status] || n.status)).join(', ') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import { fmtTime } from '../utils/rmm'

const DAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
const RUN_BADGE = { running: 'badge-primary', succeeded: 'badge-success', failed: 'badge-danger' }
const RUN_TEXT = { running: 'läuft', succeeded: 'abgeschlossen', failed: 'angehalten' }
const NODE_BADGE = { pending: 'badge-neutral', running: 'badge-primary', done: 'badge-success', current: 'badge-success',
  skipped: 'badge-neutral', failed: 'badge-danger' }
const NODE_TEXT = { pending: 'wartet', running: 'läuft', done: 'aktualisiert', current: 'aktuell', skipped: 'übersprungen',
  failed: 'fehlgeschlagen' }

const auth = useAuthStore()
const data = ref(null)
const patching = ref(null)
const jobs = ref([])
const outputs = ref({})
const editing = reactive({})
const nodesText = reactive({})
const loading = ref(false)
const error = ref('')
const notice = ref('')
let timer = null

const running = (h, n) => jobs.value.some(j => j.host_id === h.host_id && j.node === n.node && j.status === 'running')
const hostCount = h => (h.nodes || []).reduce((sum, n) => sum + (n.count || 0), 0)
const plan = h => patching.value?.hosts?.find(p => p.host_id === h.host_id)
const runsFor = h => (patching.value?.runs || []).filter(r => r.host_id === h.host_id)
const runFor = h => runsFor(h).find(r => r.status === 'running')

function scheduleText(cfg) {
  const days = cfg.days.length === 7 ? 'täglich' : cfg.days.map(d => DAYS[d]).join(', ')
  return `${days} ${cfg.start}`
}

async function load(force = false) {
  loading.value = true
  error.value = ''
  try {
    const [updates, plans] = await Promise.all([
      auth.api(`/api/pve/updates${force ? '?force=true' : ''}`),
      auth.api('/api/pve/patching'),
    ])
    data.value = updates
    jobs.value = updates.jobs
    patching.value = plans
  } catch (e) {
    error.value = e.message
  }
  loading.value = false
}
async function pollJobs() {
  try {
    jobs.value = (await auth.api('/api/pve/jobs')).items
    patching.value = await auth.api('/api/pve/patching')
    for (const id of Object.keys(outputs.value)) {
      outputs.value[id] = (await auth.api(`/api/pve/jobs/${id}`)).output
    }
  } catch { /* still */ }
}
async function toggle(j) {
  if (outputs.value[j.id] !== undefined) { delete outputs.value[j.id]; return }
  outputs.value[j.id] = ''
  outputs.value[j.id] = (await auth.api(`/api/pve/jobs/${j.id}`)).output
}
async function refresh(h, n) {
  try {
    const r = await auth.api(`/api/pve/hosts/${h.host_id}/nodes/${n.node}/updates/refresh`, { method: 'POST' })
    notice.value = r.message
  } catch (e) { error.value = e.message }
}
async function upgrade(h, n) {
  const msg = `${n.count} Paket(e) auf ${n.node} installieren?` + (n.kernel_update ? '\n\nEin neuer Kernel ist dabei – danach neu starten.' : '')
    + '\n\nLaufende VMs bleiben an. Zur Bestätigung UPGRADE eingeben:'
  if (prompt(msg) !== 'UPGRADE') return
  try {
    const j = await auth.api(`/api/pve/hosts/${h.host_id}/nodes/${n.node}/upgrade`, { method: 'POST', body: { confirm: 'UPGRADE' } })
    notice.value = `Upgrade auf ${n.node} läuft.`
    outputs.value[j.id] = ''
    await pollJobs()
  } catch (e) { error.value = e.message }
}
async function patchAll(h) {
  const cfg = plan(h)?.config || patching.value?.defaults || {}
  const rebootText = { never: 'kein Neustart', kernel: 'Neustart bei neuem Kernel', always: 'Neustart nach jedem Upgrade' }[cfg.reboot]
  const msg = `Alle Knoten von ${h.host} nacheinander patchen (${rebootText})?\n\n`
    + 'Knoten für Knoten: Paketlisten auffrischen, pveupgrade, ggf. Neustart und warten, bis er wieder online ist. '
    + 'Bei einem Fehler hält der Lauf an.\n\nZur Bestätigung UPGRADE eingeben:'
  if (prompt(msg) !== 'UPGRADE') return
  try {
    await auth.api(`/api/pve/hosts/${h.host_id}/patch`, { method: 'POST', body: { confirm: 'UPGRADE' } })
    notice.value = `Patch-Lauf auf ${h.host} gestartet.`
    await pollJobs()
  } catch (e) { error.value = e.message }
}
function toggleSchedule(h) {
  if (editing[h.host_id]) { delete editing[h.host_id]; return }
  const cfg = plan(h)?.config || patching.value?.defaults
  editing[h.host_id] = JSON.parse(JSON.stringify(cfg))
  nodesText[h.host_id] = (cfg.nodes || []).join(', ')
}
function toggleDay(h, i) {
  const days = editing[h.host_id].days
  editing[h.host_id].days = days.includes(i) ? days.filter(d => d !== i) : [...days, i].sort()
}
async function saveSchedule(h) {
  try {
    const body = { ...editing[h.host_id], nodes: String(nodesText[h.host_id] || '').split(/[\s,;]+/).filter(Boolean) }
    await auth.api(`/api/pve/patching/hosts/${h.host_id}`, { method: 'PUT', body })
    notice.value = body.enabled ? `Zeitplan für ${h.host} gespeichert.` : `Zeitplan für ${h.host} ausgeschaltet.`
    delete editing[h.host_id]
    patching.value = await auth.api('/api/pve/patching')
  } catch (e) { error.value = e.message }
}
async function reboot(h, n) {
  if (prompt(`${n.node} neu starten? Laufende Gäste werden je nach Einstellung heruntergefahren.\n\nREBOOT eingeben:`) !== 'REBOOT') return
  try {
    await auth.api(`/api/pve/hosts/${h.host_id}/nodes/${n.node}/reboot`, { method: 'POST', body: { confirm: 'REBOOT' } })
    notice.value = `Neustart von ${n.node} angestoßen.`
  } catch (e) { error.value = e.message }
}

onMounted(async () => {
  await load()
  timer = setInterval(() => {
    if (jobs.value.some(j => j.status === 'running') || (patching.value?.runs || []).some(r => r.status === 'running')) pollJobs()
  }, 4000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.pve-host-head { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }
.pve-node { border-bottom: 1px solid var(--color-border); padding: .75rem 0; }
.pve-node:last-child { border-bottom: 0; }
.pve-node-head { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin-bottom: .5rem; }
.pve-node-actions { margin-left: auto; display: flex; gap: .35rem; flex-wrap: wrap; }
.pve-plan { display: flex; flex-direction: column; gap: .6rem; padding: .75rem; margin-bottom: 1rem;
  border: 1px solid var(--color-border); border-radius: var(--radius); }
.pve-grid { display: grid; gap: .6rem; grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr)); }
.pve-grid label { display: flex; flex-direction: column; gap: .25rem; font-size: .8125rem; }
.pve-days { display: flex; flex-wrap: wrap; gap: .75rem; }
.pve-check { display: inline-flex; gap: .4rem; align-items: center; font-size: .875rem; }
.pve-run { padding: .75rem; margin-bottom: 1rem; border: 1px solid var(--color-border); border-radius: var(--radius); }
.pve-run-nodes { display: flex; flex-direction: column; gap: .25rem; margin: .5rem 0; }
.pve-run-nodes li { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; }
</style>
