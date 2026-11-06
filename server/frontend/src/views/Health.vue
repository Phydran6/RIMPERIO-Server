<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.08.14.12.00.00
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Systemzustand: Systemcheck, Auslastung, Logs, Geräte, DB.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">Systemzustand</h2>
        <p class="page-subtitle">
          Version {{ health?.version || '–' }} · Laufzeit {{ formatUptime(health?.uptime_seconds) }}
        </p>
      </div>
      <button class="btn btn-secondary btn-sm" :disabled="loading" @click="loadAll(true)">
        <AppIcon name="refresh" :size="16" />
        Aktualisieren
      </button>
    </div>

    <!-- Vollständige Prüfung des Systems (nur Admin) -->
    <div v-if="authStore.isAdmin" class="mb-4">
      <SystemCheck />
    </div>

    <!-- Gesamtzustand -->
    <div class="card mb-4" :style="{ borderColor: isHealthy ? 'var(--color-success)' : 'var(--color-warning)' }">
      <div class="card-body flex items-center gap-4">
        <span class="stat-icon" :style="{ backgroundColor: isHealthy ? 'var(--success-soft)' : 'var(--warning-soft)', color: isHealthy ? 'var(--color-success)' : 'var(--color-warning)' }">
          <AppIcon :name="isHealthy ? 'check' : 'warning'" :size="20" />
        </span>
        <div>
          <p class="font-semibold" :style="{ color: isHealthy ? 'var(--color-success)' : 'var(--color-warning)' }">
            {{ isHealthy ? 'System läuft normal' : 'System eingeschränkt' }}
          </p>
          <p class="text-sm" style="color: var(--color-text-muted)">
            Stand: {{ lastUpdated }} — die Werte aktualisieren sich nicht von selbst.
          </p>
        </div>
      </div>
    </div>

    <!-- Auslastung -->
    <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 mb-4">
      <div v-for="metric in metrics" :key="metric.label" class="stat-card">
        <div class="flex items-start justify-between gap-3">
          <span class="stat-label">{{ metric.label }}</span>
          <span class="stat-icon" :style="{ backgroundColor: 'var(--primary-soft)', color: 'var(--color-primary)' }">
            <AppIcon :name="metric.icon" :size="18" />
          </span>
        </div>
        <span class="stat-value" :style="metric.emphasize ? { color: metric.color } : null">{{ metric.value }}</span>
        <div class="bar-track">
          <div class="bar-fill" :style="{ width: metric.percent + '%', backgroundColor: metric.color }" />
        </div>
      </div>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <!-- Datenbank -->
      <div class="card lg:col-span-1">
        <div class="card-header">
          <span class="card-title">Datenbank</span>
          <span class="badge" :class="dbReachable ? 'badge-success' : 'badge-danger'">
            {{ dbReachable ? 'verbunden' : 'nicht erreichbar' }}
          </span>
        </div>
        <div class="card-body space-y-2 text-sm">
          <div class="info-row">
            <span>Ablage</span>
            <span class="badge" :class="database?.target?.external ? 'badge-primary' : 'badge-neutral'">
              {{ database?.target?.external ? 'externer Server' : 'mitgeliefert (Container)' }}
            </span>
          </div>
          <div class="info-row">
            <span>Server</span>
            <span class="font-mono">{{ database?.target?.host || '–' }}:{{ database?.target?.port || '–' }}</span>
          </div>
          <div class="info-row">
            <span>Datenbank</span>
            <span class="font-mono">{{ database?.target?.database || '–' }}</span>
          </div>
          <div class="info-row">
            <span>Verschlüsselt</span>
            <span :style="{ color: database?.target?.tls ? 'var(--color-success)' : (database?.target?.external ? 'var(--color-danger)' : 'var(--color-text-muted)') }">
              {{ database?.target?.tls ? 'ja' : 'nein' }}
            </span>
          </div>
          <div v-if="database?.server_version" class="info-row">
            <span>PostgreSQL</span>
            <span>{{ database.server_version }}</span>
          </div>
          <div v-if="database?.size" class="info-row">
            <span>Größe</span>
            <span>{{ database.size }}</span>
          </div>
          <div v-if="database?.connections" class="info-row">
            <span>Verbindungen</span>
            <span>{{ database.connections.active }} aktiv / {{ database.connections.total }}</span>
          </div>
          <div v-if="database?.latency_ms != null" class="info-row">
            <span>Antwortzeit</span>
            <span>{{ database.latency_ms }} ms</span>
          </div>

          <p v-if="database?.target?.external && !database?.target?.tls" class="warn-note">
            <AppIcon name="warning" :size="14" />
            Verbindung zu einer externen Datenbank ohne TLS — <code>DB_SSLMODE=require</code> in der
            <code>.env</code> setzen.
          </p>
          <p v-if="database?.error" class="warn-note">{{ database.error }}</p>
        </div>
      </div>

      <!-- Logs -->
      <div class="card">
        <div class="card-header"><span class="card-title">Logs</span></div>
        <div class="card-body space-y-2 text-sm">
          <div class="info-row"><span>Gesamt</span><span class="tabular">{{ fmt(health?.logs_total) }}</span></div>
          <div class="info-row"><span>Letzte 24 Stunden</span><span class="tabular">{{ fmt(health?.logs_last_24h) }}</span></div>
          <div class="info-row"><span>Ø pro Minute</span><span class="tabular">{{ fmt(logsPerMinute) }}</span></div>
          <div v-if="database?.log_rows_estimated != null" class="info-row">
            <span>Zeilen in der Tabelle</span>
            <span class="tabular" title="Schätzung des Datenbank-Planers">≈ {{ fmt(database.log_rows_estimated) }}</span>
          </div>
        </div>
      </div>

      <!-- Geräte -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Geräte</span>
          <router-link to="/agents" class="link text-xs">Übersicht</router-link>
        </div>
        <div class="card-body space-y-2 text-sm">
          <div class="info-row"><span>Erfasst</span><span class="tabular">{{ fmt(health?.agents_total) }}</span></div>
          <div class="info-row">
            <span>Online</span>
            <span class="tabular" style="color: var(--color-success)">{{ fmt(health?.agents_online) }}</span>
          </div>
          <div class="info-row">
            <span>Offline</span>
            <span class="tabular">{{ fmt((health?.agents_total || 0) - (health?.agents_online || 0)) }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import SystemCheck from '../components/SystemCheck.vue'

const authStore = useAuthStore()
const health = ref(null)
const database = ref(null)
const loading = ref(false)
const lastLoaded = ref(null)

const CACHE_MS = 5000
let cachedHealth = null
let cachedHealthTs = 0

const isHealthy = computed(() => health.value?.status === 'healthy')
const dbReachable = computed(() => database.value?.reachable ?? health.value?.database_connected ?? false)

// Achtung, zwei verschiedene Bedeutungen in derselben Kachelreihe:
// Bei CPU, Speicher und Platte ist "viel" schlecht - der Balken wird zum Ende hin rot.
// Bei der Datenbank ist der Balken kein Füllstand, sondern ein Zustand: voll und
// grün heißt verbunden, voll und rot heißt nicht erreichbar. Deshalb bringt jede
// Kachel ihre Farbe selbst mit, statt sie aus dem Prozentwert abzuleiten.
const metrics = computed(() => {
  const cpu = health.value?.cpu_percent || 0
  const memory = health.value?.memory_percent || 0
  const disk = health.value?.disk_percent || 0

  return [
    {
      label: 'CPU',
      icon: 'dashboard',
      percent: cpu,
      value: `${cpu.toFixed(1)} %`,
      color: usageColor(cpu),
    },
    {
      label: 'Arbeitsspeicher',
      icon: 'dashboard',
      percent: memory,
      value: `${memory.toFixed(1)} %`,
      color: usageColor(memory),
    },
    {
      label: 'Festplatte',
      icon: 'agents',
      percent: disk,
      value: `${disk.toFixed(1)} %`,
      color: usageColor(disk),
    },
    {
      label: 'Datenbank',
      icon: 'agents',
      percent: 100,
      value: dbReachable.value ? 'Verbunden' : 'Nicht erreichbar',
      color: dbReachable.value ? 'var(--color-success)' : 'var(--color-danger)',
      emphasize: true,
    },
  ]
})

const logsPerMinute = computed(() => {
  if (!health.value?.logs_last_24h) return 0
  return Math.round(health.value.logs_last_24h / (24 * 60))
})

const lastUpdated = computed(() =>
  lastLoaded.value ? lastLoaded.value.toLocaleTimeString('de-DE') : 'wird geladen…'
)

onMounted(() => loadAll(false))

async function loadAll(force) {
  loading.value = true
  try {
    const now = Date.now()
    if (!force && cachedHealth && now - cachedHealthTs < CACHE_MS) {
      health.value = cachedHealth
    } else {
      const data = await authStore.api('/api/health/detailed')
      cachedHealth = data
      cachedHealthTs = now
      health.value = data
    }

    // Datenbank-Auskunft ist Admins vorbehalten - für alle anderen bleibt der
    // Bereich einfach leer, statt einen Fehler zu zeigen.
    try {
      database.value = await authStore.api('/api/database/status')
    } catch {
      database.value = null
    }

    lastLoaded.value = new Date()
  } catch (e) {
    console.error('Fehler:', e)
  } finally {
    loading.value = false
  }
}

function fmt(value) {
  return (value || 0).toLocaleString('de-DE')
}

function usageColor(percent) {
  if (percent < 60) return 'var(--color-success)'
  if (percent < 80) return 'var(--color-warning)'
  return 'var(--color-danger)'
}

function formatUptime(seconds) {
  if (!seconds) return '–'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d) return `${d} Tage, ${h} Std.`
  if (h) return `${h} Std., ${m} Min.`
  return `${m} Min.`
}
</script>

<style scoped>
.bar-track {
  height: 0.375rem;
  margin-top: 0.25rem;
  border-radius: var(--radius-full);
  background-color: var(--hover-surface);
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  border-radius: var(--radius-full);
  transition: width var(--duration) var(--ease);
}

.info-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.info-row > span:first-child {
  color: var(--color-text-muted);
}

.info-row > span:last-child {
  color: var(--color-text-primary);
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
}

.warn-note {
  display: flex;
  align-items: flex-start;
  gap: 0.375rem;
  margin-top: 0.5rem;
  padding: 0.5rem 0.625rem;
  border-radius: var(--radius);
  background-color: var(--warning-soft);
  color: var(--color-warning);
  font-size: 0.75rem;
}

.warn-note code {
  font-size: 0.6875rem;
}
</style>
