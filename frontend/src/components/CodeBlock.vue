<!--
  Datei:        CodeBlock.vue
  Projekt:      RIMPERIO — Frontend
  Autor:        Phydran6
  Version:      2026.10.31.15.40.00
  Beschreibung: Code-/Befehlsblock mit Kopier-Schaltfläche.
-->
<template>
  <div class="relative">
    <pre class="bg-ink-950 border border-white/10 rounded-lg p-3 pr-24 text-xs font-mono text-slate-300 whitespace-pre-wrap break-all">{{ text }}</pre>
    <button class="absolute top-2 right-2 btn-ghost !py-1 text-xs" @click="copy">{{ copied ? 'Kopiert ✓' : 'Kopieren' }}</button>
  </div>
</template>

<script setup>
import { ref } from 'vue'
const props = defineProps({ text: { type: String, default: '' } })
const copied = ref(false)
async function copy() {
  try {
    await navigator.clipboard.writeText(props.text)
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  } catch { /* Zwischenablage nicht verfügbar */ }
}
</script>
