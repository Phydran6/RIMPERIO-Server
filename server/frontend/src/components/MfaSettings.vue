<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.05.30.17.22.26
     Beschreibung: RIMPERIO - MFA-Verwaltung für eigenen User (Setup/Disable/Backup-Codes)
     ============================================================================== -->

<template>
  <div class="space-y-3">
    <h3 class="text-sm font-semibold" :style="{ color: 'var(--color-text-primary)' }">
      🔐 Zwei-Faktor-Authentifizierung
    </h3>

    <div v-if="loadError" class="px-3 py-2 rounded text-xs" :style="errorStyle">
      {{ loadError }}
    </div>

    <div v-if="loading" class="text-sm" :style="{ color: 'var(--color-text-muted)' }">
      Lade…
    </div>

    <!-- ---------- MFA aktiv ---------- -->
    <div v-else-if="status?.enabled" class="space-y-2">
      <div class="flex items-center gap-2">
        <span class="px-2 py-1 text-xs rounded-full text-white bg-green-500">Aktiv</span>
        <span class="text-xs" :style="{ color: 'var(--color-text-muted)' }">
          Noch {{ status.backup_codes_remaining }} Backup-Codes
        </span>
      </div>

      <div class="flex flex-wrap gap-2">
        <button
          type="button"
          @click="mode = 'disable'"
          class="px-3 py-1.5 text-sm rounded hover:opacity-80"
          :style="dangerButtonStyle"
        >
          Deaktivieren
        </button>
        <button
          type="button"
          @click="mode = 'regenerate'"
          class="px-3 py-1.5 text-sm rounded hover:opacity-80"
          :style="secondaryButtonStyle"
        >
          Neue Backup-Codes
        </button>
      </div>

      <!-- Disable-Dialog -->
      <div v-if="mode === 'disable'" class="p-3 rounded space-y-2" :style="boxStyle">
        <p class="text-xs" :style="{ color: 'var(--color-text-secondary)' }">
          Passwort + aktueller TOTP-Code (oder Backup-Code) zum Deaktivieren:
        </p>
        <input
          v-model="disablePassword"
          type="password"
          placeholder="Passwort"
          class="w-full px-2 py-1 text-sm rounded"
          :style="inputStyle"
        >
        <input
          v-model="disableCode"
          type="text"
          autocomplete="one-time-code"
          placeholder="TOTP oder Backup-Code"
          class="w-full px-2 py-1 text-sm rounded"
          :style="inputStyle"
        >
        <div v-if="actionError" class="text-xs" :style="{ color: 'var(--color-danger)' }">
          {{ actionError }}
        </div>
        <div class="flex gap-2">
          <button
            type="button"
            :disabled="busy"
            @click="doDisable"
            class="px-3 py-1 text-sm rounded text-white disabled:opacity-50"
            :style="{ backgroundColor: 'var(--color-danger)' }"
          >
            {{ busy ? '…' : 'MFA deaktivieren' }}
          </button>
          <button
            type="button"
            @click="resetMode"
            class="px-3 py-1 text-sm rounded"
            :style="secondaryButtonStyle"
          >
            Abbrechen
          </button>
        </div>
      </div>

      <!-- Regenerate-Dialog -->
      <div v-if="mode === 'regenerate'" class="p-3 rounded space-y-2" :style="boxStyle">
        <p class="text-xs" :style="{ color: 'var(--color-text-secondary)' }">
          Aktueller TOTP-Code (oder Backup-Code) zum Neugenerieren:
        </p>
        <input
          v-model="regenCode"
          type="text"
          autocomplete="one-time-code"
          placeholder="TOTP oder Backup-Code"
          class="w-full px-2 py-1 text-sm rounded"
          :style="inputStyle"
        >
        <div v-if="actionError" class="text-xs" :style="{ color: 'var(--color-danger)' }">
          {{ actionError }}
        </div>
        <div class="flex gap-2">
          <button
            type="button"
            :disabled="busy"
            @click="doRegenerate"
            class="px-3 py-1 text-sm rounded text-white disabled:opacity-50"
            :style="{ backgroundColor: 'var(--color-primary)' }"
          >
            {{ busy ? '…' : 'Neue Codes' }}
          </button>
          <button
            type="button"
            @click="resetMode"
            class="px-3 py-1 text-sm rounded"
            :style="secondaryButtonStyle"
          >
            Abbrechen
          </button>
        </div>
      </div>

      <!-- Backup-Codes Anzeige (einmalig nach Regenerate) -->
      <div v-if="generatedBackupCodes.length" class="p-3 rounded space-y-2" :style="boxStyle">
        <p class="text-xs font-semibold" :style="{ color: 'var(--color-danger)' }">
          ⚠️ Speichere diese Codes JETZT — sie werden nicht erneut angezeigt.
        </p>
        <pre class="text-xs font-mono p-2 rounded select-all" :style="codeBoxStyle">{{ generatedBackupCodes.join('\n') }}</pre>
        <div class="flex gap-2">
          <button type="button" @click="copyCodes" class="px-3 py-1 text-sm rounded" :style="secondaryButtonStyle">
            {{ copied ? 'Kopiert!' : 'Kopieren' }}
          </button>
          <button type="button" @click="downloadCodes" class="px-3 py-1 text-sm rounded" :style="secondaryButtonStyle">
            Herunterladen
          </button>
          <button type="button" @click="generatedBackupCodes = []" class="px-3 py-1 text-sm rounded" :style="secondaryButtonStyle">
            Schließen
          </button>
        </div>
      </div>
    </div>

    <!-- ---------- MFA aus ---------- -->
    <div v-else class="space-y-2">
      <div class="flex items-center gap-2">
        <span class="px-2 py-1 text-xs rounded-full text-white bg-gray-500">Inaktiv</span>
      </div>

      <button
        v-if="mode !== 'setup'"
        type="button"
        @click="startSetup"
        class="px-3 py-1.5 text-sm rounded text-white hover:opacity-90"
        :style="{ backgroundColor: 'var(--color-primary)' }"
      >
        MFA einrichten
      </button>

      <!-- Setup-Wizard -->
      <div v-if="mode === 'setup'" class="p-3 rounded space-y-3" :style="boxStyle">
        <div v-if="!setupData" class="text-sm" :style="{ color: 'var(--color-text-muted)' }">
          Generiere Secret…
        </div>

        <template v-else>
          <p class="text-xs" :style="{ color: 'var(--color-text-secondary)' }">
            1. Scanne den QR mit Google Authenticator, Authy, 1Password, Aegis o.ä. — oder tippe das Secret manuell ein.
          </p>
          <div class="text-center">
            <img :src="setupData.qr_image" alt="MFA QR-Code" class="mx-auto rounded mb-2" style="max-width: 200px; image-rendering: pixelated;" />
          </div>
          <div class="p-2 rounded text-left" :style="codeBoxStyle">
            <p class="text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Secret (Base32)</p>
            <code class="text-xs break-all select-all" :style="{ color: 'var(--color-text-primary)' }">{{ setupData.secret }}</code>
          </div>

          <p class="text-xs" :style="{ color: 'var(--color-text-secondary)' }">
            2. Gib den ersten 6-stelligen Code aus der App ein:
          </p>
          <input
            v-model="verifyCode"
            type="text"
            inputmode="numeric"
            maxlength="6"
            autocomplete="one-time-code"
            placeholder="123456"
            class="w-full px-2 py-1 text-sm rounded text-center tracking-widest"
            :style="inputStyle"
          >
          <div v-if="actionError" class="text-xs" :style="{ color: 'var(--color-danger)' }">
            {{ actionError }}
          </div>
          <div class="flex gap-2">
            <button
              type="button"
              :disabled="busy || verifyCode.length < 6"
              @click="doVerify"
              class="px-3 py-1 text-sm rounded text-white disabled:opacity-50"
              :style="{ backgroundColor: 'var(--color-primary)' }"
            >
              {{ busy ? '…' : 'Aktivieren' }}
            </button>
            <button type="button" @click="cancelSetup" class="px-3 py-1 text-sm rounded" :style="secondaryButtonStyle">
              Abbrechen
            </button>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()

