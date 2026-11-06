<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.08.14.12.00.00
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Systemcheck: prueft das ganze System und erklaert Funde.
     ============================================================================== -->

<template>
  <div class="card">
    <div class="card-header">
      <div>
        <span class="card-title">Systemcheck</span>
        <p class="text-xs mt-0.5" style="color: var(--color-text-muted)">
          Prüft Datenbank, Dienste, Sicherheit, Betrieb und Netz in einem Durchlauf.
        </p>
      </div>
      <button class="btn btn-primary btn-sm" :disabled="running" @click="run">
        <AppIcon :name="running ? 'refresh' : 'check'" :size="16" :class="running ? 'spin' : ''" />
        {{ running ? 'Prüfe…' : 'Systemcheck starten' }}
      </button>
    </div>

    <div class="card-body">
      <!-- Fehler beim Aufruf selbst -->
      <p v-if="error" class="note note-fail">{{ error }}</p>

      <!-- Noch nie gelaufen -->
      <div v-if="!report && !running && !error" class="empty-state">
        <p class="empty-state-title">Noch kein Systemcheck gelaufen</p>
        <p class="text-sm" style="color: var(--color-text-muted)">
          Der Durchlauf dauert einige Sekunden und fragt dabei alle Dienste ab.
        </p>
      </div>

      <div v-if="running && !report" class="empty-state">
        <p class="empty-state-title">Prüfung läuft…</p>
        <p class="text-sm" style="color: var(--color-text-muted)">
          Dienste werden angesprochen und die Datenbank befragt.
        </p>
      </div>

      <!-- Ergebnis -->
      <template v-if="report">
        <div class="summary" :style="{ borderColor: colorOf(report.overall), backgroundColor: softOf(report.overall) }">
          <span class="stat-icon" :style="{ backgroundColor: 'transparent', color: colorOf(report.overall) }">
            <AppIcon :name="iconOf(report.overall)" :size="22" />
          </span>
          <div class="min-w-0">
            <p class="font-semibold" :style="{ color: colorOf(report.overall) }">{{ report.headline }}</p>
            <p class="text-xs" style="color: var(--color-text-muted)">
              {{ counts }} · geprüft am {{ formatTime(report.generated_at) }} ·
              Dauer {{ (report.duration_ms / 1000).toFixed(1) }} s
            </p>
          </div>
          <span class="flex-1" />
          <button
            v-if="hasHidden"
            class="btn btn-secondary btn-sm"
            @click="showAll = !showAll"
          >
            {{ showAll ? 'Nur Auffälligkeiten' : 'Alle anzeigen' }}
          </button>
        </div>

        <!-- Befunde nach Bereich -->
        <div v-for="category in visibleCategories" :key="category" class="category">
          <p class="section-title mb-2">{{ category }}</p>
          <ul class="check-list">
            <li
              v-for="check in checksOf(category)"
              :key="check.id"
              class="check-row"
              :class="{ 'is-open': opened.has(check.id) }"
            >
              <button class="check-head" @click="toggle(check.id)">
                <span class="dot" :style="{ backgroundColor: colorOf(check.status) }" />
                <span class="check-title">{{ check.title }}</span>
                <span class="check-summary" :style="{ color: colorOf(check.status) }">{{ check.summary }}</span>
                <AppIcon
                  name="chevronDown"
                  :size="15"
                  class="chevron"
                  :class="opened.has(check.id) ? 'is-open' : ''"
                />
              </button>

              <div v-if="opened.has(check.id)" class="check-body">
                <p v-if="check.detail" class="check-detail">{{ check.detail }}</p>
                <p v-if="check.hint" class="check-hint">
                  <AppIcon name="warning" :size="14" class="shrink-0" />
                  <span>{{ check.hint }}</span>
                </p>
                <p v-if="!check.detail && !check.hint" class="check-detail">
                  Keine weiteren Angaben.
                </p>
                <p class="check-meta">{{ statusLabel(check.status) }} · {{ check.duration_ms }} ms</p>
              </div>
            </li>
          </ul>
        </div>

        <p v-if="!visibleCategories.length" class="note note-ok">
          Alles unauffällig – nichts, was Aufmerksamkeit braucht.
        </p>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from './AppIcon.vue'

const auth = useAuthStore()

const report = ref(null)
const running = ref(false)
const error = ref('')
const showAll = ref(false)
const opened = ref(new Set())

const STATUS = {
  ok:   { color: 'var(--color-success)', soft: 'var(--success-soft)', icon: 'check',   label: 'In Ordnung' },
  warn: { color: 'var(--color-warning)', soft: 'var(--warning-soft)', icon: 'warning', label: 'Hinweis' },
  fail: { color: 'var(--color-danger)',  soft: 'var(--danger-soft)',  icon: 'warning', label: 'Problem' },
  info: { color: 'var(--color-primary)', soft: 'var(--primary-soft)', icon: 'logs',    label: 'Auskunft' },
  skip: { color: 'var(--color-text-muted)', soft: 'var(--hover-surface)', icon: 'filter', label: 'Nicht geprüft' },
}

