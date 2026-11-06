<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Systemtagebuch: was der Server selbst getan hat.

     Der Anspruch dahinter ist hart formuliert und genau so gemeint: es darf auf
     diesem System nichts passieren, das hinterher niemand mehr nachvollziehen
     kann. Ein "ich weiss nicht, warum das passiert ist" soll es nicht geben.

     Deshalb steht hier jeder Eingriff: Updates, Sicherungen, Aufraeumlaeufe,
     Container-Aktionen, Terminal-Sitzungen, Anmeldungen und abgewiesene
     Anmeldungen, geaenderte Einstellungen, erzeugte und zurueckgezogene
     Schluessel.

     Das Tagebuch liegt in einer EIGENEN Tabelle - ein Aufraeumlauf, der die
     Logs kuerzt, nimmt die eigene Spur also nicht mit.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">Systemtagebuch</h2>
        <p class="page-subtitle">
          Was auf diesem Server passiert ist — wer, wann, woran und mit welchem Ausgang.
        </p>
      </div>
      <div class="flex items-center gap-2">
        <span class="badge" :class="live ? 'badge-success' : 'badge-neutral'">
          <span class="status-dot" :class="live ? 'status-dot-online' : 'status-dot-offline'" />
          {{ live ? 'läuft mit' : 'nicht verbunden' }}
        </span>
        <button class="btn btn-secondary btn-sm" :disabled="loading" @click="load()">
          <AppIcon name="refresh" :size="16" />
          Neu laden
        </button>
      </div>
    </div>

    <!-- Kurzüberblick -->
    <div v-if="summary" class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
      <div class="stat-card">
        <span class="stat-label">Einträge (24 h)</span>
        <span class="stat-value">{{ summary.total }}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Davon Probleme</span>
        <span
          class="stat-value"
          :style="{ color: summary.problems ? 'var(--color-danger)' : 'var(--color-success)' }"
        >{{ summary.problems }}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Bereiche aktiv</span>
        <span class="stat-value">{{ Object.keys(summary.by_category || {}).length }}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Letzter Fehlschlag</span>
        <span class="text-sm mt-1 block" style="color: var(--color-text-secondary)">
          {{ summary.last_failure ? formatTime(summary.last_failure.at) : 'keiner verzeichnet' }}
        </span>
      </div>
    </div>

    <!-- Filter -->
    <div class="card mb-4">
      <div class="card-body grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-3">
        <div>
          <label class="label">Bereich</label>
          <select v-model="filters.category" class="select" @change="load(1)">
            <option value="">Alle</option>
            <option v-for="entry in categories" :key="entry.key" :value="entry.key">
              {{ entry.label }}
            </option>
          </select>
        </div>
        <div>
          <label class="label">Ab Stufe</label>
          <select v-model="filters.level" class="select" @change="load(1)">
            <option value="">Alle</option>
            <option value="notice">Notice und wichtiger</option>
            <option value="warning">Warnung und wichtiger</option>
            <option value="error">Nur Fehler</option>
          </select>
        </div>
        <div>
          <label class="label">Zeitraum</label>
          <select v-model.number="filters.since_hours" class="select" @change="load(1)">
            <option :value="0">Alles</option>
            <option :value="24">Letzte 24 Stunden</option>
            <option :value="168">Letzte 7 Tage</option>
            <option :value="720">Letzte 30 Tage</option>
          </select>
        </div>
        <div>
          <label class="label">Suche</label>
          <input
            v-model="filters.search"
            class="input"
            placeholder="Meldung, Vorgang oder Ziel"
            @keyup.enter="load(1)"
          >
        </div>
        <div class="flex items-end gap-2">
          <label class="flex items-center gap-2 text-sm" style="color: var(--color-text-secondary)">
            <input v-model="filters.only_failures" type="checkbox" @change="load(1)">
            nur Fehlschläge
          </label>
        </div>
      </div>
    </div>

    <div v-if="error" class="card mb-4" style="border-color: var(--color-danger)">
      <div class="card-body text-sm" style="color: var(--color-danger)">{{ error }}</div>
    </div>

    <!-- Die Einträge -->
    <div class="card">
      <div class="card-body p-0">
        <div v-if="!items.length && !loading" class="empty-state">
          <p class="empty-state-title">Zu dieser Auswahl gibt es keine Einträge.</p>
        </div>

        <table v-else class="table">
          <thead>
            <tr>
              <th style="width: 9rem">Wann</th>
              <th style="width: 8rem">Bereich</th>
              <th>Was</th>
              <th style="width: 10rem">Wer</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="entry in items" :key="entry.id" @click="toggle(entry.id)">
              <td class="whitespace-nowrap text-xs" style="color: var(--color-text-muted)">
                {{ formatTime(entry.at) }}
              </td>
              <td>
                <span class="badge" :class="levelBadge(entry.level)">{{ entry.category_label }}</span>
              </td>
              <td>
                <p style="color: var(--color-text-primary)">{{ entry.message }}</p>
                <p class="text-xs mt-0.5" style="color: var(--color-text-muted)">
                  {{ entry.event }}<template v-if="entry.target"> · {{ entry.target }}</template>
                  <template v-if="entry.duration_ms"> · {{ (entry.duration_ms / 1000).toFixed(1) }} s</template>
                  <template v-if="!entry.ok"> · fehlgeschlagen</template>
                </p>
                <pre v-if="expanded === entry.id && hasDetail(entry)" class="detail">{{ pretty(entry.detail) }}</pre>
              </td>
              <td class="text-xs" style="color: var(--color-text-secondary)">
                {{ entry.actor || 'System' }}
                <span v-if="entry.source_ip" class="block" style="color: var(--color-text-muted)">
                  {{ entry.source_ip }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Blättern -->
    <div v-if="total > pageSize" class="flex items-center justify-between mt-4">
      <span class="text-sm" style="color: var(--color-text-muted)">
        {{ (page - 1) * pageSize + 1 }}–{{ Math.min(page * pageSize, total) }} von {{ total }}
      </span>
      <div class="flex gap-2">
        <button class="btn btn-secondary btn-sm" :disabled="page <= 1" @click="load(page - 1)">
          Zurück
        </button>
        <button
          class="btn btn-secondary btn-sm"
          :disabled="page * pageSize >= total"
          @click="load(page + 1)"
        >
          Weiter
        </button>
      </div>
    </div>

    <!-- Aufräumen -->
    <div class="card mt-6">
      <div class="card-header"><p class="card-title">Aufbewahrung des Tagebuchs</p></div>
      <div class="card-body">
        <p class="text-sm mb-3" style="color: var(--color-text-secondary)">
          Die Einträge sind winzig und dürfen ruhig lange liegen bleiben — die Frage
          „was war da vor drei Monaten?" kommt spät. Standard sind 365 Tage.
          <strong>Fehler und Kritisches bleiben darüber hinaus stehen:</strong>
          genau die will man haben, wenn jemand ein halbes Jahr später fragt.
        </p>
        <button class="btn btn-secondary btn-sm" :disabled="pruning" @click="prune">
          <AppIcon name="trash" :size="14" />
          {{ pruning ? 'Räume auf…' : 'Einträge älter als 365 Tage entfernen' }}
        </button>
        <p v-if="pruneMessage" class="text-sm mt-2" style="color: var(--color-success)">{{ pruneMessage }}</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onBeforeUnmount } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'

