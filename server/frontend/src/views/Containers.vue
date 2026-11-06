<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Container: wer gehoert wem, und was ist zu aktualisieren.

     Die Seite beantwortet eine einzige Frage, und zwar fuer jeden Container
     einzeln: WIE aktualisiere ich das Ding?

       RIMPERIO selbst   aus dem Quellcode gebaut -> System -> Updates
       Fremde Dienste  fertiges Image aus einer Registry -> hier, auf Knopfdruck
       Andere          laeuft daneben, gehoert nicht dazu -> nur zur Kenntnis

     Vorher hiessen alle Container "rimperio-irgendwas", und damit sah es so aus,
     als gehoerte PostgreSQL zu RIMPERIO. Tut es nicht - und genau diese
     Verwechslung hat dazu gefuehrt, dass fremde Images nie aktualisiert wurden.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">Container</h2>
        <p class="page-subtitle">
          Was läuft hier — und wie wird welcher Teil aktualisiert?
        </p>
      </div>
      <div class="flex gap-2">
        <button class="btn btn-secondary btn-sm" :disabled="loading" @click="load()">
          <AppIcon name="refresh" :size="16" />
          Neu laden
        </button>
        <button class="btn btn-primary btn-sm" :disabled="checking" @click="check(true)">
          <AppIcon name="download" :size="16" />
          {{ checking ? 'Prüfe Registries…' : 'Auf Updates prüfen' }}
        </button>
      </div>
    </div>

    <div v-if="error" class="card mb-4" style="border-color: var(--color-danger)">
      <div class="card-body text-sm" style="color: var(--color-danger)">{{ error }}</div>
    </div>

    <div v-if="data && !data.available" class="card mb-4" style="border-color: var(--color-warning)">
      <div class="card-body">
        <p class="font-semibold mb-1" style="color: var(--color-warning)">Kein Zugriff auf den Server</p>
        <p class="text-sm" style="color: var(--color-text-secondary)">{{ data.reason }}</p>
      </div>
    </div>

    <!-- Kurzfassung ganz oben: die eine Zahl, die zählt -->
    <div v-if="summary" class="card mb-4" :style="{ borderColor: pending.length ? 'var(--color-warning)' : 'var(--color-border)' }">
      <div class="card-body flex items-center gap-4">
        <span
          class="stat-icon"
          :style="{
            backgroundColor: pending.length ? 'var(--warning-soft)' : 'var(--success-soft)',
            color: pending.length ? 'var(--color-warning)' : 'var(--color-success)',
          }"
        >
          <AppIcon :name="pending.length ? 'warning' : 'check'" :size="20" />
        </span>
        <div class="min-w-0">
          <p class="font-semibold" style="color: var(--color-text-primary)">{{ summary }}</p>
          <p class="text-sm" style="color: var(--color-text-muted)">
            Geprüft wird direkt bei der Registry — es wird nichts heruntergeladen und
            nichts ausgetauscht. Eingespielt wird erst auf Klick.
          </p>
        </div>
      </div>
    </div>

    <!-- Gruppen: eigen / fremd / andere -->
    <div v-for="group in groups" :key="group.key" class="mb-6">
      <h3 class="section-title">{{ group.title }}</h3>
      <p class="hint mb-3">{{ group.explain }}</p>

      <div v-if="!group.items.length" class="empty-state">
        <p class="empty-state-title">Nichts in dieser Gruppe.</p>
      </div>

      <div v-else class="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div
          v-for="item in group.items"
          :key="item.name"
          class="card"
          :style="{ borderColor: borderFor(item) }"
        >
          <div class="card-header flex items-start justify-between gap-3">
            <div class="min-w-0">
              <p class="card-title truncate">{{ item.name }}</p>
              <p class="text-xs mt-0.5" style="color: var(--color-text-muted)">{{ item.role || '—' }}</p>
            </div>
            <span class="badge" :class="item.running ? 'badge-success' : 'badge-warning'">
              {{ item.running ? 'läuft' : item.state }}
            </span>
          </div>

          <div class="card-body space-y-3">
            <dl class="text-sm space-y-1">
              <div class="flex gap-2">
                <dt class="w-24 shrink-0" style="color: var(--color-text-muted)">Image</dt>
                <dd class="min-w-0 break-all" style="color: var(--color-text-secondary)">{{ item.image || '—' }}</dd>
              </div>
              <div class="flex gap-2">
                <dt class="w-24 shrink-0" style="color: var(--color-text-muted)">Update über</dt>
                <dd style="color: var(--color-text-secondary)">
                  <template v-if="item.update_path === 'source'">
                    <router-link to="/updates" class="link">System → Updates</router-link>
                    <span class="hint"> (aus dem Quellcode gebaut)</span>
                  </template>
                  <template v-else>Image aus der Registry</template>
                </dd>
              </div>
              <div v-if="item.status" class="flex gap-2">
                <dt class="w-24 shrink-0" style="color: var(--color-text-muted)">Zustand</dt>
                <dd style="color: var(--color-text-secondary)">{{ item.status }}</dd>
              </div>
            </dl>

            <!-- Ergebnis der Registry-Abfrage -->
            <div v-if="item.update" class="rounded p-3 text-sm" :style="noteStyle(item)">
              <p v-if="item.update.update_available" class="font-medium" style="color: var(--color-warning)">
                Ein neueres Image liegt bereit.
              </p>
              <p v-else-if="item.update.error" style="color: var(--color-text-muted)">
                {{ item.update.error }}
              </p>
              <p v-else style="color: var(--color-success)">Aktuell.</p>
              <p v-if="item.update.remote_digest" class="text-xs mt-1 break-all" style="color: var(--color-text-muted)">
                Registry: {{ short(item.update.remote_digest) }} ·
                lokal: {{ short(item.update.local_digest) || 'unbekannt' }}
              </p>
            </div>

            <div class="flex flex-wrap gap-2">
              <button
                v-if="item.update_path === 'image' && item.service"
                class="btn btn-primary btn-sm"
                :disabled="busy === item.component"
                @click="askUpdate(item)"
              >
                <AppIcon name="download" :size="14" />
                {{ busy === item.component ? 'Läuft…' : 'Image holen & neu starten' }}
              </button>
              <button
                v-if="item.service && item.component !== 'backend'"
                class="btn btn-secondary btn-sm"
                :disabled="busy === item.component"
                @click="lifecycle(item, 'restart')"
              >
                <AppIcon name="refresh" :size="14" />
                Neu starten
              </button>
              <button class="btn btn-ghost btn-sm" @click="showLogs(item)">
                <AppIcon name="logs" :size="14" />
                Protokoll
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Aufräumen -->
    <div class="card">
      <div class="card-header"><p class="card-title">Alte Images wegräumen</p></div>
      <div class="card-body">
        <p class="text-sm mb-3" style="color: var(--color-text-secondary)">
          Nach ein paar Updates liegen die alten Stände als Karteileichen herum und fressen
          genau den Platz, den der Plattenwächter gerade freigeräumt hat. Das hier entfernt
          alle Images, die kein Container mehr benutzt.
        </p>
        <button class="btn btn-secondary btn-sm" :disabled="pruning" @click="prune">
          <AppIcon name="trash" :size="14" />
          {{ pruning ? 'Räume auf…' : 'Ungenutzte Images entfernen' }}
        </button>
        <p v-if="pruneMessage" class="text-sm mt-2" style="color: var(--color-success)">{{ pruneMessage }}</p>
      </div>
    </div>

    <!-- Protokoll eines Containers -->
    <div v-if="logView" class="modal-backdrop" @click.self="logView = null">
      <div class="modal">
        <div class="card-header flex items-center justify-between">
          <p class="card-title">Protokoll: {{ logView.name }}</p>
          <button class="btn-icon" @click="logView = null"><AppIcon name="close" :size="18" /></button>
        </div>
        <div class="card-body">
          <pre class="console">{{ logView.lines.join('\n') || 'Keine Ausgabe.' }}</pre>
        </div>
      </div>
    </div>

    <!-- Sicherungsfrage vor dem Austausch -->
    <BackupPrompt
      :open="promptOpen"
      :operation="promptOperation"
      :warning="promptWarning"
      :scopes="backupScopes"
      @confirm="runUpdate"
      @cancel="promptOpen = false"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import BackupPrompt from '../components/BackupPrompt.vue'

