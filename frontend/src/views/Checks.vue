<!--
  Datei:        views/Checks.vue
  Projekt:      LRMM — Frontend
  Autor:        Phydran6
  Version:      2026.11.05.12.00.00
  Beschreibung: Geplante Checks verwalten und ihre letzten Ergebnisse sehen.
-->
<template>
  <div class="space-y-5">
    <div class="flex items-center gap-2">
      <button class="btn-ghost" :class="tab === 'defs' ? '!bg-brand-500/20 text-brand-200' : ''" @click="tab = 'defs'">Definitionen</button>
      <button class="btn-ghost" :class="tab === 'res' ? '!bg-brand-500/20 text-brand-200' : ''" @click="tab = 'res'">Ergebnisse</button>
      <div class="flex-1"></div>
      <button v-if="tab === 'defs' && canEdit" class="btn-primary" @click="openNew">Neuer Check</button>
    </div>

    <!-- Formular -->
    <div v-if="tab === 'defs' && editing" class="card p-5 space-y-3">
      <h2 class="font-semibold">{{ form.id ? 'Check bearbeiten' : 'Neuer Check' }}</h2>
      <div class="grid sm:grid-cols-3 gap-3">
        <div><label class="label">Name</label><input v-model="form.name" class="input" /></div>
        <div>
          <label class="label">Typ</label>
          <select v-model="form.type" class="input">
            <option v-for="t in types" :key="t" :value="t">{{ t }}</option>
          </select>
        </div>
        <div><label class="label">Intervall (s)</label><input v-model.number="form.interval_sec" type="number" class="input" /></div>
        <div>
          <label class="label">Gilt für</label>
          <select v-model="form.scope_type" class="input"><option value="global">alle</option><option value="site">Standort</option><option value="agent">ein Agent</option></select>
        </div>
        <div v-if="form.scope_type !== 'global'"><label class="label">{{ form.scope_type === 'site' ? 'Standort' : 'Agent-UID' }}</label><input v-model="form.scope_value" class="input" /></div>
        <div>
          <label class="label">Schwere bei Fehler</label>
          <select v-model="form.severity" class="input"><option>warning</option><option>critical</option><option>info</option></select>
        </div>
      </div>

      <!-- Typ-spezifische Parameter -->
      <div class="grid sm:grid-cols-3 gap-3">
        <div v-if="['cpu','mem','disk'].includes(form.type)"><label class="label">Schwelle % (max)</label><input v-model.number="p.max" type="number" class="input" /></div>
        <div v-if="form.type === 'disk'"><label class="label">Mountpunkt (leer = schlimmster)</label><input v-model="p.mount" class="input" placeholder="C:\ oder /" /></div>
        <div v-if="form.type === 'service'"><label class="label">Dienstname</label><input v-model="p.name" class="input" /></div>
        <div v-if="['port','ping'].includes(form.type)"><label class="label">Host</label><input v-model="p.host" class="input" placeholder="127.0.0.1" /></div>
        <div v-if="form.type === 'port'"><label class="label">Port</label><input v-model.number="p.port" type="number" class="input" /></div>
        <div v-if="form.type === 'script'"><label class="label">Shell</label><select v-model="p.shell" class="input"><option>powershell</option><option>cmd</option><option>bash</option><option>sh</option></select></div>
        <div v-if="form.type === 'script'"><label class="label">Erwarteter Exit-Code</label><input v-model.number="p.expect_exit" type="number" class="input" /></div>
      </div>
      <div v-if="form.type === 'script'"><label class="label">Skript</label><textarea v-model="p.body" rows="4" class="input font-mono"></textarea></div>

      <p v-if="error" class="text-sm text-red-300">{{ error }}</p>
      <div class="flex gap-2">
        <button class="btn-primary" :disabled="!form.name" @click="save">Speichern</button>
        <button class="btn-ghost" @click="editing = false">Abbrechen</button>
      </div>
    </div>

    <!-- Definitionen -->
    <div v-if="tab === 'defs'" class="card overflow-hidden">
      <div class="overflow-x-auto">
        <table class="w-full">
          <thead><tr><th class="th">Name</th><th class="th">Typ</th><th class="th">Gilt für</th><th class="th">Intervall</th><th class="th">Aktiv</th><th class="th"></th></tr></thead>
          <tbody>
            <tr v-for="c in checks" :key="c.id" class="hover:bg-white/5">
              <td class="td font-medium text-slate-100">{{ c.name }}</td>
              <td class="td text-slate-300">{{ c.type }}</td>
              <td class="td text-slate-300">{{ c.scope_type === 'global' ? 'alle' : c.scope_type + ': ' + c.scope_value }}</td>
              <td class="td text-slate-400">{{ c.interval_sec }} s</td>
              <td class="td"><span class="badge" :class="c.enabled ? 'badge-online' : 'badge-offline'">{{ c.enabled ? 'an' : 'aus' }}</span></td>
              <td class="td text-right space-x-2 whitespace-nowrap">
                <button v-if="canEdit" class="btn-ghost !py-1 text-xs" @click="openEdit(c)">Bearbeiten</button>
                <button v-if="canEdit" class="btn-danger !py-1 text-xs" @click="remove(c)">Löschen</button>
              </td>
            </tr>
            <tr v-if="!checks.length"><td class="td text-center text-slate-500 py-10" colspan="6">Noch keine Checks.</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Ergebnisse -->
    <div v-if="tab === 'res'" class="card overflow-hidden">
      <div class="overflow-x-auto">
        <table class="w-full">
          <thead><tr><th class="th">Status</th><th class="th">Rechner</th><th class="th">Check</th><th class="th">Wert</th><th class="th">Details</th></tr></thead>
          <tbody>
            <tr v-for="(r, i) in results" :key="i" class="hover:bg-white/5">
              <td class="td"><span :class="r.status === 'fail' ? 'badge-crit' : (r.status === 'pass' ? 'badge-online' : 'badge-offline')">{{ r.status }}</span></td>
              <td class="td text-slate-200">{{ r.hostname }}</td>
              <td class="td text-slate-300">{{ r.name }} <span class="text-slate-600">({{ r.type }})</span></td>
              <td class="td text-slate-300">{{ r.value }}</td>
              <td class="td text-slate-500 max-w-sm truncate">{{ r.output }}</td>
            </tr>
            <tr v-if="!results.length"><td class="td text-center text-slate-500 py-10" colspan="5">Noch keine Ergebnisse.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { api } from '../api'
