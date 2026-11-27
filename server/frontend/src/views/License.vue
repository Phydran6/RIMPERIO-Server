<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Lizenz & Geräte: Ausgabe, Lizenzquelle (GitHub/API/Datei), Kontingent
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Lizenz &amp; Geräte</h1>
        <p class="page-subtitle">
          Der Server selbst ist lizenzfrei. Eine Lizenz braucht, wer RIMPERIO gewerblich einsetzt – je Gerät, je Instanz
          oder je Mandant. Angeschlossene Geräte lizenzieren sich selbst; die Überwachung läuft immer weiter, auch über
          dem Kontingent.
        </p>
      </div>
      <div class="rmm-actions">
        <button class="btn btn-secondary btn-sm" :disabled="busy" @click="checkNow"><AppIcon name="refresh" :size="16" /> Lizenz jetzt prüfen</button>
      </div>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>
    <p v-for="w in st.warnings" :key="'w' + w" class="rmm-note is-info">⚠ {{ w }}</p>
    <p v-for="e in st.errors" :key="'e' + e" class="rmm-note is-error">{{ e }}</p>
    <p v-if="st.instance_id && !st.keys_configured" class="rmm-note is-info">
      In dieser RIMPERIO-Fassung ist noch kein Lizenz-Schlüssel hinterlegt (<code>app/license_keys.py</code>) – Lizenzen
      lassen sich erst prüfen, wenn der Herausgeber seinen öffentlichen Schlüssel eingetragen hat. Bis dahin: Community.
    </p>

    <div class="lic-tiles mb-4">
      <div class="card lic-tile">
        <div class="rmm-muted">Ausgabe</div>
        <div class="lic-big">
          <span class="badge" :class="st.edition === 'enterprise' ? 'badge-success' : 'badge-neutral'">{{ st.edition_label || '…' }}</span>
        </div>
        <div class="rmm-muted">{{ st.edition === 'enterprise' ? st.model_label : 'ohne Lizenz – alles außer Enterprise-Funktionen' }}</div>
      </div>
      <div class="card lic-tile">
        <div class="rmm-muted">Geräte lizenziert</div>
        <div class="lic-big">{{ st.devices_used ?? '–' }}<span v-if="st.devices_limit" class="rmm-muted"> / {{ st.devices_limit }}</span></div>
        <div v-if="st.devices_limit" class="lic-bar"><span :style="{ width: Math.min(100, 100 * st.devices_used / st.devices_limit) + '%' }" :class="{ 'is-full': st.devices_over_limit }" /></div>
        <div class="rmm-muted">{{ st.devices_over_limit ? st.devices_over_limit + ' über Kontingent' : 'kein Gerät über Kontingent' }}</div>
      </div>
      <div class="card lic-tile">
        <div class="rmm-muted">Mandanten</div>
        <div class="lic-big">{{ st.tenants_used ?? '–' }}<span v-if="st.tenants_limit" class="rmm-muted"> / {{ st.tenants_limit }}</span></div>
        <div class="rmm-muted">bis zur Mandantenfähigkeit: Standorte</div>
      </div>
      <div class="card lic-tile">
        <div class="rmm-muted">Lizenznehmer</div>
        <div class="lic-big lic-name">{{ st.licensee || '–' }}</div>
        <div class="rmm-muted">{{ st.valid_until ? 'gültig bis ' + fmtDate(st.valid_until) : (st.edition === 'enterprise' ? 'unbefristet' : '') }}</div>
      </div>
    </div>

    <section class="card mb-4">
      <div class="card-header"><h2 class="card-title">Diese Instanz</h2></div>
      <div class="card-body">
        <p class="rmm-muted">Mit dieser Kennung stellt der Herausgeber die Lizenz aus (Datei <code>{{ request.license_file || '…' }}</code> im Lizenz-Repository).</p>
        <div class="rmm-actions lic-id">
          <code>{{ st.instance_id }}</code>
          <button class="btn btn-ghost btn-sm" @click="copy(st.instance_id)"><AppIcon name="copy" :size="15" /> Kopieren</button>
          <button class="btn btn-ghost btn-sm" @click="copy(JSON.stringify(request, null, 2))">Lizenzanfrage kopieren</button>
          <button class="btn btn-ghost btn-sm" @click="regenerate">Neue Kennung …</button>
        </div>
        <dl v-if="st.license" class="lic-dl">
          <dt>Lizenz</dt><dd>{{ st.license.license_id }} <span v-if="st.license.customer_no" class="rmm-muted">· Kunde {{ st.license.customer_no }}</span></dd>
          <dt>Modell</dt><dd>{{ st.models?.[st.license.model] }}<span v-if="st.license.max_devices"> · bis {{ st.license.max_devices }} Geräte</span><span v-if="st.license.max_tenants"> · bis {{ st.license.max_tenants }} Mandanten</span></dd>
          <dt>Funktionen</dt><dd>{{ (st.license.features || []).map(f => st.feature_labels?.[f] || f).join(' · ') }}</dd>
          <dt>Gültig</dt><dd>{{ st.license.valid_from ? fmtDate(st.license.valid_from) : 'sofort' }} – {{ st.license.valid_until ? fmtDate(st.license.valid_until) : 'unbefristet' }} (Kulanz {{ st.license.grace_days }} Tage)</dd>
          <dt>Herkunft</dt><dd>{{ originLabel }} · zuletzt bestätigt {{ fmtTime(st.last_ok_at) }}</dd>
        </dl>
      </div>
    </section>

    <section class="card mb-4">
      <div class="card-header"><h2 class="card-title">Lizenzquelle</h2></div>
      <div class="card-body rmm-form">
        <label>Woher kommt die Lizenz?
          <select v-model="src.mode" class="select">
            <option value="github">GitHub-Lizenzbereich</option>
            <option value="url">eigener Lizenz-API-Endpunkt</option>
            <option value="manual">nur von Hand (ohne Internet)</option>
          </select>
        </label>
        <template v-if="src.mode === 'github'">
          <label>Repository<input v-model="src.github_repo" class="input" placeholder="owner/RIMPERIO-Licenses" /></label>
          <label>Branch<input v-model="src.github_branch" class="input" /></label>
          <label>Ordner<input v-model="src.github_path" class="input" /></label>
        </template>
        <template v-if="src.mode === 'url'">
          <label class="is-wide">Adresse<input v-model="src.url" class="input" placeholder="https://lizenz.example.de/api/rimperio" /></label>
          <label>Zugangstoken<input v-model="urlToken" type="password" class="input" :placeholder="src.url_token_set ? 'hinterlegt – leer = unverändert' : 'optional'" /></label>
          <label class="lic-check"><input v-model="src.report_usage" type="checkbox" /> Gerätezahlen mitsenden</label>
        </template>
        <label v-if="src.mode !== 'manual'">Prüfen alle (Stunden)<input v-model.number="src.check_hours" type="number" min="1" max="168" class="input" /></label>
        <div class="is-wide rmm-muted">
          <template v-if="src.mode === 'github'">RIMPERIO holt <code>{{ src.github_path ? src.github_path + '/' : '' }}{{ st.instance_id }}.json</code>. Privates Repository: Lesetoken als <code>RIMPERIO_LICENSE_GITHUB_TOKEN</code> in der <code>.env</code>.</template>
          <template v-else-if="src.mode === 'url'">RIMPERIO sendet per POST Instanz-ID, Version und (wenn gewünscht) die Gerätezahlen; die Antwort ist die signierte Lizenzdatei.</template>
          <template v-else>Kein Abruf. Die eingespielte Datei gilt bis zu ihrem Ablaufdatum.</template>
          Letzte Prüfung: {{ fmtTime(st.last_check_at) }}<span v-if="st.last_error"> – <span style="color:var(--color-danger)">{{ st.last_error }}</span></span>
        </div>
        <div class="is-wide rmm-actions">
          <button class="btn btn-primary btn-sm" :disabled="busy" @click="saveSource">Speichern</button>
        </div>
      </div>
    </section>

    <section class="card mb-4">
      <div class="card-header"><h2 class="card-title">Lizenzdatei einspielen</h2></div>
      <div class="card-body">
        <p class="rmm-muted">Für Installationen ohne Internet: die signierte Datei (<code>….json</code>) hier einfügen oder auswählen.</p>
        <textarea v-model="uploadText" class="input lic-text" rows="4" placeholder='{"format": "rimperio-license/1", …}' />
        <div class="rmm-actions" style="margin-top:.5rem">
          <input type="file" accept=".json,application/json" @change="readFile" />
          <button class="btn btn-primary btn-sm" :disabled="busy || !uploadText.trim()" @click="upload">Einspielen</button>
          <button v-if="st.license" class="btn btn-danger btn-sm" :disabled="busy" @click="removeLicense">Lizenz entfernen</button>
        </div>
      </div>
    </section>

    <section class="card">
      <div class="card-header">
        <h2 class="card-title">Gezählte Geräte</h2>
        <div class="rmm-actions">
          <select v-model="filter.state" class="select" style="max-width:12rem" @change="loadDevices">
            <option value="">alle Zustände</option>
            <option v-for="(label, key) in states" :key="key" :value="key">{{ label }}</option>
          </select>
          <input v-model="filter.q" class="input" placeholder="Name suchen" style="max-width:12rem" @keyup.enter="loadDevices" />
          <button class="btn btn-secondary btn-sm" :disabled="busy" @click="syncNow">Jetzt abgleichen</button>
        </div>
      </div>
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Gerät</th><th>Mandant</th><th>Klasse</th><th>Zustand</th><th>Preis/Monat</th><th>Zuletzt aktiv</th><th></th></tr></thead>
          <tbody>
            <template v-for="d in devices" :key="d.id">
              <tr>
                <td><strong>{{ d.name }}</strong><div class="rmm-muted">{{ d.device_key }}<span v-if="d.note"> · {{ d.note }}</span></div></td>
                <td>{{ d.tenant }}<span v-if="d.tenant_override" class="rmm-muted"> (von Hand)</span></td>
                <td>{{ d.class_label }}<span v-if="d.class_override" class="rmm-muted"> (von Hand)</span></td>
                <td><span class="badge" :class="stateClass(d.state)">{{ d.state_label }}</span></td>
                <td>{{ money(d.unit_price) }}<span v-if="d.price_override !== null" class="rmm-muted"> (eigener)</span></td>
                <td class="rmm-muted">{{ fmtAgo(d.last_active_at) }}</td>
                <td><button class="btn btn-ghost btn-sm" @click="edit(d)">Bearbeiten</button></td>
              </tr>
              <tr v-if="editing && editing.id === d.id">
                <td colspan="7">
                  <div class="rmm-form">
                    <label class="lic-check"><input v-model="editing.billable" type="checkbox" /> abrechnen (belegt einen Platz)</label>
                    <label>Eigener Monatspreis<input v-model="editing.price" type="number" min="0" step="0.01" class="input" placeholder="leer = nach Preisliste" /></label>
                    <label>Mandant<input v-model="editing.tenant" class="input" :placeholder="d.detected_tenant + ' (erkannt)'" /></label>
                    <label>Klasse
                      <select v-model="editing.cls" class="select">
                        <option value="">{{ classes[d.detected_class] }} (erkannt)</option>
                        <option v-for="(label, key) in classes" :key="key" :value="key">{{ label }}</option>
                      </select>
                    </label>
                    <label class="is-wide">Notiz<input v-model="editing.note" class="input" /></label>
                    <div class="is-wide rmm-actions">
                      <button class="btn btn-primary btn-sm" :disabled="busy" @click="saveDevice(d)">Speichern</button>
                      <button class="btn btn-ghost btn-sm" @click="editing = null">Abbrechen</button>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
            <tr v-if="!devices.length"><td colspan="7" class="rmm-muted">Noch keine Geräte gezählt.</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import { fmtAgo, fmtTime } from '../utils/rmm'

