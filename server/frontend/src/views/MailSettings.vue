<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - System -> Mail: Postfix vollstaendig aus dem Browser.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">{{ t('mail.title') }}</h1>
        <p class="page-subtitle">{{ t('mail.intro') }}</p>
      </div>
    </div>

    <p v-if="error" class="alert alert-danger">{{ error }}</p>
    <p v-if="message" class="alert alert-success">{{ message }}</p>

    <section class="card">
      <div class="card-header">
        <h2 class="card-title">{{ t('mail.mode') }}</h2>
      </div>
      <div class="card-body space-y-4">
        <div class="mode-grid">
          <label
            v-for="option in modes"
            :key="option.id"
            class="mode"
            :class="{ 'is-picked': config.mode === option.id }"
          >
            <input v-model="config.mode" type="radio" :value="option.id">
            <span>
              <strong>{{ locale === 'en' ? option.label_en : option.label }}</strong>
              <em>{{ option.hint }}</em>
            </span>
          </label>
        </div>

        <template v-if="config.mode !== 'off'">
          <div class="grid gap-3 sm:grid-cols-2">
            <div>
              <label class="label">{{ t('mail.fromAddress') }}</label>
              <input v-model="config.from_address" type="email" class="input" placeholder="rimperio@example.com">
            </div>
            <div>
              <label class="label">{{ t('mail.fromName') }}</label>
              <input v-model="config.from_name" type="text" class="input">
            </div>
          </div>

          <!-- Nur beim eigenen Mailserver: dort gibt es Host, Port und Anmeldung.
               Beim Container legt das Backend diese Werte selbst fest. -->
          <template v-if="config.mode === 'relay'">
            <div class="grid gap-3 sm:grid-cols-3">
              <div class="sm:col-span-2">
                <label class="label">{{ t('mail.smtpHost') }}</label>
                <input v-model="config.smtp_host" type="text" class="input" placeholder="mail.example.com">
              </div>
              <div>
                <label class="label">{{ t('mail.smtpPort') }}</label>
                <input v-model.number="config.smtp_port" type="number" min="1" max="65535" class="input">
              </div>
            </div>

            <div class="grid gap-3 sm:grid-cols-2">
              <div>
                <label class="label">{{ t('mail.smtpUser') }}</label>
                <input v-model="config.smtp_user" type="text" class="input" autocomplete="off">
              </div>
              <div>
                <label class="label">{{ t('mail.smtpPassword') }}</label>
                <input
                  v-model="smtpPassword"
                  type="password"
                  class="input"
                  autocomplete="new-password"
                  :placeholder="config.smtp_password_set ? '•••••••• (unverändert)' : ''"
                >
              </div>
            </div>

            <div class="grid gap-3 sm:grid-cols-2">
              <div>
                <label class="label">{{ t('mail.encryption') }}</label>
                <select v-model="config.encryption" class="select">
                  <option v-for="option in encryptions" :key="option.id" :value="option.id">
                    {{ option.label }}
                  </option>
                </select>
              </div>
              <div class="flex items-end">
                <label class="toggle">
                  <input v-model="config.verify_certificate" type="checkbox">
                  <span>{{ t('mail.verifyCertificate') }}</span>
                </label>
              </div>
            </div>
          </template>

          <!-- Nur beim Container: Smarthost. Die meisten Anschlüsse dürfen nicht
               selbst zustellen - dann übergibt Postfix an den Provider. -->
          <template v-if="config.mode === 'container'">
            <div class="grid gap-3 sm:grid-cols-3">
              <div>
                <label class="label">Eigener Hostname</label>
                <input v-model="config.myhostname" type="text" class="input" placeholder="rimperio.example.com">
              </div>
              <div>
                <label class="label">Smarthost <span class="hint">({{ t('common.optional') }})</span></label>
                <input v-model="config.relay_host" type="text" class="input" placeholder="smtp.provider.de">
              </div>
              <div>
                <label class="label">Smarthost-Port</label>
                <input v-model.number="config.relay_port" type="number" min="1" max="65535" class="input">
              </div>
            </div>
            <div v-if="config.relay_host" class="grid gap-3 sm:grid-cols-2">
              <div>
                <label class="label">Smarthost-Benutzer</label>
                <input v-model="config.relay_user" type="text" class="input" autocomplete="off">
              </div>
              <div>
                <label class="label">Smarthost-Passwort</label>
                <input
                  v-model="relayPassword"
                  type="password"
                  class="input"
                  autocomplete="new-password"
                  :placeholder="config.relay_password_set ? '•••••••• (unverändert)' : ''"
                >
              </div>
            </div>
            <p class="hint">
              Braucht den Zusatzdienst „Postfix“ unter
              <router-link class="link" to="/stacks">{{ t('nav.stacks') }}</router-link>.
            </p>
          </template>

          <div>
            <label class="label">{{ t('mail.recipients') }}</label>
            <input
              v-model="recipientsText"
              type="text"
              class="input"
              placeholder="admin@example.com, technik@example.com"
            >
          </div>

          <!-- Die Haken "Benachrichtigen bei ..." standen frueher hier - und
               bewirkten nichts (es gab keinen Aufrufer). Was wann gemeldet
               wird, steht jetzt fuer alle Wege gemeinsam auf einer Seite. -->
          <p class="hint">
            Wann RIMPERIO eine Mail (oder Telegram/Webhook) schickt, stellst du unter
            <router-link class="link" to="/alerts">{{ t('nav.alerts') }}</router-link> ein.
          </p>
        </template>

        <div class="flex flex-wrap gap-2">
          <button class="btn btn-primary" :disabled="saving" @click="save">
            {{ saving ? t('common.saving') : t('common.save') }}
          </button>
          <button class="btn btn-secondary" :disabled="testing || config.mode === 'off'" @click="test">
            {{ testing ? t('common.loading') : t('mail.sendTest') }}
          </button>
          <button v-if="config.mode === 'container'" class="btn btn-ghost" @click="loadPreview">
            {{ t('mail.preview') }}
          </button>
        </div>

        <div v-if="preview" class="preview-box">
          <p class="hint">{{ preview.path }} — {{ preview.note }}</p>
          <pre class="preview-text">{{ preview.content }}</pre>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useI18n } from '../i18n'

