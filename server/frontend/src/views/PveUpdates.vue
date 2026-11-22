<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Integrationen -> Proxmox-Updates & Konsole: Updates der
                   Proxmox-Knoten selbst (apt ueber die API, Upgrade ueber termproxy),
                   Neustart, Shell auf dem Knoten.
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Proxmox: Updates &amp; Konsole</h1>
        <p class="page-subtitle">
          Updates der Proxmox-Knoten selbst – so wie „Updates → Upgrade“ in Proxmox: Paketliste über die API,
          Installation über die Knoten-Konsole (pveupgrade) mit automatischer Bestätigung und mitgeschnittener Ausgabe.
          Läuft auf dem Knoten zusätzlich der RIMPERIO-Agent, erscheinen die Pakete auch im Patch-Center.
        </p>
      </div>
      <button class="btn btn-secondary btn-sm" :disabled="loading" @click="load(true)">Neu abfragen</button>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>
    <p v-if="!loading && !data?.hosts?.length" class="rmm-note is-info">
      Noch kein Proxmox-Host verbunden – unter <router-link to="/integrations/proxmox" style="text-decoration:underline">Proxmox VE</router-link>.
    </p>

    <section v-for="h in data?.hosts || []" :key="h.host_id" class="card mb-4">
      <div class="card-header"><h2 class="card-title">{{ h.host }}</h2>
        <span v-if="h.error" class="badge badge-danger">{{ h.error }}</span></div>
      <div class="card-body">
        <div v-for="n in h.nodes" :key="n.node" class="pve-node">
          <div class="pve-node-head">
            <strong>{{ n.node }}</strong>
            <span v-if="!n.online" class="badge badge-neutral">offline</span>
            <span v-else-if="n.error" class="badge badge-danger">{{ n.error }}</span>
            <template v-else>
              <span class="badge" :class="n.count ? 'badge-warning' : 'badge-success'">{{ n.count ? n.count + ' Updates' : 'aktuell' }}</span>
              <span v-if="n.kernel_update" class="badge badge-warning">neuer Kernel – Neustart nötig</span>
              <span class="rmm-muted">{{ n.pve_version ? 'pve-manager ' + n.pve_version : '' }} {{ n.kversion }}</span>
            </template>
            <span class="pve-node-actions" v-if="auth.isAdmin && n.online">
              <button class="btn btn-ghost btn-sm" @click="refresh(h, n)">Paketlisten auffrischen</button>
              <button class="btn btn-primary btn-sm" :disabled="!n.count || running(h, n)" @click="upgrade(h, n)">{{ running(h, n) ? 'läuft …' : 'Jetzt aktualisieren' }}</button>
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

    <section v-if="jobs.length" class="card">
      <div class="card-header"><h2 class="card-title">Upgrades</h2></div>
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
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import { fmtTime } from '../utils/rmm'

const auth = useAuthStore()
const data = ref(null)
const jobs = ref([])
const outputs = ref({})
const loading = ref(false)
const error = ref('')
const notice = ref('')
let timer = null

const running = (h, n) => jobs.value.some(j => j.host_id === h.host_id && j.node === n.node && j.status === 'running')

async function load(force = false) {
  loading.value = true
  error.value = ''
  try {
    data.value = await auth.api(`/api/pve/updates${force ? '?force=true' : ''}`)
    jobs.value = data.value.jobs
  } catch (e) {
    error.value = e.message
  }
  loading.value = false
}
async function pollJobs() {
  try {
    jobs.value = (await auth.api('/api/pve/jobs')).items
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
async function reboot(h, n) {
  if (prompt(`${n.node} neu starten? Laufende Gäste werden je nach Einstellung heruntergefahren.\n\nREBOOT eingeben:`) !== 'REBOOT') return
  try {
    await auth.api(`/api/pve/hosts/${h.host_id}/nodes/${n.node}/reboot`, { method: 'POST', body: { confirm: 'REBOOT' } })
    notice.value = `Neustart von ${n.node} angestoßen.`
  } catch (e) { error.value = e.message }
}

onMounted(async () => {
  await load()
  timer = setInterval(() => { if (jobs.value.some(j => j.status === 'running')) pollJobs() }, 4000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.pve-node { border-bottom: 1px solid var(--color-border); padding: .75rem 0; }
.pve-node:last-child { border-bottom: 0; }
.pve-node-head { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin-bottom: .5rem; }
.pve-node-actions { margin-left: auto; display: flex; gap: .35rem; flex-wrap: wrap; }
</style>
