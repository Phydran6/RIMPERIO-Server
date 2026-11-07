<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Einstellungen -> E-Mail: Anbieter waehlen, Assistent erledigt den Rest.

     Oben steht, was gerade gilt (mit Probemail), darunter die Empfaenger und
     dann je Anbieter eine Kachel. Ein Klick oeffnet den passenden Assistenten
     (components/MailWizard.vue) - bei Microsoft 365 mit Admin-Anmeldung und
     automatisch angelegter App-Registrierung, bei den anderen mit vorbelegtem
     Server und den Schritten, die beim Anbieter vorher noetig sind.
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

    <!-- ============================================ Aktueller Stand ====== -->
    <section class="card">
      <div class="card-header">
        <h2 class="card-title">Aktueller Stand</h2>
      </div>
      <div class="card-body space-y-4">
        <p v-if="config.mode === 'off'" class="text-sm">
          Der Server verschickt derzeit <strong>keine</strong> Mails. Wähle unten deinen Anbieter —
          der Assistent erledigt den Rest.
        </p>

        <template v-else>
          <dl class="status-grid">
            <div>
              <dt>Anbieter</dt>
              <dd>{{ currentProvider?.name || config.provider || config.mode }}</dd>
            </div>
            <div>
              <dt>Absender</dt>
              <dd>{{ config.from_name }} &lt;{{ config.from_address }}&gt;</dd>
            </div>
            <div v-if="config.mode === 'm365'">
              <dt>Weg</dt>
              <dd>Microsoft Graph, App-ID {{ config.m365_client_id }}</dd>
            </div>
            <div v-else>
              <dt>Mailserver</dt>
              <dd>{{ config.smtp_host }}:{{ config.smtp_port }}</dd>
            </div>
            <div v-if="config.mode === 'm365' && config.m365_secret_expires">
              <dt>Geheimnis gültig bis</dt>
              <dd :class="secretClass">{{ config.m365_secret_expires.slice(0, 10) }}
                <span v-if="secretDays !== null && secretDays < 30">
                  ({{ secretDays < 0 ? 'abgelaufen' : `noch ${secretDays} Tage` }} — Assistent erneut durchlaufen)
                </span>
              </dd>
            </div>
            <div v-if="config.mode === 'm365' && config.m365_setup_by">
              <dt>Eingerichtet von</dt>
              <dd>{{ config.m365_setup_by }}</dd>
            </div>
          </dl>

          <details v-if="config.mode === 'm365' && restrictCommand" class="restrict">
            <summary>Empfohlen: die App auf das Absenderpostfach begrenzen</summary>
            <p class="hint mt-2">Das Anwendungsrecht Mail.Send darf ab Werk von jedem Postfach im
              Mandanten senden. Begrenzen lässt sich das nur in Exchange Online — einmal in PowerShell
              (Modul ExchangeOnlineManagement) ausführen:</p>
            <pre class="preview-text">{{ restrictCommand }}</pre>
          </details>

          <div class="flex flex-wrap items-end gap-2">
            <div class="grow test-field">
              <label class="label">Probemail an</label>
              <input v-model.trim="testRecipient" type="email" class="input"
                     :placeholder="recipientsText || 'du@example.com'">
            </div>
            <button class="btn btn-secondary" :disabled="testing" @click="test">
              {{ testing ? t('common.loading') : t('mail.sendTest') }}
            </button>
            <button v-if="currentProvider" class="btn btn-ghost" @click="openWizard(currentProvider)">
              Ändern
            </button>
            <button v-if="config.mode === 'container'" class="btn btn-ghost" @click="loadPreview">
              {{ t('mail.preview') }}
            </button>
            <button class="btn btn-ghost" :disabled="saving" @click="turnOff">Ausschalten</button>
          </div>

          <div v-if="preview" class="preview-box">
            <p class="hint">{{ preview.path }} — {{ preview.note }}</p>
            <pre class="preview-text">{{ preview.content }}</pre>
          </div>

          <p v-if="config.mode === 'm365'" class="hint">
            Beim Ausschalten oder Wechseln bleibt die App-Registrierung in Entra ID bestehen. Wer sie
            nicht mehr braucht, löscht sie dort unter App-Registrierungen.
          </p>
        </template>
      </div>
    </section>

    <!-- ============================================ Empfänger ============ -->
    <section v-if="config.mode !== 'off'" class="card">
      <div class="card-header">
        <h2 class="card-title">{{ t('mail.recipients') }}</h2>
      </div>
      <div class="card-body space-y-3">
        <input v-model="recipientsText" type="text" class="input"
               placeholder="admin@example.com, technik@example.com">
        <p class="hint">
          Wann RIMPERIO eine Mail (oder Telegram/Webhook) schickt, stellst du unter
          <router-link class="link" to="/alerts">{{ t('nav.alerts') }}</router-link> ein.
        </p>
        <button class="btn btn-primary" :disabled="saving" @click="saveRecipients">
          {{ saving ? t('common.saving') : t('common.save') }}
        </button>
      </div>
    </section>

    <!-- ============================================ Anbieter ============= -->
    <section class="card">
      <div class="card-header">
        <h2 class="card-title">{{ config.mode === 'off' ? 'Einrichten' : 'Anderen Anbieter einrichten' }}</h2>
      </div>
      <div class="card-body">
        <div class="provider-grid">
          <button
            v-for="provider in providers"
            :key="provider.id"
            type="button"
            class="provider"
            :class="{ 'is-current': config.mode !== 'off' && config.provider === provider.id,
                      'is-featured': provider.kind === 'm365' }"
            @click="openWizard(provider)"
          >
            <span class="provider-mark" aria-hidden="true">{{ monogram(provider) }}</span>
            <span class="provider-text">
              <strong>{{ provider.name }}</strong>
              <em>{{ provider.hint }}</em>
              <span class="badge-line">
                <span class="provider-badge">{{ provider.badge }}</span>
                <span v-if="config.mode !== 'off' && config.provider === provider.id"
                      class="provider-badge is-active">aktiv</span>
              </span>
            </span>
          </button>
        </div>
      </div>
    </section>

    <MailWizard
      v-if="wizardProvider"
      :provider="wizardProvider"
      :config="config"
      :default-recipient="config.default_recipients?.[0] || ''"
      @close="wizardProvider = null"
      @saved="onSaved"
    />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import MailWizard from '../components/MailWizard.vue'
