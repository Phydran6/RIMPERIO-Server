<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.05.30.18.02.35
     Beschreibung: RIMPERIO - Benutzer-Verwaltung (inkl. MFA-Status & Admin-Reset)
     ============================================================================== -->

<template>
  <div class="p-6">
    <div class="flex justify-between items-center mb-6">
      <h1 class="text-2xl font-bold" :style="{ color: 'var(--color-text-primary)' }">Benutzer</h1>
      <button
        @click="showCreateModal = true"
        class="text-white px-4 py-2 rounded hover:opacity-90"
        :style="{ backgroundColor: 'var(--color-primary)' }"
      >
        + Neuer Benutzer
      </button>
    </div>
    
    <!-- Users Tabelle -->
    <div class="rounded-lg shadow" :style="cardStyle">
      <table class="w-full">
        <thead :style="{ backgroundColor: 'var(--color-surface-elevated)' }">
          <tr>
            <th class="px-6 py-3 text-left text-xs font-medium uppercase" :style="{ color: 'var(--color-text-muted)' }">Benutzername</th>
            <th class="px-6 py-3 text-left text-xs font-medium uppercase" :style="{ color: 'var(--color-text-muted)' }">E-Mail</th>
            <th class="px-6 py-3 text-left text-xs font-medium uppercase" :style="{ color: 'var(--color-text-muted)' }">Rolle</th>
            <th class="px-6 py-3 text-left text-xs font-medium uppercase" :style="{ color: 'var(--color-text-muted)' }">Status</th>
            <th class="px-6 py-3 text-left text-xs font-medium uppercase" :style="{ color: 'var(--color-text-muted)' }">Erstellt</th>
            <th class="px-6 py-3 text-right text-xs font-medium uppercase" :style="{ color: 'var(--color-text-muted)' }">Aktionen</th>
          </tr>
        </thead>
        <tbody class="divide-y" :style="{ borderColor: 'var(--color-border)' }">
          <tr v-for="user in users" :key="user.id" class="hover-row">
            <td class="px-6 py-4 font-medium" :style="{ color: 'var(--color-text-primary)' }">{{ user.username }}</td>
            <td class="px-6 py-4" :style="{ color: 'var(--color-text-muted)' }">{{ user.email || '-' }}</td>
            <td class="px-6 py-4">
              <span 
                class="px-2 py-1 text-xs rounded-full text-white"
                :class="user.role === 'admin' ? 'bg-purple-500' : 'bg-gray-500'"
              >
                {{ user.role }}
              </span>
            </td>
            <td class="px-6 py-4">
              <span 
                class="px-2 py-1 text-xs rounded-full text-white"
                :class="user.is_active ? 'bg-green-500' : 'bg-red-500'"
              >
                {{ user.is_active ? 'Aktiv' : 'Deaktiviert' }}
              </span>
            </td>
            <td class="px-6 py-4 text-sm" :style="{ color: 'var(--color-text-muted)' }">{{ formatDate(user.created_at) }}</td>
            <td class="px-6 py-4 text-right">
              <button
                @click="editUser(user)"
                class="hover:underline mr-4"
                :style="{ color: 'var(--color-primary)' }"
              >
                Bearbeiten
              </button>
              <button
                v-if="authStore.isAdmin && user.id !== authStore.user?.id"
                @click="resetUserMfa(user)"
                class="hover:underline mr-4"
                :style="{ color: 'var(--color-text-secondary)' }"
                title="MFA für diesen User zurücksetzen (Notfall)"
              >
                MFA-Reset
              </button>
              <button
                v-if="user.id !== authStore.user?.id"
                @click="deleteUser(user)"
                class="hover:underline"
                :style="{ color: 'var(--color-danger)' }"
              >
                Löschen
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    
    <!-- Create/Edit Modal -->
    <div 
      v-if="showCreateModal || editingUser" 
      class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50"
      @click.self="closeModal"
    >
      <div class="rounded-lg shadow-xl w-full max-w-md flex flex-col" :style="modalContainerStyle">
        <div class="p-4 border-b flex-shrink-0" :style="{ borderColor: 'var(--color-border)' }">
          <h2 class="text-lg font-semibold" :style="{ color: 'var(--color-text-primary)' }">
            {{ editingUser ? 'Benutzer bearbeiten' : 'Neuer Benutzer' }}
          </h2>
        </div>
        <form @submit.prevent="saveUser" class="flex flex-col flex-1 min-h-0">
          <div class="p-4 space-y-4 overflow-y-auto flex-1">
          <div>
            <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Benutzername</label>
            <input
              v-model="form.username"
              type="text"
              required
              :disabled="!!editingUser"
              class="w-full rounded px-3 py-2 disabled:opacity-50"
              :style="inputStyle"
            >
          </div>
          <div>
            <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">E-Mail</label>
            <input
              v-model="form.email"
              type="email"
              class="w-full rounded px-3 py-2"
              :style="inputStyle"
            >
          </div>
          <div>
            <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">
              {{ editingUser ? 'Neues Passwort (leer lassen um nicht zu ändern)' : 'Passwort' }}
            </label>
            <input
              v-model="form.password"
              type="password"
              :required="!editingUser"
              minlength="6"
              class="w-full rounded px-3 py-2"
              :style="inputStyle"
            >
          </div>
          <div>
            <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Rolle</label>
            <select v-model="form.role" class="w-full rounded px-3 py-2" :style="inputStyle">
              <option value="user">User</option>
              <option value="admin">Admin</option>
            </select>
          </div>
          <div v-if="editingUser" class="flex items-center">
            <input
              v-model="form.is_active"
              type="checkbox"
              id="is_active"
              class="mr-2"
            >
            <label for="is_active" :style="{ color: 'var(--color-text-secondary)' }">Benutzer aktiv</label>
          </div>
          
          <!-- MFA-Verwaltung (nur für eigenen User) -->
          <div v-if="editingUser?.id === authStore.user?.id" class="pt-4 border-t" :style="{ borderColor: 'var(--color-border)' }">
            <MfaSettings />
          </div>

          <!-- App-Login QR (nur für eigenen User) -->
          <div v-if="editingUser?.id === authStore.user?.id" class="pt-4 border-t" :style="{ borderColor: 'var(--color-border)' }">
            <h3 class="text-sm font-semibold mb-3" :style="{ color: 'var(--color-text-primary)' }">📱 App-Anmeldung</h3>

            <div v-if="qrError" class="px-3 py-2 rounded mb-3 text-xs" :style="{ backgroundColor: 'var(--color-surface-elevated)', border: '1px solid var(--color-danger)', color: 'var(--color-danger)' }">
              {{ qrError }}
            </div>

            <div v-if="qrLoading" class="text-center py-4">
              <span class="text-sm" :style="{ color: 'var(--color-text-muted)' }">QR-Code wird generiert...</span>
            </div>

            <div v-else-if="qrData" class="text-center">
              <img :src="qrData.qr_image" alt="App-Login QR-Code" class="mx-auto rounded mb-2" style="max-width: 180px; image-rendering: pixelated;" />
              <div class="text-xs mb-2" :style="{ color: 'var(--color-text-muted)' }">
                <span v-if="qrSecondsLeft > 0">
                  Gültig noch <strong :style="{ color: qrSecondsLeft < 60 ? 'var(--color-danger)' : 'var(--color-text-primary)' }">{{ formatQrCountdown(qrSecondsLeft) }}</strong>
                </span>
                <span v-else class="font-semibold" :style="{ color: 'var(--color-danger)' }">Abgelaufen – bitte neu generieren</span>
              </div>
              <div class="p-2 rounded text-left mb-2" :style="{ backgroundColor: 'var(--color-surface-elevated)', border: '1px solid var(--color-border)' }">
                <p class="text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">API-URL</p>
                <code class="text-xs break-all" :style="{ color: 'var(--color-text-primary)' }">{{ qrData.api_url }}</code>
                <button @click="copyQrUrl" class="mt-1 text-xs px-2 py-0.5 rounded block" :style="{ backgroundColor: 'var(--color-surface)', border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)' }">
                  {{ qrCopied ? 'Kopiert!' : 'Kopieren' }}
                </button>
              </div>
            </div>

            <button @click="generateQR" :disabled="qrLoading" class="w-full py-1.5 px-3 rounded text-sm text-white disabled:opacity-50" :style="{ backgroundColor: 'var(--color-primary)' }">
              {{ qrData ? 'Neu generieren' : 'QR-Code generieren' }}
            </button>
            <p class="text-xs mt-1" :style="{ color: 'var(--color-text-muted)' }">15 Min. gültig, Einmalverwendung</p>
          </div>
          </div>

          <div class="flex justify-end gap-2 p-4 border-t flex-shrink-0" :style="{ borderColor: 'var(--color-border)', backgroundColor: 'var(--color-surface)' }">
            <button
              type="button"
              @click="closeModal"
              class="px-4 py-2 rounded hover:opacity-80"
              :style="buttonSecondaryStyle"
            >
              Abbrechen
            </button>
            <button
              type="submit"
              class="px-4 py-2 text-white rounded hover:opacity-90"
              :style="{ backgroundColor: 'var(--color-primary)' }"
            >
              Speichern
            </button>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import MfaSettings from '../components/MfaSettings.vue'

