<!--
  Datei:        views/AgentDetail.vue
  Projekt:      RIMPERIO — Frontend
  Autor:        Phydran6
  Version:      2026.10.31.15.40.00
  Beschreibung: Ein Agent im Detail: Live-Werte, Inventur, Befehl ausführen,
                Verlauf der Befehle.
-->
<template>
  <div v-if="agent" class="space-y-6">
    <!-- Kopf -->
    <div class="flex flex-wrap items-start gap-4">
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-3">
          <h1 class="text-xl font-semibold truncate">{{ agent.hostname }}</h1>
          <span :class="agent.online ? 'badge-online' : 'badge-offline'">{{ agent.online ? 'online' : 'offline' }}</span>
        </div>
        <div class="text-sm text-slate-500 mt-1">{{ agent.os }} · {{ agent.arch }} · {{ agent.local_ip }} · zuletzt {{ relTime(agent.last_seen) }}</div>
      </div>
      <router-link to="/agents" class="btn-ghost">← Zurück</router-link>
      <button v-if="auth.isAdmin" class="btn-danger" @click="remove">Entfernen</button>
    </div>

    <!-- Live-Werte -->
    <div class="grid sm:grid-cols-3 gap-3">
      <div class="card p-4">
        <div class="flex justify-between"><span class="text-xs uppercase text-slate-500">CPU</span><span :class="tone(agent.last_cpu)">{{ pct(agent.last_cpu) }}</span></div>
        <Sparkline :values="cpuSeries" color="#5b86fb" class="mt-2" />
      </div>
      <div class="card p-4">
        <div class="flex justify-between"><span class="text-xs uppercase text-slate-500">RAM</span><span :class="tone(agent.last_mem)">{{ pct(agent.last_mem) }}</span></div>
        <Sparkline :values="memSeries" color="#34d399" class="mt-2" />
      </div>
      <div class="card p-4">
        <div class="flex justify-between"><span class="text-xs uppercase text-slate-500">Platte (max)</span><span :class="tone(agent.last_disk)">{{ pct(agent.last_disk) }}</span></div>
        <Sparkline :values="diskSeries" color="#fbbf24" class="mt-2" />
      </div>
    </div>

    <div class="grid lg:grid-cols-2 gap-6">
      <!-- Inventur -->
      <div class="card">
        <div class="px-4 py-3 border-b border-white/5"><h2 class="font-semibold">Inventur</h2></div>
        <dl class="p-4 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
          <dt class="text-slate-500">Betriebssystem</dt><dd class="text-slate-200">{{ inv.os || agent.os || '—' }}</dd>
          <dt class="text-slate-500">Prozessor</dt><dd class="text-slate-200">{{ inv.cpu_model || '—' }}</dd>
          <dt class="text-slate-500">Kerne</dt><dd class="text-slate-200">{{ inv.cpu_cores ?? '—' }}</dd>
          <dt class="text-slate-500">RAM gesamt</dt><dd class="text-slate-200">{{ inv.mem_total_mb ? Math.round(inv.mem_total_mb / 1024) + ' GB' : '—' }}</dd>
          <dt class="text-slate-500">Laufzeit</dt><dd class="text-slate-200">{{ fmtUptime(inv.uptime_seconds) }}</dd>
          <dt class="text-slate-500">Agent-Version</dt><dd class="text-slate-200">{{ agent.version || '—' }}</dd>
          <dt class="text-slate-500">MAC</dt><dd class="text-slate-200 break-all">{{ (inv.macs || []).join(', ') || '—' }}</dd>
        </dl>
        <div v-if="(inv.disk || []).length" class="px-4 pb-4">
          <div class="text-xs uppercase text-slate-500 mb-2">Platten</div>
          <div v-for="d in inv.disk" :key="d.mount" class="flex items-center gap-3 text-sm mb-1">
            <span class="w-28 truncate text-slate-300">{{ d.mount }}</span>
            <div class="flex-1 h-2 rounded bg-white/5 overflow-hidden">
              <div class="h-full bg-brand-500" :style="{ width: Math.min(100, d.percent) + '%' }"></div>
            </div>
            <span class="w-28 text-right text-slate-400">{{ Math.round(d.used_gb) }}/{{ Math.round(d.total_gb) }} GB</span>
          </div>
        </div>
      </div>

      <!-- Stammdaten + Befehl -->
      <div class="space-y-6">
        <div class="card p-4 space-y-3">
          <h2 class="font-semibold">Stammdaten</h2>
          <div><label class="label">Standort</label><input v-model="editSite" class="input" :disabled="!auth.canOperate" /></div>
          <div><label class="label">Beschreibung</label><input v-model="editDesc" class="input" :disabled="!auth.canOperate" /></div>
          <button v-if="auth.canOperate" class="btn-ghost" @click="saveMeta">Speichern</button>
        </div>

        <div class="card p-4 space-y-3">
          <h2 class="font-semibold">Befehl ausführen</h2>
          <div class="flex gap-3">
            <select v-model="shell" class="input max-w-[12rem]">
              <option v-for="sh in shells" :key="sh" :value="sh">{{ sh }}</option>
            </select>
            <input v-model.number="timeout" type="number" min="1" class="input max-w-[8rem]" title="Zeitlimit (s)" />
          </div>
          <textarea v-model="command" rows="4" class="input font-mono" placeholder="z. B. Get-Service | Where-Object Status -eq 'Stopped'"></textarea>
          <p v-if="cmdError" class="text-sm text-red-300">{{ cmdError }}</p>
          <button class="btn-primary" :disabled="!auth.canOperate || !command.trim() || sending" @click="send">
            {{ sending ? 'Senden …' : 'An Agent senden' }}
          </button>
          <p v-if="!auth.canOperate" class="text-xs text-slate-500">Nur-Lesen-Rolle: Befehle sind gesperrt.</p>
        </div>

        <div class="card p-4 space-y-3">
          <h2 class="font-semibold">Skript ausführen</h2>
          <div class="flex gap-3">
            <select v-model="selectedScript" class="input">
              <option disabled value="">Skript wählen …</option>
              <option v-for="s in scripts" :key="s.id" :value="s.id">{{ s.name }}</option>
            </select>
            <button class="btn-primary" :disabled="!auth.canOperate || !selectedScript" @click="runScript">Ausführen</button>
          </div>
          <p class="text-xs text-slate-500">Aus der <router-link to="/scripts" class="text-brand-300 hover:underline">Skript-Bibliothek</router-link>. Das Ergebnis erscheint unten im Verlauf.</p>
        </div>
      </div>
    </div>

    <!-- Patches -->
    <div class="card">
      <div class="px-4 py-3 border-b border-white/5 flex items-center justify-between">
        <h2 class="font-semibold">Patches</h2>
        <div v-if="auth.canOperate" class="flex gap-2">
          <button class="btn-ghost !py-1 text-xs" @click="scanPatches">Nach Updates suchen</button>
          <button class="btn-primary !py-1 text-xs" :disabled="!patch.count" @click="applyPatches">Einspielen ({{ patch.count }})</button>
        </div>
      </div>
      <div class="p-4">
        <p class="text-sm text-slate-400 mb-2">
          {{ patch.scanned_at ? ('Zuletzt geprüft: ' + fmtTime(patch.scanned_at) + ' · ' + patch.count + ' offen') : 'Noch nicht geprüft.' }}
        </p>
        <ul v-if="patch.updates && patch.updates.length" class="text-sm divide-y divide-white/5 max-h-60 overflow-auto">
          <li v-for="(u, i) in patch.updates" :key="i" class="py-1.5 flex justify-between gap-3">
            <span class="text-slate-300 truncate">{{ u.title || u.id }}</span>
            <span class="text-slate-500 text-xs whitespace-nowrap">{{ u.severity }}</span>
          </li>
        </ul>
        <pre v-if="patch.last_apply_output" class="text-xs text-slate-500 mt-2 font-mono whitespace-pre-wrap">{{ patch.last_apply_output }}</pre>
      </div>
    </div>

    <!-- Checks dieses Agents -->
    <div v-if="agentChecks.length" class="card">
      <div class="px-4 py-3 border-b border-white/5"><h2 class="font-semibold">Checks</h2></div>
      <ul class="divide-y divide-white/5">
        <li v-for="(r, i) in agentChecks" :key="i" class="px-4 py-2 flex items-center gap-3 text-sm">
          <span :class="r.status === 'fail' ? 'badge-crit' : (r.status === 'pass' ? 'badge-online' : 'badge-offline')">{{ r.status }}</span>
          <span class="text-slate-300">{{ r.name }}</span>
          <span class="text-slate-500">{{ r.value }}</span>
        </li>
      </ul>
    </div>

    <!-- Befehlsverlauf -->
    <div class="card overflow-hidden">
      <div class="px-4 py-3 border-b border-white/5"><h2 class="font-semibold">Befehlsverlauf</h2></div>
      <div class="overflow-x-auto">
        <table class="w-full">
          <thead><tr><th class="th">#</th><th class="th">Shell</th><th class="th">Befehl</th><th class="th">Status</th><th class="th">Code</th><th class="th">Wann</th></tr></thead>
          <tbody>
            <template v-for="c in commands" :key="c.id">
              <tr class="hover:bg-white/5 cursor-pointer" @click="expanded = expanded === c.id ? null : c.id">
                <td class="td text-slate-500">{{ c.id }}</td>
                <td class="td text-slate-300">{{ c.shell }}</td>
                <td class="td font-mono text-slate-300 max-w-sm truncate">{{ c.command }}</td>
                <td class="td"><span :class="statusBadge(c.status)">{{ c.status }}</span></td>
                <td class="td text-slate-400">{{ c.exit_code ?? '—' }}</td>
                <td class="td text-slate-500">{{ relTime(c.created_at) }}</td>
              </tr>
              <tr v-if="expanded === c.id">
                <td class="td bg-ink-950/60" colspan="6">
                  <div class="text-xs text-slate-500 mb-1">von {{ c.created_by }} · {{ fmtTime(c.created_at) }}</div>
                  <pre v-if="c.stdout" class="text-xs font-mono text-emerald-200 whitespace-pre-wrap break-all">{{ c.stdout }}</pre>
                  <pre v-if="c.stderr" class="text-xs font-mono text-red-300 whitespace-pre-wrap break-all">{{ c.stderr }}</pre>
                  <span v-if="!c.stdout && !c.stderr" class="text-xs text-slate-500">keine Ausgabe</span>
                </td>
              </tr>
            </template>
            <tr v-if="!commands.length"><td class="td text-center text-slate-500 py-8" colspan="6">Noch keine Befehle.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <div v-else class="text-slate-500">Lade …</div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { pct, relTime, fmtTime, fmtUptime } from '../util'
