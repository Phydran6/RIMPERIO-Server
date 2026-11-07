<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.08.02.14.00.00
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Icon-Set (Inline-SVG, ohne externe Bibliothek).

     Jedes Icon ist eine Liste von SVG-Elementen auf einem 24x24-Raster mit
     durchgehender Strichstaerke. Bewusst selbst gezeichnet statt Icon-Paket:
     spart eine Abhaengigkeit und die Icons erben die Textfarbe.
     ============================================================================== -->

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="strokeWidth"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <template v-for="(shape, index) in shapes" :key="index">
      <path v-if="shape.d" :d="shape.d" />
      <circle v-else-if="shape.circle" :cx="shape.circle[0]" :cy="shape.circle[1]" :r="shape.circle[2]" />
      <rect
        v-else-if="shape.rect"
        :x="shape.rect[0]"
        :y="shape.rect[1]"
        :width="shape.rect[2]"
        :height="shape.rect[3]"
        :rx="shape.rect[4] || 0"
      />
      <line v-else-if="shape.line" :x1="shape.line[0]" :y1="shape.line[1]" :x2="shape.line[2]" :y2="shape.line[3]" />
    </template>
  </svg>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 20 },
  strokeWidth: { type: [Number, String], default: 1.75 },
})

const ICONS = {
  // Navigation
  dashboard: [
    { rect: [3, 3, 7, 7, 1.5] },
    { rect: [14, 3, 7, 7, 1.5] },
    { rect: [3, 14, 7, 7, 1.5] },
    { rect: [14, 14, 7, 7, 1.5] },
  ],
  logs: [
    { d: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z' },
    { d: 'M14 3v5h5' },
    { line: [9, 13, 15, 13] },
    { line: [9, 17, 13, 17] },
  ],
  agents: [
    { rect: [3, 4, 18, 7, 1.5] },
    { rect: [3, 13, 18, 7, 1.5] },
    { line: [7, 7.5, 7.01, 7.5] },
    { line: [7, 16.5, 7.01, 16.5] },
  ],
  webhooks: [
    { d: 'M10 13a5 5 0 0 0 7.07 0l2.83-2.83a5 5 0 0 0-7.07-7.07L11.5 4.5' },
    { d: 'M14 11a5 5 0 0 0-7.07 0L4.1 13.83a5 5 0 0 0 7.07 7.07L12.5 19.5' },
  ],
  users: [
    { d: 'M16 20v-1.5a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4V20' },
    { circle: [9, 7, 3.5] },
    { d: 'M22 20v-1.5a4 4 0 0 0-3-3.87' },
    { d: 'M16 3.63a4 4 0 0 1 0 7.24' },
  ],
  settings: [
    { line: [4, 7, 20, 7] },
    { line: [4, 17, 20, 17] },
    { circle: [10, 7, 2.5] },
    { circle: [16, 17, 2.5] },
  ],
  branding: [
    { d: 'M12 3a9 9 0 0 0 0 18c1.1 0 2-.9 2-2 0-.53-.21-1-.55-1.36-.33-.35-.53-.82-.53-1.34 0-1.1.9-2 2-2H17a4 4 0 0 0 4-4c0-4.42-4.03-7.3-9-7.3z' },
    { circle: [7.5, 11, 1.1] },
    { circle: [11, 7.5, 1.1] },
    { circle: [15.5, 9.5, 1.1] },
  ],
  health: [
    { d: 'M3 12h4l2.5-6 4 12 2.5-6H21' },
  ],
  devices: [
    { rect: [2, 5, 14, 11, 1.5] },
    { line: [7, 20, 13, 20] },
    { line: [10, 16, 10, 20] },
    { rect: [18, 10, 4, 10, 1] },
  ],

  // Aktionen / Zustaende
  menu: [
    { line: [3, 6, 21, 6] },
    { line: [3, 12, 21, 12] },
    { line: [3, 18, 21, 18] },
  ],
  close: [
    { line: [18, 6, 6, 18] },
    { line: [6, 6, 18, 18] },
  ],
  chevronLeft: [{ d: 'M15 18 9 12l6-6' }],
  chevronRight: [{ d: 'M9 18l6-6-6-6' }],
  chevronDown: [{ d: 'M6 9l6 6 6-6' }],
  logout: [
    { d: 'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4' },
    { d: 'M16 17l5-5-5-5' },
    { line: [21, 12, 9, 12] },
  ],
  sun: [
    { circle: [12, 12, 4.2] },
    { line: [12, 2, 12, 4] },
    { line: [12, 20, 12, 22] },
    { line: [4.2, 4.2, 5.6, 5.6] },
    { line: [18.4, 18.4, 19.8, 19.8] },
    { line: [2, 12, 4, 12] },
    { line: [20, 12, 22, 12] },
    { line: [4.2, 19.8, 5.6, 18.4] },
    { line: [18.4, 5.6, 19.8, 4.2] },
  ],
  moon: [{ d: 'M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z' }],
  search: [
    { circle: [11, 11, 7] },
    { line: [16.5, 16.5, 21, 21] },
  ],
  refresh: [
    { d: 'M21 12a9 9 0 1 1-2.64-6.36' },
    { d: 'M21 4v5h-5' },
  ],
  download: [
    { d: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4' },
    { d: 'M7 10l5 5 5-5' },
    { line: [12, 15, 12, 3] },
  ],
  filter: [{ d: 'M3 5h18l-7 8v6l-4 2v-8z' }],
  warning: [
    { d: 'M10.3 3.9 1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z' },
    { line: [12, 9, 12, 13] },
    { line: [12, 17, 12.01, 17] },
  ],
  check: [{ d: 'M20 6 9 17l-5-5' }],
  lock: [
    { rect: [4, 11, 16, 10, 2] },
    { d: 'M8 11V7a4 4 0 0 1 8 0v4' },
  ],
  plus: [
    { line: [12, 5, 12, 19] },
    { line: [5, 12, 19, 12] },
  ],
  trash: [
    { d: 'M3 6h18' },
    { d: 'M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2' },
    { d: 'M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6' },
  ],

  // Bereiche, die mit dem linken Menue dazugekommen sind
  backup: [
    { d: 'M21 8v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8' },
    { rect: [2, 3, 20, 5, 1] },
    { line: [10, 13, 14, 13] },
  ],
  restore: [
    { d: 'M3 12a9 9 0 1 0 2.64-6.36' },
    { d: 'M3 4v5h5' },
  ],
  ai: [
    { rect: [6, 6, 12, 12, 2] },
    { line: [9, 2, 9, 6] },
    { line: [15, 2, 15, 6] },
    { line: [9, 18, 9, 22] },
    { line: [15, 18, 15, 22] },
    { line: [2, 9, 6, 9] },
    { line: [2, 15, 6, 15] },
    { line: [18, 9, 22, 9] },
    { line: [18, 15, 22, 15] },
    { circle: [12, 12, 2] },
  ],
  stacks: [
    { d: 'M12 3 2 8l10 5 10-5z' },
    { d: 'M2 14l10 5 10-5' },
    { d: 'M2 11l10 5 10-5' },
  ],
  mail: [
    { rect: [2, 5, 20, 14, 2] },
    { d: 'M3 7l9 6 9-6' },
  ],
  terminal: [
    { rect: [2, 4, 20, 16, 2] },
    { d: 'M6 9l3 3-3 3' },
    { line: [12, 15, 17, 15] },
  ],
  globe: [
    { circle: [12, 12, 9] },
    { line: [3, 12, 21, 12] },
    { d: 'M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18z' },
  ],
  database: [
    { d: 'M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z' },
    { d: 'M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6' },
    { d: 'M20 12c0 1.7-3.6 3-8 3s-8-1.3-8-3' },
  ],
  network: [
    { rect: [8, 2, 8, 6, 1.5] },
    { rect: [2, 16, 6, 6, 1.5] },
    { rect: [16, 16, 6, 6, 1.5] },
    { d: 'M12 8v4' },
    { d: 'M5 16v-2h14v2' },
  ],
  clock: [
    { circle: [12, 12, 9] },
    { d: 'M12 7v5l3 2' },
  ],
  archive: [
    { rect: [3, 4, 18, 4, 1] },
    { d: 'M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8' },
    { line: [10, 12, 14, 12] },
  ],
  key: [
    { circle: [8, 15, 4] },
    { d: 'M10.8 12.2 21 2' },
    { d: 'M18 5l2 2' },
    { d: 'M15 8l2 2' },
  ],
  power: [
    { d: 'M12 3v9' },
    { d: 'M18.4 6.6a9 9 0 1 1-12.8 0' },
  ],

  // Mit der neuen Menuestruktur dazugekommen
  shield: [
    { d: 'M12 22s8-3.5 8-10V5.5L12 2 4 5.5V12c0 6.5 8 10 8 10z' },
    { d: 'M9 12l2 2 4-4' },
  ],
  help: [
    { circle: [12, 12, 9] },
    { d: 'M9.2 9.2a3 3 0 0 1 5.8 1c0 2-3 2.5-3 4' },
    { line: [12, 17.5, 12.01, 17.5] },
  ],
  bell: [
    { d: 'M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9' },
    { d: 'M13.73 21a2 2 0 0 1-3.46 0' },
  ],
  router: [
    { rect: [2, 14, 20, 7, 2] },
    { line: [6, 17.5, 6.01, 17.5] },
    { line: [10, 17.5, 10.01, 17.5] },
    { d: 'M12 14V9' },
    { d: 'M8.5 6.5a5 5 0 0 1 7 0' },
    { d: 'M6 4a8.5 8.5 0 0 1 12 0' },
  ],
  mobile: [
    { rect: [6, 2, 12, 20, 2] },
    { line: [10.5, 18.5, 13.5, 18.5] },
  ],
  container: [
    { rect: [3, 7, 18, 12, 1.5] },
    { line: [8, 7, 8, 19] },
    { line: [12, 7, 12, 19] },
    { line: [16, 7, 16, 19] },
  ],
  // Alias: 'palette' liest sich an manchen Stellen natuerlicher als 'branding'.
  palette: [
    { d: 'M12 3a9 9 0 0 0 0 18c1.1 0 2-.9 2-2 0-.53-.21-1-.55-1.36-.33-.35-.53-.82-.53-1.34 0-1.1.9-2 2-2H17a4 4 0 0 0 4-4c0-4.42-4.03-7.3-9-7.3z' },
    { circle: [7.5, 11, 1.1] },
    { circle: [11, 7.5, 1.1] },
    { circle: [15.5, 9.5, 1.1] },
  ],
}

const shapes = computed(() => ICONS[props.name] || [])
</script>
