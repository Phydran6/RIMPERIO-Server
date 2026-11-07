<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.11.07.23.00.00
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Uebersicht: wie steht es um alles, und was muss ich tun?

     Vorher war das ein Log-Dashboard mit zwei angehaengten RMM-Kacheln. Jetzt
     ist es die Startseite eines RMM, von oben nach unten:

       1. Kennzahlen      Rechner, Alarme, Freigaben, Netzwerkgeraete, Fehler-Logs
       2. Zu erledigen    aktive Alarme, Rechner offline, wartende Updates
       3. Logs            Verteilung nach Schweregrad und die neuesten Zeilen

     Jede Kachel und jede Zeile fuehrt auf die Seite, auf der man es erledigt.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <p class="page-subtitle">Stand: {{ lastUpdated }}</p>
      </div>
      <button class="btn btn-secondary btn-sm" :disabled="loading" @click="load(true)">
        <AppIcon name="refresh" :size="16" />
        Aktualisieren
      </button>
    </div>

    <!-- ================================================================
         1. KENNZAHLEN
         ================================================================ -->
    <div class="kpi-grid mb-5">
      <router-link
        v-for="card in statCards"
        :key="card.key"
        :to="card.to"
        class="stat-card card-hover"
        :title="card.hint"
      >
        <div class="flex items-start justify-between gap-3">
          <span class="stat-label">{{ card.label }}</span>
          <span class="stat-icon" :style="{ backgroundColor: card.tint, color: card.color }">
            <AppIcon :name="card.icon" :size="18" />
          </span>
        </div>
        <span v-if="!loaded" class="skeleton h-8 w-24" />
        <span v-else class="stat-value" :style="card.valueColor ? { color: card.valueColor } : null">
          {{ card.value }}
        </span>
        <span class="text-xs" style="color: var(--color-text-muted)">{{ card.hint }}</span>
      </router-link>
    </div>

    <!-- Noch gar nichts angebunden: sagen, wo es losgeht -->
    <div v-if="loaded && !rmm?.agents_total && !snmp?.devices" class="card mb-5">
      <div class="card-body getting-started">
        <AppIcon name="devices" :size="28" />
        <div>
          <p class="font-semibold" style="color: var(--color-text-primary)">Noch kein Gerät angebunden</p>
          <p class="text-sm" style="color: var(--color-text-muted)">
            Unter <router-link to="/rmm" class="link">Geräte · Rechner</router-link> steht der Befehl zum
            Installieren des Agents, unter <router-link to="/rmm/snmp" class="link">Netzwerkgeräte</router-link>
            ein Assistent für Switches, Drucker und Firewalls.
          </p>
        </div>
      </div>
    </div>

    <!-- ================================================================
         2. ZU ERLEDIGEN
         ================================================================ -->
    <div class="grid grid-cols-1 xl:grid-cols-3 gap-4 mb-5">
      <!-- Aktive Alarme -->
      <section class="card xl:col-span-2">
        <div class="card-header">
          <span class="card-title">Aktive Alarme</span>
          <router-link to="/rmm/alerts" class="link text-xs">Alle Alarme →</router-link>
        </div>
        <ul v-if="alerts.length" class="todo-list">
          <li v-for="alert in alerts.slice(0, 8)" :key="alert.id" class="todo-row">
            <span class="badge" :class="severityBadge(alert.severity)">{{ severityLabel(alert.severity) }}</span>
            <span class="todo-main">
              <span class="todo-title">{{ alert.message }}</span>
              <span class="todo-sub">{{ alert.hostname || 'Server' }} · {{ fmtAgo(alert.created_at) }}</span>
            </span>
          </li>
        </ul>
        <div v-else class="card-body">
          <p class="all-good"><AppIcon name="check" :size="16" /> Keine aktiven Alarme.</p>
        </div>
      </section>

      <!-- Rechner offline -->
      <section class="card">
        <div class="card-header">
          <span class="card-title">Rechner offline</span>
          <router-link to="/rmm" class="link text-xs">Alle Rechner →</router-link>
        </div>
        <ul v-if="offlineAgents.length" class="todo-list">
          <li v-for="agent in offlineAgents.slice(0, 8)" :key="agent.agent_uid">
            <router-link :to="`/rmm/agents/${agent.agent_uid}`" class="todo-row todo-link">
              <span class="status-dot status-dot-offline" />
              <span class="todo-main">
                <span class="todo-title">{{ agent.hostname }}</span>
                <span class="todo-sub">{{ agent.site }} · zuletzt {{ fmtAgo(agent.last_seen) }}</span>
              </span>
            </router-link>
          </li>
        </ul>
        <div v-else class="card-body">
          <p class="all-good"><AppIcon name="check" :size="16" />
            {{ rmm?.agents_total ? 'Alle Rechner melden sich.' : 'Noch keine Rechner angebunden.' }}
          </p>
        </div>
      </section>
    </div>

    <!-- Patchstand -->
    <section v-if="patchTotals" class="card mb-5">
      <div class="card-header">
        <span class="card-title">Patchstand</span>
        <router-link to="/rmm/patches" class="link text-xs">Patch-Center →</router-link>
      </div>
      <div class="card-body patch-row">
        <router-link to="/rmm/patches" class="patch-cell">
          <span class="patch-value" :class="{ 'is-warn': patchTotals.needs_approval }">{{ fmt(patchTotals.needs_approval) }}</span>
          <span class="patch-label">warten auf Freigabe</span>
        </router-link>
        <div class="patch-cell">
          <span class="patch-value">{{ fmt(patchTotals.approved) }}</span>
          <span class="patch-label">freigegeben</span>
        </div>
        <div class="patch-cell">
          <span class="patch-value">{{ fmt(patchTotals.auto) }}</span>
          <span class="patch-label">automatisch</span>
        </div>
        <div class="patch-cell">
          <span class="patch-value">{{ fmt(patchTotals.review) }}</span>
          <span class="patch-label">in KI-Prüfung</span>
        </div>
        <div class="patch-cell">
          <span class="patch-value" :class="{ 'is-bad': patchTotals.failed }">{{ fmt(patchTotals.failed) }}</span>
          <span class="patch-label">fehlgeschlagen</span>
        </div>
      </div>
    </section>

    <!-- ================================================================
         3. LOGS
         ================================================================ -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div class="card lg:col-span-1">
        <div class="card-header">
          <span class="card-title">Logs nach Schweregrad</span>
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

      <div class="card overflow-hidden lg:col-span-2">
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
                <th>Nachricht</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!loaded">
                <td colspan="4">
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
                  >{{ log.hostname }}</router-link>
                  <span v-else>–</span>
                </td>
                <td><span class="badge" :class="levelBadgeClass(log.level)">{{ log.level || 'unbekannt' }}</span></td>
                <td class="msg-cell">{{ log.message }}</td>
              </tr>
              <tr v-if="loaded && !logs.length">
                <td colspan="4">
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
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useAuthStore } from '../stores/auth'
import { fmtAgo } from '../utils/rmm'
import AppIcon from '../components/AppIcon.vue'

