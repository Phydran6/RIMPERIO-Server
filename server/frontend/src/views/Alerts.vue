<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - System -> Benachrichtigungen: Mail, Telegram oder
                   Webhook (n8n), wenn RIMPERIO selbst nicht mehr sammelt.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Benachrichtigungen</h1>
        <p class="page-subtitle">
          RIMPERIO meldet sich selbst, wenn etwas nicht stimmt – Datenbank weg, Platte voll,
          keine Logs mehr, ein Gerät flutet. Per Mail, Telegram oder Webhook (z.&nbsp;B. an n8n).
        </p>
      </div>
      <button class="btn btn-secondary btn-sm" :disabled="checking" @click="checkNow">
        {{ checking ? 'Prüfe…' : 'Jetzt prüfen' }}
      </button>
    </div>

    <p v-if="error" class="alert alert-danger">{{ error }}</p>
    <p v-if="message" class="alert alert-success">{{ message }}</p>

    <!-- ================================================================
         ZUSTAND: sammelt RIMPERIO gerade?
         ================================================================ -->
    <section class="card mb-4">
      <div class="card-header">
        <h2 class="card-title">Zustand</h2>
        <span class="badge" :class="problems.length ? 'badge-warning' : 'badge-success'">
          {{ problems.length ? 'Achtung' : 'sammelt' }}
        </span>
      </div>
      <div class="card-body state-grid">
        <div class="state">
          <span>Datenbank</span>
          <strong :class="state.database === false ? 'is-bad' : 'is-good'">
            {{ state.database === null || state.database === undefined ? 'noch nicht geprüft'
              : (state.database ? 'erreichbar' : 'NICHT erreichbar') }}
          </strong>
        </div>
        <div class="state">
          <span>Letzter Log</span>
          <strong :class="state.silent ? 'is-bad' : ''">{{ formatTime(state.last_log_at) }}</strong>
        </div>
        <div class="state">
          <span>Platte</span>
          <strong :class="disk.percent >= disk.ingest_stop ? 'is-bad' : ''">
            {{ disk.percent ?? '–' }} %
            <template v-if="disk.percent >= disk.ingest_stop"> – Annahme gestoppt</template>
          </strong>
        </div>
        <div class="state">
          <span>Notreserve</span>
          <strong>{{ disk.reserve_present ? 'vorhanden' : 'nicht angelegt' }}</strong>
        </div>
        <div v-if="Object.keys(state.floods || {}).length" class="state state-wide">
          <span>Flutet gerade</span>
          <strong class="is-bad">
            {{ Object.entries(state.floods).map(([h, r]) => `${h} (${r.toLocaleString('de-DE')}/min)`).join(', ') }}
          </strong>
        </div>
        <div v-if="(state.offline || []).length" class="state state-wide">
          <span>Verstummt</span>
          <strong>{{ state.offline.join(', ') }}</strong>
        </div>
        <p class="hint state-wide">
          Für externe Überwachung (Uptime Kuma o.&nbsp;ä.):
          <code class="font-mono">/api/health/ready</code> liefert 200, wenn RIMPERIO sammelt, sonst 503.
        </p>
      </div>
    </section>

    <!-- ================================================================
         WEGE
         ================================================================ -->
    <div class="grid gap-4 lg:grid-cols-3">
      <!-- Mail -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">Mail</h2>
          <label class="toggle"><input v-model="config.channels.mail.enabled" type="checkbox"> an</label>
        </div>
        <div class="card-body space-y-3">
          <p class="hint">
            Geht über den Mailversand unter
            <router-link class="link" to="/mail">System → Mail</router-link>.
          </p>
          <div>
            <label class="label">Empfänger (leer = Standard-Empfänger)</label>
            <input
              v-model="mailRecipients"
              type="text"
              class="input"
              :placeholder="mailDefault || 'admin@example.com'"
            >
          </div>
          <button class="btn btn-secondary btn-sm" :disabled="testing === 'mail'" @click="test('mail')">
            {{ testing === 'mail' ? 'Sende…' : 'Probemeldung' }}
          </button>
        </div>
      </section>

      <!-- Telegram -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">Telegram</h2>
          <label class="toggle"><input v-model="config.channels.telegram.enabled" type="checkbox"> an</label>
        </div>
        <div class="card-body space-y-3">
          <p class="hint">
            Direkt aus RIMPERIO, ohne Zusatzdienst. Bot bei <code>@BotFather</code> anlegen,
            Bot in den Chat holen, Chat-ID eintragen.
          </p>
          <div>
            <label class="label">Bot-Token</label>
            <input
              v-model="telegramToken"
              type="password"
              class="input"
              autocomplete="new-password"
              :placeholder="config.channels.telegram.bot_token_set ? '•••••••• (unverändert)' : '123456:ABC…'"
            >
          </div>
          <div>
            <label class="label">Chat-ID</label>
            <input v-model="config.channels.telegram.chat_id" type="text" class="input" placeholder="-1001234567890">
          </div>
          <button class="btn btn-secondary btn-sm" :disabled="testing === 'telegram'" @click="test('telegram')">
            {{ testing === 'telegram' ? 'Sende…' : 'Probemeldung' }}
          </button>
        </div>
      </section>

      <!-- Webhook / n8n -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">Webhook / n8n</h2>
          <label class="toggle"><input v-model="config.channels.webhook.enabled" type="checkbox"> an</label>
        </div>
        <div class="card-body space-y-3">
          <p class="hint">
            JSON-POST an eine Adresse – z.&nbsp;B. ein n8n-Webhook-Knoten, der daraus Telegram,
            Teams, Signal oder ein Ticket macht.
          </p>
          <div>
            <label class="label">Adresse</label>
            <input v-model="config.channels.webhook.url" type="url" class="input"
                   placeholder="https://n8n.example.com/webhook/rimperio">
          </div>
          <div>
            <label class="label">Geheimnis (optional, Kopfzeile X-RIMPERIO-Secret)</label>
            <input
              v-model="webhookSecret"
              type="password"
              class="input"
              autocomplete="new-password"
              :placeholder="config.channels.webhook.secret_set ? '•••••••• (unverändert)' : ''"
            >
          </div>
          <button class="btn btn-secondary btn-sm" :disabled="testing === 'webhook'" @click="test('webhook')">
            {{ testing === 'webhook' ? 'Sende…' : 'Probemeldung' }}
          </button>
          <details class="hint">
            <summary class="cursor-pointer">So sieht die Nachricht aus</summary>
            <pre class="payload">{{ payloadExample }}</pre>
          </details>
        </div>
      </section>
    </div>

    <!-- ================================================================
         WANN
         ================================================================ -->
    <section class="card mt-4">
      <div class="card-header">
        <h2 class="card-title">Wann melden?</h2>
      </div>
      <div class="card-body space-y-4">
        <div class="event-grid">
          <label v-for="(spec, key) in events" :key="key" class="event">
            <input v-model="config.events[key]" type="checkbox">
            <span>
              <strong>{{ spec.label }}</strong>
              <em>{{ spec.hint }}</em>
            </span>
          </label>
        </div>

        <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label class="label">Funkstille ab (Minuten, 0 = aus)</label>
            <input v-model.number="config.silence_minutes" type="number" min="0" class="input">
          </div>
          <div>
            <label class="label">Flut ab (Logs pro Minute je Gerät, 0 = aus)</label>
            <input v-model.number="config.flood_per_minute" type="number" min="0" class="input">
          </div>
          <div>
            <label class="label">Gerät verstummt nach (Minuten)</label>
            <input v-model.number="config.agent_offline_minutes" type="number" min="5" class="input">
          </div>
          <div>
            <label class="label">Dieselbe Meldung höchstens alle (Minuten)</label>
            <input v-model.number="config.cooldown_minutes" type="number" min="1" class="input">
          </div>
        </div>

        <p class="hint">
          Die Meldungen funktionieren auch, wenn die Datenbank nicht mehr läuft – die
          Einstellungen liegen zusätzlich als Datei auf dem Server.
        </p>

        <button class="btn btn-primary" :disabled="saving" @click="save">
          {{ saving ? 'Speichere…' : 'Speichern' }}
        </button>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()

