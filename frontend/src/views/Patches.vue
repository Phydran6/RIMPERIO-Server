<!--
  Datei:        views/Patches.vue
  Projekt:      LRMM — Frontend
  Autor:        Phydran6
  Version:      2026.11.05.12.00.00
  Beschreibung: Patch-Überblick über alle Agents. Suchen und Einspielen passiert
                je Agent in der Agent-Ansicht.
-->
<template>
  <div class="space-y-4">
    <p class="text-sm text-slate-400">Letzter bekannter Patch-Stand je Rechner. Suche/Einspielen: in der jeweiligen Agent-Ansicht.</p>
    <div class="card overflow-hidden">
      <div class="overflow-x-auto">
        <table class="w-full">
          <thead><tr><th class="th">Rechner</th><th class="th">Plattform</th><th class="th">Offene Updates</th><th class="th">Zuletzt geprüft</th><th class="th"></th></tr></thead>
          <tbody>
            <tr v-for="r in rows" :key="r.agent_uid" class="hover:bg-white/5 cursor-pointer" @click="open(r)">
              <td class="td font-medium text-slate-100">{{ r.hostname }}</td>
              <td class="td text-slate-300">{{ r.platform }}</td>
              <td class="td"><span :class="r.count ? 'badge-warn' : 'badge-online'">{{ r.count }}</span></td>
              <td class="td text-slate-400">{{ r.scanned_at ? fmtTime(r.scanned_at) : 'nie' }}</td>
              <td class="td text-right"><router-link :to="`/agents/${r.agent_uid}`" class="text-xs text-brand-300 hover:underline">öffnen</router-link></td>
            </tr>
            <tr v-if="!rows.length"><td class="td text-center text-slate-500 py-10" colspan="5">Noch kein Patch-Stand. In einer Agent-Ansicht „Nach Updates suchen" starten.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { fmtTime } from '../util'

const router = useRouter()
const rows = ref([])
let timer = null

async function load() { try { rows.value = await api.get('/patches') } catch { /* */ } }
function open(r) { router.push(`/agents/${r.agent_uid}`) }

onMounted(() => { load(); timer = setInterval(load, 10000) })
onUnmounted(() => clearInterval(timer))
</script>
