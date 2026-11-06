<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Speicherplatz: der Plattenwaechter und was er tut.

     Warum es diese Seite gibt: Die Automatik, die den Server vor einer vollen
     Platte bewahren soll, war genau dann kaputt, wenn man sie braucht - ab 95 %.
     Der Grund war nicht offensichtlich (VACUUM FULL braucht noch einmal so viel
     Platz, wie die Tabelle gross ist), und man konnte ihm auch nirgends
     zusehen. Beides ist jetzt anders: der Waechter faengt frueher an, und hier
     steht nachvollziehbar, was er tut und was er bewusst nicht tut.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">Speicherplatz</h2>
        <p class="page-subtitle">
          Der Plattenwächter räumt auf, <em>bevor</em> es eng wird — hier steht, wie es steht.
        </p>
      </div>
      <button class="btn btn-secondary btn-sm" :disabled="loading" @click="load">
        <AppIcon name="refresh" :size="16" />
        Neu laden
      </button>
    </div>

    <div v-if="error" class="card mb-4" style="border-color: var(--color-danger)">
      <div class="card-body text-sm" style="color: var(--color-danger)">{{ error }}</div>
    </div>

    <!-- Belegung -->
    <div v-if="status" class="card mb-4" :style="{ borderColor: stateColor }">
      <div class="card-body">
        <div class="flex items-baseline justify-between gap-3 mb-2">
          <span class="text-2xl font-bold" :style="{ color: stateColor }">{{ status.percent }} %</span>
          <span class="text-sm" style="color: var(--color-text-secondary)">
            {{ status.used_human }} von {{ status.total_human }} belegt ·
            {{ status.free_human }} frei
          </span>
        </div>

        <!-- Balken mit den drei Marken, damit man die Schwellen sieht -->
        <div class="gauge">
          <div class="gauge-fill" :style="{ width: Math.min(status.percent, 100) + '%', backgroundColor: stateColor }" />
          <span class="gauge-mark" :style="{ left: status.thresholds.target + '%' }" title="Ziel" />
          <span class="gauge-mark is-warn" :style="{ left: status.thresholds.warn + '%' }" title="Vorwarnung" />
          <span class="gauge-mark is-crit" :style="{ left: status.thresholds.critical + '%' }" title="Kritisch" />
        </div>
        <div class="flex justify-between text-xs mt-1" style="color: var(--color-text-muted)">
          <span>Ziel {{ status.thresholds.target }} %</span>
          <span>Aufräumen ab {{ status.thresholds.warn }} %</span>
          <span>Kritisch ab {{ status.thresholds.critical }} %</span>
        </div>

        <p class="text-sm mt-3" style="color: var(--color-text-secondary)">{{ status.advice }}</p>
      </div>
    </div>

    <!-- Was liegt da eigentlich -->
    <div v-if="status" class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
      <div class="stat-card">
        <span class="stat-label">Logs (Tabelle)</span>
        <span class="stat-value">{{ status.logs_human }}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Datenbank gesamt</span>
        <span class="stat-value">{{ status.database_human }}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Logzeilen (geschätzt)</span>
        <span class="stat-value">{{ formatNumber(status.log_rows_estimate) }}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Ältester Eintrag</span>
        <span class="text-sm mt-1 block" style="color: var(--color-text-secondary)">
          {{ formatDate(status.oldest_log) }}
        </span>
      </div>
    </div>

    <!-- Wie der Wächter arbeitet -->
    <div class="card mb-4">
      <div class="card-header"><p class="card-title">Wie aufgeräumt wird</p></div>
      <div class="card-body text-sm space-y-2" style="color: var(--color-text-secondary)">
        <p>
          Der Wächter sieht alle
          <strong>{{ status?.check_interval_seconds || 60 }} Sekunden</strong> nach und arbeitet
          sich von unten nach oben durch: erst abgelaufene Einmal-Token und altes
          Systemtagebuch, dann Logzeilen jenseits der eingestellten Aufbewahrung, dann –
          wenn nötig – eine schrittweise verkürzte Aufbewahrung.
        </p>
        <p>
          Gelöscht wird <strong>in Häppchen</strong>, jedes für sich festgeschrieben. Ein
          einziges riesiges DELETE würde das Transaktionslog aufblähen und die Platte
          während des Aufräumens erst einmal <em>voller</em> machen.
        </p>
        <p>
          <strong>Die letzten {{ status?.min_keep_hours || 24 }} Stunden bleiben immer stehen.</strong>
          Ein Log-Server ohne die letzten Stunden ist bei einem Zwischenfall wertlos.
          Ein automatisches „alles weg" gibt es nicht mehr<template v-if="status?.truncate_allowed">
            — außer es wurde per <code>DISK_ALLOW_TRUNCATE=true</code> ausdrücklich erlaubt,
            und das ist auf diesem Server der Fall</template>.
        </p>
        <p>
          <code>VACUUM FULL</code> läuft nur, wenn genug Luft dafür da ist. Es schreibt die
          Tabelle neu und braucht dafür noch einmal so viel freien Platz, wie sie groß ist
          — genau daran ist die frühere Automatik gescheitert.
        </p>
      </div>
    </div>

    <!-- Von Hand -->
    <div class="card mb-4">
      <div class="card-header"><p class="card-title">Jetzt aufräumen</p></div>
      <div class="card-body">
        <p class="text-sm mb-3" style="color: var(--color-text-secondary)">
          Derselbe Ablauf, den der Wächter von selbst fährt — nur eben jetzt. Der Bericht
          sagt danach, was getan wurde und was bewusst nicht.
        </p>
        <div class="flex flex-wrap gap-2">
          <button class="btn btn-primary btn-sm" :disabled="running" @click="cleanup(false)">
            <AppIcon name="refresh" :size="14" />
            {{ running ? 'Räume auf…' : 'Aufräumen' }}
          </button>
          <button class="btn btn-secondary btn-sm" :disabled="running" @click="cleanup(true)">
            Auch unterhalb der Vorwarnstufe
          </button>
          <router-link to="/settings/retention" class="btn btn-ghost btn-sm">
            Aufbewahrung einstellen
          </router-link>
        </div>

        <div v-if="report" class="mt-4 rounded p-3" style="background-color: var(--color-surface-elevated)">
          <p class="font-medium mb-2" style="color: var(--color-text-primary)">
            {{ report.before_percent }} % → {{ report.after_percent }} %
            <span class="hint">({{ report.freed_human }} frei geworden, {{ report.duration_seconds }} s)</span>
          </p>
          <ul class="text-sm space-y-1" style="color: var(--color-text-secondary)">
            <li v-for="(step, index) in report.steps" :key="index">· {{ step }}</li>
            <li v-if="!report.steps.length">· Es war nichts zu tun.</li>
          </ul>
          <p v-if="report.note" class="hint mt-2">{{ report.note }}</p>
        </div>
      </div>
    </div>

    <p class="hint">
      Jeder Aufräumlauf steht mit Uhrzeit, Anlass und Zeilenzahl im
      <router-link to="/journal" class="link">Systemtagebuch</router-link>.
    </p>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'

