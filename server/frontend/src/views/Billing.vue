<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Abrechnung je Gerät: Preise, Mandanten, Monatsabrechnung, Abrechnungs-API
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Abrechnung</h1>
        <p class="page-subtitle">
          Was ein überwachtes Gerät im Monat kostet, legen Sie selbst fest – ein Preis für alle, je Geräteklasse, je
          Mandant oder je Gerät. RIMPERIO zählt jeden Tag mit, an dem ein Gerät aktiv war, und rechnet monatlich ab.
        </p>
      </div>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>
    <p v-if="loaded && !licensed" class="rmm-note is-info">
      Enterprise-Funktion: Preise einstellen und die Vorschau ansehen geht immer. Export, Festschreiben und die
      Abrechnungs-API brauchen eine Lizenz – siehe <router-link to="/license">Lizenz &amp; Geräte</router-link>.
    </p>

    <section class="card mb-4">
      <div class="card-header"><h2 class="card-title">Preise</h2></div>
      <div class="card-body">
        <div class="rmm-form">
          <label>Währung<input v-model="cfg.currency" class="input" maxlength="3" /></label>
          <label>Standardpreis je Gerät/Monat<input v-model.number="cfg.default_price" type="number" min="0" step="0.01" class="input" /></label>
          <label>Zählweise
            <select v-model="cfg.count_method" class="select">
              <option v-for="(label, key) in methods" :key="key" :value="key">{{ label }}</option>
            </select>
          </label>
          <label v-if="cfg.count_method === 'month'">zählt ab … aktiven Tagen<input v-model.number="cfg.min_days" type="number" min="1" max="31" class="input" /></label>
          <label>Steuersatz %<input v-model.number="cfg.tax_percent" type="number" min="0" max="100" step="0.1" class="input" /></label>
          <label>Platz frei nach … Tagen ohne Lebenszeichen<input v-model.number="cfg.release_after_days" type="number" min="1" max="365" class="input" /></label>
        </div>
        <div class="rmm-table-wrap" style="margin-top:1rem">
          <table class="table">
            <thead><tr><th>Geräteklasse</th><th>abrechnen</th><th>Preis/Monat</th></tr></thead>
            <tbody>
              <tr v-for="(label, key) in classes" :key="key">
                <td>{{ label }}</td>
                <td><input v-model="cfg.class_billable[key]" type="checkbox" /></td>
                <td><input v-model="classPrices[key]" type="number" min="0" step="0.01" class="input bil-num" :placeholder="'Standard (' + money(cfg.default_price) + ')'" /></td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="rmm-muted">Reihenfolge, der erste Treffer gilt: Preis am Gerät → Klasse beim Mandanten → Standard des Mandanten → Klasse → Standardpreis.</p>
      </div>
    </section>

    <section class="card mb-4">
      <div class="card-header">
        <h2 class="card-title">Mandanten</h2>
        <div class="rmm-actions">
          <input v-model="newTenant" class="input" placeholder="Mandant hinzufügen" style="max-width:14rem" @keyup.enter="addTenant" />
          <button class="btn btn-secondary btn-sm" :disabled="!newTenant.trim()" @click="addTenant">Hinzufügen</button>
        </div>
      </div>
      <div class="card-body">
        <p class="rmm-muted">Bis zur Mandantenfähigkeit gilt der Standort eines Geräts als Mandant. Ohne Eintrag gelten die Preise oben.</p>
        <div v-for="name in tenantNames" :key="name" class="bil-tenant">
          <div class="bil-tenant-head">
            <strong>{{ name }}</strong>
            <span v-if="!cfg.tenants[name]" class="rmm-muted">Preisliste</span>
            <button v-if="!cfg.tenants[name]" class="btn btn-ghost btn-sm" @click="ensureTenant(name)">Eigene Bedingungen</button>
            <button v-else class="btn btn-ghost btn-sm" @click="removeTenant(name)">Bedingungen entfernen</button>
          </div>
          <div v-if="cfg.tenants[name]" class="rmm-form">
            <label>Name auf der Rechnung<input v-model="cfg.tenants[name].display_name" class="input" :placeholder="name" /></label>
            <label>Kundennummer<input v-model="cfg.tenants[name].customer_no" class="input" /></label>
            <label>Standardpreis<input v-model="cfg.tenants[name].default_price" type="number" min="0" step="0.01" class="input" placeholder="wie oben" /></label>
            <label>Grundgebühr/Monat<input v-model.number="cfg.tenants[name].flat_fee" type="number" min="0" step="0.01" class="input" /></label>
            <label>Mindestbetrag/Monat<input v-model.number="cfg.tenants[name].minimum_fee" type="number" min="0" step="0.01" class="input" /></label>
            <label>Rabatt %<input v-model.number="cfg.tenants[name].discount_percent" type="number" min="0" max="100" step="0.1" class="input" /></label>
            <label>Inklusiv-Geräte<input v-model.number="cfg.tenants[name].free_devices" type="number" min="0" class="input" /></label>
            <label class="bil-check"><input v-model="cfg.tenants[name].billable" type="checkbox" /> abrechnen</label>
            <details class="is-wide">
              <summary class="rmm-muted">Preise je Geräteklasse für diesen Mandanten</summary>
              <div class="rmm-form" style="margin-top:.5rem">
                <label v-for="(label, key) in classes" :key="key">{{ label }}
                  <input v-model="cfg.tenants[name].class_prices[key]" type="number" min="0" step="0.01" class="input" placeholder="wie oben" />
                </label>
              </div>
            </details>
          </div>
        </div>
        <div class="rmm-actions" style="margin-top:1rem">
          <button class="btn btn-primary btn-sm" :disabled="busy" @click="save">Preise speichern</button>
        </div>
      </div>
    </section>

    <section class="card mb-4">
      <div class="card-header">
        <h2 class="card-title">Monatsabrechnung</h2>
        <div class="rmm-actions">
          <input v-model="month" type="month" class="input" style="max-width:11rem" @change="loadReport" />
          <label class="bil-check rmm-muted"><input v-model="live" type="checkbox" @change="loadReport" /> neu rechnen</label>
          <button class="btn btn-secondary btn-sm" :disabled="!licensed" @click="download(false)"><AppIcon name="download" :size="15" /> CSV</button>
          <button class="btn btn-secondary btn-sm" :disabled="!licensed" @click="download(true)">Gerätenachweis</button>
          <button class="btn btn-secondary btn-sm" :disabled="!licensed || report.partial" @click="closeMonth">Festschreiben</button>
        </div>
      </div>
      <div class="card-body">
        <p class="rmm-muted">
          {{ report.count_method_label }} ·
          <template v-if="report.statement">festgeschrieben am {{ fmtTime(report.statement.created_at) }} ({{ report.statement.created_by }})</template>
          <template v-else-if="report.partial">laufender Monat – Stand jetzt</template>
          <template v-else>noch nicht festgeschrieben (geschieht am Monatsersten von selbst)</template>
        </p>
        <div v-for="t in report.tenants" :key="t.tenant" class="bil-invoice">
          <div class="bil-tenant-head"><strong>{{ t.display_name }}</strong><span v-if="t.customer_no" class="rmm-muted">Kunde {{ t.customer_no }}</span><span class="rmm-muted">{{ t.device_count }} Gerät(e)</span></div>
          <table class="table">
            <thead><tr><th>Position</th><th>Geräte</th><th>Menge</th><th>Einzelpreis</th><th class="bil-r">Betrag</th></tr></thead>
            <tbody>
              <tr v-for="l in t.lines" :key="l.device_class + l.unit_price">
                <td>{{ l.class_label }}<div class="rmm-muted">{{ l.devices.map(d => d.name).join(', ') }}</div></td>
                <td>{{ l.device_count }}</td>
                <td>{{ num(l.quantity) }}<span v-if="l.free" class="rmm-muted"> (+{{ num(l.free) }} inklusive)</span></td>
                <td>{{ money(l.unit_price) }}</td>
                <td class="bil-r">{{ money(l.amount) }}</td>
              </tr>
              <tr v-if="t.discount"><td colspan="4">Rabatt {{ num(t.discount_percent) }} %</td><td class="bil-r">−{{ money(t.discount) }}</td></tr>
              <tr v-if="t.flat_fee"><td colspan="4">Grundgebühr</td><td class="bil-r">{{ money(t.flat_fee) }}</td></tr>
              <tr v-if="t.minimum_applied"><td colspan="4">Mindestbetrag angewendet</td><td class="bil-r">{{ money(t.minimum_fee) }}</td></tr>
              <tr class="bil-sum"><td colspan="4">Netto</td><td class="bil-r">{{ money(t.net) }}</td></tr>
              <tr v-if="t.tax"><td colspan="4">Steuer {{ num(report.tax_percent) }} %</td><td class="bil-r">{{ money(t.tax) }}</td></tr>
              <tr v-if="t.tax" class="bil-sum"><td colspan="4">Brutto</td><td class="bil-r">{{ money(t.gross) }}</td></tr>
            </tbody>
          </table>
        </div>
        <p v-if="!report.tenants?.length" class="rmm-muted">Für diesen Monat gibt es keine abrechenbaren Geräte.</p>
        <p v-if="report.excluded_tenants?.length" class="rmm-muted">Nicht abgerechnet: {{ report.excluded_tenants.join(', ') }}</p>
        <p v-if="report.totals" class="bil-total">Gesamt: {{ report.totals.devices }} Gerät(e) · netto {{ money(report.totals.net) }}<span v-if="report.totals.tax"> · brutto {{ money(report.totals.gross) }}</span></p>
        <details v-if="statements.length">
          <summary class="rmm-muted">Festgeschriebene Monate</summary>
          <ul class="bil-list">
            <li v-for="s in statements" :key="s.month"><a href="#" @click.prevent="month = s.month; live = false; loadReport()">{{ s.month }}</a>
              <span class="rmm-muted"> – {{ s.devices }} Gerät(e), netto {{ money(s.net) }}</span></li>
          </ul>
        </details>
      </div>
    </section>

    <section class="card">
      <div class="card-header">
        <h2 class="card-title">Abrechnungs-API</h2>
        <div class="rmm-actions">
          <input v-model="keyName" class="input" placeholder="Name, z. B. ERP" style="max-width:12rem" />
          <button class="btn btn-primary btn-sm" :disabled="!licensedApi || busy" @click="createKey"><AppIcon name="plus" :size="15" /> Schlüssel anlegen</button>
        </div>
      </div>
      <div class="card-body">
        <p class="rmm-muted">Für Rechnungsprogramm, ERP oder n8n: nur lesen, mit eigenem Schlüssel.</p>
        <p v-if="newKey" class="rmm-note is-ok">Neuer Schlüssel (nur jetzt sichtbar): <code>{{ newKey }}</code></p>
        <pre class="bil-pre">curl -H "Authorization: Bearer rbk_…" {{ origin }}/api/license/v1/usage?month={{ month }}