function colorOf(status) { return (STATUS[status] || STATUS.info).color }
function softOf(status) { return (STATUS[status] || STATUS.info).soft }
function iconOf(status) { return (STATUS[status] || STATUS.info).icon }
function statusLabel(status) { return (STATUS[status] || STATUS.info).label }

const counts = computed(() => {
  if (!report.value) return ''
  const c = report.value.counts || {}
  const parts = []
  if (c.fail) parts.push(`${c.fail} Problem(e)`)
  if (c.warn) parts.push(`${c.warn} Hinweis(e)`)
  if (c.ok) parts.push(`${c.ok} in Ordnung`)
  if (c.skip) parts.push(`${c.skip} nicht geprüft`)
  return parts.join(' · ')
})

// Standardmäßig nur zeigen, was Aufmerksamkeit braucht.
const relevant = computed(() => {
  if (!report.value) return []
  const checks = report.value.checks || []
  return showAll.value ? checks : checks.filter(c => c.status === 'fail' || c.status === 'warn')
})

const hasHidden = computed(() => {
  if (!report.value) return false
  return (report.value.checks || []).length !== relevant.value.length || showAll.value
})

const visibleCategories = computed(() => {
  const used = new Set(relevant.value.map(c => c.category))
  return (report.value?.categories || []).filter(category => used.has(category))
})

function checksOf(category) {
  return relevant.value.filter(c => c.category === category)
}

function toggle(id) {
  const next = new Set(opened.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  opened.value = next
}

function formatTime(value) {
  if (!value) return '–'
  const date = new Date(value)
  return isNaN(date.getTime()) ? value : date.toLocaleString('de-DE')
}

/** Auffälligkeiten gleich aufgeklappt zeigen - danach sucht man ja. */
function openFindings() {
  const next = new Set()
  for (const check of report.value?.checks || []) {
    if (check.status === 'fail' || check.status === 'warn') next.add(check.id)
  }
  opened.value = next
}

onMounted(async () => {
  // Letztes Ergebnis zeigen, ohne neu zu messen.
  try {
    const data = await auth.api('/api/diagnostics/last')
    if (data?.available) {
      report.value = data.report
      openFindings()
    }
  } catch {
    // Kein Ergebnis vorhanden oder keine Berechtigung - dann bleibt der Knopf.
  }
})

async function run() {
  running.value = true
  error.value = ''
  try {
    const data = await auth.api('/api/diagnostics/run', { method: 'POST' })
    report.value = data.report
    showAll.value = false
    openFindings()
  } catch (e) {
    error.value = e.message || 'Der Systemcheck konnte nicht ausgeführt werden.'
  } finally {
    running.value = false
  }
}
</script>

<style scoped>
.summary {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.875rem 1rem;
  border: 1px solid;
  border-radius: var(--radius);
  margin-bottom: 1.25rem;
}

.category + .category {
  margin-top: 1.25rem;
}

.check-list {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.check-row {
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  overflow: hidden;
}

.check-head {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  width: 100%;
  padding: 0.625rem 0.75rem;
  text-align: left;
  cursor: pointer;
}

.check-head:hover {
  background-color: var(--hover-surface);
}

.dot {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: var(--radius-full);
  flex-shrink: 0;
}

.check-title {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--color-text-primary);
  flex-shrink: 0;
}

.check-summary {
  flex: 1;
  min-width: 0;
  font-size: 0.8125rem;
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chevron {
  color: var(--color-text-muted);
  flex-shrink: 0;
  transition: transform var(--duration) var(--ease);
}

.chevron.is-open {
  transform: rotate(180deg);
}

.check-body {
  padding: 0 0.75rem 0.75rem 1.75rem;
  border-top: 1px solid var(--color-border);
  padding-top: 0.625rem;
}

.check-detail {
  font-size: 0.8125rem;
  color: var(--color-text-secondary);
  line-height: 1.5;
}

.check-hint {
  display: flex;
  align-items: flex-start;
  gap: 0.375rem;
  margin-top: 0.5rem;
  padding: 0.5rem 0.625rem;
  border-radius: var(--radius);
  background-color: var(--warning-soft);
  color: var(--color-warning);
  font-size: 0.8125rem;
  line-height: 1.45;
}

.check-meta {
  margin-top: 0.5rem;
  font-size: 0.6875rem;
  color: var(--color-text-muted);
}

.note {
  padding: 0.625rem 0.75rem;
  border-radius: var(--radius);
  font-size: 0.8125rem;
  margin-bottom: 0.75rem;
}

.note-fail {
  background-color: var(--danger-soft);
  color: var(--color-danger);
}

.note-ok {
  background-color: var(--success-soft);
  color: var(--color-success);
  margin-bottom: 0;
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