const status = ref(null)
const loading = ref(true)
const loadError = ref('')
const actionError = ref('')
const busy = ref(false)

// Modus: null | 'setup' | 'disable' | 'regenerate'
const mode = ref(null)

const setupData = ref(null)        // { secret, otpauth_uri, qr_image }
const verifyCode = ref('')
const disablePassword = ref('')
const disableCode = ref('')
const regenCode = ref('')
const generatedBackupCodes = ref([])
const copied = ref(false)

// Styles
const inputStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  borderColor: 'var(--color-border)',
  color: 'var(--color-text-primary)',
  border: '1px solid var(--color-border)'
}))

const boxStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  border: '1px solid var(--color-border)',
}))

const codeBoxStyle = computed(() => ({
  backgroundColor: 'var(--color-surface)',
  color: 'var(--color-text-primary)',
  border: '1px solid var(--color-border)'
}))

const secondaryButtonStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  color: 'var(--color-text-primary)',
  border: '1px solid var(--color-border)'
}))

const dangerButtonStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  color: 'var(--color-danger)',
  border: '1px solid var(--color-danger)'
}))

const errorStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  border: '1px solid var(--color-danger)',
  color: 'var(--color-danger)'
}))

onMounted(loadStatus)

async function loadStatus() {
  loading.value = true
  loadError.value = ''
  try {
    status.value = await auth.api('/api/auth/mfa/status')
  } catch (e) {
    loadError.value = e.message || 'Status konnte nicht geladen werden'
  } finally {
    loading.value = false
  }
}

