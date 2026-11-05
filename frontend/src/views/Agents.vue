<!--
  Datei:        views/Agents.vue
  Projekt:      RIMPERIO — Frontend
  Autor:        Phydran6
  Version:      2026.10.31.15.40.00
  Beschreibung: Liste aller Agents mit Suche, Filter und Live-Werten. Dazu ein
                Bereich mit fertigen Installationsbefehlen für neue Rechner.
-->
<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-center gap-3">
      <input v-model="search" class="input max-w-xs" placeholder="Hostname suchen …" />
      <select v-model="onlineFilter" class="input max-w-[10rem]">
        <option value="">Alle</option>
        <option value="online">Nur online</option>
        <option value="offline">Nur offline</option>
      </select>
      <div class="flex-1"></div>
      <button class="btn-primary" @click="showAdd = !showAdd">{{ showAdd ? 'Schließen' : 'Agent hinzufügen' }}</button>
    </div>

    <!-- Agent hinzufügen -->
    <div v-if="showAdd" class="card p-5 space-y-4">
      <h2 class="font-semibold">Neuen Agent anbinden</h2>
      <p class="text-sm text-slate-400">
        Auf dem Zielrechner ausführen. Server und Geheimnis sind bereits eingesetzt.
      </p>

      <div>
        <div class="label">Windows — schnell (als Administrator)</div>
        <CodeBlock :text="winOneLiner" />
      </div>
      <div>
        <div class="label">Windows — MSI (GPO / Intune)</div>
        <CodeBlock :text="msiLine" />
      </div>
      <div>
        <div class="label">Linux (als root)</div>
        <CodeBlock :text="linuxLine" />
      </div>

      <p class="text-xs text-slate-500">
        Enrollment-Geheimnis wechselbar unter <router-link to="/settings" class="text-brand-300 hover:underline">Einstellungen</router-link>.
        Signatur &amp; Virenscanner: siehe <span class="font-mono">installer/README.md</span>.
      </p>
    </div>

    <!-- Tabelle -->
    <div class="card overflow-hidden">
      <div class="overflow-x-auto">
        <table class="w-full">
          <thead>
            <tr>
              <th class="th">Status</th><th class="th">Hostname</th><th class="th">Standort</th>
              <th class="th">System</th><th class="th">CPU</th><th class="th">RAM</th>
              <th class="th">Platte</th><th class="th">Zuletzt</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in filtered" :key="a.agent_uid" class="hover:bg-white/5 cursor-pointer" @click="open(a)">
              <td class="td"><span :class="a.online ? 'badge-online' : 'badge-offline'">{{ a.online ? 'online' : 'offline' }}</span></td>
              <td class="td font-medium text-slate-100">{{ a.hostname }}<div class="text-xs text-slate-500">{{ a.local_ip }}</div></td>
              <td class="td text-slate-300">{{ a.site }}</td>
              <td class="td text-slate-300">{{ a.os || a.platform }}<div class="text-xs text-slate-500">{{ a.arch }}</div></td>
              <td class="td" :class="metricTone(a.last_cpu)">{{ pct(a.last_cpu) }}</td>
              <td class="td" :class="metricTone(a.last_mem)">{{ pct(a.last_mem) }}</td>
              <td class="td" :class="metricTone(a.last_disk)">{{ pct(a.last_disk) }}</td>
              <td class="td text-slate-400">{{ relTime(a.last_seen) }}</td>
            </tr>
            <tr v-if="!filtered.length">
              <td class="td text-center text-slate-500 py-10" colspan="8">
                Noch keine Agents. Über „Agent hinzufügen" den ersten Rechner anbinden.
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { pct, relTime } from '../util'
import CodeBlock from '../components/CodeBlock.vue'

const router = useRouter()
const agents = ref([])
const search = ref('')
const onlineFilter = ref('')
const showAdd = ref(false)
const secret = ref('GEHEIMNIS')
let timer = null

const serverUrl = window.location.origin
const RAW = 'https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main'

const winOneLiner = computed(() =>
  `& ([scriptblock]::Create((irm ${RAW}/installer/windows/install-agent.ps1))) \`\n  -Server ${serverUrl} -Secret ${secret.value}`)
const msiLine = computed(() =>
  `msiexec /i rimperio-agent.msi SERVER=${serverUrl} SECRET=${secret.value} SITE=Standard /qn`)
const linuxLine = computed(() =>
  `curl -fsSL ${RAW}/installer/linux/install-agent.sh | sudo bash -s -- --server ${serverUrl} --secret ${secret.value}`)

const filtered = computed(() => {
  let list = agents.value
  if (search.value) list = list.filter(a => a.hostname.toLowerCase().includes(search.value.toLowerCase()))
  if (onlineFilter.value === 'online') list = list.filter(a => a.online)
  if (onlineFilter.value === 'offline') list = list.filter(a => !a.online)
  return list
})

function metricTone(v) {
  if (v === null || v === undefined) return 'text-slate-500'
  if (v >= 90) return 'text-red-300'
  if (v >= 75) return 'text-amber-300'
  return 'text-slate-300'
}
function open(a) { router.push({ name: 'agent', params: { uid: a.agent_uid } }) }

async function load() {
  try { agents.value = await api.get('/agents') } catch { /* nächster Takt */ }
}
async function loadSecret() {
  try { const r = await api.get('/settings/enroll-secret'); if (r?.secret) secret.value = r.secret }
  catch { /* kein Admin: Platzhalter bleibt */ }
}

onMounted(() => { load(); loadSecret(); timer = setInterval(load, 5000) })
onUnmounted(() => clearInterval(timer))
</script>
