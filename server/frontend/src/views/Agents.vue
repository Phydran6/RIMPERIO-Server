<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.08.02.14.00.00
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Geräteübersicht. Klick auf eine Karte öffnet die
                   Log-Ansicht des Geräts.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">Geräte</h2>
        <p class="page-subtitle">
          {{ total.toLocaleString('de-DE') }} erfasst · {{ onlineCount }} gerade online
        </p>
      </div>
      <button class="btn btn-secondary btn-sm" :disabled="loading" @click="loadAgents">
        <AppIcon name="refresh" :size="16" />
        Aktualisieren
      </button>
    </div>

    <!-- ================================================================
         SUCHE & FILTER
         ================================================================ -->
    <div class="card mb-4">
      <div class="card-body flex flex-col sm:flex-row gap-3">
        <div class="search-wrap flex-1">
          <AppIcon name="search" :size="16" class="search-icon" />
          <input
            v-model="search"
            type="text"
            placeholder="Hostname, IP oder MAC…"
            class="input pl-9"
            @keyup.enter="applySearch"
          >
        </div>
        <select v-model="deviceType" class="select sm:w-56" @change="applySearch">
          <option value="">Alle Gerätearten</option>
          <option v-for="(label, key) in TYPE_LABELS" :key="key" :value="key">{{ label }}</option>
        </select>
        <button class="btn btn-primary" @click="applySearch">Suchen</button>
      </div>
    </div>

    <!-- ================================================================
         GERÄTE
         ================================================================ -->
    <div v-if="loading && !agents.length" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
      <div v-for="n in 6" :key="n" class="card p-5 space-y-3">
        <div class="skeleton h-5 w-2/3" />
        <div class="skeleton h-3 w-1/3" />
        <div class="skeleton h-3 w-full" />
        <div class="skeleton h-3 w-4/5" />
      </div>
    </div>

    <div v-else-if="agents.length" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
      <article
        v-for="agent in agents"
        :key="agent.id"
        class="card card-hover device-card"
        :title="`Logs von ${agent.hostname} anzeigen`"
        @click="openDevice(agent)"
      >
        <!-- Kopf -->
        <div class="flex items-start justify-between gap-3 mb-3">
          <div class="min-w-0">
            <h3 class="device-name">{{ agent.hostname }}</h3>
            <p class="device-ip font-mono">{{ agent.ip_address || 'keine IP' }}</p>
          </div>
          <span class="badge" :class="isOnline(agent) ? 'badge-success' : 'badge-neutral'">
            <span class="status-dot" :class="isOnline(agent) ? 'status-dot-online' : 'status-dot-offline'" />
            {{ isOnline(agent) ? 'Online' : 'Offline' }}
          </span>
        </div>

        <!-- Eckdaten -->
        <dl class="device-facts">
          <div>
            <dt>Art</dt>
            <dd>{{ typeLabel(agent.device_type) }}</dd>
          </div>
          <div v-if="agent.mac_address">
            <dt>MAC</dt>
            <dd class="font-mono">{{ agent.mac_address }}</dd>
          </div>
          <div>
            <dt>Zuletzt gesehen</dt>
            <dd>{{ formatTime(agent.last_seen) }}</dd>
          </div>
          <div>
            <dt>Erstmals gesehen</dt>
            <dd>{{ formatTime(agent.first_seen) }}</dd>
          </div>
        </dl>

        <!-- Aufbewahrung -->
        <p
          v-if="agent.retention_max_logs || agent.retention_days"
          class="device-retention"
        >
          <AppIcon name="trash" :size="13" />
          <span v-if="agent.retention_max_logs">max. {{ agent.retention_max_logs.toLocaleString('de-DE') }} Logs</span>
          <span v-if="agent.retention_max_logs && agent.retention_days">·</span>
          <span v-if="agent.retention_days">älter als {{ agent.retention_days }} Tage</span>
        </p>

        <!-- Aktionen -->
        <div class="device-actions">
          <router-link
            :to="{ name: 'DeviceLogs', params: { hostname: agent.hostname } }"
            class="link text-sm"
            @click.stop
          >Logs anzeigen →</router-link>

          <div class="flex items-center gap-1">
            <button
              class="btn-icon"
              title="Aufbewahrung einstellen"
              aria-label="Aufbewahrung einstellen"
              @click.stop="openRetention(agent)"
            >
              <AppIcon name="settings" :size="16" />
            </button>
            <button
              class="btn-icon device-delete"
              title="Gerät löschen"
              aria-label="Gerät löschen"
              @click.stop="deleteAgent(agent)"
            >
              <AppIcon name="trash" :size="16" />
            </button>
          </div>
        </div>
      </article>
    </div>

    <!-- Leer -->
    <div v-else class="card">
      <div class="empty-state">
        <AppIcon name="agents" :size="30" />
        <p class="empty-state-title">Keine Geräte gefunden</p>
        <p class="text-sm">
          {{ search || deviceType
            ? 'Für diese Suche gibt es keinen Treffer.'
            : 'Geräte erscheinen automatisch, sobald sie Logs senden.' }}
        </p>
      </div>
    </div>

    <!-- ================================================================
         PAGINATION
         ================================================================ -->
    <div v-if="total > pageSize" class="flex items-center justify-center gap-2 mt-5">
      <button class="btn btn-secondary btn-sm" :disabled="page <= 1" @click="goToPage(page - 1)">
        <AppIcon name="chevronLeft" :size="16" /> Zurück
      </button>
      <span class="text-sm tabular" style="color: var(--color-text-muted)">
        Seite {{ page }} von {{ pageCount }}
      </span>
      <button class="btn btn-secondary btn-sm" :disabled="page >= pageCount" @click="goToPage(page + 1)">
        Weiter <AppIcon name="chevronRight" :size="16" />
      </button>
    </div>

    <!-- ================================================================
         AUFBEWAHRUNG (MODAL)
         ================================================================ -->
    <div v-if="retentionModal.open" class="modal-backdrop" @click.self="retentionModal.open = false">
      <div class="modal max-w-md">
        <div class="card-header">
          <span class="card-title">Aufbewahrung – {{ retentionModal.hostname }}</span>
          <button class="btn-icon" aria-label="Schließen" @click="retentionModal.open = false">
            <AppIcon name="close" :size="18" />
          </button>
        </div>

        <div class="card-body space-y-4 overflow-y-auto">
          <p class="text-sm" style="color: var(--color-text-muted)">
            Leer lassen = kein Limit. Die Regel wird stündlich automatisch angewendet.
          </p>

          <div>
            <label class="label">Höchstzahl gespeicherter Logs</label>
            <input v-model.number="retentionModal.max_logs" type="number" min="1000" placeholder="z. B. 50000" class="input">
          </div>

          <div>
            <label class="label">Logs löschen, die älter sind als (Tage)</label>
            <input v-model.number="retentionModal.days" type="number" min="1" placeholder="z. B. 30" class="input">
          </div>

          <div v-if="retentionModal.error" class="login-error text-sm">
            {{ retentionModal.error }}
          </div>
        </div>

        <div class="modal-footer">
          <button class="btn btn-ghost" @click="retentionModal.open = false">Abbrechen</button>
          <button class="btn btn-secondary" @click="executeRetention">Jetzt bereinigen</button>
          <button class="btn btn-primary" @click="saveRetention">Speichern</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'

