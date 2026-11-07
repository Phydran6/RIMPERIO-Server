<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Proxmox VE: Hosts, Zuordnung Rechner ↔ VM, Sicherungen vor dem Patchen
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Proxmox &amp; Snapshots</h1>
        <p class="page-subtitle">
          Vor jedem Patch-Lauf legt RIMPERIO einen Snapshot der VM an und löscht ihn nach der eingestellten Frist,
          wenn alles läuft. Rechner werden ihren VMs über MAC-Adresse oder Namen selbst zugeordnet.
        </p>
      </div>
      <button v-if="auth.isAdmin" class="btn btn-primary btn-sm" @click="form = blank()"><AppIcon name="plus" :size="16" /> Proxmox-Host</button>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>

    <section v-if="form" class="card mb-4">
      <div class="card-header"><h2 class="card-title">{{ form.id ? form.name : 'Neuer Proxmox-Host' }}</h2></div>
      <div class="card-body rmm-form">
        <label>Name<input v-model="form.name" class="input" placeholder="pve-cluster" /></label>
        <label>Adresse<input v-model="form.url" class="input" placeholder="https://pve.example:8006" /></label>
        <label>Anmeldung
          <select v-model="form.auth_type" class="select"><option value="token">API-Token (empfohlen)</option><option value="password">Benutzer + Passwort</option></select>
        </label>
        <label v-if="form.auth_type === 'token'">Token-ID<input v-model="form.token_id" class="input" placeholder="rimperio@pve!patch" /></label>
        <label v-else>Benutzer<input v-model="form.username" class="input" placeholder="root@pam" /></label>
        <label>{{ form.auth_type === 'token' ? 'Token-Geheimnis' : 'Passwort' }}
          <input v-model="form.secret" type="password" class="input" :placeholder="form.secret_set ? 'hinterlegt — leer lassen = unverändert' : ''" /></label>
        <label>Speicher für vzdump (optional)<input v-model="form.default_storage" class="input" placeholder="local / pbs" /></label>
        <label class="pol-check"><input v-model="form.verify_tls" type="checkbox" /> Zertifikat prüfen</label>
        <label class="pol-check"><input v-model="form.enabled" type="checkbox" /> aktiv</label>
        <p class="is-wide rmm-muted">
          Token anlegen: Rechenzentrum → Berechtigungen → API-Token. Rechte (Rolle am Pfad <span class="rmm-code">/vms</span>):
          VM.Snapshot, VM.Snapshot.Rollback, VM.Audit, VM.PowerMgmt; für vzdump zusätzlich VM.Backup und
          Datastore.AllocateSpace am Speicher. Bei Benutzer + Passwort darf keine Zwei-Faktor-Anmeldung aktiv sein.
        </p>
        <div class="is-wide rmm-actions">
          <button class="btn btn-primary btn-sm" :disabled="busy" @click="saveHost">Speichern</button>
          <button class="btn btn-ghost btn-sm" @click="form = null">Abbrechen</button>
        </div>
      </div>
    </section>

    <section class="card mb-4">
      <div class="card-header"><h2 class="card-title">Hosts</h2></div>
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Name</th><th>Adresse</th><th>Anmeldung</th><th>Zustand</th><th>Gäste</th><th></th></tr></thead>
          <tbody>
            <tr v-if="!hosts.length"><td colspan="6" class="rmm-muted">Noch kein Proxmox-Host. Ohne Host laufen Patches ohne Snapshot.</td></tr>
            <tr v-for="h in hosts" :key="h.id">
              <td><strong>{{ h.name }}</strong></td><td class="rmm-code">{{ h.url }}</td>
              <td>{{ h.auth_type === 'token' ? h.token_id : h.username }}</td>
              <td><span v-if="h.last_error" class="badge badge-danger" :title="h.last_error">Fehler</span>
                <span v-else-if="h.last_ok_at" class="badge badge-success">verbunden {{ fmtAgo(h.last_ok_at) }}</span>
                <span v-else class="badge badge-neutral">ungeprüft</span>
                <div v-if="h.last_error" class="rmm-muted" style="max-width:24rem">{{ h.last_error }}</div></td>
              <td>{{ h.guests.length }}</td>
              <td class="rmm-actions" style="justify-content:flex-end">
                <template v-if="auth.isAdmin">
                  <button class="btn btn-secondary btn-sm" :disabled="busy" @click="test(h)">Testen</button>
                  <button class="btn btn-secondary btn-sm" :disabled="busy" @click="refresh(h)">VMs lesen</button>
                  <button class="btn btn-ghost btn-sm" @click="form = { ...h, secret: '' }">Bearbeiten</button>
                  <button class="btn btn-danger btn-sm" @click="removeHost(h)">Entfernen</button>
                </template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="card mb-4">
      <div class="card-header"><h2 class="card-title">Zuordnung Rechner ↔ VM</h2>
        <button v-if="auth.isAdmin && hosts.length" class="btn btn-secondary btn-sm" :disabled="busy" @click="automatch">Automatisch zuordnen</button>
      </div>
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Rechner</th><th>VM / Container</th><th>erkannt über</th><th></th></tr></thead>
          <tbody>
            <tr v-for="a in agents" :key="a.agent_uid">
              <td><strong>{{ a.hostname }}</strong><div class="rmm-muted">{{ a.os }}</div></td>
              <td>
                <select v-if="auth.isAdmin" class="select" :value="linkKey(a)" @change="setLink(a, $event.target.value)">
                  <option value="">— keine —</option>
                  <optgroup v-for="h in hosts" :key="h.id" :label="h.name">
                    <option v-for="g in h.guests" :key="`${h.id}:${g.node}:${g.vmid}`" :value="`${h.id}|${g.node}|${g.vmid}|${g.type}|${g.name}`">
                      {{ g.vmid }} · {{ g.name }} ({{ g.type === 'lxc' ? 'CT' : 'VM' }}, {{ g.node }}, {{ g.status }})
                    </option>
                  </optgroup>
                </select>
                <span v-else>{{ links[a.agent_uid] ? `${links[a.agent_uid].vmid} · ${links[a.agent_uid].guest_name}` : '–' }}</span>
              </td>
              <td>{{ { mac: 'MAC-Adresse', name: 'Name', manual: 'von Hand' }[links[a.agent_uid]?.matched_by] || '–' }}</td>
              <td></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="card">
      <div class="card-header"><h2 class="card-title">Sicherungen von RIMPERIO</h2></div>
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Rechner</th><th>Sicherung</th><th>Lauf</th><th>angelegt</th><th>wird gelöscht</th><th></th></tr></thead>
          <tbody>
            <tr v-if="!snapshots.length"><td colspan="6" class="rmm-muted">Keine offenen Sicherungen.</td></tr>
            <tr v-for="s in snapshots" :key="s.run_id">
              <td>{{ s.hostname }}</td>
              <td class="rmm-code">{{ s.name || s.volid }}<div class="rmm-muted">VM {{ s.vmid }} auf {{ s.node }} · {{ s.mode === 'backup' ? 'vzdump' : 'Snapshot' }}</div></td>
              <td><router-link :to="`/rmm/patches?tab=runs&run=${s.run_id}`">#{{ s.run_id }}</router-link> <span class="badge" :class="RUN_STATUS[s.status]?.[0]">{{ RUN_STATUS[s.status]?.[1] }}</span></td>
              <td>{{ fmtTime(s.created_at) }}</td>
              <td>{{ s.keep ? 'behalten' : (s.delete_after ? fmtTime(s.delete_after) : 'nach erfolgreicher Prüfung') }}<div v-if="s.delete_error" class="rmm-muted" style="color:var(--color-danger)">{{ s.delete_error }}</div></td>
              <td class="rmm-actions" style="justify-content:flex-end">
                <template v-if="auth.isAdmin">
                  <button v-if="s.mode === 'snapshot'" class="btn btn-danger btn-sm" :disabled="busy" @click="runAction(s, 'rollback')">Zurückrollen</button>
                  <button class="btn btn-secondary btn-sm" :disabled="busy" @click="runAction(s, s.keep ? 'unkeep' : 'keep')">{{ s.keep ? 'Löschen nach Frist' : 'Behalten' }}</button>
                  <button class="btn btn-ghost btn-sm" :disabled="busy" @click="runAction(s, 'delete')">Jetzt löschen</button>
                </template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import { fmtAgo, fmtTime, RUN_STATUS } from '../utils/rmm'