import { useAuth } from '../stores/auth'

const auth = useAuth()
const canEdit = computed(() => auth.canOperate)
const types = ['cpu', 'mem', 'disk', 'service', 'port', 'ping', 'script']

const tab = ref('defs')
const checks = ref([])
const results = ref([])
const editing = ref(false)
const error = ref('')
let timer = null

const blank = { id: null, name: '', type: 'cpu', interval_sec: 300, scope_type: 'global', scope_value: '', severity: 'warning' }
const form = reactive({ ...blank })
const p = reactive({ max: 90, mount: '', name: '', host: '127.0.0.1', port: 0, shell: 'powershell', expect_exit: 0, body: '' })

function resetParams() { Object.assign(p, { max: 90, mount: '', name: '', host: '127.0.0.1', port: 0, shell: 'powershell', expect_exit: 0, body: '' }) }

function buildParams() {
  switch (form.type) {
    case 'cpu': case 'mem': return { max: p.max }
    case 'disk': return p.mount ? { max: p.max, mount: p.mount } : { max: p.max }
    case 'service': return { name: p.name }
    case 'port': return { host: p.host, port: p.port }
    case 'ping': return { host: p.host }
    case 'script': return { shell: p.shell, body: p.body, expect_exit: p.expect_exit }
    default: return {}
  }
}

async function loadChecks() { try { checks.value = await api.get('/checks') } catch (e) { error.value = e.message } }
async function loadResults() { try { results.value = await api.get('/checks/results') } catch { /* */ } }

function openNew() { Object.assign(form, blank); resetParams(); editing.value = true; error.value = '' }
function openEdit(c) {
  Object.assign(form, { id: c.id, name: c.name, type: c.type, interval_sec: c.interval_sec, scope_type: c.scope_type, scope_value: c.scope_value, severity: c.severity })
  resetParams(); Object.assign(p, c.params || {})
  editing.value = true; error.value = ''
}

async function save() {
  error.value = ''
  const payload = { name: form.name, type: form.type, params: buildParams(), interval_sec: form.interval_sec, scope_type: form.scope_type, scope_value: form.scope_value, severity: form.severity, enabled: true }
  try {
    if (form.id) await api.put(`/checks/${form.id}`, payload)
    else await api.post('/checks', payload)
    editing.value = false
    await loadChecks()
  } catch (e) { error.value = e.message }
}
async function remove(c) {
  if (!confirm(`Check „${c.name}" löschen?`)) return
  await api.del(`/checks/${c.id}`); await loadChecks()
}

watch(tab, (t) => { if (t === 'res') loadResults() })
onMounted(() => { loadChecks(); loadResults(); timer = setInterval(() => { if (tab.value === 'res') loadResults() }, 5000) })
onUnmounted(() => clearInterval(timer))
</script>