import Sparkline from '../components/Sparkline.vue'
import { useAuth } from '../stores/auth'

const props = defineProps({ uid: String })
const auth = useAuth()
const router = useRouter()

const agent = ref(null)
const telemetry = ref([])
const commands = ref([])
const expanded = ref(null)

const editSite = ref('')
const editDesc = ref('')

const shells = ['powershell', 'cmd', 'bash', 'sh']
const shell = ref('powershell')
const command = ref('')
const timeout = ref(300)
const sending = ref(false)
const cmdError = ref('')

const scripts = ref([])
const selectedScript = ref('')
const patch = ref({ count: 0, updates: [], scanned_at: null, last_apply_output: null })
const agentChecks = ref([])

let timer = null

const inv = computed(() => agent.value?.inventory || {})
const cpuSeries = computed(() => telemetry.value.map(t => t.cpu_percent))
const memSeries = computed(() => telemetry.value.map(t => t.mem_percent))
const diskSeries = computed(() => telemetry.value.map(t => Math.max(0, ...(t.disk || []).map(d => d.percent), 0)))

function tone(v) {
  if (v === null || v === undefined) return 'text-slate-500'
  if (v >= 90) return 'text-red-300'
  if (v >= 75) return 'text-amber-300'
  return 'text-slate-200'
}
function statusBadge(s) {
  return { done: 'badge-online', pending: 'badge-warn', running: 'badge-warn', error: 'badge-crit', timeout: 'badge-crit' }[s] || 'badge-offline'
}

