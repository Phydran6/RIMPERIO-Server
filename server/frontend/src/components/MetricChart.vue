<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Verlauf eines Messwerts als schlanke Linie (Inline-SVG)
     Eine Linie, eine Achse; beim Überfahren Fadenkreuz mit Zeit und Wert.
     ============================================================================== -->
<template>
  <div class="mc" ref="wrap">
    <p v-if="!points.length" class="rmm-muted">Noch kein Verlauf.</p>
    <svg v-else :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none" class="mc-svg"
         @mousemove="hover" @mouseleave="hi = null" role="img" :aria-label="label">
      <line v-for="g in grid" :key="g.y" :x1="PAD" :x2="W" :y1="g.y" :y2="g.y" class="mc-grid" />
      <path :d="path" class="mc-line" vector-effect="non-scaling-stroke" />
      <template v-if="hi">
        <line :x1="hi.x" :x2="hi.x" y1="0" :y2="H" class="mc-cross" vector-effect="non-scaling-stroke" />
      </template>
    </svg>
    <div v-if="points.length" class="mc-axis">
      <span>{{ fmt(max) }}</span><span>{{ fmt(min) }}</span>
    </div>
    <div v-if="hi" class="mc-tip" :style="{ left: `${hi.pct}%` }">
      <strong>{{ fmt(hi.p.value) }}</strong><span>{{ new Date(hi.p.ts).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' }) }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { fmtRate } from '../utils/rmm'

const props = defineProps({ points: { type: Array, default: () => [] }, unit: { type: String, default: '' }, label: { type: String, default: '' } })
const W = 600, H = 120, PAD = 0
const hi = ref(null)

const min = computed(() => Math.min(...props.points.map(p => p.value)))
const max = computed(() => Math.max(...props.points.map(p => p.value)))
const t0 = computed(() => new Date(props.points[0]?.ts).getTime())
const t1 = computed(() => new Date(props.points[props.points.length - 1]?.ts).getTime())
const xs = t => props.points.length < 2 ? W / 2 : PAD + (t - t0.value) / Math.max(1, t1.value - t0.value) * (W - PAD)
const ys = v => { const span = max.value - min.value || 1; return H - 6 - (v - min.value) / span * (H - 12) }
const path = computed(() => props.points.map((p, i) => `${i ? 'L' : 'M'}${xs(new Date(p.ts).getTime()).toFixed(1)},${ys(p.value).toFixed(1)}`).join(' '))
const grid = computed(() => [6, H / 2, H - 6].map(y => ({ y })))
const fmt = v => fmtRate(v, props.unit)

function hover(e) {
  const r = e.currentTarget.getBoundingClientRect()
  const x = (e.clientX - r.left) / r.width * W
  let best = props.points[0], bd = Infinity
  for (const p of props.points) { const d = Math.abs(xs(new Date(p.ts).getTime()) - x); if (d < bd) { bd = d; best = p } }
  const px = xs(new Date(best.ts).getTime())
  hi.value = { p: best, x: px, pct: px / W * 100 }
}
</script>

<style scoped>
.mc { position: relative; padding-right: 4.5rem; }
.mc-svg { width: 100%; height: 7.5rem; display: block; cursor: crosshair; }
.mc-grid { stroke: var(--color-border); stroke-width: 1; vector-effect: non-scaling-stroke; }
.mc-line { fill: none; stroke: var(--color-primary); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.mc-cross { stroke: var(--color-text-muted); stroke-width: 1; stroke-dasharray: 3 3; }
.mc-axis { position: absolute; right: 0; top: 0; bottom: 0; display: flex; flex-direction: column; justify-content: space-between; font-size: .7rem; color: var(--color-text-muted); }
.mc-tip { position: absolute; top: -.25rem; transform: translate(-50%, -100%); background: var(--color-surface-elevated, var(--color-surface)); border: 1px solid var(--color-border); border-radius: var(--radius-sm); padding: .2rem .45rem; font-size: .75rem; display: flex; gap: .4rem; white-space: nowrap; pointer-events: none; color: var(--color-text-primary); }
.mc-tip span { color: var(--color-text-muted); }
</style>
