<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.08.02.14.00.00
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Dashboard: Kennzahlen, Verteilung nach Schweregrad,
                   aktivste Quellen und die neuesten Logs. Alle Kacheln fuehren
                   in die passend gefilterte Log-Ansicht.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">Übersicht</h2>
        <p class="page-subtitle">Stand: {{ lastUpdated }}</p>
      </div>
      <button class="btn btn-secondary btn-sm" :disabled="loading" @click="load(true)">
        <AppIcon name="refresh" :size="16" />
        Aktualisieren
      </button>
    </div>

    <!-- ================================================================
         KENNZAHLEN
         ================================================================ -->
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-5" :class="statCards.length > 4 ? 'xl:grid-cols-3' : 'xl:grid-cols-4'">
      <component
        :is="card.to ? 'router-link' : 'div'"
        v-for="card in statCards"
        :key="card.label"
        :to="card.to"
        class="stat-card"
        :class="card.to ? 'card-hover' : ''"
        :title="card.hint"
      >
        <div class="flex items-start justify-between gap-3">
          <span class="stat-label">{{ card.label }}</span>
          <span class="stat-icon" :style="{ backgroundColor: card.tint, color: card.color }">
            <AppIcon :name="card.icon" :size="18" />
          </span>
        </div>
        <span v-if="loading && !stats" class="skeleton h-8 w-24" />
        <span v-else class="stat-value" :style="card.valueColor ? { color: card.valueColor } : null">
          {{ card.value }}
        </span>
        <span class="text-xs" style="color: var(--color-text-muted)">{{ card.hint }}</span>
      </component>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-5">
      <!-- ==============================================================
           VERTEILUNG NACH SCHWEREGRAD
           ============================================================== -->
      <div class="card lg:col-span-1">
        <div class="card-header">
          <span class="card-title">Nach Schweregrad</span>
          <router-link to="/logs" class="link text-xs">Alle Logs</router-link>
        </div>
        <div class="card-body space-y-3">
          <p v-if="!levelBars.length" class="text-sm" style="color: var(--color-text-muted)">
            Noch keine Daten.
          </p>
          <router-link
            v-for="bar in levelBars"
            :key="bar.level"
            :to="{ path: '/logs', query: { level: bar.level } }"
            class="block group"
            :title="`Nur ${bar.level} anzeigen`"
          >
            <div class="flex items-center justify-between text-xs mb-1">
              <span class="font-medium" style="color: var(--color-text-secondary)">{{ bar.label }}</span>
              <span class="tabular" style="color: var(--color-text-muted)">{{ bar.count.toLocaleString('de-DE') }}</span>
            </div>
            <div class="bar-track">
              <div class="bar-fill" :style="{ width: bar.percent + '%', backgroundColor: bar.color }" />
            </div>
          </router-link>
        </div>
      </div>

      <!-- ==============================================================
           AKTIVSTE QUELLEN
           ============================================================== -->
      <div class="card lg:col-span-2">
        <div class="card-header">
          <span class="card-title">Aktivste Quellen</span>
          <span class="text-xs" style="color: var(--color-text-muted)">Top {{ topSources.length }}</span>
        </div>
        <div class="card-body">
          <p v-if="!topSources.length" class="text-sm" style="color: var(--color-text-muted)">
            Noch keine Daten.
          </p>
          <div v-else class="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3">
            <router-link
              v-for="src in topSources"
              :key="src.name"
              :to="{ path: '/logs', query: { source: src.name } }"
              class="block"
              :title="`Logs von ${src.name} anzeigen`"
            >
              <div class="flex items-center justify-between text-xs mb-1">
                <span class="font-mono truncate" style="color: var(--color-text-secondary)">{{ src.name }}</span>
                <span class="tabular ml-2 shrink-0" style="color: var(--color-text-muted)">
                  {{ src.count.toLocaleString('de-DE') }}
                </span>
              </div>
              <div class="bar-track">
                <div class="bar-fill" :style="{ width: src.percent + '%', backgroundColor: 'var(--color-primary)' }" />
              </div>
            </router-link>
          </div>
        </div>
      </div>
    </div>

    <!-- ================================================================
         NEUESTE LOGS
         ================================================================ -->
    <div class="card overflow-hidden">
      <div class="card-header">
        <span class="card-title">Neueste Logs</span>
        <router-link to="/logs" class="link text-xs">Alle anzeigen →</router-link>
      </div>

      <div class="overflow-x-auto">
        <table class="table">
          <thead>
            <tr>
              <th>Zeit</th>
              <th>Gerät</th>
              <th>Level</th>
              <th>Quelle</th>
              <th>Nachricht</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="loading && !logs.length">
              <td colspan="5">
                <div class="space-y-2 py-2">
                  <div v-for="n in 5" :key="n" class="skeleton h-4 w-full" />
                </div>
              </td>
            </tr>
            <tr v-for="log in logs" :key="log.id">
              <td class="whitespace-nowrap tabular" style="color: var(--color-text-muted)">
                {{ formatTime(log.timestamp) }}
              </td>
              <td>
                <router-link
                  v-if="log.hostname"
                  :to="{ name: 'DeviceLogs', params: { hostname: log.hostname } }"
                  class="link"
                  title="Logs dieses Geräts anzeigen"
                >{{ log.hostname }}</router-link>
                <span v-else>–</span>
              </td>
              <td><span class="badge" :class="levelBadgeClass(log.level)">{{ log.level || 'unbekannt' }}</span></td>
              <td class="font-mono text-xs">{{ log.source || '–' }}</td>
              <td class="msg-cell">{{ log.message }}</td>
            </tr>
            <tr v-if="!loading && !logs.length">
              <td colspan="5">
                <div class="empty-state">
                  <AppIcon name="logs" :size="28" />
                  <p class="empty-state-title">Noch keine Logs</p>
                  <p class="text-sm">Sobald ein Gerät Logs sendet, erscheinen sie hier.</p>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'

