<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Backup -> Orte: WOHIN gesichert wird. Ordner am
                   RIMPERIO-Server, SMB-Freigabe (z. B. auf dem Synology), SFTP, FTP(S).
                   Zugangsdaten liegen in Vaultwarden bzw. lokal verschluesselt.
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Backup – Orte</h1>
        <p class="page-subtitle">Wohin gesichert wird. Jeder Ort wird beim Testen beschrieben, gelesen und wieder aufgeräumt.
          Ein Ordner am Server liegt im Volume <span class="rmm-code">backup_data</span> – für echte Sicherheit zusätzlich einen Ort
          außerhalb nehmen (z. B. eine Freigabe auf dem NAS).</p>
      </div>
      <button v-if="auth.isAdmin" class="btn btn-primary btn-sm" @click="openForm()"><AppIcon name="plus" :size="16" /> Ort anlegen</button>
    </div>
    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>

    <div class="bt-grid">
      <article v-for="t in targets" :key="t.id" class="card bt-card" :style="t.enabled ? '' : 'opacity:.6'">
        <header class="bt-head">
          <span class="rmm-dot" :class="t.last_error ? 'is-offline' : (t.last_ok_at ? 'is-online' : '')" />
          <strong>{{ t.name }}</strong><span class="badge badge-neutral">{{ t.kind_label }}</span>
        </header>
        <div class="rmm-code bt-where">{{ t.where || '–' }}</div>
        <dl class="bt-facts">
          <div><dt>Zugang</dt><dd>{{ t.kind === 'local' ? '–' : (t.username || '–') }}
            <span v-if="t.secret_backend" class="rmm-muted">({{ t.secret_backend === 'vaultwarden' ? 'Vaultwarden' : 'lokal' }})</span></dd></div>
          <div><dt>Aufträge</dt><dd>{{ t.jobs }}</dd></div>
          <div><dt>Zuletzt ok</dt><dd>{{ fmtAgo(t.last_ok_at) }}</dd></div>
          <div v-if="t.free_bytes"><dt>Frei</dt><dd>{{ fmtBytes(t.free_bytes) }}</dd></div>
        </dl>
        <p v-if="t.last_error" class="rmm-muted bt-err">{{ t.last_error }}</p>
        <footer v-if="auth.isAdmin" class="rmm-actions">
          <button class="btn btn-secondary btn-sm" :disabled="busy" @click="test(t)">Testen</button>
          <button class="btn btn-ghost btn-sm" @click="openForm(t)">Bearbeiten</button>
          <button class="btn btn-danger btn-sm" @click="remove(t)">Entfernen</button>
        </footer>
      </article>
    </div>
    <div v-if="!targets.length" class="card"><div class="card-body rmm-muted">Noch kein Ort. Für ein Synology-NAS: SMB-Freigabe (z. B. „Backup“) mit eigenem Benutzer anlegen und hier eintragen.</div></div>

    <div v-if="form" class="modal-backdrop" @click.self="form = null">
      <div class="modal max-w-2xl">
        <div class="card-header"><h2 class="card-title">{{ form.id ? form.name : 'Ort anlegen' }}</h2>
          <button class="btn-icon" aria-label="Schließen" @click="form = null"><AppIcon name="close" :size="18" /></button></div>
        <div class="card-body rmm-form">
          <label>Name<input v-model="form.name" class="input" placeholder="z. B. DS920 Backup-Freigabe" /></label>
          <label>Art
            <select v-model="form.kind" class="select">
              <option value="local">Ordner am RIMPERIO-Server</option><option value="smb">SMB-Freigabe (NAS, Windows)</option>
              <option value="sftp">SFTP</option><option value="ftps">FTPS</option><option value="ftp">FTP (unverschlüsselt)</option>
            </select></label>
          <template v-if="form.kind !== 'local'">
            <label v-if="nasList.length">NAS übernehmen (optional)
              <select v-model.number="form.nas_id" class="select" @change="fromNas">
                <option :value="null">—</option>
                <option v-for="n in nasList" :key="n.id" :value="n.id">{{ n.name }} ({{ n.host }})</option>
              </select></label>
            <label>Server / NAS<input v-model="form.host" class="input" placeholder="192.168.1.20" /></label>
            <label>Port (leer = Standard)<input v-model.number="form.port" type="number" class="input" :placeholder="String(DEFAULT_PORT[form.kind] || '')" /></label>
            <label v-if="form.kind === 'smb'">Freigabe<input v-model="form.share" class="input" placeholder="Backup" /></label>
            <label>Benutzer<input v-model="form.username" class="input" autocomplete="off" /></label>
            <label>Passwort<input v-model="form.password" type="password" class="input" autocomplete="off" :placeholder="form.secret_id ? 'hinterlegt – leer = unverändert' : ''" /></label>
          </template>
          <label class="is-wide">{{ form.kind === 'local' ? 'Ordner (leer = /backups/jobs/<Name>)' : 'Unterordner' }}
            <input v-model="form.path" class="input" :placeholder="form.kind === 'local' ? '/backups/extern' : 'rimperio'" /></label>
          <label class="pol-check"><input v-model="form.enabled" type="checkbox" /> aktiv</label>
          <p class="is-wide rmm-muted">Zugangsdaten landen {{ vaultActive ? 'in Vaultwarden' : 'lokal verschlüsselt' }}. Am Ort entsteht je Auftrag ein Unterordner.
            SMB: Synology → Systemsteuerung → Freigegebener Ordner, Benutzer mit Lese-/Schreibrecht, SMB2/3.</p>
          <p v-if="formError" class="is-wide rmm-note is-error">{{ formError }}</p>
          <div class="is-wide rmm-actions">
            <button class="btn btn-primary btn-sm" :disabled="busy" @click="save">Speichern und testen</button>
            <button class="btn btn-ghost btn-sm" @click="form = null">Abbrechen</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import { fmtAgo } from '../utils/rmm'

