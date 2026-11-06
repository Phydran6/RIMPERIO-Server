<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.08.02.16.00.00
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Archivierung alter Logs auf FTP/SFTP/SMB einrichten.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">Archivierung</h2>
        <p class="page-subtitle">
          Alte Logs werden gepackt (<code>.ndjson.gz</code>) auf ein externes Ziel geschrieben —
          auf Wunsch danach aus der Datenbank gelöscht.
        </p>
      </div>
      <span class="badge" :class="form.enabled ? 'badge-success' : 'badge-neutral'">
        {{ form.enabled ? 'aktiv' : 'aus' }}
      </span>
    </div>

    <div v-if="message" class="card mb-4" :style="{ borderColor: messageOk ? 'var(--color-success)' : 'var(--color-danger)' }">
      <div class="card-body flex items-start gap-2 text-sm" :style="{ color: messageOk ? 'var(--color-success)' : 'var(--color-danger)' }">
        <AppIcon :name="messageOk ? 'check' : 'warning'" :size="18" class="shrink-0" />
        <span>{{ message }}</span>
      </div>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <!-- ==============================================================
           ZIEL
           ============================================================== -->
      <div class="card lg:col-span-2">
        <div class="card-header">
          <span class="card-title">Ziel</span>
          <label class="flex items-center gap-2 text-sm cursor-pointer">
            <input v-model="form.enabled" type="checkbox">
            <span>Archivierung aktiv</span>
          </label>
        </div>

        <div class="card-body space-y-5">
          <!-- Protokoll -->
          <div>
            <label class="label">Übertragungsart</label>
            <div class="flex flex-wrap gap-2">
              <button
                v-for="p in PROTOCOLS"
                :key="p.key"
                class="btn btn-sm"
                :class="form.protocol === p.key ? 'btn-primary' : 'btn-secondary'"
                @click="form.protocol = p.key"
              >{{ p.label }}</button>
            </div>
            <p class="hint">{{ protocolHint }}</p>
          </div>

          <!-- Verbindung -->
          <div v-if="form.protocol !== 'local'" class="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div class="sm:col-span-2">
              <label class="label">Server</label>
              <input v-model="form.host" class="input" placeholder="nas.firma.local">
            </div>
            <div>
              <label class="label">Port</label>
              <input v-model.number="form.port" type="number" min="0" max="65535" class="input" :placeholder="String(defaultPort)">
              <p class="hint">0 = Standard ({{ defaultPort }})</p>
            </div>
            <div v-if="form.protocol === 'smb'">
              <label class="label">Freigabe</label>
              <input v-model="form.share" class="input" placeholder="backup">
            </div>
            <div v-if="form.protocol === 'smb'">
              <label class="label">Domäne (optional)</label>
              <input v-model="form.domain" class="input" placeholder="FIRMA">
            </div>
            <div>
              <label class="label">Benutzer</label>
              <input v-model="form.username" class="input" autocomplete="off">
            </div>
            <div>
              <label class="label">Passwort</label>
              <input v-model="form.password" type="password" class="input" :placeholder="passwordPlaceholder" autocomplete="new-password">
              <p class="hint">Leer = gespeichertes behalten</p>
            </div>
            <label v-if="form.protocol === 'ftps'" class="flex items-end gap-2 text-sm pb-2">
              <input v-model="form.verify_cert" type="checkbox"> Zertifikat prüfen
            </label>
          </div>

          <div>
            <label class="label">Zielordner</label>
            <input v-model="form.remote_path" class="input font-mono text-xs" placeholder="/rimperio/archiv">
            <p class="hint">
              Wird angelegt, falls er fehlt.
              <template v-if="form.protocol === 'smb'">Pfad innerhalb der Freigabe.</template>
              <template v-if="form.protocol === 'local'">Ordner im Backend-Container — als Docker-Volume einbinden.</template>
            </p>
          </div>

          <p v-if="form.protocol === 'ftp'" class="warn-note">
            <AppIcon name="warning" :size="14" />
            Einfaches FTP überträgt Zugangsdaten und Logs unverschlüsselt. Wenn möglich FTPS oder SFTP nehmen.
          </p>
        </div>
      </div>

      <!-- ==============================================================
           REGELN
           ============================================================== -->
      <div class="card h-fit">
        <div class="card-header"><span class="card-title">Was und wann</span></div>
        <div class="card-body space-y-4">
          <div>
            <label class="label">Logs archivieren, die älter sind als</label>
            <div class="flex items-center gap-2">
              <input v-model.number="form.age_days" type="number" min="1" max="3650" class="input w-24">
              <span class="text-sm" style="color: var(--color-text-muted)">Tage</span>
            </div>
          </div>

          <div>
            <label class="label">Zeitplan</label>
            <select v-model.number="form.schedule_hour" class="select">
              <option :value="-1">Nur von Hand</option>
              <option v-for="h in 24" :key="h - 1" :value="h - 1">
                Täglich um {{ String(h - 1).padStart(2, '0') }}:00 Uhr
              </option>
            </select>
            <p class="hint">Serverzeit (UTC im Container).</p>
          </div>

          <label class="flex items-start gap-2 text-sm cursor-pointer">
            <input v-model="form.delete_after" type="checkbox" class="mt-0.5">
            <span>
              Nach erfolgreicher Übertragung aus der Datenbank löschen
              <span class="block text-xs" style="color: var(--color-text-muted)">
                Gelöscht wird nur, was nachweislich in der Datei gelandet ist.
              </span>
            </span>
          </label>

          <div class="flex flex-col gap-2 pt-2 border-t" style="border-color: var(--color-border)">
            <button class="btn btn-primary" :disabled="loading" @click="save">Speichern</button>
            <button class="btn btn-secondary" :disabled="testing || loading" @click="runTest">
              {{ testing ? 'Teste…' : 'Verbindung testen' }}
            </button>
            <button class="btn btn-secondary" :disabled="running || loading" @click="runNow">
              {{ running ? 'Läuft…' : 'Jetzt archivieren' }}
            </button>
            <p class="hint">Test und Lauf nutzen die <strong>gespeicherten</strong> Einstellungen.</p>
          </div>
        </div>
      </div>
    </div>

    <!-- ================================================================
         HISTORIE
         ================================================================ -->
    <div class="card mt-4">
      <div class="card-header">
        <span class="card-title">Letzte Läufe</span>
        <button class="btn-icon" title="Neu laden" @click="loadHistory">
          <AppIcon name="refresh" :size="16" />
        </button>
      </div>

      <div v-if="!history.length" class="empty-state">
        <AppIcon name="download" :size="26" />
        <p class="empty-state-title">Noch kein Lauf</p>
        <p class="text-sm">Hier stehen künftig Zeitpunkt, Menge und Ergebnis jeder Archivierung.</p>
      </div>

      <div v-else class="overflow-x-auto">
        <table class="table">
          <thead>
            <tr>
              <th>Zeitpunkt</th>
              <th>Auslöser</th>
              <th>Ergebnis</th>
              <th>Archiviert</th>
              <th>Gelöscht</th>
              <th>Größe</th>
              <th>Dauer</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(run, index) in history" :key="index">
              <td class="whitespace-nowrap tabular">{{ formatTime(run.at) }}</td>
              <td>{{ run.triggered_by || '–' }}</td>
              <td>
                <span class="badge" :class="run.success ? 'badge-success' : 'badge-danger'">
                  {{ run.success ? 'OK' : 'Fehler' }}
                </span>
                <span class="block text-xs mt-1" style="color: var(--color-text-muted)">{{ run.message }}</span>
              </td>
              <td class="tabular">{{ (run.archived || 0).toLocaleString('de-DE') }}</td>
              <td class="tabular">{{ (run.deleted || 0).toLocaleString('de-DE') }}</td>
              <td class="tabular">{{ formatBytes(run.bytes) }}</td>
              <td class="tabular">{{ run.duration_seconds != null ? run.duration_seconds + ' s' : '–' }}</td>
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