const auth = useAuthStore()
const stats = ref(null)
const logs = ref([])
const loading = ref(false)
const lastLoaded = ref(null)

// Clientseitiger Kurzzeit-Cache (reduziert wiederholte API-Calls beim Navigieren)
const CACHE_MS = 30000
let cachedStats = null
let cachedStatsTs = 0
let cachedLogs = null
let cachedLogsTs = 0

const LEVEL_META = {
  emergency: { label: 'Notfall', color: 'var(--color-danger)' },
  alert: { label: 'Alarm', color: 'var(--color-danger)' },
  critical: { label: 'Kritisch', color: 'var(--color-danger)' },
  error: { label: 'Fehler', color: 'var(--color-danger)' },
  warning: { label: 'Warnung', color: 'var(--color-warning)' },
  notice: { label: 'Notice', color: 'var(--color-accent)' },
  info: { label: 'Info', color: 'var(--color-primary)' },
  debug: { label: 'Debug', color: 'var(--color-text-muted)' },
}

const errorCount = computed(() => {
  const byLevel = stats.value?.logs_by_level || {}
  return ['error', 'critical', 'alert', 'emergency'].reduce((sum, key) => sum + (byLevel[key] || 0), 0)
})

const warningCount = computed(() => stats.value?.logs_by_level?.warning || 0)

const rmm = ref(null)

const statCards = computed(() => [
  {
    label: 'Logs gesamt',
    value: fmt(stats.value?.total_logs),
    hint: 'Alle gespeicherten Einträge',
    icon: 'logs',
    color: 'var(--color-primary)',
    tint: 'var(--primary-soft)',
    to: '/logs',
  },
  {
    label: 'Heute',
    value: fmt(stats.value?.logs_today),
    hint: 'Seit Mitternacht empfangen',
    icon: 'dashboard',
    color: 'var(--color-accent)',
    tint: 'var(--accent-soft)',
    to: { path: '/logs', query: { start_date: todayIso() } },
  },
  {
    label: 'Fehler & kritisch',
    value: fmt(errorCount.value),
    hint: warningCount.value ? `Dazu ${fmt(warningCount.value)} Warnungen` : 'Fehler und dringender',
    icon: 'warning',
    color: 'var(--color-danger)',
    tint: 'var(--danger-soft)',
    valueColor: errorCount.value > 0 ? 'var(--color-danger)' : null,
    to: { path: '/logs', query: { min_severity: 'error' } },
  },
  {
    label: 'Geräte',
    value: fmt(stats.value?.unique_hosts),
    hint: 'Hosts, die Logs geliefert haben',
    icon: 'agents',
    color: 'var(--color-secondary)',
    tint: 'var(--primary-soft)',
    to: '/agents',
  },
  // Fernwartung: nur, wenn schon Rechner mit RIMPERIO-Agent angebunden sind.
  ...(rmm.value?.agents_total ? [
    {
      label: 'Rechner online',
      value: `${fmt(rmm.value.agents_online)} / ${fmt(rmm.value.agents_total)}`,
      hint: rmm.value.agents_offline ? `${fmt(rmm.value.agents_offline)} offline` : 'Alle Rechner melden sich',
      icon: 'devices',
      color: 'var(--color-success)',
      tint: 'var(--success-soft)',
      valueColor: rmm.value.agents_offline ? 'var(--color-warning)' : null,
      to: '/rmm',
    },
    {
      label: 'Rechner-Alarme',
      value: fmt(rmm.value.alerts_active),
      hint: rmm.value.patches_pending ? `${fmt(rmm.value.patches_pending)} Updates ausstehend` : 'Offline, Schwellen, Checks',
      icon: 'warning',
      color: 'var(--color-warning)',
      tint: 'var(--warning-soft)',
      valueColor: rmm.value.alerts_active ? 'var(--color-danger)' : null,
      to: '/rmm/alerts',
    },
  ] : []),
])

