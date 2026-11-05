<!--
  Datei:        Sparkline.vue
  Projekt:      RIMPERIO — Frontend
  Autor:        Phydran6
  Version:      2026.10.31.15.40.00
  Beschreibung: Winziges Verlaufsdiagramm als Inline-SVG — ohne Chart-Bibliothek.
-->
<template>
  <svg :viewBox="`0 0 ${w} ${h}`" class="w-full h-10" preserveAspectRatio="none">
    <polyline v-if="points" :points="points" fill="none" :stroke="color" stroke-width="1.5"
      stroke-linejoin="round" stroke-linecap="round" />
    <line v-else x1="0" :y1="h - 1" :x2="w" :y2="h - 1" :stroke="color" stroke-width="1" opacity="0.3" />
  </svg>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({
  values: { type: Array, default: () => [] },
  color: { type: String, default: '#5b86fb' },
  max: { type: Number, default: 100 }
})
const w = 100
const h = 30
const points = computed(() => {
  const v = props.values
  if (!v.length) return ''
  const n = v.length
  return v.map((val, i) => {
    const x = n === 1 ? w / 2 : (i / (n - 1)) * w
    const clamped = Math.max(0, Math.min(props.max, Number(val) || 0))
    const y = h - (clamped / props.max) * h
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
})
</script>
