<!--
  Datei:        views/Dashboard.vue
  Projekt:      RIMPERIO — Frontend
  Autor:        Phydran6
  Version:      2026.10.31.15.40.00
  Beschreibung: Startseite: Kennzahlen, aktive Alarme, Verteilung.
-->
<template>
  <div class="space-y-6">
    <div class="grid grid-cols-2 lg:grid-cols-5 gap-3">
      <StatCard label="Agents" :value="s.agents_total" />
      <StatCard label="Online" :value="s.agents_online" tone="good" />
      <StatCard label="Offline" :value="s.agents_offline" :tone="s.agents_offline ? 'bad' : ''" />
      <StatCard label="Aktive Alarme" :value="s.alerts_active" :tone="s.alerts_active ? 'warn' : ''" />
      <StatCard label="Befehle offen" :value="s.commands_pending" />
    </div>

    <div class="grid md:grid-cols-2 gap-6">
      <div class="card">
        <div class="px-4 py-3 border-b border-white/5 flex items-center justify-between">
          <h2 class="font-semibold">Aktive Alarme</h2>
          <router-link to="/alerts" class="text-xs text-brand-300 hover:underline">alle ansehen</router-link>
        </div>
        <ul v-if="alerts.length" class="divide-y divide-white/5">
          <li v-for="a in alerts" :key="a.id" class="px-4 py-3 flex items-start gap-3">
            <span :class="a.severity === 'critical' ? 'badge-crit' : 'badge-warn'">{{ a.severity }}</span>
            <span class="text-sm text-slate-300">{{ a.message }}</span>
          </li>
        </ul>
        <p v-else class="px-4 py-8 text-center text-sm text-slate-500">Keine aktiven Alarme. Alles ruhig.</p>
      </div>

      <div class="card">
        <div class="px-4 py-3 border-b border-white/5"><h2 class="font-semibold">Verteilung</h2></div>
        <div class="p-4 grid grid-cols-2 gap-6">
          <div>
            <div class="text-xs uppercase tracking-wide text-slate-500 mb-2">Nach Plattform</div>
            <ul class="space-y-1 text-sm">
              <li v-for="(n, k) in s.by_platform" :key="k" class="flex justify-between">
                <span class="text-slate-300">{{ k }}</span><span class="text-slate-500">{{ n }}</span>
              </li>
              <li v-if="!Object.keys(s.by_platform || {}).length" class="text-slate-500">—</li>
            </ul>
          </div>
          <div>
            <div class="text-xs uppercase tracking-wide text-slate-500 mb-2">Nach Standort</div>
            <ul class="space-y-1 text-sm">
              <li v-for="(n, k) in s.by_site" :key="k" class="flex justify-between">
                <span class="text-slate-300">{{ k }}</span><span class="text-slate-500">{{ n }}</span>
              </li>
              <li v-if="!Object.keys(s.by_site || {}).length" class="text-slate-500">—</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api'
import StatCard from '../components/StatCard.vue'

const s = ref({ agents_total: 0, agents_online: 0, agents_offline: 0, alerts_active: 0, commands_pending: 0, by_platform: {}, by_site: {} })
const alerts = ref([])
let timer = null

async function load() {
  try {
    const [summary, al] = await Promise.all([
      api.get('/dashboard/summary'),
      api.get('/alerts?active=true&limit=5')
    ])
    s.value = summary
    alerts.value = al
  } catch { /* vom globalen 401-Handler oder beim nächsten Takt erledigt */ }
}

onMounted(() => { load(); timer = setInterval(load, 5000) })
onUnmounted(() => clearInterval(timer))
</script>
