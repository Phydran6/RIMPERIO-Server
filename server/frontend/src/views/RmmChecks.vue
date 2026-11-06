<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Fernwartung: Checks (wiederkehrende Prüfungen) und Ergebnisse
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Checks</h1>
        <p class="page-subtitle">
          Prüfungen, die der Agent selbst im eigenen Takt ausführt. Ein Fehler hebt einen Alarm
          und wird – je nach <router-link to="/alerts">Benachrichtigungen</router-link> – gemeldet.
        </p>
      </div>
      <button v-if="auth.isAdmin" class="btn btn-primary btn-sm" @click="open()"><AppIcon name="plus" :size="16" /> Neuer Check</button>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>

    <section v-if="form" class="card mb-4">
      <div class="card-header"><h2 class="card-title">{{ form.id ? 'Check bearbeiten' : 'Neuer Check' }}</h2></div>
      <div class="card-body rmm-form">
        <label>Name<input v-model="form.name" class="input" /></label>
        <label>Typ
          <select v-model="form.type" class="select"><option v-for="(t, k) in TYPES" :key="k" :value="k">{{ t.label }}</option></select>
        </label>
        <label v-for="p in TYPES[form.type].params" :key="p.key">{{ p.label }}
          <input v-model="form.params[p.key]" class="input" :type="p.number ? 'number' : 'text'" :placeholder="p.hint || ''" />
        </label>
        <label>Intervall (s)<input v-model.number="form.interval_sec" type="number" min="30" class="input" /></label>
        <label>Gilt für
          <select v-model="form.scope_type" class="select">
            <option value="global">alle Rechner</option><option value="site">einen Standort</option><option value="agent">einen Rechner</option>
          </select>
        </label>
        <label v-if="form.scope_type === 'site'">Standort<input v-model="form.scope_value" class="input" /></label>
        <label v-if="form.scope_type === 'agent'">Rechner
          <select v-model="form.scope_value" class="select"><option v-for="a in agents" :key="a.agent_uid" :value="a.agent_uid">{{ a.hostname }}</option></select>
        </label>
        <label>Schwere
          <select v-model="form.severity" class="select"><option value="warning">Warnung</option><option value="critical">kritisch</option><option value="info">Info</option></select>
        </label>
        <label style="flex-direction: row; align-items: center; gap: .5rem"><input v-model="form.enabled" type="checkbox" /> aktiv</label>
        <div class="is-wide rmm-actions">
          <button class="btn btn-primary btn-sm" :disabled="!form.name" @click="save">Speichern</button>
          <button class="btn btn-ghost btn-sm" @click="form = null">Abbrechen</button>
        </div>
      </div>
    </section>

    <section class="card mb-4">
      <div class="card-header"><h2 class="card-title">Definierte Checks</h2></div>
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Name</th><th>Typ</th><th>Gilt für</th><th>Intervall</th><th>Schwere</th><th></th></tr></thead>
          <tbody>
            <tr v-if="!checks.length"><td colspan="6" class="rmm-muted">Noch keine Checks.</td></tr>
            <tr v-for="c in checks" :key="c.id" :style="c.enabled ? '' : 'opacity: .55'">
              <td><strong>{{ c.name }}</strong></td><td>{{ TYPES[c.type]?.label || c.type }}</td>
              <td>{{ scopeLabel(c) }}</td><td>{{ c.interval_sec }} s</td>
              <td><span class="badge" :class="STATUS_BADGE[c.severity]">{{ STATUS_LABEL[c.severity] }}</span></td>
              <td class="rmm-actions" style="justify-content: flex-end">
                <button v-if="auth.isAdmin" class="btn btn-secondary btn-sm" @click="open(c)">Bearbeiten</button>
                <button v-if="auth.isAdmin" class="btn btn-danger btn-sm" @click="remove(c)">Löschen</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="card">
      <div class="card-header"><h2 class="card-title">Ergebnisse</h2>
        <label class="rmm-muted"><input v-model="onlyFail" type="checkbox" /> nur Fehler</label>
      </div>
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Rechner</th><th>Check</th><th>Status</th><th>Wert</th><th>Zeit</th></tr></thead>
          <tbody>
            <tr v-if="!shownResults.length"><td colspan="5" class="rmm-muted">Keine Ergebnisse.</td></tr>
            <tr v-for="r in shownResults" :key="`${r.agent_uid}-${r.check_id}`" :title="r.output || ''">
              <td><router-link :to="`/rmm/agents/${r.agent_uid}`">{{ r.hostname }}</router-link></td>
              <td>{{ r.name }}</td>
              <td><span class="badge" :class="STATUS_BADGE[r.status]">{{ STATUS_LABEL[r.status] || r.status }}</span></td>
              <td>{{ r.value }}</td><td>{{ fmtTime(r.ts) }}</td>
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
import { fmtTime, STATUS_BADGE, STATUS_LABEL } from '../utils/rmm'

