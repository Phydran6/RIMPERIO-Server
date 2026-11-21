<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Datenbank umstellen: eingebaut <-> eigener
                   PostgreSQL-Server. Installiert wird immer mit der eingebauten,
                   damit das System sofort laeuft; danach hier umziehen (und zurueck).
     ============================================================================== -->
<template>
  <div class="card">
    <div class="card-header">
      <h2 class="card-title">Wo liegen die Daten?</h2>
      <span class="badge" :class="mode === 'external' ? 'badge-primary' : 'badge-neutral'">
        {{ mode === 'external' ? 'eigener Datenbankserver' : 'eingebaute Datenbank' }}
      </span>
    </div>
    <div class="card-body space-y-4 text-sm">
      <div class="db-choice">
        <label class="db-option" :class="{ 'is-active': choice === 'builtin' }">
          <input v-model="choice" type="radio" value="builtin" :disabled="running">
          <span>
            <strong>Eingebaute Datenbank</strong>
            <span class="hint block">PostgreSQL als Container neben RIMPERIO. Standard nach der Installation — läuft sofort, Sicherung über „Backup“.</span>
          </span>
        </label>
        <label class="db-option" :class="{ 'is-active': choice === 'external' }">
          <input v-model="choice" type="radio" value="external" :disabled="running">
          <span>
            <strong>Eigener Datenbankserver</strong>
            <span class="hint block">Dedizierter PostgreSQL-Server (ab Version 13), Cluster oder verwaltete Cloud-Datenbank. RIMPERIO läuft dann nur noch als Anwendung.</span>
          </span>
        </label>
      </div>

      <template v-if="choice === 'external' && mode === 'builtin'">
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div class="md:col-span-2"><label class="label">Server</label><input v-model="target.host" class="input font-mono" placeholder="db.firma.local"></div>
          <div><label class="label">Port</label><input v-model.number="target.port" type="number" class="input"></div>
          <div><label class="label">Datenbank</label><input v-model="target.database" class="input font-mono"></div>
          <div><label class="label">Benutzer</label><input v-model="target.user" class="input font-mono" autocomplete="off"></div>
          <div><label class="label">Passwort</label><input v-model="target.password" type="password" class="input" autocomplete="new-password"></div>
          <div>
            <label class="label">Verschlüsselung</label>
            <select v-model="target.sslmode" class="select">
              <option value="">aus (nur im eigenen Netz)</option>
              <option value="require">TLS (require)</option>
              <option value="verify-ca">TLS + Zertifikat prüfen</option>
              <option value="verify-full">TLS + Zertifikat + Name prüfen</option>
            </select>
          </div>
        </div>
        <p class="hint">
          Auf dem Server vorher eine leere Datenbank samt Benutzer anlegen, z.B.
          <code>CREATE USER rimperio PASSWORD '…'; CREATE DATABASE rimperio OWNER rimperio;</code>
          und in <code>pg_hba.conf</code> die IP dieses RIMPERIO-Servers zulassen.
        </p>
        <label class="flex items-start gap-2">
          <input v-model="migrate" type="checkbox" class="mt-0.5">
          <span>Vorhandene Daten mitnehmen (Logs, Rechner, Einstellungen, Benutzer)
            <span class="hint block">Aus: es wird nur das Grundschema angelegt — oder, falls dort schon RIMPERIO-Daten liegen, diese benutzt.</span>
          </span>
        </label>
        <div class="flex flex-wrap gap-2">
          <button class="btn btn-secondary btn-sm" :disabled="busy || !ready" @click="testTarget">{{ busy ? 'Prüfe…' : 'Verbindung testen' }}</button>
          <button class="btn btn-primary btn-sm" :disabled="busy || !tested?.ok" @click="switchTo(true)">Umziehen</button>
        </div>
        <p v-if="tested" class="db-note" :class="tested.ok ? 'is-ok' : 'is-error'">{{ tested.message }}</p>
      </template>

      <template v-else-if="choice === 'builtin' && mode === 'external'">
        <p>
          Zurück auf die eingebaute Datenbank. Sie hat den Datenstand vom Zeitpunkt des Umzugs —
          was seitdem auf dem externen Server dazukam, bleibt dort und wird nicht zurückkopiert.
        </p>
        <button class="btn btn-primary btn-sm" :disabled="busy" @click="switchTo(false)">Zur eingebauten Datenbank wechseln</button>
      </template>

      <p v-else-if="!running" class="hint">
        {{ mode === 'external' ? 'RIMPERIO nutzt den eigenen Datenbankserver oben.' : 'RIMPERIO nutzt die eingebaute Datenbank.' }}
      </p>

      <div v-if="running || progress" class="db-progress">
        <p class="font-medium">{{ progressTitle }}</p>
        <ul>
          <li v-for="st in progressSteps" :key="st.step" :class="'is-' + st.status">
            {{ STEP_LABELS[st.step] || st.step }} — {{ { ok: 'erledigt', error: 'Fehler', running: 'läuft …' }[st.status] || st.status }}
          </li>
        </ul>
        <details v-if="progress?.log?.length">
          <summary class="hint cursor-pointer">Protokoll</summary>
          <pre class="rmm-pre">{{ progress.log.join('\n') }}</pre>
        </details>
      </div>
      <p v-if="error" class="db-note is-error">{{ error }}</p>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const mode = ref('builtin')