const authStore = useAuthStore()
const router = useRouter()

const TYPE_LABELS = {
  syslog: 'Syslog',
  windows_agent: 'Windows-Agent',
  linux_agent: 'Linux-Agent',
  fritzbox: 'FRITZ!Box',
  unifi_ap: 'UniFi AP',
  linux: 'Linux',
  windows: 'Windows',
  unknown: 'Unbekannt',
}

const agents = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 50
const loading = ref(false)
const search = ref('')
const deviceType = ref('')
const offlineTimeout = ref(300)

const retentionModal = ref({
  open: false,
  agentId: null,
  hostname: '',
  max_logs: null,
  days: null,
  error: '',
})

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const onlineCount = computed(() => agents.value.filter(isOnline).length)

onMounted(async () => {
  await loadSettings()
  await loadAgents()
})

async function loadSettings() {
  try {
    const data = await authStore.api('/api/settings')
    if (data.settings?.agent_offline_timeout) {
      offlineTimeout.value = data.settings.agent_offline_timeout
    }
  } catch (e) {
    console.error('Settings laden fehlgeschlagen:', e)
  }
}

async function loadAgents() {
  loading.value = true
  try {
    const params = new URLSearchParams({
      page: page.value,
      page_size: pageSize,
    })
    if (search.value) params.append('search', search.value)
    if (deviceType.value) params.append('device_type', deviceType.value)

    const data = await authStore.api(`/api/agents?${params}`)
    agents.value = data.items
    total.value = data.total
  } catch (e) {
    console.error('Fehler:', e)
  } finally {
    loading.value = false
  }
}

