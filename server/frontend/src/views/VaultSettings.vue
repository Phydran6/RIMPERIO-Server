<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Integrationen -> Zugangsdaten -> Vaultwarden. Tresor
                   per HTTPS anbinden; NAS-Anmeldungen, Backup-Passwoerter und
                   Zugaenge zu Backup-Orten liegen dann dort.
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Vaultwarden</h1>
        <p class="page-subtitle">
          Alle Zugänge, die RIMPERIO braucht – NAS-Anmeldungen (samt TOTP), das Backup-Passwort, Zugänge zu Backup-Orten –
          liegen in deinem Vaultwarden, im Ordner „{{ cfg.folder || 'RIMPERIO' }}“ (oder einer Sammlung deiner Organisation).
          RIMPERIO spricht den Tresor über dieselbe HTTPS-Schnittstelle an wie die Bitwarden-Apps und ver-/entschlüsselt selbst –
          Vaultwarden sieht nie einen Klartext. In Vaultwarden geändert = sofort in RIMPERIO geändert.
        </p>
      </div>
    </div>
    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>

    <div class="vs-grid">
      <section class="card">
        <div class="card-header"><h2 class="card-title">Verbindung</h2>
          <span class="badge" :class="cfg.enabled && cfg.url ? 'badge-success' : 'badge-neutral'">{{ cfg.enabled && cfg.url ? 'aktiv' : 'nicht eingerichtet' }}</span></div>
        <div class="card-body rmm-form">
          <label class="is-wide">Adresse<input v-model="form.url" class="input" placeholder="https://vault.firma.de" /></label>
          <label>E-Mail des Kontos<input v-model="form.email" class="input" autocomplete="off" placeholder="rimperio@firma.de" /></label>
          <label>Master-Passwort<input v-model="form.master_password" type="password" class="input" autocomplete="off" :placeholder="cfg.master_password_set ? 'hinterlegt – leer = unverändert' : 'zum Entschlüsseln nötig'" /></label>
          <label>API-Schlüssel: client_id<input v-model="form.client_id" class="input" autocomplete="off" placeholder="user.xxxxxxxx-… (empfohlen)" /></label>
          <label>client_secret<input v-model="form.client_secret" type="password" class="input" autocomplete="off" :placeholder="cfg.client_secret_set ? 'hinterlegt – leer = unverändert' : ''" /></label>
          <label v-if="needTotp" class="is-wide">Zwei-Faktor-Code des Vaultwarden-Kontos<input v-model="form.totp" class="input" inputmode="numeric" maxlength="8" /></label>
          <details class="is-wide">
            <summary class="rmm-muted" style="cursor:pointer">Organisation, Ordner, Zertifikat</summary>
            <div class="rmm-form mt-2">
              <label>Organisation (optional)
                <select v-model="form.organization_id" class="select">
                  <option value="">— persönlicher Tresor —</option>
                  <option v-for="o in info.organizations || []" :key="o.id" :value="o.id">{{ o.name }}</option>
                  <option v-if="form.organization_id && !(info.organizations || []).some(o => o.id === form.organization_id)" :value="form.organization_id">{{ form.organization_id }}</option>
                </select></label>
              <label v-if="form.organization_id">Sammlung
                <select v-model="form.collection_id" class="select">
                  <option value="">— wählen —</option>
                  <option v-for="c in (info.collections || []).filter(c => c.organization_id === form.organization_id)" :key="c.id" :value="c.id">{{ c.name || c.id }}</option>
                  <option v-if="form.collection_id && !(info.collections || []).some(c => c.id === form.collection_id)" :value="form.collection_id">{{ form.collection_id }}</option>
                </select></label>
              <label v-else>Ordner<input v-model="form.folder" class="input" placeholder="RIMPERIO" /></label>
              <label class="pol-check"><input v-model="form.verify_tls" type="checkbox" /> Zertifikat prüfen</label>
              <label class="pol-check"><input v-model="form.allow_http" type="checkbox" /> unverschlüsselt (http) erlauben – nur für Tests</label>
            </div>
          </details>
          <p class="is-wide rmm-muted">Empfohlen: eigenes Vaultwarden-Konto für RIMPERIO (oder eine Organisation, in der das Team mitliest) und dessen
            <strong>persönlicher API-Schlüssel</strong> (Vaultwarden → Konto → Sicherheit → Schlüssel → API-Schlüssel anzeigen). Damit geht die Anmeldung auch
            mit Zwei-Faktor ohne Code. Das Master-Passwort braucht RIMPERIO trotzdem – nur damit lassen sich die Einträge entschlüsseln. Es liegt lokal verschlüsselt.</p>
          <div class="is-wide rmm-actions">
            <button class="btn btn-primary btn-sm" :disabled="busy || !form.url || !form.email" @click="save">{{ busy ? 'Verbinde …' : 'Verbinden und speichern' }}</button>
            <button v-if="cfg.url" class="btn btn-secondary btn-sm" :disabled="busy" @click="test">Testen</button>
            <button v-if="cfg.enabled" class="btn btn-ghost btn-sm" :disabled="busy" @click="disable">Abschalten</button>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-header"><h2 class="card-title">Stand</h2></div>
        <dl class="card-body vs-facts">
          <div><dt>Zuletzt verbunden</dt><dd>{{ cfg.last_ok_at ? fmtTime(cfg.last_ok_at) : '–' }}</dd></div>
          <div><dt>Gerät gemerkt (2FA)</dt><dd>{{ cfg.remembered ? 'ja' : 'nein' }}</dd></div>
          <div><dt>In Vaultwarden</dt><dd>{{ cfg.counts?.vaultwarden || 0 }}</dd></div>
          <div><dt>Lokal verschlüsselt</dt><dd>{{ cfg.counts?.local || 0 }}</dd></div>
        </dl>
        <div v-if="cfg.enabled && cfg.counts?.local" class="card-body">
          <p class="rmm-muted">{{ cfg.counts.local }} Zugänge liegen noch lokal. Ein Klick legt sie in Vaultwarden an; lokal bleibt dann nichts Geheimes.</p>
          <button class="btn btn-primary btn-sm" :disabled="busy" @click="migrate">Alle nach Vaultwarden verschieben</button>
        </div>
      </section>
    </div>

    <section class="card">
      <div class="card-header"><h2 class="card-title">Zugänge, die RIMPERIO benutzt</h2></div>
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Name</th><th>Art</th><th>Benutzer</th><th>Wo</th><th>Benutzt von</th><th></th></tr></thead>
          <tbody>
            <tr v-if="!entries.length"><td colspan="6" class="rmm-muted">Noch keine. Sie entstehen beim Verbinden eines NAS, beim ersten verschlüsselten Backup und bei Backup-Orten mit Anmeldung.</td></tr>
            <tr v-for="e in entries" :key="e.id">
              <td><strong>{{ e.name }}</strong><div v-if="e.last_error" class="rmm-muted vs-err">{{ e.last_error }}</div></td>
              <td>{{ KIND[e.kind] || e.kind }}</td>
              <td>{{ e.username || '–' }}</td>
              <td><span class="badge" :class="e.backend === 'vaultwarden' ? 'badge-success' : 'badge-neutral'">{{ e.backend === 'vaultwarden' ? 'Vaultwarden' : 'lokal' }}</span></td>
              <td class="rmm-muted">{{ (e.used_by || []).join(', ') || '–' }}</td>
              <td><button class="btn btn-ghost btn-sm" @click="check(e)">{{ checks[e.id] || 'Lesbar?' }}</button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="card-body rmm-muted">Den Backup-Schlüssel nie löschen – ohne ihn lassen sich verschlüsselte Sicherungen nicht zurückholen.
        Einträge in Vaultwarden heißen „RIMPERIO · …“; Passwort oder TOTP dort ändern wirkt beim nächsten Gebrauch.</p>
    </section>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import { fmtTime } from '../utils/rmm'