function resetMode() {
  mode.value = null
  actionError.value = ''
  verifyCode.value = ''
  disablePassword.value = ''
  disableCode.value = ''
  regenCode.value = ''
  setupData.value = null
}

async function startSetup() {
  resetMode()
  mode.value = 'setup'
  busy.value = true
  try {
    setupData.value = await auth.api('/api/auth/mfa/setup', { method: 'POST', body: {} })
  } catch (e) {
    actionError.value = e.message
    mode.value = null
  } finally {
    busy.value = false
  }
}

function cancelSetup() {
  // Backend hat zwar das Secret bereits gesetzt, aber mfa_enabled=false → nächster Setup-Call überschreibt es.
  resetMode()
}

async function doVerify() {
  busy.value = true
  actionError.value = ''
  try {
    const res = await auth.api('/api/auth/mfa/verify', { method: 'POST', body: { code: verifyCode.value.trim() } })
    generatedBackupCodes.value = res.backup_codes || []
    setupData.value = null
    verifyCode.value = ''
    mode.value = null
    await loadStatus()
  } catch (e) {
    actionError.value = e.message
  } finally {
    busy.value = false
  }
}

async function doDisable() {
  busy.value = true
  actionError.value = ''
  try {
    await auth.api('/api/auth/mfa/disable', {
      method: 'POST',
      body: { password: disablePassword.value, code: disableCode.value.trim() },
    })
    resetMode()
    await loadStatus()
  } catch (e) {
    actionError.value = e.message
  } finally {
    busy.value = false
  }
}

async function doRegenerate() {
  busy.value = true
  actionError.value = ''
  try {
    const res = await auth.api('/api/auth/mfa/backup-codes/regenerate', {
      method: 'POST',
      body: { code: regenCode.value.trim() },
    })
    generatedBackupCodes.value = res.backup_codes || []
    resetMode()
    await loadStatus()
  } catch (e) {
    actionError.value = e.message
  } finally {
    busy.value = false
  }
}

async function copyCodes() {
  const text = generatedBackupCodes.value.join('\n')
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const el = document.createElement('textarea')
    el.value = text
    document.body.appendChild(el)
    el.select()
    document.execCommand('copy')
    document.body.removeChild(el)
  }
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}

function downloadCodes() {
  const text = `RIMPERIO MFA Backup-Codes\n` +
    `User: ${auth.user?.username || ''}\n` +
    `Erstellt: ${new Date().toISOString()}\n\n` +
    `Jeder Code ist nur EINMAL verwendbar.\n\n` +
    generatedBackupCodes.value.map((c, i) => `${String(i + 1).padStart(2, '0')}. ${c}`).join('\n') + '\n'
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `rimperio-backup-codes-${auth.user?.username || 'user'}.txt`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
</script>
