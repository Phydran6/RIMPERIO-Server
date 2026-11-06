<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.08.02.18.00.00
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Eigene Anmeldesicherheit: Passkeys verwalten.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">Anmeldesicherheit</h2>
        <p class="page-subtitle">Passkeys für {{ auth.user?.username }}</p>
      </div>
    </div>

    <div v-if="message" class="card mb-4" :style="{ borderColor: messageOk ? 'var(--color-success)' : 'var(--color-danger)' }">
      <div class="card-body flex items-start gap-2 text-sm" :style="{ color: messageOk ? 'var(--color-success)' : 'var(--color-danger)' }">
        <AppIcon :name="messageOk ? 'check' : 'warning'" :size="18" class="shrink-0" />
        <span>{{ message }}</span>
      </div>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <!-- Erklärung + Anlegen -->
      <div class="card h-fit">
        <div class="card-header"><span class="card-title">Passkey einrichten</span></div>
        <div class="card-body space-y-3">
          <p class="text-sm" style="color: var(--color-text-muted)">
            Ein Passkey ersetzt Passwort und Einmalcode: die Anmeldung läuft über Windows Hello,
            Face ID, Fingerabdruck oder einen Sicherheitsschlüssel. Der geheime Teil bleibt auf
            dem Gerät und die Signatur gilt nur für diese Adresse — eine nachgebaute
            Anmeldeseite bekommt damit nichts Verwertbares.
          </p>

          <div v-if="!supported" class="warn-note">
            <AppIcon name="warning" :size="14" />
            <span>
              Dieser Browser bietet keine Passkeys an. Sie brauchen HTTPS mit gültigem
              Zertifikat — über <code>http://</code> oder eine IP-Adresse sperrt der Browser sie.
            </span>
          </div>

          <template v-else>
            <div>
              <label class="label">Name (zur Wiedererkennung)</label>
              <input v-model="newName" class="input" placeholder="z. B. Notebook Büro" maxlength="100">
            </div>
            <button class="btn btn-primary w-full" :disabled="registering" @click="registerPasskey">
              <AppIcon name="plus" :size="16" />
              {{ registering ? 'Warte auf Gerät…' : 'Passkey hinzufügen' }}
            </button>
          </template>
        </div>
      </div>

      <!-- Liste -->
      <div class="card lg:col-span-2">
        <div class="card-header">
          <span class="card-title">Hinterlegte Passkeys</span>
          <button class="btn-icon" title="Neu laden" @click="load">
            <AppIcon name="refresh" :size="16" />
          </button>
        </div>

        <div v-if="!credentials.length" class="empty-state">
          <AppIcon name="lock" :size="26" />
          <p class="empty-state-title">Noch kein Passkey</p>
          <p class="text-sm">Solange keiner hinterlegt ist, bleibt es bei Passwort (und ggf. Einmalcode).</p>
        </div>

        <div v-else class="overflow-x-auto">
          <table class="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Angelegt</th>
                <th>Zuletzt benutzt</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="cred in credentials" :key="cred.id">
                <td>
                  <input
                    v-if="editing === cred.id"
                    v-model="editName"
                    class="input"
                    @keyup.enter="saveName(cred)"
                    @keyup.esc="editing = null"
                  >
                  <span v-else class="font-medium" style="color: var(--color-text-primary)">{{ cred.name }}</span>
                </td>
                <td class="whitespace-nowrap">{{ formatTime(cred.created_at) }}</td>
                <td class="whitespace-nowrap">{{ cred.last_used_at ? formatTime(cred.last_used_at) : 'nie' }}</td>
                <td>
                  <div class="flex justify-end gap-1">
                    <button v-if="editing === cred.id" class="btn-icon" title="Speichern" @click="saveName(cred)">
                      <AppIcon name="check" :size="16" />
                    </button>
                    <button v-else class="btn-icon" title="Umbenennen" @click="startEdit(cred)">
                      <AppIcon name="settings" :size="16" />
                    </button>
                    <button class="btn-icon danger" title="Entfernen" @click="removePasskey(cred)">
                      <AppIcon name="trash" :size="16" />
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import { isSupported, createCredential, describeError } from '../utils/webauthn'

const auth = useAuthStore()

const credentials = ref([])
const newName = ref('')
const registering = ref(false)
const editing = ref(null)
const editName = ref('')
const message = ref('')
const messageOk = ref(true)
const supported = isSupported()

onMounted(load)

async function load() {
  try {
    const data = await auth.api('/api/auth/passkey/credentials')
    credentials.value = data.items || []
  } catch (e) {
    show(e.message, false)
  }
}

async function registerPasskey() {
  registering.value = true
  try {
    const start = await auth.api('/api/auth/passkey/register/options', { method: 'POST' })
    const credential = await createCredential(start.options)
    await auth.api('/api/auth/passkey/register/verify', {
      method: 'POST',
      body: {
        challenge_handle: start.challenge_handle,
        credential,
        name: newName.value.trim(),
      },
    })
    newName.value = ''
    await load()
    show('Passkey hinzugefügt.', true)
  } catch (e) {
    show(e.name ? describeError(e) : e.message, false)
  } finally {
    registering.value = false
  }
}

function startEdit(cred) {
  editing.value = cred.id
  editName.value = cred.name
}

async function saveName(cred) {
  const name = editName.value.trim()
  if (!name) return
  try {
    await auth.api(`/api/auth/passkey/credentials/${cred.id}`, { method: 'PUT', body: { name } })
    editing.value = null
    await load()
  } catch (e) {
    show(e.message, false)
  }
}

async function removePasskey(cred) {
  if (!confirm(`Passkey "${cred.name}" entfernen?`)) return
  try {
    await auth.api(`/api/auth/passkey/credentials/${cred.id}`, { method: 'DELETE' })
    await load()
    show('Passkey entfernt.', true)
  } catch (e) {
    show(e.message, false)
  }
}

function show(text, ok) {
  message.value = text
  messageOk.value = ok
  setTimeout(() => { message.value = '' }, 6000)
}

function formatTime(ts) {
  return ts ? new Date(ts).toLocaleString('de-DE') : '–'
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

.btn-icon.danger:hover {
  background-color: var(--danger-soft);
  color: var(--color-danger);
}
</style>
