<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - RIMPERIO-Server -> Zusatzdienste: Portainer, Watchtower, n8n, Postfix.

     Die Zugangsdaten holt die Seite bewusst erst auf Klick nach: sonst stuende
     bei jedem Seitenaufruf ein Passwort in der Antwort - auch dann, wenn es
     niemand sehen will.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">{{ t('stacks.title') }}</h1>
        <p class="page-subtitle">{{ t('stacks.intro') }}</p>
      </div>
      <button class="btn btn-ghost btn-sm" @click="load">
        <AppIcon name="refresh" :size="16" />
        {{ t('common.refresh') }}
      </button>
    </div>

    <p v-if="error" class="alert alert-danger">{{ error }}</p>
    <p v-if="message" class="alert alert-success">{{ message }}</p>

    <p v-if="overview && !overview.available" class="alert alert-warning">
      {{ overview.reason }}
    </p>

    <div v-if="capacity" class="alert" :class="capacity.verdict === 'ok' ? 'alert-success' : 'alert-warning'">
      <strong>{{ capacity.message }}</strong>
      <ul v-if="capacity.problems.length" class="capacity-list">
        <li v-for="(problem, index) in capacity.problems" :key="index">{{ problem }}</li>
      </ul>
    </div>

    <div class="stack-grid">
      <section v-for="stack in stacks" :key="stack.id" class="card stack-card">
        <div class="card-header">
          <div class="min-w-0">
            <h2 class="card-title">{{ stack.label }}</h2>
            <p class="hint">{{ stack.hint }}</p>
          </div>
          <span class="badge" :class="stack.running ? 'badge-success' : 'badge-neutral'">
            {{ stack.running ? t('common.online') : stack.state }}
          </span>
        </div>

        <div class="card-body space-y-3">
          <p v-if="stack.warning" class="stack-warning">{{ stack.warning }}</p>

          <p class="hint">
            Bedarf: ~{{ stack.requirements.ram_mb }} MB RAM,
            ~{{ Math.round(stack.requirements.disk_mb / 1024 * 10) / 10 }} GB Platte
          </p>

          <div class="flex flex-wrap gap-2">
            <button
              class="btn btn-sm"
              :class="stack.enabled ? 'btn-secondary' : 'btn-primary'"
              :disabled="busy === stack.id || !overview?.available"
              @click="askToggle(stack)"
            >
              {{ stack.enabled ? t('common.stop') : t('common.start') }}
            </button>

            <a
              v-if="stack.url && stack.running"
              class="btn btn-ghost btn-sm"
              :href="stack.url"
              target="_blank"
              rel="noopener"
            >{{ t('stacks.openService') }}</a>

            <button
              v-if="stack.has_credentials"
              class="btn btn-ghost btn-sm"
              @click="showCredentials(stack)"
            >
              <AppIcon name="key" :size="15" />
              {{ t('stacks.showCredentials') }}
            </button>

            <button
              v-if="stack.enabled"
              class="btn btn-ghost btn-sm"
              :disabled="busy === stack.id"
              @click="restart(stack)"
            >{{ t('common.restart') }}</button>

            <button
              v-if="stack.enabled"
              class="btn btn-ghost btn-sm"
              @click="showLogs(stack)"
            >{{ t('stacks.logs') }}</button>
          </div>

          <!-- Zugangsdaten, erst nach Klick geholt -->
          <div v-if="credentials[stack.id]" class="cred-box">
            <div class="cred-row">
              <span class="cred-key">Benutzer</span>
              <code>{{ credentials[stack.id].user || '—' }}</code>
            </div>
            <div class="cred-row">
              <span class="cred-key">Passwort</span>
              <code>{{ revealed[stack.id] ? credentials[stack.id].password : '••••••••••••' }}</code>
              <button class="btn btn-ghost btn-sm" @click="revealed[stack.id] = !revealed[stack.id]">
                {{ revealed[stack.id] ? t('common.hide') : t('common.show') }}
              </button>
              <button class="btn btn-ghost btn-sm" @click="copy(credentials[stack.id].password)">
                {{ t('common.copy') }}
              </button>
            </div>
            <p class="hint">{{ credentials[stack.id].note }}</p>
            <button class="btn btn-ghost btn-sm" @click="rotate(stack)">
              {{ t('stacks.newPassword') }}
            </button>
          </div>

          <pre v-if="logsFor === stack.id" class="log-box">{{ logLines.join('\n') || '—' }}</pre>
        </div>
      </section>
    </div>

    <BackupPrompt
      :open="promptOpen"
      :operation="promptOperation"
      :scopes="backupScopes"
      @confirm="runToggle"
      @cancel="promptOpen = false"
    />
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useI18n } from '../i18n'
import AppIcon from '../components/AppIcon.vue'
import BackupPrompt from '../components/BackupPrompt.vue'