const levelBars = computed(() => {
  const byLevel = stats.value?.logs_by_level || {}
  const entries = Object.entries(byLevel).filter(([, count]) => count > 0)
  if (!entries.length) return []
  const max = Math.max(...entries.map(([, count]) => count))
  return entries
    .sort((a, b) => b[1] - a[1])
    .map(([level, count]) => ({
      level,
      count,
      label: LEVEL_META[level]?.label || level,
      color: LEVEL_META[level]?.color || 'var(--color-text-muted)',
      percent: Math.max(2, Math.round((count / max) * 100)),
    }))
})

const topSources = computed(() => {
  const bySource = stats.value?.logs_by_source || {}
  const entries = Object.entries(bySource).filter(([, count]) => count > 0)
  if (!entries.length) return []
  const max = Math.max(...entries.map(([, count]) => count))
  return entries
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([name, count]) => ({
      name,
      count,
      percent: Math.max(2, Math.round((count / max) * 100)),
    }))
})

const lastUpdated = computed(() =>
  lastLoaded.value ? lastLoaded.value.toLocaleTimeString('de-DE') : 'wird geladen…'
)

onMounted(() => load(false))

async function load(force) {
  loading.value = true
  try {
    const now = Date.now()
    const needsStats = force || !cachedStats || now - cachedStatsTs > CACHE_MS
    const needsLogs = force || !cachedLogs || now - cachedLogsTs > CACHE_MS

    const [s, l] = await Promise.all([
      needsStats ? auth.api('/api/logs/stats') : Promise.resolve(cachedStats),
      needsLogs ? auth.api('/api/logs/recent?limit=10') : Promise.resolve(cachedLogs),
    ])

    cachedStats = s
    cachedStatsTs = now
    cachedLogs = l
    cachedLogsTs = now

    stats.value = s
    // RMM-Zahlen: klein, ungecacht, ein Fehler darf das Dashboard nicht leeren.
    auth.api('/api/rmm/summary').then(r => { rmm.value = r }).catch(() => {})
    logs.value = l || []
    lastLoaded.value = new Date()
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

function fmt(value) {
  return (value || 0).toLocaleString('de-DE')
}

function todayIso() {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  // datetime-local-Format, das die Log-Ansicht erwartet
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T00:00`
}

function formatTime(ts) {
  return ts ? new Date(ts).toLocaleString('de-DE') : '–'
}

function levelBadgeClass(level) {
  const key = (level || '').toLowerCase()
  if (['error', 'critical', 'alert', 'emergency', 'err', 'crit'].includes(key)) return 'badge-danger'
  if (['warning', 'warn'].includes(key)) return 'badge-warning'
  if (['notice'].includes(key)) return 'badge-success'
  if (['info', 'information'].includes(key)) return 'badge-primary'
  return 'badge-neutral'
}
</script>

<style scoped>
.bar-track {
  height: 0.375rem;
  border-radius: var(--radius-full);
  background-color: var(--hover-surface);
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  border-radius: var(--radius-full);
  transition: width var(--duration) var(--ease);
}

.msg-cell {
  max-width: 32rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
