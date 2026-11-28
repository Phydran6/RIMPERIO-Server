<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Backup -> Orte: WOHIN gesichert wird. SMB-Freigabe
                   (z. B. auf dem Synology), SFTP, FTP(S), Cloud-Speicher (S3). RIMPERIO
                   selbst speichert keine Sicherungen (Ordner am Server nur Altbestand).
                   Zugangsdaten liegen in Vaultwarden bzw. lokal verschluesselt.
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Backup – Orte</h1>
        <p class="page-subtitle">Wohin gesichert wird – immer außerhalb des RIMPERIO-Servers: eine Freigabe auf dem NAS, ein
          SFTP-Server oder Cloud-Speicher (S3). Jeder Ort wird beim Testen beschrieben, gelesen und wieder aufgeräumt.</p>
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
          <div><dt>{{ t.kind === 's3' ? 'Access Key' : 'Zugang' }}</dt><dd>{{ t.kind === 'local' ? '–' : (t.username || '–') }}
            <span v-if="t.secret_backend" class="rmm-muted">({{ t.secret_backend === 'vaultwarden' ? 'Vaultwarden' : 'lokal' }})</span></dd></div>
          <div><dt>Aufträge</dt><dd>{{ t.jobs }}</dd></div>
          <div><dt>Zuletzt ok</dt><dd>{{ fmtAgo(t.last_ok_at) }}</dd></div>
          <div v-if="t.free_bytes"><dt>Frei</dt><dd>{{ fmtBytes(t.free_bytes) }}</dd></div>
        </dl>
        <p v-if="t.deprecated" class="rmm-note is-error">Ordner auf dem RIMPERIO-Server – RIMPERIO speichert keine Sicherungen
          mehr selbst. Aufträge auf einen Ort außerhalb umstellen; vorhandene Sicherungen lassen sich weiter herunterladen.</p>
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
              <option value="smb">SMB-Freigabe (NAS, Windows)</option>
              <option value="s3">Cloud-Speicher (S3: AWS, Hetzner, Wasabi, B2, IONOS, R2, MinIO …)</option>
              <option value="sftp">SFTP</option><option value="ftps">FTPS</option><option value="ftp">FTP (unverschlüsselt)</option>
              <option v-if="form.id && form.kind === 'local'" value="local" disabled>Ordner am RIMPERIO-Server (nicht mehr erlaubt)</option>
            </select></label>
          <template v-if="form.kind === 's3'">
            <label>Anbieter
              <select v-model="form.options.provider" class="select" @change="applyPreset">
                <option v-for="(p, key) in presets" :key="key" :value="key">{{ p.label }}</option>
              </select></label>
            <label>Region<input v-model="form.options.region" class="input" placeholder="eu-central-1" @input="applyEndpoint" /></label>
            <label class="is-wide">Endpunkt (leer = Amazon S3)<input v-model="form.host" class="input" placeholder="https://fsn1.your-objectstorage.com" /></label>
            <label>Bucket<input v-model="form.share" class="input" placeholder="firma-backup" /></label>
            <label>Speicherklasse (optional)<input v-model="form.options.storage_class" class="input" placeholder="STANDARD" /></label>
            <label>Access Key<input v-model="form.username" class="input" autocomplete="off" /></label>
            <label>Secret Key<input v-model="form.password" type="password" class="input" autocomplete="off" :placeholder="form.secret_id ? 'hinterlegt – leer = unverändert' : ''" /></label>
            <label class="pol-check"><input v-model="form.options.path_style" type="checkbox" /> Pfad-Adressierung (MinIO, R2, eigene Server)</label>
            <label class="pol-check"><input v-model="form.options.insecure" type="checkbox" /> Zertifikat nicht prüfen (nur eigener Server mit selbst signiertem Zertifikat)</label>
          </template>
          <template v-else-if="form.kind !== 'local'">
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
          <label class="is-wide">{{ form.kind === 's3' ? 'Präfix im Bucket' : 'Unterordner' }}
            <input v-model="form.path" class="input" placeholder="rimperio" /></label>
          <label class="pol-check"><input v-model="form.enabled" type="checkbox" /> aktiv</label>
          <p class="is-wide rmm-muted">Zugangsdaten landen {{ vaultActive ? 'in Vaultwarden' : 'lokal verschlüsselt' }}. Am Ort entsteht je Auftrag ein Unterordner.
            <template v-if="form.kind === 's3'">S3: Der Schlüssel braucht im Bucket Lesen, Schreiben und Löschen (s3:GetObject, s3:PutObject,
              s3:DeleteObject). Für Schutz vor Ransomware im Bucket Versionierung bzw. Object Lock einschalten.</template>
            <template v-else>SMB: Synology → Systemsteuerung → Freigegebener Ordner, Benutzer mit Lese-/Schreibrecht, SMB2/3.</template></p>
          <p v-if="busy" class="is-wide rmm-note is-info">Speichere und teste den Ort … (höchstens 90 Sekunden)</p>
          <p v-if="formError" ref="errBox" class="is-wide rmm-note is-error" role="alert">{{ formError }}</p>
          <div class="is-wide rmm-actions">
            <button class="btn btn-primary btn-sm" :disabled="busy" @click="save">{{ busy ? 'Teste …' : 'Speichern und testen' }}</button>
            <button class="btn btn-ghost btn-sm" @click="form = null">Abbrechen</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onMounted, ref } from 'vue'
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
const presets = ref({ aws: { label: 'Amazon S3', endpoint: '', region: 'eu-central-1', path_style: false } })
const errBox = ref(null)