const auth = useAuthStore()

const data = ref(null)
const loading = ref(false)
const checking = ref(false)
const busy = ref('')
const error = ref('')
const pruning = ref(false)
const pruneMessage = ref('')
const logView = ref(null)

const promptOpen = ref(false)
const promptOperation = ref('')
const promptWarning = ref('')
const pendingTarget = ref(null)
const backupScopes = ref([])

const groups = computed(() => data.value?.groups || [])
const pending = computed(() => data.value?.updates_pending || [])
const summary = computed(() => data.value?.summary || '')

function short(digest) {
  if (!digest) return ''
  return digest.replace('sha256:', '').slice(0, 12)
}

function borderFor(item) {
  if (item.update?.update_available) return 'var(--color-warning)'
  if (!item.running && item.critical) return 'var(--color-danger)'
  return 'var(--color-border)'
}

function noteStyle(item) {
  if (item.update?.update_available) return { backgroundColor: 'var(--warning-soft)' }
  return { backgroundColor: 'var(--color-surface-elevated)' }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await auth.api('/api/containers')
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function check(force = false) {
  checking.value = true
  error.value = ''
  try {
    data.value = await auth.api(`/api/containers/check${force ? '?force=true' : ''}`)
  } catch (e) {
    error.value = e.message
  } finally {
    checking.value = false
  }
}

function askUpdate(item) {
  pendingTarget.value = item
  promptOperation.value = `${item.name} auf ein neueres Image heben`
  promptWarning.value = item.critical
    ? 'Dieser Container gehört zum Kern. Während des Austauschs ist RIMPERIO kurz nicht erreichbar.'
    : 'Der Container wird gestoppt, neu erzeugt und wieder gestartet.'
  promptOpen.value = true
}

async function runUpdate(decision) {
  promptOpen.value = false
  const item = pendingTarget.value
  if (!item) return

  busy.value = item.component
  error.value = ''
  try {
    await auth.api(`/api/containers/${item.component}/update`, {
      method: 'POST',
      body: { confirm: item.component, backup: decision },
    })
    await check(true)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = ''
    pendingTarget.value = null
  }
}

async function lifecycle(item, action) {
  busy.value = item.component
  error.value = ''
  try {
    await auth.api(`/api/containers/${item.component}/lifecycle`, {
      method: 'POST',
      body: { action },
    })
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = ''
  }
}

async function showLogs(item) {
  try {
    const result = await auth.api(`/api/containers/${encodeURIComponent(item.name)}/logs?lines=300`)
    logView.value = { name: item.name, lines: result.lines || [] }
  } catch (e) {
    error.value = e.message
  }
}

async function prune() {
  pruning.value = true
  pruneMessage.value = ''
  error.value = ''
  try {
    const result = await auth.api('/api/containers/prune-images', { method: 'POST' })
    pruneMessage.value = result.message
  } catch (e) {
    error.value = e.message
  } finally {
    pruning.value = false
  }
}

async function loadBackupScopes() {
  try {
    const overview = await auth.api('/api/backup/overview')
    backupScopes.value = overview.scopes || []
  } catch {
    // Ohne Bereichsliste bleibt der Dialog benutzbar - er sichert dann den
    // Standardumfang. Kein Grund, die Seite deswegen scheitern zu lassen.
  }
}

onMounted(() => {
  load()
  loadBackupScopes()
})
</script>

<style scoped>
.console {
  background-color: var(--color-surface-elevated);
  color: var(--color-text-secondary);
  border-radius: 6px;
  padding: 0.75rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.75rem;
  line-height: 1.5;
  max-height: 60vh;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