const authStore = useAuthStore()

const users = ref([])
const showCreateModal = ref(false)
const editingUser = ref(null)
// App-Login QR
const qrData = ref(null)
const qrLoading = ref(false)
const qrError = ref('')
const qrSecondsLeft = ref(0)
const qrCopied = ref(false)
let qrCountdownTimer = null
const form = ref({
  username: '',
  email: '',
  password: '',
  role: 'user',
  is_active: true
})

// Computed Styles
const cardStyle = computed(() => ({
  backgroundColor: 'var(--color-surface)',
  borderColor: 'var(--color-border)'
}))

// Modal-Container mit Höhenbegrenzung, damit langer Inhalt (MFA + QR) scrollbar bleibt
const modalContainerStyle = computed(() => ({
  backgroundColor: 'var(--color-surface)',
  borderColor: 'var(--color-border)',
  maxHeight: '90vh',
}))

const inputStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  borderColor: 'var(--color-border)',
  color: 'var(--color-text-primary)',
  border: '1px solid var(--color-border)'
}))

const buttonSecondaryStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  color: 'var(--color-text-primary)',
  border: '1px solid var(--color-border)'
}))

onMounted(() => loadUsers())
onUnmounted(() => { if (qrCountdownTimer) clearInterval(qrCountdownTimer) })