function applyEndpoint() {
  const p = presets.value[form.value.options.provider]
  if (p && p.endpoint && p.endpoint.includes('{region}')) form.value.host = p.endpoint.replace('{region}', form.value.options.region || p.region)
}
function applyPreset() {
  const p = presets.value[form.value.options.provider]
  if (!p) return
  form.value.options.region = p.region
  form.value.options.path_style = !!p.path_style
  form.value.host = p.endpoint.replace('{region}', p.region)
}
async function showError(msg) {
  formError.value = msg
  await nextTick()
  errBox.value?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
}
function validate(f) {
  if (!(f.name || '').trim()) return 'Bitte einen Namen angeben.'
  if (f.kind === 's3') {
    if (!(f.share || '').trim()) return 'Bitte den Bucket angeben.'
    if (!f.secret_id && (!(f.username || '').trim() || !f.password)) return 'Bitte Access Key und Secret Key angeben.'
    return ''
  }
  if (f.kind !== 'local' && !(f.host || '').trim()) return 'Bitte Server bzw. NAS-Adresse angeben.'
  if (f.kind === 'smb' && !(f.share || '').trim()) return 'Für SMB bitte die Freigabe angeben (z. B. Backup).'
  if (f.port && (f.port < 1 || f.port > 65535)) return 'Port zwischen 1 und 65535.'
  return ''
}

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
    if (o.s3_presets) presets.value = o.s3_presets
  } catch (e) { error.value = e.message }
}
async function openForm(t) {
  formError.value = ''
  nasList.value = await auth.api('/api/nas').catch(() => [])
  form.value = t
    ? { ...t, password: '', options: { provider: 'aws', region: '', path_style: false, storage_class: '', insecure: false, ...(t.options || {}) } }
    : { name: '', kind: 'smb', host: '', port: null, share: '', path: 'rimperio', username: '', password: '', nas_id: null, enabled: true,
        options: { provider: 'aws', region: 'eu-central-1', path_style: false, storage_class: '', insecure: false } }
}
function fromNas() {
  const n = nasList.value.find(x => x.id === form.value.nas_id)
  if (n) { form.value.host = n.host; if (!form.value.name) form.value.name = n.name + ' – Backup' }
}
async function save() {
  formError.value = ''
  const f = form.value
  const problem = validate(f)
  if (problem) { await showError(problem); return }
  busy.value = true
  let saved = null
  try {
    const body = { name: f.name.trim(), kind: f.kind, host: f.host || '', port: f.port || null, share: f.share || '', path: f.path || '',
      username: f.username || '', password: f.password || '', nas_id: f.nas_id || null, enabled: f.enabled,
      options: f.kind === 's3' ? { ...f.options } : {} }
    saved = await auth.api(f.id ? `/api/backups/targets/${f.id}` : '/api/backups/targets', { method: f.id ? 'PUT' : 'POST', body })
  } catch (e) {
    busy.value = false
    await showError('Nicht gespeichert: ' + (e.message || 'unbekannter Fehler'))
    return
  }
  // Ab hier ist der Ort gespeichert - ein fehlgeschlagener Test laesst das Formular offen.
  form.value = { ...f, id: saved.id, secret_id: saved.secret_id, password: '' }
  try {
    const r = await auth.api(`/api/backups/targets/${saved.id}/test`, { method: 'POST' })
    if (!r.ok) {
      await load()
      await showError('Gespeichert, aber der Test schlug fehl: ' + (r.message || 'ohne Begründung'))
      return
    }
    notice.value = `„${saved.name}“ gespeichert. ${r.message}`
    form.value = null
    await load()
  } catch (e) {
    await showError('Gespeichert, Test nicht möglich: ' + (e.message || 'unbekannter Fehler'))
  } finally { busy.value = false }
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