const PROTOCOLS = [
  { key: 'sftp', label: 'SFTP' },
  { key: 'ftps', label: 'FTPS' },
  { key: 'ftp', label: 'FTP' },
  { key: 'smb', label: 'SMB / Windows-Freigabe' },
  { key: 'local', label: 'Eingebundener Ordner' },
]

const HINTS = {
  sftp: 'Dateiübertragung über SSH — verschlüsselt, Standardport 22.',
  ftps: 'FTP mit TLS. Auch die Datenverbindung wird verschlüsselt.',
  ftp: 'Klassisches FTP, unverschlüsselt.',
  smb: 'Windows-/NAS-Freigabe (SMB2/3).',
  local: 'Ein Ordner im Backend-Container, z. B. ein per Docker eingebundenes Netzlaufwerk.',
}

const DEFAULT_PORTS = { sftp: 22, ftps: 21, ftp: 21, smb: 445, local: 0 }

const DEFAULTS = {
  enabled: false,
  protocol: 'sftp',
  host: '',
  port: 0,
  username: '',
  password: '',
  remote_path: '/rimperio',
  share: '',
  domain: '',
  age_days: 90,
  delete_after: false,
  schedule_hour: 3,
  verify_cert: true,
}

const form = ref({ ...DEFAULTS })
const passwordSet = ref(false)
const history = ref([])
const loading = ref(false)
const testing = ref(false)
const running = ref(false)
const message = ref('')
const messageOk = ref(true)

