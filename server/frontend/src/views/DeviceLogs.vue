<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.08.02.14.00.00
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Log-Ansicht fuer EIN Geraet (Steckbrief + Logliste).
                   Aufruf ueber /devices/<hostname> aus der Geräteliste,
                   dem Dashboard oder der allgemeinen Logliste.
     ============================================================================== -->

<template>
  <div class="page">
    <!-- Kopfzeile -->
    <div class="page-header">
      <div class="min-w-0">
        <router-link to="/agents" class="link text-sm inline-flex items-center gap-1">
          <AppIcon name="chevronLeft" :size="14" /> Geräte
        </router-link>
        <div class="flex items-center gap-3 mt-1">
          <h2 class="page-title truncate">{{ hostname }}</h2>
          <span v-if="agent" class="badge" :class="agent.is_online ? 'badge-success' : 'badge-neutral'">
            <span class="status-dot" :class="agent.is_online ? 'status-dot-online' : 'status-dot-offline'" />
            {{ agent.is_online ? 'Online' : 'Offline' }}
          </span>
        </div>
      </div>
      <router-link to="/logs" class="btn btn-secondary btn-sm">Alle Logs →</router-link>
    </div>

    <!-- Steckbrief -->
    <div v-if="agent" class="card mb-4">
      <div class="card-body">
        <dl class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
          <div v-for="fact in facts" :key="fact.label">
            <dt class="fact-label">{{ fact.label }}</dt>
            <dd class="fact-value" :class="fact.mono ? 'font-mono' : ''">{{ fact.value }}</dd>
          </div>
        </dl>

        <p v-if="agent.retention_max_logs || agent.retention_days" class="retention-note">
          <AppIcon name="trash" :size="13" />
          Aufbewahrung:
          <span v-if="agent.retention_max_logs">max. {{ agent.retention_max_logs.toLocaleString('de-DE') }} Logs</span>
          <span v-if="agent.retention_max_logs && agent.retention_days">·</span>
          <span v-if="agent.retention_days">älter als {{ agent.retention_days }} Tage werden gelöscht</span>
        </p>
      </div>
    </div>

    <!-- Kein Agent-Eintrag (z.B. Logs eines inzwischen geloeschten Geraets) -->
    <div v-else-if="!loadingAgent" class="card mb-4">
      <div class="card-body text-sm" style="color: var(--color-text-muted)">
        Zu diesem Hostnamen gibt es keinen Geräte-Eintrag (mehr). Die gespeicherten Logs werden trotzdem angezeigt.
      </div>
    </div>

    <!-- Logliste, fest auf dieses Geraet -->
    <LogTable :locked-hostname="hostname" />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import LogTable from '../components/LogTable.vue'
import AppIcon from '../components/AppIcon.vue'

const route = useRoute()
const authStore = useAuthStore()

const agent = ref(null)
const loadingAgent = ref(true)

const hostname = computed(() => String(route.params.hostname || ''))

const facts = computed(() => {
  const a = agent.value
  if (!a) return []
  return [
    { label: 'IP-Adresse', value: a.ip_address || '–', mono: true },
    { label: 'MAC', value: a.mac_address || '–', mono: true },
    { label: 'Art', value: typeLabel(a.device_type) },
    { label: 'Zuletzt gesehen', value: formatTime(a.last_seen) },
    { label: 'Erstmals gesehen', value: formatTime(a.first_seen) },
    { label: 'Logs gespeichert', value: a.log_count != null ? a.log_count.toLocaleString('de-DE') : '–' },
  ]
})

onMounted(loadAgent)
watch(hostname, loadAgent)

async function loadAgent() {
  loadingAgent.value = true
  agent.value = null
  try {
    // Die Suche liefert Teiltreffer -> exakten Hostnamen selbst heraussuchen.
    const params = new URLSearchParams({ search: hostname.value, page_size: '200' })
    const data = await authStore.api(`/api/agents?${params}`)
    const match = (data.items || []).find(
      a => (a.hostname || '').toLowerCase() === hostname.value.toLowerCase()
    )
    // Detail-Endpoint liefert zusaetzlich log_count und Retention.
    agent.value = match ? await authStore.api(`/api/agents/${match.id}`) : null
  } catch {
    agent.value = null
  } finally {
    loadingAgent.value = false
  }
}

function typeLabel(t) {
  const map = {
    syslog: 'Syslog',
    windows_agent: 'Windows-Agent',
    linux_agent: 'Linux-Agent',
    unifi_ap: 'UniFi AP',
    fritzbox: 'FRITZ!Box',
    linux: 'Linux',
    windows: 'Windows',
    unknown: 'Unbekannt',
  }
  return map[t] || t || 'Unbekannt'
}

function formatTime(ts) {
  if (!ts) return '–'
  return new Date(ts).toLocaleString('de-DE')
}
</script>

<style scoped>
.fact-label {
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-text-muted);
  margin-bottom: 0.125rem;
}

.fact-value {
  font-size: 0.875rem;
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.retention-note {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  margin-top: 1rem;
  padding-top: 0.875rem;
  border-top: 1px solid var(--color-border);
  font-size: 0.75rem;
  color: var(--color-text-muted);
}
</style>