function formatQrCountdown(secs) {
  const m = Math.floor(secs / 60)
  const s = secs % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

function startQrCountdown(expiresAt) {
  if (qrCountdownTimer) clearInterval(qrCountdownTimer)
  const expiryStr = (expiresAt.endsWith('Z') || expiresAt.includes('+')) ? expiresAt : expiresAt + 'Z'
  const expiryMs = new Date(expiryStr).getTime()
  const update = () => {
    const diff = Math.max(0, Math.floor((expiryMs - Date.now()) / 1000))
    qrSecondsLeft.value = diff
    if (diff === 0) clearInterval(qrCountdownTimer)
  }
  update()
  qrCountdownTimer = setInterval(update, 1000)
}

async function generateQR() {
  qrLoading.value = true
  qrError.value = ''
  qrData.value = null
  if (qrCountdownTimer) clearInterval(qrCountdownTimer)
  try {
    const data = await authStore.api('/api/auth/app-qr', { cache: 'no-store' })
    qrData.value = data
    startQrCountdown(data.expires_at)
  } catch (e) {
    qrError.value = e.message || 'QR-Code konnte nicht generiert werden'
  } finally {
    qrLoading.value = false
  }
}

async function copyQrUrl() {
  if (!qrData.value?.api_url) return
  try {
    await navigator.clipboard.writeText(qrData.value.api_url)
    qrCopied.value = true
    setTimeout(() => { qrCopied.value = false }, 2000)
  } catch {
    const el = document.createElement('textarea')
    el.value = qrData.value.api_url
    document.body.appendChild(el)
    el.select()
    document.execCommand('copy')
    document.body.removeChild(el)
    qrCopied.value = true
    setTimeout(() => { qrCopied.value = false }, 2000)
  }
}

async function loadUsers() {
  try {
    users.value = await authStore.api('/api/users')
  } catch (e) {
    console.error('Fehler:', e)
  }
}

function editUser(user) {
  editingUser.value = user
  form.value = {
    username: user.username,
    email: user.email || '',
    password: '',
    role: user.role,
    is_active: user.is_active
  }
  if (user.id === authStore.user?.id) {
    generateQR()
  }
}

function closeModal() {
  showCreateModal.value = false
  editingUser.value = null
  form.value = { username: '', email: '', password: '', role: 'user', is_active: true }
  qrData.value = null
  qrError.value = ''
  if (qrCountdownTimer) clearInterval(qrCountdownTimer)
}

async function saveUser() {
  try {
    if (editingUser.value) {
      const data = {
        email: form.value.email,
        role: form.value.role,
        is_active: form.value.is_active
      }
      if (form.value.password) {
        data.password = form.value.password
      }
      await authStore.api(`/api/users/${editingUser.value.id}`, {
        method: 'PUT',
        body: data
      })
    } else {
      await authStore.api('/api/users', {
        method: 'POST',
        body: form.value
      })
    }
    closeModal()
    loadUsers()
  } catch (e) {
    alert('Fehler: ' + e.message)
  }
}

async function deleteUser(user) {
  if (!confirm(`Benutzer "${user.username}" wirklich löschen?`)) return

  try {
    await authStore.api(`/api/users/${user.id}`, { method: 'DELETE' })
    loadUsers()
  } catch (e) {
    alert('Fehler: ' + e.message)
  }
}

async function resetUserMfa(user) {
  if (!confirm(`MFA für "${user.username}" wirklich zurücksetzen? Backup-Codes und TOTP werden gelöscht.`)) return
  try {
    await authStore.api(`/api/users/${user.id}/mfa/reset`, { method: 'POST', body: {} })
    alert(`MFA für ${user.username} zurückgesetzt.`)
  } catch (e) {
    alert('Fehler: ' + e.message)
  }
}

function formatDate(timestamp) {
  if (!timestamp) return '-'
  return new Date(timestamp).toLocaleDateString('de-DE')
}
</script>

<style scoped>
.hover-row:hover {
  background-color: var(--color-surface-elevated);
}

.divide-y > tr {
  border-color: var(--color-border);
}
</style>