const auth = useAuthStore()

const stats = ref(null)
const logs = ref([])
const rmm = ref(null)
const snmp = ref(null)
const patchTotals = ref(null)
const alerts = ref([])
const agents = ref([])
const loading = ref(false)
const loaded = ref(false)
const lastLoaded = ref(null)
let refreshTimer = null

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

const snmpProblems = computed(() => {
  const by = snmp.value?.by_status || {}
  return (by.down || 0) + (by.critical || 0) + (by.warning || 0)
})

const offlineAgents = computed(() => agents.value.filter(a => !a.online))

const statCards = computed(() => {
  const r = rmm.value || {}
  const p = patchTotals.value || {}
  const s = snmp.value || {}
  return [
    {
      key: 'computers',
      label: 'Rechner online',
      value: `${fmt(r.agents_online)} / ${fmt(r.agents_total)}`,
      hint: r.agents_offline ? `${fmt(r.agents_offline)} offline` : (r.agents_total ? 'Alle melden sich' : 'Noch keine Rechner'),
      icon: 'devices',
      color: 'var(--color-success)',
      tint: 'var(--success-soft)',
      valueColor: r.agents_offline ? 'var(--color-warning)' : null,
      to: '/rmm',
    },
    {
      key: 'alarms',
      label: 'Aktive Alarme',
      value: fmt(r.alerts_active),
      hint: 'Offline, Schwellen, Checks, SNMP',
      icon: 'warning',
      color: 'var(--color-danger)',
      tint: 'var(--danger-soft)',
      valueColor: r.alerts_active ? 'var(--color-danger)' : null,
      to: '/rmm/alerts',
    },
    {
      key: 'approvals',
      label: 'Updates zur Freigabe',
      value: fmt(p.needs_approval),
      hint: p.failed ? `${fmt(p.failed)} fehlgeschlagen` : `${fmt(r.patches_pending)} ausstehend insgesamt`,
      icon: 'download',
      color: 'var(--color-warning)',
      tint: 'var(--warning-soft)',
      valueColor: p.needs_approval ? 'var(--color-warning)' : null,
      to: '/rmm/patches',
    },
    {
      key: 'snmp',
      label: 'Netzwerkgeräte',
      value: fmt(s.devices),
      hint: snmpProblems.value ? `${fmt(snmpProblems.value)} mit Problemen` : 'SNMP: Switches, Drucker, Firewalls',
      icon: 'router',
      color: 'var(--color-primary)',
      tint: 'var(--primary-soft)',
      valueColor: snmpProblems.value ? 'var(--color-danger)' : null,
      to: '/rmm/snmp',
    },
    {
      key: 'errors',
      label: 'Fehler-Logs',
      value: fmt(errorCount.value),
      hint: `${fmt(stats.value?.logs_today)} Logs heute`,
      icon: 'logs',
      color: 'var(--color-accent)',
      tint: 'var(--accent-soft)',
      valueColor: errorCount.value ? 'var(--color-danger)' : null,
      to: { path: '/logs', query: { min_severity: 'error' } },
    },
  ]
})

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

