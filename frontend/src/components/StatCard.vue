<!--
  Datei:        StatCard.vue
  Projekt:      RIMPERIO — Frontend
  Autor:        Phydran6
  Version:      2026.10.31.15.40.00
  Beschreibung: Kleine Kennzahlen-Kachel fürs Dashboard.
-->
<template>
  <div class="card p-4">
    <div class="text-xs uppercase tracking-wide text-slate-500">{{ label }}</div>
    <div class="mt-1 text-2xl font-semibold" :class="toneClass">{{ value }}</div>
    <div v-if="sub" class="text-xs text-slate-500 mt-1">{{ sub }}</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({
  label: String,
  value: [String, Number],
  sub: String,
  tone: { type: String, default: '' }
})
const toneClass = computed(() => ({
  good: 'text-emerald-300', warn: 'text-amber-300', bad: 'text-red-300'
}[props.tone] || 'text-slate-100'))
</script>