import { useAuthStore } from '../stores/auth'
import { useI18n } from '../i18n'

const auth = useAuthStore()
const { t } = useI18n()

const config = ref({ mode: 'off', default_recipients: [] })
const providers = ref([])
const restrictCommand = ref('')
const preview = ref(null)
const wizardProvider = ref(null)
const testRecipient = ref('')

const saving = ref(false)
const testing = ref(false)
const error = ref('')
const message = ref('')

const currentProvider = computed(() => providers.value.find(item => item.id === config.value.provider))

const secretDays = computed(() => {
  const expires = config.value.m365_secret_expires
  if (!expires) return null
  return Math.floor((new Date(expires) - new Date()) / 86400000)
})
const secretClass = computed(() => {
  if (secretDays.value === null) return ''
  if (secretDays.value < 0) return 'is-bad'
  return secretDays.value < 30 ? 'is-warn' : ''
})

const recipientsText = computed({
  get: () => (config.value.default_recipients || []).join(', '),
  set: (value) => {
    config.value.default_recipients = value
      .split(/[,;\s]+/)
      .map(entry => entry.trim())
      .filter(Boolean)
  },
})

function monogram(provider) {
  const special = { m365: 'M', google: 'G', webde: 'W', tonline: 'T', mailboxorg: 'mb', smtp: '@', container: 'P' }
  return special[provider.id] || provider.name.slice(0, 2)
}

function openWizard(provider) {
  error.value = ''
  message.value = ''
  wizardProvider.value = provider
}

async function load() {
  try {
    const data = await auth.api('/api/mail/config')
    config.value = data.config
    providers.value = data.providers || []
    restrictCommand.value = data.m365_restrict_command || ''
  } catch (err) {
    error.value = err.message
  }
}

async function onSaved() {
  preview.value = null
  await load()
}

async function saveRecipients() {
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    const data = await auth.api('/api/mail/config', {
      method: 'PUT', body: { default_recipients: config.value.default_recipients },
    })
    config.value = data.config
    message.value = t('common.success')
  } catch (err) {
    error.value = err.message
  } finally {
    saving.value = false
  }
}

async function turnOff() {
  if (!window.confirm('Mailversand ausschalten? Die Zugangsdaten bleiben gespeichert.')) return
  saving.value = true
  error.value = ''
  try {
    const data = await auth.api('/api/mail/config', { method: 'PUT', body: { mode: 'off' } })
    config.value = data.config
    message.value = 'Der Mailversand ist ausgeschaltet.'
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
    const result = await auth.api('/api/mail/test', {
      method: 'POST', body: { recipient: testRecipient.value },
    })
    message.value = `Verschickt an ${result.recipients.join(', ')} über ${result.via}.`
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
.status-grid {
  display: grid;
  gap: 0.75rem 1.5rem;
  grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr));
  margin: 0;
}

.status-grid dt {
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--color-text-muted);
}

.status-grid dd {
  margin: 0;
  font-size: 0.875rem;
  font-weight: 500;
  overflow-wrap: anywhere;
}

.test-field {
  min-width: 14rem;
}

.restrict summary {
  cursor: pointer;
  font-size: 0.8125rem;
  color: var(--color-primary);
}

.provider-grid {
  display: grid;
  gap: 0.625rem;
  grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr));
}

.provider {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.875rem;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: none;
  text-align: left;
  cursor: pointer;
  color: inherit;
  font: inherit;
}

.provider:hover {
  background-color: var(--hover-surface);
  border-color: var(--color-primary);
}

.provider.is-featured {
  grid-column: span 2;
}

@media (max-width: 640px) {
  .provider.is-featured {
    grid-column: auto;
  }
}

.provider.is-current {
  border-color: var(--color-primary);
  background-color: var(--primary-soft);
}

.provider-mark {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2.25rem;
  height: 2.25rem;
  flex-shrink: 0;
  border-radius: var(--radius);
  background-color: var(--color-surface-elevated);
  border: 1px solid var(--color-border);
  font-weight: 700;
  font-size: 0.875rem;
  color: var(--color-primary);
}

.provider-text {
  display: flex;
  flex-direction: column;
  gap: 0.1875rem;
  min-width: 0;
}

.provider-text strong {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.provider-text em {
  font-style: normal;
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.badge-line {
  display: flex;
  gap: 0.375rem;
  margin-top: 0.25rem;
}

.provider-badge {
  font-size: 0.625rem;
  padding: 0.0625rem 0.5rem;
  border-radius: 9999px;
  border: 1px solid var(--color-border);
  color: var(--color-text-secondary);
}

.provider-badge.is-active {
  border-color: var(--color-success);
  color: var(--color-success);
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

.is-warn { color: var(--color-warning); }
.is-bad { color: var(--color-danger); }

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