const auth = useAuthStore()
const { t } = useI18n()

const overview = ref(null)
const stacks = ref([])
const capacity = ref(null)
const error = ref('')
const message = ref('')
const busy = ref('')

const credentials = reactive({})
const revealed = reactive({})
const logsFor = ref('')
const logLines = ref([])

const promptOpen = ref(false)
const promptOperation = ref('')
const pending = ref(null)
const backupScopes = ref([])

async function load() {
  error.value = ''
  try {
    const data = await auth.api('/api/stacks')
    overview.value = data
    stacks.value = data.stacks || []
    if (data.available) {
      const enabled = stacks.value.filter(s => s.enabled).map(s => s.id)
      capacity.value = await auth.api('/api/stacks/capacity', {
        method: 'POST', body: { stacks: enabled },
      })
    }
  } catch (err) {
    error.value = err.message
  }
}

async function loadBackupScopes() {
  try {
    const data = await auth.api('/api/backup/overview')
    backupScopes.value = data.scopes
  } catch {
    // Ohne die Liste bietet der Dialog eben keinen Umfang an - der Server
    // nimmt dann seinen Standardumfang.
    backupScopes.value = []
  }
}

function askToggle(stack) {
  pending.value = { id: stack.id, enable: !stack.enabled }
  promptOperation.value = `${stack.label} ${stack.enabled ? t('common.stop') : t('common.start')}`
  promptOpen.value = true
}

async function runToggle(decision) {
  promptOpen.value = false
  const target = pending.value
  if (!target) return
  busy.value = target.id
  error.value = ''
  message.value = ''
  try {
    const result = await auth.api(`/api/stacks/${target.id}/toggle`, {
      method: 'POST',
      body: { enable: target.enable, confirm: 'CHANGE', backup: decision },
    })
    message.value = `${target.id}: ${result.action}`
    await load()
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = ''
    pending.value = null
  }
}

async function restart(stack) {
  busy.value = stack.id
  try {
    await auth.api(`/api/stacks/${stack.id}/restart`, { method: 'POST' })
    message.value = `${stack.label}: ${t('common.restart')}`
    await load()
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = ''
  }
}

async function showCredentials(stack) {
  try {
    credentials[stack.id] = await auth.api(`/api/stacks/${stack.id}/credentials`)
    revealed[stack.id] = false
  } catch (err) {
    error.value = err.message
  }
}

async function rotate(stack) {
  try {
    const result = await auth.api(`/api/stacks/${stack.id}/password`, {
      method: 'POST', body: { password: '' },
    })
    credentials[stack.id] = { ...credentials[stack.id], password: result.password }
    revealed[stack.id] = true
    message.value = result.note
  } catch (err) {
    error.value = err.message
  }
}

async function showLogs(stack) {
  if (logsFor.value === stack.id) {
    logsFor.value = ''
    return
  }
  try {
    const data = await auth.api(`/api/stacks/${stack.id}/logs?lines=200`)
    logLines.value = data.lines
    logsFor.value = stack.id
  } catch (err) {
    error.value = err.message
  }
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
    message.value = t('common.copied')
  } catch {
    error.value = 'Kopieren nicht möglich — der Browser hat es abgelehnt.'
  }
}

onMounted(() => {
  load()
  loadBackupScopes()
})
</script>

<style scoped>
.stack-grid {
  display: grid;
  gap: 1rem;
}

@media (min-width: 900px) {
  .stack-grid {
    grid-template-columns: 1fr 1fr;
  }
}

.stack-card {
  display: flex;
  flex-direction: column;
}

.stack-warning {
  padding: 0.5rem 0.625rem;
  border-radius: var(--radius);
  background-color: var(--primary-soft);
  color: var(--color-warning);
  font-size: 0.75rem;
  border: 1px solid var(--color-warning);
}

.cred-box {
  padding: 0.625rem 0.75rem;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background-color: var(--color-surface-elevated);
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.cred-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  font-size: 0.8125rem;
}

.cred-key {
  min-width: 5rem;
  color: var(--color-text-muted);
}

.cred-row code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.75rem;
  color: var(--color-text-primary);
  word-break: break-all;
}

.log-box {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.6875rem;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 18rem;
  overflow: auto;
  padding: 0.625rem;
  border-radius: var(--radius);
  background-color: var(--color-bg);
  color: var(--color-text-secondary);
}

.capacity-list {
  margin-top: 0.375rem;
  padding-left: 1.125rem;
  list-style: disc;
  font-size: 0.75rem;
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

.alert-warning {
  background-color: var(--primary-soft);
  color: var(--color-warning);
  border-color: var(--color-warning);
}
</style>