const defaultPort = computed(() => DEFAULT_PORTS[form.value.protocol] ?? 0)
const protocolHint = computed(() => HINTS[form.value.protocol] || '')
const passwordPlaceholder = computed(() => (passwordSet.value ? '•••••••• (gespeichert)' : 'Passwort'))

onMounted(async () => {
  await load()
  await loadHistory()
})

async function load() {
  loading.value = true
  try {
    const data = await auth.api('/api/archiving/config')
    passwordSet.value = !!data.password_set
    form.value = { ...DEFAULTS, ...data, password: '' }
  } catch (e) {
    show(e.message, false)
  } finally {
    loading.value = false
  }
}

async function loadHistory() {
  try {
    const data = await auth.api('/api/archiving/history')
    history.value = data.items || []
  } catch {
    history.value = []
  }
}

async function save() {
  loading.value = true
  try {
    const data = await auth.api('/api/archiving/config', { method: 'PUT', body: { ...form.value } })
    passwordSet.value = !!data.password_set
    form.value.password = ''
    show('Einstellungen gespeichert.', true)
  } catch (e) {
    show(e.message, false)
  } finally {
    loading.value = false
  }
}

async function runTest() {
  testing.value = true
  try {
    const result = await auth.api('/api/archiving/test', { method: 'POST' })
    show(result.message, result.success)
  } catch (e) {
    show(e.message, false)
  } finally {
    testing.value = false
  }
}

async function runNow() {
  if (form.value.delete_after && !confirm(
    'Die archivierten Logs werden nach erfolgreicher Übertragung aus der Datenbank gelöscht. Fortfahren?'
  )) return

  running.value = true
  try {
    const result = await auth.api('/api/archiving/run', { method: 'POST' })
    show(result.message, result.success)
    await loadHistory()
  } catch (e) {
    show(e.message, false)
  } finally {
    running.value = false
  }
}

function show(text, ok) {
  message.value = text
  messageOk.value = ok
  setTimeout(() => { message.value = '' }, 8000)
}

function formatTime(ts) {
  return ts ? new Date(ts).toLocaleString('de-DE') : '–'
}

function formatBytes(bytes) {
  if (!bytes) return '–'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit++
  }
  return `${value.toFixed(unit ? 1 : 0)} ${units[unit]}`
}
</script>

<style scoped>
.warn-note {
  display: flex;
  align-items: flex-start;
  gap: 0.375rem;
  padding: 0.625rem 0.75rem;
  border-radius: var(--radius);
  background-color: var(--warning-soft);
  color: var(--color-warning);
  font-size: 0.8125rem;
}
</style>