const auth = useAuthStore()
const st = ref({ warnings: [], errors: [] })
const src = ref({ mode: 'github' })
const request = ref({})
const devices = ref([])
const classes = ref({})
const states = ref({})
const currency = ref('EUR')
const filter = ref({ state: '', q: '' })
const editing = ref(null)
const urlToken = ref('')
const uploadText = ref('')
const error = ref('')
const notice = ref('')
const busy = ref(false)

const originLabel = computed(() => ({ github: 'GitHub', url: 'Lizenz-Endpunkt', upload: 'von Hand eingespielt' }[st.value.origin] || '–'))
const fmtDate = (d) => d ? new Date(d + 'T00:00:00').toLocaleDateString('de-DE') : ''
const money = (v) => (v ?? 0).toLocaleString('de-DE', { style: 'currency', currency: currency.value })
const stateClass = (s) => ({ licensed: 'badge-success', free: 'badge-primary', over_limit: 'badge-danger', excluded: 'badge-neutral', inactive: 'badge-warning' }[s] || 'badge-neutral')

async function guard(fn) {
  busy.value = true
  try { await fn(); error.value = '' } catch (e) { error.value = e.message; notice.value = '' } finally { busy.value = false }
}
function apply(data) {
  st.value = data
  src.value = { ...data.source }
}
async function load() {
  await guard(async () => {
    apply(await auth.api('/api/license/status'))
    request.value = await auth.api('/api/license/request')
    await loadDevices()
  })
}
async function loadDevices() {
  const p = new URLSearchParams()
  if (filter.value.state) p.set('state', filter.value.state)
  if (filter.value.q) p.set('q', filter.value.q)
  const data = await auth.api(`/api/license/devices?${p}`)
  devices.value = data.devices
  classes.value = data.classes
  states.value = data.states
  currency.value = data.currency
}
async function checkNow() {
  await guard(async () => {
    apply(await auth.api('/api/license/check', { method: 'POST' }))
    notice.value = st.value.edition === 'enterprise' ? 'Lizenz geprüft – gültig.' : 'Geprüft.'
    await loadDevices()
  })
}
async function saveSource() {
  await guard(async () => {
    const body = { ...src.value }
    delete body.url_token_set
    if (urlToken.value) body.url_token = urlToken.value
    await auth.api('/api/license/source', { method: 'PUT', body })
    urlToken.value = ''
    notice.value = 'Lizenzquelle gespeichert.'
    if (body.mode !== 'manual') await checkNow()
    else await load()
  })
}
function readFile(ev) {
  const file = ev.target.files?.[0]
  if (!file) return
  file.text().then(text => { uploadText.value = text })
}
async function upload() {
  await guard(async () => {
    let license
    try { license = JSON.parse(uploadText.value) } catch { throw new Error('Das ist kein JSON.') }
    apply(await auth.api('/api/license/upload', { method: 'POST', body: { license } }))
    uploadText.value = ''
    notice.value = 'Lizenz eingespielt.'
    await loadDevices()
  })
}
async function removeLicense() {
  const confirm = prompt('Lizenz entfernen? RIMPERIO läuft danach als Community-Ausgabe. Zur Bestätigung ENTFERNEN eingeben:')
  if (!confirm) return
  await guard(async () => { apply(await auth.api('/api/license/remove', { method: 'POST', body: { confirm } })); await loadDevices() })
}
async function regenerate() {
  const confirm = prompt('Neue Instanz-Kennung nur für einen Klon aus derselben Sicherung. Die bisherige Lizenz gilt dann hier nicht mehr. Zur Bestätigung NEU eingeben:')
  if (!confirm) return
  await guard(async () => { apply(await auth.api('/api/license/instance/regenerate', { method: 'POST', body: { confirm } })); request.value = await auth.api('/api/license/request') })
}
async function syncNow() {
  await guard(async () => { await auth.api('/api/license/sync', { method: 'POST' }); apply(await auth.api('/api/license/status')); await loadDevices() })
}
function edit(d) {
  editing.value = { id: d.id, billable: d.billable, price: d.price_override ?? '', tenant: d.tenant_override || '', cls: d.class_override || '', note: d.note }
}
async function saveDevice(d) {
  await guard(async () => {
    const e = editing.value
    const body = { billable: e.billable, tenant_override: e.tenant, class_override: e.cls, note: e.note }
    if (e.price === '' || e.price === null) body.clear_price = true
    else body.price_override = Number(e.price)
    await auth.api(`/api/license/devices/${d.id}`, { method: 'PATCH', body })
    editing.value = null
    apply(await auth.api('/api/license/status'))
    await loadDevices()
  })
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); notice.value = 'Kopiert.' } catch { error.value = 'Kopieren nicht möglich – bitte von Hand markieren.' }
}

onMounted(load)
</script>

<style scoped>
.lic-tiles { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fill, minmax(13rem, 1fr)); }
.lic-tile { padding: 1rem; display: flex; flex-direction: column; gap: .35rem; }
.lic-big { font-size: 1.5rem; font-weight: 700; }
.lic-name { font-size: 1.05rem; overflow-wrap: anywhere; }
.lic-bar { height: .4rem; border-radius: 999px; background: var(--color-border, #ddd); overflow: hidden; }
.lic-bar span { display: block; height: 100%; background: var(--color-success); }
.lic-bar span.is-full { background: var(--color-danger); }
.lic-id code { font-size: .95rem; overflow-wrap: anywhere; }
.lic-dl { display: grid; grid-template-columns: max-content 1fr; gap: .35rem 1rem; margin: 1rem 0 0; font-size: .875rem; }
.lic-dl dt { color: var(--color-text-muted); }
.lic-dl dd { margin: 0; }
.lic-check { flex-direction: row !important; align-items: center; gap: .5rem !important; }
.lic-text { width: 100%; font-family: monospace; font-size: .8rem; }
</style>