const lastUpdated = computed(() =>
  lastLoaded.value ? lastLoaded.value.toLocaleTimeString('de-DE') : 'wird geladen …'
)

// Jede Quelle einzeln: fällt eine aus (z.B. SNMP noch nie benutzt), bleibt
// der Rest der Übersicht trotzdem stehen.
async function load() {
  loading.value = true
  const safe = (promise) => promise.catch(() => null)
  const [s, l, r, sn, po, al, ag] = await Promise.all([
    safe(auth.api('/api/logs/stats')),
    safe(auth.api('/api/logs/recent?limit=8')),
    safe(auth.api('/api/rmm/summary')),
    safe(auth.api('/api/snmp/summary')),
    safe(auth.api('/api/patching/overview')),
    safe(auth.api('/api/rmm/alerts?active=true&limit=50')),
    safe(auth.api('/api/rmm/agents')),
  ])
  stats.value = s
  logs.value = Array.isArray(l) ? l : []
  rmm.value = r
  snmp.value = sn
  patchTotals.value = po?.totals || null
  alerts.value = Array.isArray(al) ? al : []
  agents.value = Array.isArray(ag) ? ag : []
  lastLoaded.value = new Date()
  loaded.value = true
  loading.value = false
}

onMounted(() => {
  load()
  refreshTimer = setInterval(load, 60000)
})
onBeforeUnmount(() => { if (refreshTimer) clearInterval(refreshTimer) })

function fmt(value) {
  return (value || 0).toLocaleString('de-DE')
}

function formatTime(ts) {
  return ts ? new Date(ts).toLocaleString('de-DE') : '–'
}

function severityBadge(severity) {
  return { critical: 'badge-danger', warning: 'badge-warning', info: 'badge-primary' }[severity] || 'badge-neutral'
}

function severityLabel(severity) {
  return { critical: 'kritisch', warning: 'Warnung', info: 'Info' }[severity] || severity || '–'
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
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(12.5rem, 1fr));
  gap: 1rem;
}

.getting-started {
  display: flex;
  gap: 1rem;
  align-items: flex-start;
  color: var(--color-primary);
}

.todo-list {
  display: flex;
  flex-direction: column;
}

.todo-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.625rem 1.25rem;
  border-top: 1px solid var(--color-border);
}

.todo-link:hover {
  background-color: var(--hover-surface);
}

.todo-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.todo-title {
  font-size: 0.875rem;
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.todo-sub {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.all-good {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
  color: var(--color-success);
}

.patch-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr));
  gap: 1rem;
}

.patch-cell {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

.patch-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--color-text-primary);
  font-variant-numeric: tabular-nums;
}

.patch-value.is-warn {
  color: var(--color-warning);
}

.patch-value.is-bad {
  color: var(--color-danger);
}

.patch-label {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

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