const auth = useAuthStore()
const targets = ref([])
const nasList = ref([])
const vaultActive = ref(false)
const form = ref(null)
const formError = ref('')
const error = ref('')
const notice = ref('')
const busy = ref(false)
const DEFAULT_PORT = { smb: 445, sftp: 22, ftp: 21, ftps: 21 }

function fmtBytes(v) {
  if (!v) return '–'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0; let x = Number(v)
  while (x >= 1024 && i < u.length - 1) { x /= 1024; i++ }
  return x.toFixed(i >= 3 ? 1 : 0) + ' ' + u[i]
}
async function load() {
  try {
    const [t, o] = await Promise.all([auth.api('/api/backups/targets'), auth.api('/api/backups/overview')])
    targets.value = t; vaultActive.value = !!o.vault_active; error.value = ''
  } catch (e) { error.value = e.message }
}
async function openForm(t) {
  formError.value = ''
  nasList.value = await auth.api('/api/nas').catch(() => [])
  form.value = t ? { ...t, password: '' } : { name: '', kind: 'smb', host: '', port: null, share: '', path: 'rimperio', username: '', password: '', nas_id: null, enabled: true }
}
function fromNas() {
  const n = nasList.value.find(x => x.id === form.value.nas_id)
  if (n) { form.value.host = n.host; if (!form.value.name) form.value.name = n.name + ' – Backup' }
}
async function save() {
  formError.value = ''
  busy.value = true
  try {
    const f = form.value
    const body = { name: f.name, kind: f.kind, host: f.host || '', port: f.port || null, share: f.share || '', path: f.path || '',
      username: f.username || '', password: f.password || '', nas_id: f.nas_id || null, enabled: f.enabled }
    const t = await auth.api(f.id ? `/api/backups/targets/${f.id}` : '/api/backups/targets', { method: f.id ? 'PUT' : 'POST', body })
    const r = await auth.api(`/api/backups/targets/${t.id}/test`, { method: 'POST' })
    if (!r.ok) { formError.value = r.message; form.value = { ...f, id: t.id, secret_id: t.secret_id, password: '' }; await load(); return }
    notice.value = `„${t.name}“ gespeichert. ${r.message}`
    form.value = null
    await load()
  } catch (e) { formError.value = e.message } finally { busy.value = false }
}
async function test(t) {
  busy.value = true
  try {
    const r = await auth.api(`/api/backups/targets/${t.id}/test`, { method: 'POST' })
    if (r.ok) { notice.value = r.message; error.value = '' } else { error.value = r.message }
    await load()
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function remove(t) {
  if (!confirm(`Ort „${t.name}“ entfernen? Die Dateien am Ort bleiben liegen.`)) return
  try { await auth.api(`/api/backups/targets/${t.id}`, { method: 'DELETE' }); await load() } catch (e) { error.value = e.message }
}
onMounted(load)
</script>

<style scoped>
.pol-check { flex-direction: row !important; align-items: center; gap: .5rem !important; }
.bt-grid { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fill, minmax(min(100%, 22rem), 1fr)); }
.bt-card { padding: 1rem; display: flex; flex-direction: column; gap: .6rem; }
.bt-head { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; }
.bt-where { overflow-wrap: anywhere; font-size: .8rem; }
.bt-facts { display: grid; grid-template-columns: 1fr 1fr; gap: .5rem 1rem; margin: 0; font-size: .875rem; }
.bt-facts dt { color: var(--color-text-muted); font-size: .72rem; text-transform: uppercase; letter-spacing: .03em; }
.bt-facts dd { margin: 0; }
.bt-err { color: var(--color-danger); overflow-wrap: anywhere; }
</style>