const auth = useAuthStore()

const status = ref(null)
const report = ref(null)
const loading = ref(false)
const running = ref(false)
const error = ref('')

const stateColor = computed(() => {
  const state = status.value?.state
  if (state === 'critical') return 'var(--color-danger)'
  if (state === 'high') return 'var(--color-warning)'
  if (state === 'warn') return 'var(--color-warning)'
  return 'var(--color-success)'
})

function formatNumber(value) {
  return Number(value || 0).toLocaleString('de-DE')
}

function formatDate(value) {
  if (!value) return '–'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('de-DE')
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    status.value = await auth.api('/api/settings/disk/status')
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function cleanup(force) {
  running.value = true
  error.value = ''
  try {
    report.value = await auth.api(`/api/settings/disk/cleanup?force=${force ? 'true' : 'false'}`,
                                  { method: 'POST' })
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    running.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.gauge {
  position: relative;
  height: 12px;
  border-radius: 999px;
  overflow: hidden;
  background-color: var(--color-surface-elevated);
}

.gauge-fill {
  height: 100%;
  border-radius: 999px;
  transition: width 0.3s ease;
}

/* Die Marken sitzen ueber dem Balken - so sieht man auf einen Blick, wo man
   gegenueber den Schwellen steht, statt drei Zahlen vergleichen zu muessen. */
.gauge-mark {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  background-color: var(--color-text-muted);
  opacity: 0.6;
}

.gauge-mark.is-warn {
  background-color: var(--color-warning);
  opacity: 0.9;
}

.gauge-mark.is-crit {
  background-color: var(--color-danger);
  opacity: 0.9;
}
</style>
