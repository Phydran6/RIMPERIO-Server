<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Fernwartung: aktive Alarme und Schwellen
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Rechner-Alarme</h1>
        <p class="page-subtitle">
          Offline, Schwellen überschritten, Checks fehlgeschlagen. Wohin gemeldet wird (Mail, Telegram, n8n),
          steht unter <router-link to="/alerts">Benachrichtigungen</router-link>.
        </p>
      </div>
      <label class="rmm-muted"><input v-model="showAll" type="checkbox" @change="load" /> auch erledigte</label>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>

    <section class="card mb-4">
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Schwere</th><th>Rechner</th><th>Meldung</th><th>Seit</th><th></th></tr></thead>
          <tbody>
            <tr v-if="!alerts.length"><td colspan="5" class="rmm-muted">Keine {{ showAll ? '' : 'aktiven ' }}Alarme.</td></tr>
            <tr v-for="a in alerts" :key="a.id" :style="a.active ? '' : 'opacity: .55'">
              <td><span class="badge" :class="STATUS_BADGE[a.severity]">{{ STATUS_LABEL[a.severity] || a.severity }}</span></td>
              <td>{{ a.hostname || '–' }}</td>
              <td>{{ a.message }}</td>
              <td :title="fmtTime(a.created_at)">{{ fmtAgo(a.created_at) }}<div v-if="a.resolved_at" class="rmm-muted">erledigt {{ fmtTime(a.resolved_at) }}</div></td>
              <td><button v-if="a.active && auth.isAdmin" class="btn btn-secondary btn-sm" @click="resolve(a)">Quittieren</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="card">
      <div class="card-header"><h2 class="card-title">Schwellen</h2></div>
      <div class="card-body rmm-form">
        <label>CPU ab (%)<input v-model.number="th.cpu_percent" type="number" min="1" max="100" class="input" :disabled="!auth.isAdmin" /></label>
        <label>Arbeitsspeicher ab (%)<input v-model.number="th.mem_percent" type="number" min="1" max="100" class="input" :disabled="!auth.isAdmin" /></label>
        <label>Platte ab (%)<input v-model.number="th.disk_percent" type="number" min="1" max="100" class="input" :disabled="!auth.isAdmin" /></label>
        <label>Offline nach (s)<input v-model.number="th.offline_after" type="number" min="60" class="input" :disabled="!auth.isAdmin" /></label>
        <div v-if="auth.isAdmin" class="is-wide rmm-actions">
          <button class="btn btn-primary btn-sm" @click="saveThresholds">Speichern</button>
          <span v-if="saved" class="rmm-muted">Gespeichert.</span>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import { fmtAgo, fmtTime, STATUS_BADGE, STATUS_LABEL } from '../utils/rmm'

const auth = useAuthStore()
const alerts = ref([])
const th = ref({ cpu_percent: 90, mem_percent: 90, disk_percent: 90, offline_after: 300 })
const showAll = ref(false)
const saved = ref(false)
const error = ref('')

async function load() {
  try {
    const [a, s] = await Promise.all([auth.api(`/api/rmm/alerts?active=${!showAll.value}`), auth.api('/api/rmm/settings')])
    alerts.value = a
    th.value = s.thresholds
    error.value = ''
  } catch (e) { error.value = e.message }
}

async function resolve(a) {
  try { await auth.api(`/api/rmm/alerts/${a.id}/resolve`, { method: 'POST' }); await load() } catch (e) { error.value = e.message }
}

async function saveThresholds() {
  try {
    th.value = await auth.api('/api/rmm/settings/thresholds', { method: 'PUT', body: { ...th.value } })
    saved.value = true
    setTimeout(() => { saved.value = false }, 3000)
  } catch (e) { error.value = e.message }
}

onMounted(load)
</script>