curl -H "Authorization: Bearer rbk_…" "{{ origin }}/api/license/v1/usage?month={{ month }}&amp;format=csv"
curl -H "Authorization: Bearer rbk_…" {{ origin }}/api/license/v1/devices
curl -H "Authorization: Bearer rbk_…" {{ origin }}/api/license/v1/status</pre>
        <table v-if="keys.length" class="table">
          <thead><tr><th>Name</th><th>Schlüssel</th><th>angelegt</th><th>zuletzt benutzt</th><th></th></tr></thead>
          <tbody>
            <tr v-for="k in keys" :key="k.id">
              <td>{{ k.name }}</td><td><code>{{ k.prefix }}…</code></td>
              <td class="rmm-muted">{{ fmtTime(k.created_at) }} ({{ k.created_by }})</td>
              <td class="rmm-muted">{{ k.last_used_at ? fmtAgo(k.last_used_at) : 'nie' }}</td>
              <td><button class="btn btn-danger btn-sm" @click="revoke(k)">Widerrufen</button></td>
            </tr>
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
const cfg = ref({ currency: 'EUR', class_billable: {}, class_prices: {}, tenants: {} })
const classPrices = ref({})
const classes = ref({})
const methods = ref({})
const knownTenants = ref([])
const licensed = ref(false)
const licensedApi = ref(false)
const loaded = ref(false)
const report = ref({})
const statements = ref([])
const keys = ref([])
const newKey = ref('')
const keyName = ref('ERP')
const newTenant = ref('')
const month = ref(new Date().toISOString().slice(0, 7))
const live = ref(false)
const error = ref('')
const notice = ref('')
const busy = ref(false)
const origin = window.location.origin