const auth = useAuthStore()
const { t, locale } = useI18n()

const config = ref({ mode: 'off', notify_on: {}, default_recipients: [] })
const modes = ref([])
const encryptions = ref([])
const events = ref([])
const smtpPassword = ref('')
const relayPassword = ref('')
const preview = ref(null)

const saving = ref(false)
const testing = ref(false)
const error = ref('')
const message = ref('')

const recipientsText = computed({
  get: () => (config.value.default_recipients || []).join(', '),
  set: (value) => {
    config.value.default_recipients = value
      .split(/[,;\s]+/)
      .map(entry => entry.trim())
      .filter(Boolean)
  },
})

async function load() {
  try {
    const data = await auth.api('/api/mail/config')
    config.value = data.config
    config.value.notify_on = config.value.notify_on || {}
    modes.value = data.modes
    encryptions.value = data.encryptions
    events.value = data.events
  } catch (err) {
    error.value = err.message
  }
}

async function save() {
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    const body = { ...config.value }
    // Leere Passwortfelder heißen "unverändert" - nur Getipptes mitschicken.
    if (smtpPassword.value) body.smtp_password = smtpPassword.value
    if (relayPassword.value) body.relay_password = relayPassword.value
    delete body.smtp_password_set
    delete body.relay_password_set
    delete body.postfix_file_written

    const data = await auth.api('/api/mail/config', { method: 'PUT', body })
    config.value = data.config
    config.value.notify_on = config.value.notify_on || {}
    smtpPassword.value = ''
    relayPassword.value = ''
    message.value = data.config.postfix_file_written?.written
      ? `${t('common.success')} — ${data.config.postfix_file_written.note}`
      : t('common.success')
    if (data.config.postfix_file_written && !data.config.postfix_file_written.written) {
      error.value = data.config.postfix_file_written.reason
    }
  } catch (err) {
    error.value = err.message
  } finally {
    saving.value = false
  }
}

async function test() {
  testing.value = true
  error.value = ''
  message.value = ''
  try {
    const result = await auth.api('/api/mail/test', { method: 'POST', body: { recipient: '' } })
    message.value = `${result.recipients.join(', ')} — ${result.via}`
  } catch (err) {
    error.value = err.message
  } finally {
    testing.value = false
  }
}

async function loadPreview() {
  try {
    preview.value = await auth.api('/api/mail/preview')
  } catch (err) {
    error.value = err.message
  }
}

onMounted(load)
</script>

<style scoped>
.mode-grid {
  display: grid;
  gap: 0.5rem;
}

@media (min-width: 768px) {
  .mode-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

.mode {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.75rem;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  cursor: pointer;
  font-size: 0.8125rem;
}

.mode:hover {
  background-color: var(--hover-surface);
}

.mode.is-picked {
  border-color: var(--color-primary);
  background-color: var(--primary-soft);
}

.mode input {
  margin-top: 0.1875rem;
  flex-shrink: 0;
}

.mode span {
  display: flex;
  flex-direction: column;
  gap: 0.1875rem;
  min-width: 0;
}

.mode strong {
  color: var(--color-text-primary);
  font-weight: 600;
}

.mode em {
  font-style: normal;
  font-size: 0.6875rem;
  color: var(--color-text-muted);
}

.toggle {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8125rem;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.event-grid {
  display: grid;
  gap: 0.375rem;
}

@media (min-width: 640px) {
  .event-grid {
    grid-template-columns: 1fr 1fr;
  }
}

.preview-box {
  padding: 0.75rem;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background-color: var(--color-surface-elevated);
}

.preview-text {
  margin-top: 0.5rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.6875rem;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 20rem;
  overflow: auto;
  color: var(--color-text-secondary);
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