const auth = useAuthStore()
const cfg = ref({})
const info = ref({})
const entries = ref([])
const checks = ref({})
const form = ref({ url: '', email: '', master_password: '', client_id: '', client_secret: '', organization_id: '', collection_id: '',
  folder: 'RIMPERIO', verify_tls: true, allow_http: false, totp: '' })
const needTotp = ref(false)
const error = ref('')
const notice = ref('')
const busy = ref(false)
const KIND = { nas: 'NAS-Anmeldung', backup_key: 'Backup-Schlüssel', backup_target: 'Backup-Ort', ssh: 'SSH', other: 'Sonstiges' }

async function load() {
  try {
    cfg.value = await auth.api('/api/vault/config')
    entries.value = await auth.api('/api/vault/entries')
    Object.assign(form.value, { url: cfg.value.url, email: cfg.value.email, client_id: cfg.value.client_id,
      organization_id: cfg.value.organization_id, collection_id: cfg.value.collection_id, folder: cfg.value.folder || 'RIMPERIO',
      verify_tls: cfg.value.verify_tls, allow_http: cfg.value.allow_http, master_password: '', client_secret: '', totp: '' })
    error.value = ''
  } catch (e) { error.value = e.message }
}
async function save() {
  busy.value = true
  error.value = ''
  try {
    const r = await auth.api('/api/vault/config', { method: 'PUT', body: { ...form.value, enabled: true } })
    info.value = r.info || {}
    needTotp.value = false
    notice.value = `Verbunden als ${r.info?.email} – ${r.info?.ciphers ?? 0} Einträge im Tresor.`
    await load()
  } catch (e) {
    const msg = String(e.message || '')
    if (msg.startsWith('need_totp')) { needTotp.value = true; error.value = 'Das Konto verlangt einen Zwei-Faktor-Code (einmalig) – oder API-Schlüssel eintragen.' } else error.value = msg
  } finally { busy.value = false }
}
async function test() {
  busy.value = true
  try {
    const r = await auth.api('/api/vault/test', { method: 'POST' })
    if (r.ok) { notice.value = r.message; info.value = r.info || {}; error.value = '' } else { error.value = r.message; needTotp.value = !!r.need_totp }
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function disable() {
  if (!confirm('Vaultwarden für neue Zugänge abschalten? Vorhandene Einträge bleiben in Vaultwarden und werden weiter von dort gelesen.')) return
  try { await auth.api('/api/vault/config', { method: 'DELETE' }); await load() } catch (e) { error.value = e.message }
}
async function migrate() {
  busy.value = true
  try {
    const r = await auth.api('/api/vault/migrate', { method: 'POST' })
    notice.value = `${r.moved} Zugänge nach Vaultwarden verschoben.` + (r.failed.length ? ` ${r.failed.length} fehlgeschlagen.` : '')
    await load()
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function check(e) {
  checks.value = { ...checks.value, [e.id]: '…' }
  try {
    const r = await auth.api(`/api/vault/entries/${e.id}/check`, { method: 'POST' })
    checks.value = { ...checks.value, [e.id]: r.ok ? `lesbar ✓${r.has_totp ? ' · TOTP' : ''}` : 'Fehler' }
    if (!r.ok) error.value = r.message
  } catch (err) { checks.value = { ...checks.value, [e.id]: 'Fehler' }; error.value = err.message }
}
onMounted(load)
</script>

<style scoped>
.pol-check { flex-direction: row !important; align-items: center; gap: .5rem !important; }
.vs-grid { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fit, minmax(min(100%, 26rem), 1fr)); align-items: start; }
.vs-facts { display: grid; grid-template-columns: 1fr 1fr; gap: .6rem 1rem; margin: 0; font-size: .875rem; }
.vs-facts dt { color: var(--color-text-muted); font-size: .72rem; text-transform: uppercase; letter-spacing: .03em; }
.vs-facts dd { margin: 0; }
.vs-err { color: var(--color-danger); }
</style>