const auth = useAuthStore()

const items = ref([])
const categories = ref([])
const summary = ref(null)
const total = ref(0)
const page = ref(1)
const pageSize = ref(100)
const loading = ref(false)
const error = ref('')
const expanded = ref(null)
const pruning = ref(false)
const pruneMessage = ref('')
const live = ref(false)

const filters = reactive({
  category: '',
  level: '',
  search: '',
  since_hours: 24,
  only_failures: false,
})

let stream = null

function levelBadge(level) {
  if (level === 'critical' || level === 'error') return 'badge-danger'
  if (level === 'warning') return 'badge-warning'
  if (level === 'notice') return 'badge-primary'
  return 'badge-neutral'
}

function formatTime(value) {
  if (!value) return '–'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('de-DE')
}

function hasDetail(entry) {
  return entry.detail && Object.keys(entry.detail).length > 0
}

function pretty(detail) {
  try {
    return JSON.stringify(detail, null, 2)
  } catch {
    return String(detail)
  }
}

function toggle(id) {
  expanded.value = expanded.value === id ? null : id
}

async function load(targetPage = page.value) {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({
      page: String(targetPage),
      page_size: String(pageSize.value),
    })
    if (filters.category) params.set('category', filters.category)
    if (filters.level) params.set('level', filters.level)
    if (filters.search) params.set('search', filters.search)
    if (filters.only_failures) params.set('only_failures', 'true')
    if (filters.since_hours) params.set('since_hours', String(filters.since_hours))

    const data = await auth.api(`/api/journal?${params}`)
    items.value = data.items || []
    total.value = data.total || 0
    page.value = data.page || 1
    categories.value = data.categories || []
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function loadSummary() {
  try {
    summary.value = await auth.api('/api/journal/summary?hours=24')
  } catch {
    // Der Überblick ist Beiwerk - ohne ihn funktioniert die Liste weiter.
  }
}

async function prune() {
  pruning.value = true
  pruneMessage.value = ''
  try {
    const result = await auth.api('/api/journal/prune?days=365', { method: 'POST' })
    pruneMessage.value = result.message
    await load(1)
  } catch (e) {
    error.value = e.message
  } finally {
    pruning.value = false
  }
}

/**
 * Neue Einträge in dem Moment, in dem sie entstehen.
 *
 * Wozu: Während ein Update läuft oder der Plattenwächter aufräumt, soll man
 * zusehen können statt hinterher nachzulesen. Neue Zeilen kommen oben dazu -
 * aber nur auf der ersten Seite, sonst würde die Liste unter dem Finger
 * weglaufen, während jemand auf Seite 4 liest.
 */
function listen() {
  if (typeof EventSource === 'undefined') return
  stream = new EventSource(`/api/journal/stream?token=${encodeURIComponent(auth.token)}`)

  stream.addEventListener('system.event', (event) => {
    live.value = true
    if (page.value !== 1) return
    try {
      const entry = JSON.parse(event.data)
      items.value = [{ id: `live-${Date.now()}-${Math.random()}`, category_label: entry.category,
                       ...entry, detail: {} }, ...items.value].slice(0, pageSize.value)
      total.value += 1
    } catch {
      // Eine unlesbare Zeile ist kein Grund, den Strom aufzugeben.
    }
  })

  stream.onopen = () => { live.value = true }
  stream.onerror = () => { live.value = false }
}

onMounted(() => {
  load()
  loadSummary()
  listen()
})

onBeforeUnmount(() => {
  stream?.close()
})
</script>

<style scoped>
.detail {
  margin-top: 0.5rem;
  background-color: var(--color-surface-elevated);
  color: var(--color-text-secondary);
  border-radius: 6px;
  padding: 0.5rem 0.75rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.7rem;
  line-height: 1.5;
  max-height: 16rem;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