const config = ref({
  channels: {
    mail: { enabled: false, recipients: [] },
    telegram: { enabled: false, chat_id: '', bot_token_set: false },
    webhook: { enabled: false, url: '', secret_set: false },
  },
  events: {},
  silence_minutes: 60,
  flood_per_minute: 2000,
  agent_offline_minutes: 30,
  cooldown_minutes: 60,
})
const events = ref({})
const state = ref({})
const disk = ref({})
const mailDefault = ref('')

const telegramToken = ref('')
const webhookSecret = ref('')

const saving = ref(false)
const checking = ref(false)
const testing = ref('')
const error = ref('')
const message = ref('')

const problems = computed(() => {
  const found = []
  if (state.value.database === false) found.push('database')
  if (state.value.silent) found.push('silence')
  if (Object.keys(state.value.floods || {}).length) found.push('flood')
  if (disk.value.percent >= disk.value.ingest_stop) found.push('disk')
  return found
})

const mailRecipients = computed({
  get: () => (config.value.channels.mail.recipients || []).join(', '),
  set: (value) => {
    config.value.channels.mail.recipients = value
      .split(/[,;\s]+/)
      .map(entry => entry.trim())
      .filter(Boolean)
  },
})

const payloadExample = JSON.stringify({
  source: 'rimperio',
  event: 'db_down',
  level: 'critical',
  title: 'Datenbank nicht erreichbar – es wird NICHTS gespeichert',
  text: 'Seit 10.10.2026 19:03 UTC antwortet die Datenbank nicht. …',
  server: 'rimperio.example.com',
  version: '2026.10.10.22.00.00',
  time: '2026-10-10 19:05:00',
  detail: {},
}, null, 2)

