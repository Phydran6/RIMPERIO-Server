<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - SNMP-Geräte: Übersicht, Assistent, Zugangsdaten, Vorlagen
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">SNMP-Geräte</h1>
        <p class="page-subtitle">
          Switches, Router, Firewalls, Drucker, USVs, NAS — alles ohne Agent. Gerät eintragen, testen,
          Vorlagen werden vorgeschlagen, Schwellen sind sofort aktiv. Abgefragt wird vom Server oder von einem
          RIMPERIO-Agent im Netz des Geräts.
        </p>
      </div>
      <button v-if="auth.isAdmin" class="btn btn-primary btn-sm" @click="startWizard()"><AppIcon name="plus" :size="16" /> Gerät hinzufügen</button>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>

    <nav class="rmm-tabs">
      <button v-for="t in TABS" :key="t.key" :class="{ 'is-active': tab === t.key }" @click="tab = t.key">{{ t.label }}</button>
    </nav>

    <!-- Assistent ---------------------------------------------------------- -->
    <section v-if="wizard" class="card mb-4">
      <div class="card-header"><h2 class="card-title">Gerät hinzufügen</h2></div>
      <div class="card-body rmm-form">
        <label>Adresse (IP oder Name)<input v-model="wizard.host" class="input" placeholder="192.168.1.20" /></label>
        <label>Zugangsdaten
          <select v-model="wizard.credential_id" class="select">
            <option :value="null">v2c „public“</option>
            <option v-for="c in credentials" :key="c.id" :value="c.id">{{ c.name }} (v{{ c.version }})</option>
          </select>
        </label>
        <label>Abfrage durch
          <select v-model="wizard.poller" class="select">
            <option value="server">den RIMPERIO-Server</option>
            <option v-for="a in agents" :key="a.agent_uid" :value="a.agent_uid">Agent {{ a.hostname }} ({{ a.site }})</option>
          </select>
        </label>
        <div class="is-wide rmm-actions">
          <button class="btn btn-secondary btn-sm" :disabled="busy || !wizard.host" @click="probe">Testen</button>
          <span class="rmm-muted" v-if="wizard.poller !== 'server'">Der Test läuft vom Server aus. Erreicht nur der Agent das Gerät, einfach ohne Test speichern — der erste Wert kommt nach ≤ 1 Minute.</span>
          <button class="btn btn-ghost btn-sm" @click="credForm = blankCred(); tab = 'credentials'">Neue Zugangsdaten</button>
        </div>
        <div v-if="probeResult" class="is-wide">
          <p v-if="probeResult.ok" class="rmm-note is-ok">
            <strong>{{ probeResult.system.sys_name || wizard.host }}</strong> — {{ probeResult.system.sys_descr }}
            <span v-if="probeResult.system.sys_location"> · Standort: {{ probeResult.system.sys_location }}</span>
          </p>
          <p v-else class="rmm-note is-error">{{ probeResult.error }}</p>
        </div>
        <label>Name<input v-model="wizard.name" class="input" /></label>
        <label>Standort<input v-model="wizard.site" class="input" /></label>
        <label>Abfrage alle (Sekunden)<input v-model.number="wizard.interval_sec" type="number" min="30" class="input" /></label>
        <label class="pol-check is-wide">Vorlagen:
          <span v-for="t in templates" :key="t.key" :title="t.description || ''">
            <input v-model="wizard.templates" type="checkbox" :value="t.key" /> {{ t.name }}
            <span v-if="probeResult?.suggested?.includes(t.key)" class="badge badge-success">passt</span>
          </span>
        </label>
        <div class="is-wide rmm-actions">
          <button class="btn btn-primary btn-sm" :disabled="busy || !wizard.host || !wizard.name" @click="saveDevice">Speichern</button>
          <button class="btn btn-ghost btn-sm" @click="wizard = null; probeResult = null">Abbrechen</button>
        </div>
      </div>
    </section>

    <!-- Geräte -------------------------------------------------------------- -->
    <section v-if="tab === 'devices'" class="card">
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Gerät</th><th>Standort</th><th>Status</th><th>Probleme</th><th>Abfrage</th><th>Antwort</th></tr></thead>
          <tbody>
            <tr v-if="!devices.length"><td colspan="6" class="rmm-muted">Noch keine Geräte. „Gerät hinzufügen“ oder unter „Aus Netzwerksuche“ übernehmen.</td></tr>
            <tr v-for="d in devices" :key="d.id" :style="d.enabled ? '' : 'opacity:.55'">
              <td><router-link :to="`/rmm/snmp/${d.id}`"><strong>{{ d.name }}</strong></router-link>
                <div class="rmm-muted">{{ d.host }} · {{ d.system?.sys_descr?.slice(0, 60) }}</div></td>
              <td>{{ d.site }}</td>
              <td><span class="badge" :class="SNMP_STATUS[d.status]?.[0]">{{ SNMP_STATUS[d.status]?.[1] || d.status }}</span></td>
              <td class="rmm-muted">
                <div v-for="p in d.problems" :key="p.key"><span class="badge" :class="SNMP_STATUS[p.status]?.[0]">{{ p.label }}</span> {{ p.text || fmtRate(p.value, p.unit) }}</div>
                <span v-if="d.status === 'down'">{{ d.last_error }}</span>
              </td>
              <td>{{ d.poller === 'server' ? 'Server' : agentName(d.poller) }} · {{ d.interval_sec }} s</td>
              <td class="rmm-muted">{{ fmtAgo(d.last_ok_at) }}<span v-if="d.rtt_ms"> · {{ d.rtt_ms }} ms</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Netzwerksuche ---------------------------------------------------- -->
    <section v-if="tab === 'discovered'" class="card">
      <div class="card-header"><h2 class="card-title">Gefunden, aber noch nicht überwacht</h2></div>
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Adresse</th><th>Name</th><th>Art</th><th>SNMP</th><th></th></tr></thead>
          <tbody>
            <tr v-if="!discovered.length"><td colspan="5" class="rmm-muted">Die <router-link to="/discovery">Netzwerksuche</router-link> hat keine weiteren SNMP-Geräte gefunden.</td></tr>
            <tr v-for="d in discovered" :key="d.id">
              <td class="rmm-code">{{ d.ip }}</td><td>{{ d.snmp?.sys_name || d.hostname || '–' }}</td>
              <td>{{ d.os_guess || d.kind }}</td><td class="rmm-muted">{{ d.snmp?.sys_descr?.slice(0, 80) || 'Port 161 offen' }}</td>
              <td><button v-if="auth.isAdmin" class="btn btn-secondary btn-sm" @click="startWizard(d)">Überwachen</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Zugangsdaten ---------------------------------------------------- -->
    <section v-if="tab === 'credentials'">
      <div v-if="credForm" class="card mb-4">
        <div class="card-header"><h2 class="card-title">{{ credForm.id ? credForm.name : 'Neue Zugangsdaten' }}</h2></div>
        <div class="card-body rmm-form">
          <label>Name<input v-model="credForm.name" class="input" /></label>
          <label>Version<select v-model="credForm.version" class="select"><option value="2c">v2c</option><option value="1">v1</option><option value="3">v3</option></select></label>
          <label>Port<input v-model.number="credForm.port" type="number" class="input" /></label>
          <template v-if="credForm.version !== '3'">
            <label>Community<input v-model="credForm.community" type="password" class="input" :placeholder="credForm.community_set ? 'hinterlegt — leer lassen = unverändert' : 'public'" /></label>
          </template>
          <template v-else>
            <label>Benutzer<input v-model="credForm.username" class="input" /></label>
            <label>Authentisierung<select v-model="credForm.auth_proto" class="select"><option v-for="p in AUTH" :key="p">{{ p }}</option></select></label>
            <label v-if="credForm.auth_proto !== 'none'">Auth-Passwort<input v-model="credForm.auth_pass" type="password" class="input" :placeholder="credForm.auth_pass_set ? 'hinterlegt' : ''" /></label>
            <label v-if="credForm.auth_proto !== 'none'">Verschlüsselung<select v-model="credForm.priv_proto" class="select"><option v-for="p in PRIV" :key="p">{{ p }}</option></select></label>
            <label v-if="credForm.priv_proto !== 'none'">Priv-Passwort<input v-model="credForm.priv_pass" type="password" class="input" :placeholder="credForm.priv_pass_set ? 'hinterlegt' : ''" /></label>
            <label>Kontext (meist leer)<input v-model="credForm.context" class="input" /></label>
            <p class="is-wide rmm-muted">Der Server kann MD5/SHA/SHA-2 mit DES oder AES-128. AES-192/256 nur mit einem Agent als Poller.</p>
          </template>
          <div class="is-wide rmm-actions">
            <button class="btn btn-primary btn-sm" :disabled="busy || !credForm.name" @click="saveCred">Speichern</button>
            <button class="btn btn-ghost btn-sm" @click="credForm = null">Abbrechen</button>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="card-header"><h2 class="card-title">Zugangsdaten</h2><button v-if="auth.isAdmin" class="btn btn-secondary btn-sm" @click="credForm = blankCred()">Neu</button></div>
        <div class="rmm-table-wrap">
          <table class="table">
            <thead><tr><th>Name</th><th>Version</th><th>Benutzer</th><th>Verfahren</th><th></th></tr></thead>
            <tbody>
              <tr v-for="c in credentials" :key="c.id">
                <td><strong>{{ c.name }}</strong></td><td>v{{ c.version }}</td><td>{{ c.username || '–' }}</td>
                <td>{{ c.version === '3' ? `${c.auth_proto} / ${c.priv_proto}` : 'Community' }}</td>
                <td class="rmm-actions" style="justify-content:flex-end">
                  <template v-if="auth.isAdmin">
                    <button class="btn btn-ghost btn-sm" @click="credForm = { ...c, community: '', auth_pass: '', priv_pass: '' }">Bearbeiten</button>
                    <button class="btn btn-danger btn-sm" @click="removeCred(c)">Löschen</button>
                  </template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- Vorlagen ------------------------------------------------------------- -->
    <section v-if="tab === 'templates'">
      <div v-if="tplForm" class="card mb-4">
        <div class="card-header"><h2 class="card-title">Eigene Vorlage</h2></div>
        <div class="card-body rmm-form">
          <label>Schlüssel<input v-model="tplForm.key" class="input" :disabled="tplForm.existing" placeholder="mein-switch" /></label>
          <label>Name<input v-model="tplForm.name" class="input" /></label>
          <label>Hersteller<input v-model="tplForm.vendor" class="input" /></label>
          <label>Erkennungs-OID (optional)<input v-model="tplForm.probe" class="input rmm-code" placeholder="1.3.6.1.4.1.…" /></label>
          <label class="is-wide">Metriken (JSON, Format wie die mitgelieferten Vorlagen)
            <textarea v-model="tplForm.metricsText" class="input rmm-code" rows="14" /></label>
          <div class="is-wide rmm-actions">
            <button class="btn btn-primary btn-sm" :disabled="busy" @click="saveTpl">Speichern</button>
            <button class="btn btn-ghost btn-sm" @click="tplForm = null">Abbrechen</button>
          </div>
        </div>
      </div>
      <div class="snmp-tpls">
        <article v-for="t in templates" :key="t.key" class="card snmp-tpl">
          <header><strong>{{ t.name }}</strong> <span class="badge badge-neutral">{{ t.vendor }}</span><span v-if="!t.builtin" class="badge badge-primary">eigene</span></header>
          <p class="rmm-muted">{{ t.description }}</p>
          <ul><li v-for="m in t.metrics" :key="m.key">{{ m.label }} <span class="rmm-muted">({{ m.type }}<span v-if="m.warn !== undefined && !Array.isArray(m.warn)">, Warnung {{ m.op === '<' ? '≤' : '≥' }} {{ m.warn }}{{ m.unit ? ' ' + m.unit : '' }}</span>)</span></li></ul>
          <footer v-if="auth.isAdmin" class="rmm-actions">
            <button class="btn btn-ghost btn-sm" @click="copyTpl(t)">{{ t.builtin ? 'Als eigene kopieren' : 'Bearbeiten' }}</button>
            <button v-if="!t.builtin" class="btn btn-danger btn-sm" @click="removeTpl(t)">Löschen</button>
          </footer>
        </article>
      </div>
    </section>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import { fmtAgo, fmtRate, SNMP_STATUS } from '../utils/rmm'