const auth = useAuthStore()
const hosts = ref([])
const agents = ref([])
const links = ref({})
const snapshots = ref([])
const form = ref(null)
const error = ref('')
const notice = ref('')
const busy = ref(false)

const blank = () => ({ name: '', url: 'https://', auth_type: 'token', token_id: '', username: '', secret: '', verify_tls: false, enabled: true, default_storage: '' })

async function guard(fn) {
  busy.value = true
  try { await fn(); error.value = '' } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function load() {
  await guard(async () => {
    const [h, a, l, s] = await Promise.all([auth.api('/api/patching/pve/hosts'), auth.api('/api/rmm/agents'),
      auth.api('/api/patching/pve/links'), auth.api('/api/patching/pve/snapshots')])
    hosts.value = h; agents.value = a; snapshots.value = s
    links.value = Object.fromEntries(l.map(x => [x.agent_uid, x]))
  })
}
const linkKey = a => { const l = links.value[a.agent_uid]; return l ? `${l.host_id}|${l.node}|${l.vmid}|${l.guest_type}|${l.guest_name}` : '' }

async function saveHost() {
  await guard(async () => {
    const { id, guests, guests_at, last_ok_at, last_error, secret_set, ...body } = form.value
    await auth.api(id ? `/api/patching/pve/hosts/${id}` : '/api/patching/pve/hosts', { method: id ? 'PUT' : 'POST', body })
    form.value = null; notice.value = 'Gespeichert.'; await load()
  })
}
async function test(h) {
  await guard(async () => { const r = await auth.api(`/api/patching/pve/hosts/${h.id}/test`, { method: 'POST' }); if (r.ok) notice.value = r.message; else error.value = r.message; await load() })
}
async function refresh(h) {
  await guard(async () => { const r = await auth.api(`/api/patching/pve/hosts/${h.id}/refresh`, { method: 'POST' }); notice.value = `${r.guests.length} Gäste gelesen, ${r.matched} neu zugeordnet.`; await load() })
}
async function removeHost(h) {
  if (!confirm(`Proxmox-Host „${h.name}“ entfernen? Zuordnungen gehen verloren, Snapshots bleiben auf dem Host.`)) return
  await guard(async () => { await auth.api(`/api/patching/pve/hosts/${h.id}`, { method: 'DELETE' }); await load() })
}
async function automatch() {
  await guard(async () => { const r = await auth.api('/api/patching/pve/automatch', { method: 'POST' }); notice.value = `${r.matched} Rechner neu zugeordnet.`; await load() })
}
async function setLink(a, value) {
  await guard(async () => {
    if (!value) { await auth.api(`/api/patching/pve/links/${a.agent_uid}`, { method: 'DELETE' }) }
    else {
      const [host_id, node, vmid, guest_type, ...name] = value.split('|')
      await auth.api(`/api/patching/pve/links/${a.agent_uid}`, { method: 'PUT', body: { host_id: Number(host_id), node, vmid: Number(vmid), guest_type, guest_name: name.join('|') } })
    }
    await load()
  })
}
async function runAction(s, what) {
  if (what === 'rollback' && !confirm(`VM ${s.vmid} auf ${s.name} zurückrollen? Alles seit dem Snapshot geht verloren.`)) return
  if (what === 'delete' && !confirm('Sicherung jetzt löschen?')) return
  await guard(async () => {
    const path = { rollback: 'rollback', delete: 'snapshot/delete', keep: 'snapshot/keep?keep=true', unkeep: 'snapshot/keep?keep=false' }[what]
    await auth.api(`/api/patching/runs/${s.run_id}/${path}`, { method: 'POST' })
    notice.value = 'Erledigt.'; await load()
  })
}

onMounted(load)
</script>

<style scoped>
.pol-check { flex-direction: row !important; align-items: center; gap: .5rem !important; }
</style>
