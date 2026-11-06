<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.05.13.20.58.33
     Beschreibung: RIMPERIO - App-Login via QR-Code
     ============================================================================== -->

<template>
  <div class="p-6 max-w-lg mx-auto">
    <h1 class="text-2xl font-bold mb-2" :style="{ color: 'var(--color-text-primary)' }">
      App-Anmeldung
    </h1>
    <p class="mb-6 text-sm" :style="{ color: 'var(--color-text-muted)' }">
      Scanne den QR-Code mit der RIMPERIO Android-App oder gib die API-URL manuell ein.
    </p>

    <!-- Fehler -->
    <div v-if="error" class="px-4 py-3 rounded mb-4 text-sm" :style="errorStyle">
      {{ error }}
    </div>

    <!-- Loading -->
    <div v-if="loading" class="flex items-center justify-center py-16">
      <span :style="{ color: 'var(--color-text-muted)' }">QR-Code wird generiert...</span>
    </div>

    <!-- QR-Code -->
    <div v-else-if="qrData" class="rounded-lg p-6 text-center" :style="cardStyle">
      <img
        :src="qrData.qr_image"
        alt="App-Login QR-Code"
        class="mx-auto rounded"
        style="max-width: 280px; image-rendering: pixelated;"
      />

      <!-- Ablaufzeit -->
      <div class="mt-4 text-sm" :style="{ color: 'var(--color-text-muted)' }">
        <span v-if="secondsLeft > 0">
          Gültig noch <strong :style="{ color: secondsLeft < 60 ? 'var(--color-danger)' : 'var(--color-text-primary)' }">{{ formatCountdown(secondsLeft) }}</strong>
        </span>
        <span v-else class="font-semibold" :style="{ color: 'var(--color-danger)' }">
          Abgelaufen – bitte neu generieren
        </span>
      </div>

      <!-- API-URL -->
      <div class="mt-4 p-3 rounded text-left" :style="urlBoxStyle">
        <p class="text-xs font-semibold mb-1" :style="{ color: 'var(--color-text-muted)' }">API-URL (für manuelle Eingabe in der App)</p>
        <code class="text-sm break-all" :style="{ color: 'var(--color-text-primary)' }">{{ qrData.api_url }}</code>
        <button @click="copyUrl" class="mt-2 text-xs px-2 py-1 rounded" :style="copyBtnStyle">
          {{ copied ? 'Kopiert!' : 'Kopieren' }}
        </button>
      </div>

      <!-- Methoden-Erklärung -->
      <div class="mt-4 text-left text-sm space-y-2" :style="{ color: 'var(--color-text-secondary)' }">
        <div class="flex gap-2">
          <span class="font-bold">Methode 1 – QR-Code:</span>
          <span>QR-Code mit der App scannen. Die App meldet sich automatisch an.</span>
        </div>
        <div class="flex gap-2">
          <span class="font-bold">Methode 2 – URL:</span>
          <span>API-URL in der App als Server-URL eingeben und mit Benutzername + Passwort einloggen.</span>
        </div>
      </div>
    </div>

    <!-- Aktions-Buttons -->
    <div class="mt-4 flex gap-3">
      <button
        @click="generateQR"
        :disabled="loading"
        class="flex-1 py-2 px-4 rounded font-semibold text-white disabled:opacity-50"
        :style="{ backgroundColor: 'var(--color-primary)' }"
      >
        {{ qrData ? 'Neu generieren' : 'QR-Code generieren' }}
      </button>
    </div>

    <!-- Sicherheitshinweis -->
    <p class="mt-4 text-xs" :style="{ color: 'var(--color-text-muted)' }">
      Der QR-Code ist 15 Minuten gültig und kann nur einmal verwendet werden.
      Nach dem Scannen wird er sofort ungültig.
    </p>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()

const loading = ref(false)
const error = ref('')
const qrData = ref(null)
const secondsLeft = ref(0)
const copied = ref(false)
let countdownTimer = null

// Computed Styles
const cardStyle = computed(() => ({
  backgroundColor: 'var(--color-surface)',
  border: '1px solid var(--color-border)',
}))

const urlBoxStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  border: '1px solid var(--color-border)',
}))

const errorStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  border: '1px solid var(--color-danger)',
  color: 'var(--color-danger)',
}))

const copyBtnStyle = computed(() => ({
  backgroundColor: 'var(--color-surface)',
  border: '1px solid var(--color-border)',
  color: 'var(--color-text-secondary)',
  display: 'block',
}))

function formatCountdown(secs) {
  const m = Math.floor(secs / 60)
  const s = secs % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

function startCountdown(expiresAt) {
  if (countdownTimer) clearInterval(countdownTimer)
  const expiryMs = new Date(expiresAt).getTime()
  const update = () => {
    const diff = Math.max(0, Math.floor((expiryMs - Date.now()) / 1000))
    secondsLeft.value = diff
    if (diff === 0) clearInterval(countdownTimer)
  }
  update()
  countdownTimer = setInterval(update, 1000)
}

async function generateQR() {
  loading.value = true
  error.value = ''
  qrData.value = null
  if (countdownTimer) clearInterval(countdownTimer)
  try {
    const data = await auth.api('/api/auth/app-qr')
    qrData.value = data
    startCountdown(data.expires_at)
  } catch (e) {
    error.value = e.message || 'QR-Code konnte nicht generiert werden'
  } finally {
    loading.value = false
  }
}

async function copyUrl() {
  if (!qrData.value?.api_url) return
  try {
    await navigator.clipboard.writeText(qrData.value.api_url)
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  } catch {
    // Fallback für Browser ohne Clipboard-API
    const el = document.createElement('textarea')
    el.value = qrData.value.api_url
    document.body.appendChild(el)
    el.select()
    document.execCommand('copy')
    document.body.removeChild(el)
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  }
}

onMounted(() => generateQR())
onUnmounted(() => { if (countdownTimer) clearInterval(countdownTimer) })
</script>
