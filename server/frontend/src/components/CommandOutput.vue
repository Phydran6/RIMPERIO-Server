<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Ausgabe eines Agent-Auftrags lesbar darstellen: Patch-
                   Suche als Tabelle, Installation als Zusammenfassung, sonstiges
                   JSON eingerückt, Text wie in einer Konsole
     ============================================================================== -->
<template>
  <div class="cmd-out">
    <!-- Patch-Suche: Liste der Updates -->
    <template v-if="scan">
      <p class="cmd-out-head">
        {{ scan.updates.length }} Update(s) gefunden<span v-if="scan.manager"> · {{ scan.manager }}</span>
        <span v-if="scan.reboot_pending" class="badge badge-warning">Neustart ausstehend</span>
      </p>
      <div v-if="scan.updates.length" class="rmm-table-wrap">
        <table class="table cmd-out-table">
          <thead><tr><th>Update</th><th>Version</th><th>Kategorie</th><th></th></tr></thead>
          <tbody>
            <tr v-for="(u, i) in scan.updates" :key="u.id || i">
              <td>{{ u.package || u.title || u.id }}<div v-if="u.kb" class="rmm-muted">{{ u.kb }}</div></td>
              <td class="rmm-code">
                <template v-if="u.current || u.available">{{ u.current || '?' }} → {{ u.available || '?' }}</template>
                <template v-else>{{ u.size || '' }}</template>
              </td>
              <td>{{ u.category || u.source || '' }}</td>
              <td class="cmd-out-flags">
                <span v-if="u.security" class="badge badge-primary">Sicherheit</span>
                <span v-if="u.reboot" class="badge badge-neutral">Neustart</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- Installation: Zusammenfassung -->
    <template v-else-if="apply">
      <p class="cmd-out-head">
        {{ apply.installed.length }} installiert<span v-if="apply.failed.length">, {{ apply.failed.length }} fehlgeschlagen</span>
        <span v-if="apply.reboot_scheduled" class="badge badge-warning">Neustart geplant</span>
        <span v-else-if="apply.reboot_required" class="badge badge-warning">Neustart nötig</span>
      </p>
      <ul v-if="apply.installed.length" class="cmd-out-list"><li v-for="x in apply.installed" :key="x">{{ x }}</li></ul>
      <ul v-if="apply.failed.length" class="cmd-out-list is-bad">
        <li v-for="(f, i) in apply.failed" :key="i">{{ f.id || f.name || f }}<span v-if="f.error"> — {{ f.error }}</span></li>
      </ul>
      <p v-if="apply.message" class="rmm-muted">{{ apply.message }}</p>
    </template>

    <pre v-else-if="pretty" class="cmd-out-pre">{{ pretty }}</pre>
    <pre v-else-if="stdout" class="cmd-out-pre">{{ stdout }}</pre>

    <pre v-if="stderr" class="cmd-out-pre is-bad">{{ stderr }}</pre>
    <p v-if="!stdout && !stderr" class="rmm-muted">{{ emptyText }}</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { parseJSON } from '../utils/rmm'

const props = defineProps({
  kind: { type: String, default: 'shell' },
  stdout: { type: String, default: '' },
  stderr: { type: String, default: '' },
  emptyText: { type: String, default: 'Keine Ausgabe.' },
})

const data = computed(() => parseJSON(props.stdout))
const scan = computed(() => {
  const d = data.value
  return d && Array.isArray(d.updates) && (props.kind === 'patch_scan' || 'manager' in d) ? d : null
})
const apply = computed(() => {
  const d = data.value
  if (!d || props.kind !== 'patch_apply' || (!('installed' in d) && !('failed' in d))) return null
  return { ...d, installed: d.installed || [], failed: d.failed || [] }
})
const pretty = computed(() => (data.value ? JSON.stringify(data.value, null, 2) : ''))
</script>

<style scoped>
.cmd-out { display: flex; flex-direction: column; gap: .5rem; margin-top: .5rem; }
.cmd-out-head { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; margin: 0; font-weight: 600; font-size: .875rem; }
.cmd-out-table { font-size: .8125rem; }
.cmd-out-flags { display: flex; gap: .25rem; flex-wrap: wrap; }
.cmd-out-list { margin: 0; padding-left: 1.2rem; font-size: .8125rem; }
.cmd-out-list.is-bad { color: var(--color-danger); }
.cmd-out-pre {
  margin: 0; padding: .75rem 1rem; border-radius: var(--radius-sm);
  background: var(--color-bg); border: 1px solid var(--color-border); color: var(--color-text-secondary);
  font-family: ui-monospace, Consolas, monospace; font-size: .8rem; font-weight: 400; line-height: 1.55;
  white-space: pre-wrap; overflow-wrap: anywhere; max-height: 28rem; overflow: auto;
}
.cmd-out-pre.is-bad { color: var(--color-danger); }
</style>
