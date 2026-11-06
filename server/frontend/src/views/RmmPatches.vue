<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Fernwartung: Patch-Stand aller Rechner
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Patches</h1>
        <p class="page-subtitle">Ausstehende Updates je Rechner (Windows Update bzw. Paketverwaltung unter Linux).</p>
      </div>
      <button v-if="auth.isAdmin && agents.length" class="btn btn-secondary btn-sm" :disabled="busy" @click="scanAll">Alle nach Updates suchen</button>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>

    <section class="card">
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Rechner</th><th>Standort</th><th>Ausstehend</th><th>Geprüft</th><th>Zuletzt installiert</th><th></th></tr></thead>
          <tbody>
            <tr v-if="!rows.length"><td colspan="6" class="rmm-muted">Noch kein Patch-Stand. „Alle nach Updates suchen“ startet die erste Prüfung.</td></tr>
            <tr v-for="r in rows" :key="r.agent_uid">
              <td><router-link :to="`/rmm/agents/${r.agent_uid}`"><strong>{{ r.hostname }}</strong></router-link></td>
              <td>{{ r.site }}</td>
              <td><span class="badge" :class="r.count ? 'badge-warning' : 'badge-success'">{{ r.count }}</span></td>
              <td>{{ fmtTime(r.scanned_at) }}</td><td>{{ fmtTime(r.last_apply_at) }}</td>
              <td class="rmm-actions" style="justify-content: flex-end">
                <template v-if="auth.isAdmin">
                  <button class="btn btn-secondary btn-sm" :disabled="busy" @click="action(r, 'scan')">Suchen</button>
                  <button class="btn btn-primary btn-sm" :disabled="busy || !r.count" @click="action(r, 'apply')">Installieren</button>
                </template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import { fmtTime } from '../utils/rmm'

const auth = useAuthStore()
const reports = ref([])
const agents = ref([])
const error = ref('')
const notice = ref('')
const busy = ref(false)

// Rechner ohne Bericht trotzdem zeigen, damit man sie anstoßen kann.
const rows = computed(() => {
  const byUid = Object.fromEntries(reports.value.map(r => [r.agent_uid, r]))
  return agents.value.map(a => byUid[a.agent_uid] ||
    { agent_uid: a.agent_uid, hostname: a.hostname, site: a.site, count: 0, scanned_at: null, last_apply_at: null })
    .sort((a, b) => b.count - a.count || a.hostname.localeCompare(b.hostname))
})

async function load() {
  try {
    [reports.value, agents.value] = await Promise.all([auth.api('/api/rmm/patches'), auth.api('/api/rmm/agents')])
    error.value = ''
  } catch (e) { error.value = e.message }
}

async function action(r, what) {
  if (what === 'apply' && !confirm(`${r.count} Updates auf ${r.hostname} installieren? Ein Neustart kann nötig sein.`)) return
  busy.value = true
  try {
    await auth.api(`/api/rmm/agents/${r.agent_uid}/patches/${what}`, { method: 'POST' })
    notice.value = `${r.hostname}: Auftrag eingereiht.`
  } catch (e) { error.value = e.message } finally { busy.value = false }
}

async function scanAll() {
  busy.value = true
  try {
    await Promise.all(agents.value.map(a => auth.api(`/api/rmm/agents/${a.agent_uid}/patches/scan`, { method: 'POST' })))
    notice.value = `Suche auf ${agents.value.length} Rechnern eingereiht. Ergebnisse kommen mit dem nächsten Check-in.`
  } catch (e) { error.value = e.message } finally { busy.value = false }
}

onMounted(load)
</script>