function applySearch() {
  page.value = 1
  loadAgents()
}

function goToPage(target) {
  page.value = Math.min(Math.max(1, target), pageCount.value)
  loadAgents()
}

async function deleteAgent(agent) {
  if (!confirm(`Gerät "${agent.hostname}" wirklich löschen?`)) return

  try {
    await authStore.api(`/api/agents/${agent.id}`, { method: 'DELETE' })
    loadAgents()
  } catch (e) {
    alert('Fehler: ' + e.message)
  }
}

function openDevice(agent) {
  if (!agent?.hostname) return
  router.push({ name: 'DeviceLogs', params: { hostname: agent.hostname } })
}

function typeLabel(t) {
  return TYPE_LABELS[t] || t || 'Unbekannt'
}

function isOnline(agent) {
  // Bevorzugt das Server-Flag; sonst selbst rechnen (Toleranz bei Zeitdrift).
  if (agent && agent.is_online === true) return true
  const lastSeen = agent?.last_seen
  if (!lastSeen) return false
  const cutoff = Date.now() - offlineTimeout.value * 1000

  let ts = String(lastSeen).trim()
  // SQLAlchemy liefert oft 'YYYY-MM-DD HH:MM:SS' (ohne 'T' / Zeitzone)
  if (ts.includes(' ')) ts = ts.replace(' ', 'T')
  const hasTZ = /[zZ]|[+-]\d{2}:?\d{2}$/.test(ts)
  if (!hasTZ) ts = `${ts}Z`

  const time = Date.parse(ts)
  if (!Number.isFinite(time)) return false
  return time > cutoff
}

function formatTime(timestamp) {
  if (!timestamp) return '–'
  return new Date(timestamp).toLocaleString('de-DE')
}

function openRetention(agent) {
  retentionModal.value = {
    open: true,
    agentId: agent.id,
    hostname: agent.hostname,
    max_logs: agent.retention_max_logs || null,
    days: agent.retention_days || null,
    error: '',
  }
}

async function saveRetention() {
  retentionModal.value.error = ''
  try {
    await authStore.api(`/api/agents/${retentionModal.value.agentId}/retention`, {
      method: 'PUT',
      body: {
        retention_max_logs: retentionModal.value.max_logs || null,
        retention_days: retentionModal.value.days || null,
      },
    })
    retentionModal.value.open = false
    loadAgents()
  } catch (e) {
    retentionModal.value.error = e.message
  }
}

async function executeRetention() {
  retentionModal.value.error = ''
  try {
    // Erst speichern, dann sofort ausführen
    await authStore.api(`/api/agents/${retentionModal.value.agentId}/retention`, {
      method: 'PUT',
      body: {
        retention_max_logs: retentionModal.value.max_logs || null,
        retention_days: retentionModal.value.days || null,
      },
    })
    const result = await authStore.api(
      `/api/agents/${retentionModal.value.agentId}/retention/execute`,
      { method: 'POST' }
    )
    retentionModal.value.open = false
    loadAgents()
    alert(`Bereinigung abgeschlossen: ${result.deleted_count} Logs gelöscht.`)
  } catch (e) {
    retentionModal.value.error = e.message
  }
}
</script>

<style scoped>
.search-wrap {
  position: relative;
}

.search-icon {
  position: absolute;
  top: 50%;
  left: 0.75rem;
  transform: translateY(-50%);
  color: var(--color-text-muted);
  pointer-events: none;
}

.device-card {
  padding: 1.25rem;
  cursor: pointer;
}

.device-name {
  font-size: 1rem;
  font-weight: 600;
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.device-ip {
  font-size: 0.8125rem;
  color: var(--color-text-muted);
}

.device-facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem 1rem;
  font-size: 0.8125rem;
}

.device-facts dt {
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-text-muted);
  margin-bottom: 0.125rem;
}

.device-facts dd {
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.device-retention {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  margin-top: 0.875rem;
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.device-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  margin-top: 1rem;
  padding-top: 0.875rem;
  border-top: 1px solid var(--color-border);
}

.device-delete:hover {
  background-color: var(--danger-soft);
  color: var(--color-danger);
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  padding: 0.875rem 1.25rem;
  border-top: 1px solid var(--color-border);
}

.login-error {
  display: flex;
  gap: 0.5rem;
  padding: 0.625rem 0.75rem;
  border-radius: var(--radius);
  background-color: var(--danger-soft);
  color: var(--color-danger);
}
</style>