const TABS = [{ key: 'devices', label: 'Geräte' }, { key: 'discovered', label: 'Aus Netzwerksuche' },
  { key: 'credentials', label: 'Zugangsdaten' }, { key: 'templates', label: 'Vorlagen' }]
const AUTH = ['md5', 'sha', 'sha224', 'sha256', 'sha384', 'sha512', 'none']
const PRIV = ['aes', 'des', 'aes192', 'aes256', 'none']

const auth = useAuthStore()
const route = useRoute()
const tab = ref(route.query.tab || 'devices')
const devices = ref([])
const credentials = ref([])
const templates = ref([])
const agents = ref([])
const discovered = ref([])
const wizard = ref(null)
const probeResult = ref(null)
const credForm = ref(null)
const tplForm = ref(null)
const error = ref('')
const notice = ref('')
const busy = ref(false)
let timer = null

const blankCred = () => ({ name: '', version: '2c', community: '', username: '', auth_proto: 'sha', auth_pass: '', priv_proto: 'aes', priv_pass: '', context: '', port: 161 })
const agentName = uid => agents.value.find(a => a.agent_uid === uid)?.hostname || uid

async function guard(fn) {
  busy.value = true
  try { await fn(); error.value = '' } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function load() {
  try {
    ;[devices.value, credentials.value, templates.value, agents.value] = await Promise.all([
      auth.api('/api/snmp/devices'), auth.api('/api/snmp/credentials'), auth.api('/api/snmp/templates'), auth.api('/api/rmm/agents')])
    if (tab.value === 'discovered' && auth.isAdmin) discovered.value = await auth.api('/api/snmp/discovered')
  } catch (e) { error.value = e.message }
}

function startWizard(d = null) {
  probeResult.value = null
  wizard.value = { host: d?.ip || '', name: d?.snmp?.sys_name || d?.hostname || '', credential_id: credentials.value[0]?.id ?? null,
    poller: 'server', site: 'Standard', interval_sec: 300, templates: ['system', 'interfaces'], discovered_device_id: d?.id || null }
  window.scrollTo({ top: 0, behavior: 'smooth' })
}
async function probe() {
  await guard(async () => {
    probeResult.value = await auth.api('/api/snmp/probe', { method: 'POST', body: { host: wizard.value.host, credential_id: wizard.value.credential_id } })
    if (probeResult.value.ok) {
      wizard.value.templates = [...probeResult.value.suggested]
      if (!wizard.value.name) wizard.value.name = probeResult.value.system.sys_name || wizard.value.host
    }
  })
}
async function saveDevice() {
  await guard(async () => {
    const d = await auth.api('/api/snmp/devices', { method: 'POST', body: wizard.value })
    notice.value = `${d.name} wird überwacht.`; wizard.value = null; probeResult.value = null; tab.value = 'devices'; await load()
  })
}
async function saveCred() {
  await guard(async () => {
    const { id, community_set, auth_pass_set, priv_pass_set, ...body } = credForm.value
    await auth.api(id ? `/api/snmp/credentials/${id}` : '/api/snmp/credentials', { method: id ? 'PUT' : 'POST', body })
    credForm.value = null; notice.value = 'Zugangsdaten gespeichert.'; await load()
  })
}
async function removeCred(c) {
  if (!confirm(`Zugangsdaten „${c.name}“ löschen?`)) return
  await guard(async () => { await auth.api(`/api/snmp/credentials/${c.id}`, { method: 'DELETE' }); await load() })
}
function copyTpl(t) {
  tplForm.value = { key: t.builtin ? `${t.key}-eigen` : t.key, existing: !t.builtin, name: t.builtin ? `${t.name} (eigene)` : t.name,
    vendor: t.vendor || '', probe: t.probe || '', description: t.description || '', metricsText: JSON.stringify(t.metrics, null, 2) }
}
async function saveTpl() {
  await guard(async () => {
    let metrics
    try { metrics = JSON.parse(tplForm.value.metricsText) } catch { throw new Error('Die Metriken sind kein gültiges JSON.') }
    const body = { key: tplForm.value.key, name: tplForm.value.name, vendor: tplForm.value.vendor, description: tplForm.value.description, probe: tplForm.value.probe || null, metrics }
    await auth.api(tplForm.value.existing ? `/api/snmp/templates/${body.key}` : '/api/snmp/templates', { method: tplForm.value.existing ? 'PUT' : 'POST', body })
    tplForm.value = null; notice.value = 'Vorlage gespeichert.'; await load()
  })
}
async function removeTpl(t) {
  if (!confirm(`Vorlage „${t.name}“ löschen?`)) return
  await guard(async () => { await auth.api(`/api/snmp/templates/${t.key}`, { method: 'DELETE' }); await load() })
}

watch(tab, load)
onMounted(() => { load(); timer = setInterval(() => { if (tab.value === 'devices') load() }, 30000) })
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.pol-check { flex-direction: row !important; align-items: center; gap: .5rem !important; flex-wrap: wrap; }
.pol-check > span { display: inline-flex; gap: .25rem; align-items: center; margin-right: .75rem; }
.snmp-tpls { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fill, minmax(min(100%, 20rem), 1fr)); }
.snmp-tpl { padding: 1rem; display: flex; flex-direction: column; gap: .4rem; font-size: .875rem; }
.snmp-tpl ul { margin: 0; padding-left: 1.1rem; }
.snmp-tpl header { display: flex; gap: .4rem; align-items: center; flex-wrap: wrap; }
</style>
