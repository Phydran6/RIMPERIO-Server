<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - System -> Sicherung: anlegen, herunterladen, zurueckspielen.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">{{ t('backup.title') }}</h1>
        <p class="page-subtitle">{{ t('backup.intro') }}</p>
      </div>
      <button class="btn btn-ghost btn-sm" :disabled="loading" @click="load">
        <AppIcon name="refresh" :size="16" />
        {{ t('common.refresh') }}
      </button>
    </div>

    <p v-if="error" class="alert alert-danger">{{ error }}</p>
    <p v-if="message" class="alert alert-success">{{ message }}</p>

    <!-- ========================================================== Anlegen -->
    <section class="card">
      <div class="card-header">
        <h2 class="card-title">{{ t('backup.create') }}</h2>
      </div>
      <div class="card-body space-y-4">
        <div>
          <span class="label">{{ t('backup.scope') }}</span>
          <p class="hint mb-2">{{ t('backup.scopeHint') }}</p>
          <div class="scope-grid">
            <label v-for="scope in scopes" :key="scope.id" class="scope-item">
              <input v-model="form.scopes" type="checkbox" :value="scope.id">
              <span>
                <strong>{{ scopeLabel(scope) }}</strong>
                <em>{{ scope.hint }}</em>
              </span>
            </label>
          </div>
        </div>

        <div class="grid gap-3 sm:grid-cols-2">
          <div>
            <label class="label">{{ t('backup.note') }}</label>
            <input v-model="form.note" type="text" class="input" :placeholder="t('backup.noteHint')">
          </div>
          <div>
            <label class="label">
              <input v-model="form.encrypt" type="checkbox" class="mr-2">
              {{ t('backup.encrypt') }}
            </label>
            <input
              v-if="form.encrypt"
              v-model="form.passphrase"
              type="password"
              class="input"
              autocomplete="new-password"
              :placeholder="t('backup.passphrase')"
            >
            <p class="hint mt-1">{{ t('backup.encryptHint') }}</p>
          </div>
        </div>

        <div class="flex flex-wrap items-center gap-2">
          <button class="btn btn-primary" :disabled="creating || !form.scopes.length" @click="create">
            <AppIcon name="backup" :size="16" />
            {{ creating ? t('backup.creating') : t('backup.create') }}
          </button>
          <span v-if="storage" class="hint">
            {{ t('backup.storage') }}: {{ formatBytes(storage.backup_bytes) }}
            ({{ storage.count }}) · {{ formatBytes(storage.free_bytes) }} frei
          </span>
        </div>
      </div>
    </section>

    <!-- ===================================================== Hochladen -->
    <section class="card">
      <div class="card-header">
        <h2 class="card-title">{{ t('backup.uploadTitle') }}</h2>
      </div>
      <div class="card-body">
        <p class="hint mb-2">{{ t('backup.uploadHint') }}</p>
        <input type="file" accept=".zip" class="input" :disabled="uploading" @change="upload">
      </div>
    </section>

    <!-- ================================================ Vorhandene Liste -->
    <section class="card">
      <div class="card-header">
        <h2 class="card-title">{{ t('backup.existing') }}</h2>
        <button class="btn btn-ghost btn-sm" @click="prune">{{ t('backup.prune') }}</button>
      </div>

      <div v-if="!backups.length" class="empty-state">
        <p class="empty-state-title">{{ t('backup.empty') }}</p>
      </div>

      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>{{ t('backup.created') }}</th>
              <th>{{ t('common.version') }}</th>
              <th>{{ t('backup.scope') }}</th>
              <th>{{ t('backup.rows') }}</th>
              <th>{{ t('common.size') }}</th>
              <th>{{ t('common.actions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in backups" :key="item.name">
              <td>
                <div class="cell-main">{{ formatDate(item.created_at) }}</div>
                <div v-if="item.note" class="cell-sub">{{ item.note }}</div>
                <div v-if="item.error" class="cell-sub text-danger">{{ item.error }}</div>
              </td>
              <td>
                <span class="cell-main">{{ item.server_version || '—' }}</span>
                <span
                  v-if="item.compatibility"
                  class="badge"
                  :class="compatBadge(item.compatibility.level)"
                >{{ compatLabel(item.compatibility.level) }}</span>
              </td>
              <td>
                <span v-for="scope in item.scopes || []" :key="scope" class="badge badge-neutral scope-chip">
                  {{ shortScope(scope) }}
                </span>
              </td>
              <td class="tabular">{{ totalRows(item) }}</td>
              <td class="tabular">
                {{ formatBytes(item.size_bytes) }}
                <AppIcon v-if="item.encrypted" name="lock" :size="14" class="inline-icon" :title="t('backup.encrypted')" />
              </td>
              <td>
                <div class="flex gap-1">
                  <a class="btn btn-ghost btn-sm" :href="downloadUrl(item.name)" :title="t('common.download')">
                    <AppIcon name="download" :size="15" />
                  </a>
                  <button class="btn btn-ghost btn-sm" :disabled="!!item.error" :title="t('backup.restore')" @click="openRestore(item)">
                    <AppIcon name="restore" :size="15" />
                  </button>
                  <button class="btn btn-ghost btn-sm" :title="t('common.delete')" @click="remove(item)">
                    <AppIcon name="trash" :size="15" />
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ================================================== Zurückspielen -->
    <teleport to="body">
      <div v-if="restoreTarget" class="modal-backdrop" @click.self="restoreTarget = null">
        <div class="modal" role="dialog" aria-modal="true">
          <div class="card-header">
            <div>
              <h2 class="card-title">{{ t('backup.restore') }}</h2>
              <p class="hint mt-1">{{ restoreTarget.name }}</p>
            </div>
          </div>

          <div class="card-body space-y-4">
            <!-- Passt die Sicherung überhaupt zu diesem Server? -->
            <p
              v-if="restoreTarget.compatibility"
              class="alert"
              :class="compatAlert(restoreTarget.compatibility.level)"
            >{{ restoreTarget.compatibility.message }}</p>

            <div>
              <span class="label">{{ t('backup.restoreScope') }}</span>
              <div class="scope-grid mt-2">
                <label
                  v-for="detail in restoreTarget.scope_details || []"
                  :key="detail.id"
                  class="scope-item"
                  :class="{ 'is-unknown': !detail.known }"
                >
                  <input v-model="restoreScopes" type="checkbox" :value="detail.id" :disabled="!detail.known">
                  <span>
                    <strong>{{ locale === 'en' ? detail.label_en : detail.label }}</strong>
                    <em>{{ detail.rows }} {{ t('backup.rows') }}</em>
                  </span>
                </label>
              </div>
            </div>

            <div>
              <span class="label">{{ t('backup.restoreMode') }}</span>
              <label class="choice mt-2" :class="{ 'is-picked': restoreMode === 'replace' }">
                <input v-model="restoreMode" type="radio" value="replace">
                <span>{{ t('backup.modeReplace') }}</span>
              </label>
              <label class="choice mt-1" :class="{ 'is-picked': restoreMode === 'merge' }">
                <input v-model="restoreMode" type="radio" value="merge">
                <span>{{ t('backup.modeMerge') }}</span>
              </label>
            </div>

            <div v-if="restoreTarget.encrypted">
              <label class="label">{{ t('backup.passphrase') }}</label>
              <input v-model="restorePassphrase" type="password" class="input" autocomplete="off">
            </div>

            <label v-if="restoreTarget.compatibility?.level === 'blocking'" class="choice is-danger">
              <input v-model="forceVersion" type="checkbox">
              <span>{{ t('backup.forceVersion') }}</span>
            </label>

            <p v-if="restoreError" class="alert alert-danger">{{ restoreError }}</p>
          </div>

          <div class="modal-actions">
            <button class="btn btn-ghost" @click="restoreTarget = null">{{ t('common.cancel') }}</button>
            <button class="btn btn-primary" :disabled="!canRestore || restoring" @click="askBackupThenRestore">
              {{ restoring ? t('backup.restoring') : t('backup.restore') }}
            </button>
          </div>
        </div>
      </div>
    </teleport>

    <!-- Vor dem Zurückspielen: dieselbe Rückfrage wie vor jedem Eingriff -->
    <BackupPrompt
      :open="promptOpen"
      :operation="t('backup.restore') + ' — ' + (restoreTarget?.name || '')"
      :warning="restoreWarning"
      :scopes="scopes"
      @confirm="runRestore"
      @cancel="promptOpen = false"
    />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useI18n } from '../i18n'
import AppIcon from '../components/AppIcon.vue'
import BackupPrompt from '../components/BackupPrompt.vue'

const auth = useAuthStore()
const { t, locale } = useI18n()

const loading = ref(false)
const creating = ref(false)
const uploading = ref(false)
const restoring = ref(false)
const error = ref('')
const message = ref('')

const scopes = ref([])
const backups = ref([])
const storage = ref(null)

const form = ref({ scopes: [], note: '', encrypt: false, passphrase: '' })

const restoreTarget = ref(null)
const restoreScopes = ref([])
const restoreMode = ref('replace')
const restorePassphrase = ref('')
const forceVersion = ref(false)
const restoreError = ref('')
const promptOpen = ref(false)

function scopeLabel(scope) {
  return locale.value === 'en' ? (scope.label_en || scope.label) : scope.label
}

function shortScope(id) {
  const hit = scopes.value.find(s => s.id === id)
  return hit ? scopeLabel(hit) : id
}

function totalRows(item) {
  const counts = item.counts || {}
  return Object.values(counts)
    .reduce((sum, tables) => sum + Object.values(tables || {}).reduce((a, b) => a + b, 0), 0)
    .toLocaleString()
}

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(value < 10 && unit > 0 ? 1 : 0)} ${units[unit]}`
}

function formatDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString(locale.value === 'en' ? 'en-GB' : 'de-DE')
  } catch {
    return iso
  }
}

function compatBadge(level) {
  return { ok: 'badge-success', info: 'badge-neutral', warn: 'badge-warning', blocking: 'badge-danger' }[level]
    || 'badge-neutral'
}

function compatAlert(level) {
  return { ok: 'alert-success', info: 'alert-info', warn: 'alert-warning', blocking: 'alert-danger' }[level]
    || 'alert-info'
}

function compatLabel(level) {
  return { ok: '=', info: 'älter', warn: '?', blocking: 'neuer!' }[level] || level
}

function downloadUrl(name) {
  // Der Download läuft über einen normalen Link - der Browser schickt dabei
  // keinen Authorization-Kopf mit, deshalb der Token in der Adresse.
  return `/api/backup/${encodeURIComponent(name)}/download?token=${encodeURIComponent(auth.token)}`
}

const canRestore = computed(() => {
  if (!restoreTarget.value || !restoreScopes.value.length) return false
  if (restoreTarget.value.encrypted && !restorePassphrase.value) return false
  if (restoreTarget.value.compatibility?.level === 'blocking' && !forceVersion.value) return false
  return true
})

const restoreWarning = computed(() => (
  restoreMode.value === 'replace'
    ? t('backup.modeReplace')
    : ''
))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await auth.api('/api/backup/overview')
    scopes.value = data.scopes
    backups.value = data.backups
    storage.value = data.storage
    if (!form.value.scopes.length) {
      form.value.scopes = data.scopes.filter(s => s.default).map(s => s.id)
    }
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

async function create() {
  creating.value = true
  error.value = ''
  message.value = ''
  try {
    const result = await auth.api('/api/backup/create', {
      method: 'POST',
      body: {
        scopes: form.value.scopes,
        passphrase: form.value.encrypt ? form.value.passphrase : '',
        note: form.value.note,
      },
    })
    message.value = `${result.name} (${formatBytes(result.size_bytes)})`
    form.value.passphrase = ''
    form.value.note = ''
    await load()
  } catch (err) {
    error.value = err.message
  } finally {
    creating.value = false
  }
}

async function upload(event) {
  const file = event.target.files?.[0]
  if (!file) return
  uploading.value = true
  error.value = ''
  try {
    const body = new FormData()
    body.append('file', file)
    const result = await auth.api('/api/backup/upload', { method: 'POST', body })
    message.value = `${result.name} — ${result.compatibility?.message || ''}`
    await load()
  } catch (err) {
    error.value = err.message
  } finally {
    uploading.value = false
    event.target.value = ''
  }
}

async function remove(item) {
  if (!window.confirm(t('backup.deleteConfirm'))) return
  try {
    await auth.api(`/api/backup/${encodeURIComponent(item.name)}`, { method: 'DELETE' })
    await load()
  } catch (err) {
    error.value = err.message
  }
}

async function prune() {
  try {
    const result = await auth.api('/api/backup/prune', { method: 'POST' })
    message.value = `${result.removed}`
    await load()
  } catch (err) {
    error.value = err.message
  }
}

async function openRestore(item) {
  restoreError.value = ''
  restorePassphrase.value = ''
  forceVersion.value = false
  restoreMode.value = 'replace'
  try {
    // Erst nachsehen, was drinsteckt - dann entscheiden. Nicht umgekehrt.
    const detail = await auth.api(`/api/backup/${encodeURIComponent(item.name)}/inspect`)
    restoreTarget.value = detail
    restoreScopes.value = (detail.scope_details || []).filter(d => d.known).map(d => d.id)
  } catch (err) {
    error.value = err.message
  }
}

function askBackupThenRestore() {
  restoreError.value = ''
  promptOpen.value = true
}

async function runRestore(decision) {
  promptOpen.value = false
  restoring.value = true
  restoreError.value = ''
  try {
    const result = await auth.api('/api/backup/restore', {
      method: 'POST',
      body: {
        name: restoreTarget.value.name,
        scopes: restoreScopes.value,
        passphrase: restorePassphrase.value,
        mode: restoreMode.value,
        force_version_mismatch: forceVersion.value,
        confirm: 'RESTORE',
        backup: decision,
      },
    })
    const rows = (result.tables || []).reduce((sum, entry) => sum + (entry.inserted || 0), 0)
    message.value = `${result.scopes.join(', ')} — ${rows} ${t('backup.rows')}. ${result.note}`
    restoreTarget.value = null
    await load()
  } catch (err) {
    restoreError.value = err.message
  } finally {
    restoring.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.scope-grid {
  display: grid;
  gap: 0.375rem;
}

@media (min-width: 640px) {
  .scope-grid {
    grid-template-columns: 1fr 1fr;
  }
}

.scope-item {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.5rem 0.625rem;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  cursor: pointer;
  font-size: 0.8125rem;
}

.scope-item:hover {
  background-color: var(--hover-surface);
}

.scope-item.is-unknown {
  opacity: 0.55;
  cursor: not-allowed;
}

.scope-item input {
  margin-top: 0.1875rem;
  flex-shrink: 0;
}

.scope-item span {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.scope-item strong {
  color: var(--color-text-primary);
  font-weight: 600;
}

.scope-item em {
  font-style: normal;
  font-size: 0.6875rem;
  color: var(--color-text-muted);
}

.scope-chip {
  margin-right: 0.25rem;
  margin-bottom: 0.125rem;
}

.choice {
  display: flex;
  align-items: flex-start;
  gap: 0.625rem;
  padding: 0.5rem 0.75rem;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  cursor: pointer;
  font-size: 0.8125rem;
  color: var(--color-text-primary);
}

.choice.is-picked {
  border-color: var(--color-primary);
  background-color: var(--primary-soft);
}

.choice.is-danger {
  border-color: var(--color-danger);
  color: var(--color-danger);
}

.table-wrap {
  overflow-x: auto;
}

.cell-main {
  color: var(--color-text-primary);
  font-size: 0.8125rem;
}

.cell-sub {
  font-size: 0.6875rem;
  color: var(--color-text-muted);
}

.text-danger {
  color: var(--color-danger);
}

.tabular {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.inline-icon {
  display: inline-block;
  vertical-align: -2px;
  margin-left: 0.25rem;
  color: var(--color-text-muted);
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
  background-color: var(--success-soft, var(--primary-soft));
  color: var(--color-success);
}

.alert-warning {
  background-color: var(--warning-soft, var(--primary-soft));
  color: var(--color-warning);
}

.alert-info {
  background-color: var(--primary-soft);
  color: var(--color-primary);
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  padding: 0.875rem 1.25rem;
  border-top: 1px solid var(--color-border);
}
</style>