const tenantNames = computed(() => [...new Set([...knownTenants.value, ...Object.keys(cfg.value.tenants || {})])].sort())
const money = (v) => Number(v || 0).toLocaleString('de-DE', { style: 'currency', currency: report.value.currency || cfg.value.currency || 'EUR' })
const num = (v) => Number(v || 0).toLocaleString('de-DE', { maximumFractionDigits: 4 })

async function guard(fn) {
  busy.value = true
  try { await fn(); error.value = '' } catch (e) { error.value = e.message; notice.value = '' } finally { busy.value = false }
}
function applyConfig(c) {
  for (const t of Object.values(c.tenants || {})) t.class_prices = t.class_prices || {}
  cfg.value = c
  classPrices.value = { ...c.class_prices }
}
async function load() {
  await guard(async () => {
    const data = await auth.api('/api/license/billing/config')
    classes.value = data.classes
    methods.value = data.methods
    knownTenants.value = data.known_tenants
    licensed.value = data.licensed
    applyConfig(data.config)
    const k = await auth.api('/api/license/api-keys')
    keys.value = k.keys
    licensedApi.value = k.licensed
    await loadReport()
    loaded.value = true
  })
}
async function loadReport() {
  report.value = await auth.api(`/api/license/billing/report?month=${month.value}${live.value ? '&live=true' : ''}`)
  statements.value = (await auth.api('/api/license/billing/statements')).statements
}
function ensureTenant(name) {
  cfg.value.tenants = { ...cfg.value.tenants, [name]: { display_name: '', customer_no: '', default_price: '', class_prices: {}, flat_fee: 0, minimum_fee: 0, discount_percent: 0, free_devices: 0, billable: true, note: '' } }
}
function removeTenant(name) {
  const copy = { ...cfg.value.tenants }
  delete copy[name]
  cfg.value.tenants = copy
}
function addTenant() {
  const name = newTenant.value.trim()
  if (!name) return
  if (!cfg.value.tenants[name]) ensureTenant(name)
  newTenant.value = ''
}
async function save() {
  await guard(async () => {
    const body = JSON.parse(JSON.stringify(cfg.value))
    body.class_prices = Object.fromEntries(Object.entries(classPrices.value).filter(([, v]) => v !== '' && v !== null && v !== undefined))
    for (const t of Object.values(body.tenants)) {
      t.class_prices = Object.fromEntries(Object.entries(t.class_prices || {}).filter(([, v]) => v !== '' && v !== null && v !== undefined))
      if (t.default_price === '') t.default_price = null
    }
    const data = await auth.api('/api/license/billing/config', { method: 'PUT', body })
    applyConfig(data.config)
    notice.value = 'Preise gespeichert.'
    await loadReport()
  })
}
async function download(detail) {
  await guard(async () => {
    const ticket = await auth.ticket('license-export')
    const p = new URLSearchParams({ month: month.value, token: ticket })
    if (detail) p.set('detail', 'true')
    if (live.value) p.set('live', 'true')
    window.location.href = `/api/license/billing/report.csv?${p}`
  })
}
async function closeMonth() {
  if (!confirm(`Abrechnung ${month.value} mit den aktuellen Preisen festschreiben? Eine vorhandene wird ersetzt.`)) return
  await guard(async () => {
    report.value = await auth.api(`/api/license/billing/statements/${month.value}`, { method: 'POST' })
    statements.value = (await auth.api('/api/license/billing/statements')).statements
    notice.value = `Abrechnung ${month.value} festgeschrieben.`
  })
}
async function createKey() {
  await guard(async () => {
    const r = await auth.api('/api/license/api-keys', { method: 'POST', body: { name: keyName.value } })
    newKey.value = r.key
    keys.value = (await auth.api('/api/license/api-keys')).keys
  })
}
async function revoke(k) {
  if (!confirm(`Schlüssel „${k.name}“ widerrufen? Systeme, die ihn nutzen, bekommen keinen Zugriff mehr.`)) return
  await guard(async () => {
    await auth.api(`/api/license/api-keys/${k.id}`, { method: 'DELETE' })
    keys.value = (await auth.api('/api/license/api-keys')).keys
  })
}

onMounted(load)
</script>

<style scoped>
.bil-num { max-width: 12rem; }
.bil-check { flex-direction: row !important; align-items: center; gap: .4rem !important; display: inline-flex; }
.bil-tenant { border-top: 1px solid var(--color-border, #e5e7eb); padding: .75rem 0; }
.bil-tenant-head { display: flex; gap: .75rem; align-items: center; flex-wrap: wrap; margin-bottom: .5rem; }
.bil-invoice { margin-bottom: 1.25rem; }
.bil-r { text-align: right; white-space: nowrap; }
.bil-sum td { font-weight: 600; }
.bil-total { font-weight: 700; margin: .5rem 0 1rem; }
.bil-list { margin: .5rem 0 0; padding-left: 1.2rem; font-size: .875rem; }
.bil-pre { background: var(--color-bg-secondary, rgba(0,0,0,.04)); padding: .75rem; border-radius: var(--radius); font-size: .78rem; overflow-x: auto; white-space: pre; }
</style>