const choice = ref('builtin')
const target = ref({ host: '', port: 5432, database: 'rimperio', user: 'rimperio', password: '', sslmode: '' })
const migrate = ref(true)
const tested = ref(null)
const busy = ref(false)
const running = ref(false)
const progress = ref(null)
const error = ref('')
let timer = null

const STEP_LABELS = {
  backup: 'Einstellungen sichern', stop: 'RIMPERIO anhalten', copy: 'Daten übernehmen',
  schema: 'Grundschema anlegen', env: 'Verbindung eintragen', start: 'Neu starten', done: 'Fertig',
}

const ready = computed(() => target.value.host && target.value.user && target.value.password && target.value.database)
const progressSteps = computed(() => {
  const map = new Map()
  for (const st of progress.value?.steps || []) map.set(st.step, st)
  return [...map.values()]
})
const progressTitle = computed(() => {
  if (!progress.value?.done) return 'Umstellung läuft — RIMPERIO ist dabei kurz nicht erreichbar.'
  return progress.value.ok ? 'Umstellung abgeschlossen.' : 'Umstellung fehlgeschlagen — siehe Protokoll.'
})

watch(target, () => { tested.value = null }, { deep: true })

async function loadMode() {
  try {
    const s = await auth.api('/api/database/status')
    mode.value = s.target?.external ? 'external' : 'builtin'
    if (!running.value) choice.value = mode.value
  } catch { /* Status kommt beim nächsten Mal */ }
}

async function testTarget() {
  busy.value = true
  error.value = ''
  try {
    tested.value = await auth.api('/api/database/external/test', { method: 'POST', body: target.value })
  } catch (e) {
    tested.value = { ok: false, message: e.message }
  } finally {
    busy.value = false
  }
}

async function switchTo(external) {
  const text = external
    ? `RIMPERIO auf ${target.value.host} umziehen${migrate.value ? ' und alle Daten übernehmen' : ''}? RIMPERIO ist dabei einige Minuten nicht erreichbar.`
    : 'Zurück auf die eingebaute Datenbank wechseln? RIMPERIO startet dabei neu.'
  if (!confirm(text)) return
  busy.value = true
  error.value = ''
  try {
    await auth.api('/api/database/switch', { method: 'POST', body: {
      target: external ? target.value : null, migrate: migrate.value, confirm: true } })
    running.value = true
    progress.value = { steps: [], log: [] }
    poll()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

function poll() {
  clearTimeout(timer)
  timer = setTimeout(async () => {
    try {
      progress.value = await auth.api('/api/database/switch/status')
      if (progress.value.done) {
        running.value = false
        await loadMode()
        return
      }
    } catch {
      // Backend startet gerade neu - einfach weiter fragen.
    }
    poll()
  }, 3000)
}

onMounted(loadMode)
onBeforeUnmount(() => clearTimeout(timer))
</script>

<style scoped>
.db-choice { display: grid; gap: .5rem; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); }
.db-option { display: flex; gap: .6rem; align-items: flex-start; padding: .75rem; border: 1px solid var(--color-border); border-radius: var(--radius); cursor: pointer; }
.db-option.is-active { border-color: var(--color-primary); background: var(--primary-soft); }
.db-note { padding: .5rem .75rem; border-radius: var(--radius); }
.db-note.is-ok { background: var(--success-soft); color: var(--color-success); }
.db-note.is-error { background: var(--danger-soft); color: var(--color-danger); }
.db-progress ul { margin: .25rem 0 .5rem; padding-left: 1.1rem; list-style: disc; }
.db-progress li.is-error { color: var(--color-danger); }
.db-progress li.is-ok { color: var(--color-success); }
</style>
