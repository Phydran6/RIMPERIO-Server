<!--
  Datei:        views/Alerts.vue
  Projekt:      RIMPERIO — Frontend
  Autor:        Phydran6
  Version:      2026.10.31.15.40.00
  Beschreibung: Alarme ansehen und auflösen.
-->
<template>
  <div class="space-y-4">
    <div class="flex items-center gap-3">
      <label class="flex items-center gap-2 text-sm text-slate-400">
        <input type="checkbox" v-model="onlyActive" class="accent-brand-500" /> nur aktive
      </label>
    </div>

    <div class="card overflow-hidden">
      <div class="overflow-x-auto">
        <table class="w-full">
          <thead><tr><th class="th">Schwere</th><th class="th">Art</th><th class="th">Meldung</th><th class="th">Seit</th><th class="th"></th></tr></thead>
          <tbody>
            <tr v-for="a in alerts" :key="a.id" class="hover:bg-white/5">
              <td class="td"><span :class="a.severity === 'critical' ? 'badge-crit' : (a.severity === 'warning' ? 'badge-warn' : 'badge-offline')">{{ a.severity }}</span></td>
              <td class="td text-slate-300">{{ a.kind }}</td>
              <td class="td text-slate-200">{{ a.message }}<span v-if="!a.active" class="text-xs text-slate-500"> · aufgelöst</span></td>
              <td class="td text-slate-500">{{ relTime(a.created_at) }}</td>
              <td class="td text-right">
                <button v-if="a.active && canOperate" class="btn-ghost !py-1 text-xs" @click="resolve(a)">Auflösen</button>
              </td>
            </tr>
            <tr v-if="!alerts.length"><td class="td text-center text-slate-500 py-10" colspan="5">Keine Alarme.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch, computed } from 'vue'
import { api } from '../api'
import { relTime } from '../util'
import { useAuth } from '../stores/auth'

const auth = useAuth()
const canOperate = computed(() => auth.canOperate)
const alerts = ref([])
const onlyActive = ref(true)
let timer = null

async function load() {
  try { alerts.value = await api.get('/alerts?limit=200' + (onlyActive.value ? '&active=true' : '')) } catch { /* */ }
}
async function resolve(a) {
  await api.post(`/alerts/${a.id}/resolve`)
  await load()
}

watch(onlyActive, load)
onMounted(() => { load(); timer = setInterval(load, 5000) })
onUnmounted(() => clearInterval(timer))
</script>