async function loadAgent() {
  try {
    agent.value = await api.get('/agents/' + props.uid)
    if (editSite.value === '' && editDesc.value === '') {
      editSite.value = agent.value.site
      editDesc.value = agent.value.description
    }
  } catch { /* nächster Takt */ }
}
async function loadTelemetry() {
  try { telemetry.value = await api.get('/agents/' + props.uid + '/telemetry?limit=60') } catch { /* */ }
}
async function loadCommands() {
  try { commands.value = await api.get('/agents/' + props.uid + '/commands?limit=25') } catch { /* */ }
}

async function saveMeta() {
  await api.patch('/agents/' + props.uid, { site: editSite.value, description: editDesc.value })
  await loadAgent()
}
async function send() {
  cmdError.value = ''
  sending.value = true
  try {
    await api.post('/agents/' + props.uid + '/commands', { shell: shell.value, command: command.value, timeout: timeout.value })
    command.value = ''
    await loadCommands()
  } catch (e) {
    cmdError.value = e.message
  } finally {
    sending.value = false
  }
}
async function remove() {
  if (!confirm('Diesen Agent wirklich entfernen?')) return
  await api.del('/agents/' + props.uid)
  router.push('/agents')
}

async function loadScripts() { try { scripts.value = await api.get('/scripts') } catch { /* */ } }
async function runScript() {
  if (!selectedScript.value) return
  await api.post(`/agents/${props.uid}/run-script/${selectedScript.value}`, {})
  await loadCommands()
}
async function loadPatches() { try { patch.value = await api.get(`/agents/${props.uid}/patches`) } catch { /* */ } }
async function scanPatches() { await api.post(`/agents/${props.uid}/patches/scan`) }
async function applyPatches() {
  if (!confirm('Updates jetzt einspielen? Das kann einen Neustart auslösen.')) return
  await api.post(`/agents/${props.uid}/patches/apply`)
}
async function loadAgentChecks() {
  try {
    const all = await api.get('/checks/results')
    agentChecks.value = all.filter((r) => r.agent_uid === props.uid)
  } catch { /* */ }
}

function refresh() { loadAgent(); loadCommands(); loadPatches(); loadAgentChecks() }
onMounted(() => {
  loadAgent(); loadTelemetry(); loadCommands(); loadScripts(); loadPatches(); loadAgentChecks()
  timer = setInterval(() => { refresh(); loadTelemetry() }, 5000)
})
onUnmounted(() => clearInterval(timer))
</script>