// Parameter je Typ — so, wie der Agent sie erwartet (agent/internal/core/checks.go).
const TYPES = {
  cpu: { label: 'CPU-Last', params: [{ key: 'max', label: 'Schwelle (%)', number: true, hint: '90' }] },
  mem: { label: 'Arbeitsspeicher', params: [{ key: 'max', label: 'Schwelle (%)', number: true, hint: '90' }] },
  disk: { label: 'Plattenplatz', params: [{ key: 'max', label: 'Schwelle (%)', number: true, hint: '90' }, { key: 'mount', label: 'Laufwerk (leer = alle)', hint: 'C:\\ oder /' }] },
  service: { label: 'Dienst läuft', params: [{ key: 'name', label: 'Dienstname', hint: 'Spooler / nginx' }] },
  port: { label: 'Port erreichbar', params: [{ key: 'host', label: 'Host', hint: '127.0.0.1' }, { key: 'port', label: 'Port', number: true, hint: '443' }] },
  ping: { label: 'Ping', params: [{ key: 'host', label: 'Host', hint: '8.8.8.8' }] },
  script: { label: 'Skript (Exit-Code prüfen)', params: [{ key: 'shell', label: 'Shell', hint: 'powershell / bash' }, { key: 'body', label: 'Skript' }, { key: 'expect_exit', label: 'Erwarteter Exit-Code', number: true, hint: '0' }] },
}

const auth = useAuthStore()
const checks = ref([])
const results = ref([])
const agents = ref([])
const form = ref(null)
const error = ref('')
const onlyFail = ref(false)

const shownResults = computed(() => results.value.filter(r => !onlyFail.value || r.status === 'fail'))
const blank = () => ({ name: '', type: 'disk', params: {}, interval_sec: 300, scope_type: 'global',
  scope_value: '', severity: 'warning', enabled: true })

function scopeLabel(c) {
  if (c.scope_type === 'global') return 'alle Rechner'
  if (c.scope_type === 'site') return `Standort ${c.scope_value}`
  return agents.value.find(a => a.agent_uid === c.scope_value)?.hostname || 'ein Rechner'
}

async function load() {
  try {
    [checks.value, results.value, agents.value] = await Promise.all([
      auth.api('/api/rmm/checks'), auth.api('/api/rmm/checks/results'), auth.api('/api/rmm/agents')])
    error.value = ''
  } catch (e) { error.value = e.message }
}

function open(c = null) { form.value = c ? { ...c, params: { ...c.params } } : blank() }

async function save() {
  const { id, created_at, ...body } = form.value
  // Zahlenfelder als Zahl schicken.
  for (const p of TYPES[body.type].params) if (p.number && body.params[p.key] !== undefined && body.params[p.key] !== '') body.params[p.key] = Number(body.params[p.key])
  try {
    await auth.api(id ? `/api/rmm/checks/${id}` : '/api/rmm/checks', { method: id ? 'PUT' : 'POST', body })
    form.value = null
    await load()
  } catch (e) { error.value = e.message }
}

async function remove(c) {
  if (!confirm(`Check „${c.name}“ löschen?`)) return
  try { await auth.api(`/api/rmm/checks/${c.id}`, { method: 'DELETE' }); await load() } catch (e) { error.value = e.message }
}

onMounted(load)
</script>