function apply(data) {
  config.value = data.config
  if (data.events) events.value = data.events
  if (data.state) state.value = data.state
  if (data.disk) disk.value = data.disk
  if (data.mail_recipients) mailDefault.value = data.mail_recipients.join(', ')
}

async function load() {
  try {
    apply(await auth.api('/api/alerts/config'))
  } catch (err) {
    error.value = err.message
  }
}

async function save() {
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    const channels = JSON.parse(JSON.stringify(config.value.channels))
    // Leere Geheimnis-Felder heißen "unverändert" - nur Getipptes mitschicken.
    if (telegramToken.value) channels.telegram.bot_token = telegramToken.value
    if (webhookSecret.value) channels.webhook.secret = webhookSecret.value
    const body = {
      channels,
      events: config.value.events,
      silence_minutes: config.value.silence_minutes,
      flood_per_minute: config.value.flood_per_minute,
      agent_offline_minutes: config.value.agent_offline_minutes,
      cooldown_minutes: config.value.cooldown_minutes,
    }
    const data = await auth.api('/api/alerts/config', { method: 'PUT', body })
    config.value = data.config
    telegramToken.value = ''
    webhookSecret.value = ''
    message.value = 'Gespeichert.'
  } catch (err) {
    error.value = err.message
  } finally {
    saving.value = false
  }
}

async function test(channel) {
  testing.value = channel
  error.value = ''
  message.value = ''
  try {
    // Erst speichern: die Probe geht mit dem gespeicherten Stand raus.
    await save()
    if (error.value) return
    await auth.api(`/api/alerts/test/${channel}`, { method: 'POST' })
    message.value = 'Probemeldung verschickt – bitte nachsehen, ob sie angekommen ist.'
  } catch (err) {
    error.value = err.message
  } finally {
    testing.value = ''
  }
}

async function checkNow() {
  checking.value = true
  try {
    const data = await auth.api('/api/alerts/check', { method: 'POST' })
    state.value = data.state
    await load()
  } catch (err) {
    error.value = err.message
  } finally {
    checking.value = false
  }
}

function formatTime(value) {
  if (!value) return '–'
  const date = new Date(value.endsWith('Z') || value.includes('+') ? value : value + 'Z')
  return isNaN(date.getTime()) ? value : date.toLocaleString('de-DE')
}

onMounted(load)
</script>

<style scoped>
.state-grid {
  display: grid;
  gap: 0.75rem;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

@media (min-width: 1024px) {
  .state-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

.state {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
  min-width: 0;
}

.state-wide {
  grid-column: 1 / -1;
}

.state span {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.state strong {
  font-size: 0.875rem;
  color: var(--color-text-primary);
  overflow-wrap: anywhere;
}

.is-good { color: var(--color-success) !important; }
.is-bad { color: var(--color-danger) !important; }

.toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.8125rem;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.event-grid {
  display: grid;
  gap: 0.5rem;
}

@media (min-width: 768px) {
  .event-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

.event {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.625rem 0.75rem;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  cursor: pointer;
  font-size: 0.8125rem;
}

.event:hover {
  background-color: var(--hover-surface);
}

.event input {
  margin-top: 0.1875rem;
  flex-shrink: 0;
}

.event span {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
  min-width: 0;
}

.event strong {
  color: var(--color-text-primary);
  font-weight: 600;
}

.event em {
  font-style: normal;
  font-size: 0.6875rem;
  color: var(--color-text-muted);
}

.payload {
  margin-top: 0.5rem;
  padding: 0.5rem;
  border-radius: var(--radius);
  background-color: var(--color-surface-elevated);
  border: 1px solid var(--color-border);
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  white-space: pre-wrap;
  word-break: break-word;
}

.hint {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.link {
  color: var(--color-primary);
  text-decoration: underline;
}

.alert {
  padding: 0.625rem 0.875rem;
  border-radius: var(--radius);
  font-size: 0.8125rem;
  margin-bottom: 0.75rem;
  border: 1px solid var(--color-border);
}

.alert-danger {
  background-color: var(--danger-soft);
  color: var(--color-danger);
  border-color: var(--color-danger);
}

.alert-success {
  background-color: var(--primary-soft);
  color: var(--color-success);
}
</style>
