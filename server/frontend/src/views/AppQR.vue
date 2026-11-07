<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.11.07.23.00.00
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Mein Konto -> App verbinden (QR-Code-Anmeldung)

     Was hier kaputt war:
     * Im QR-Code stand hinter einem externen HTTPS-Proxy (Nginx Proxy Manager)
       "http://...". Die App verbindet nur ueber https und lehnte den Code ab.
       Jetzt schickt die Seite mit, unter welcher Adresse sie selbst offen ist,
       und der Server nimmt die (bzw. eine fest eingestellte, siehe
       Einstellungen -> HTTPS & Adresse).
     * Der Countdown las die Ablaufzeit (UTC, ohne Zeitzone) als Ortszeit - in
       Deutschland stand der Code beim Erscheinen schon auf "abgelaufen". Jetzt
       zaehlt er ab der vom Server genannten Gueltigkeitsdauer.
     ============================================================================== -->

<template>
  <div class="page app-page">
    <div class="page-header">
      <div>
        <p class="page-subtitle">
          Mit der RIMPERIO-App auf dem Handy scannen – die App meldet sich dann als
          <strong>{{ auth.user?.username }}</strong> an.
        </p>
      </div>
      <button class="btn btn-secondary btn-sm" :disabled="loading" @click="generateQR">
        <AppIcon name="refresh" :size="16" /> {{ qrData ? 'Neuen Code erzeugen' : 'Code erzeugen' }}
      </button>
    </div>

    <div v-if="error" class="card mb-4 notice is-error">{{ error }}</div>

    <div class="app-grid">
      <!-- QR-Code -->
      <section class="card">
        <div class="card-body qr-body">
          <div v-if="loading" class="qr-placeholder">QR-Code wird erzeugt …</div>
          <template v-else-if="qrData">
            <img
              :src="qrData.qr_image"
              alt="QR-Code für die App-Anmeldung"
              class="qr-image"
              :class="{ 'is-expired': secondsLeft === 0 }"
            >
            <p class="qr-timer">
              <template v-if="secondsLeft > 0">
                Gültig noch <strong :class="{ 'is-urgent': secondsLeft < 60 }">{{ formatCountdown(secondsLeft) }}</strong>
              </template>
              <template v-else>
                <strong class="is-urgent">Abgelaufen</strong> –
                <button class="link" @click="generateQR">neuen Code erzeugen</button>
              </template>
            </p>
          </template>
        </div>
      </section>

      <!-- Adresse und Anleitung -->
      <section class="card">
        <div class="card-header"><h2 class="card-title">Adresse im Code</h2></div>
        <div class="card-body space-y-3">
          <div v-if="qrData" class="url-box">
            <code class="url-text">{{ qrData.api_url }}</code>
            <button class="btn btn-secondary btn-sm" @click="copyUrl">{{ copied ? 'Kopiert' : 'Kopieren' }}</button>
          </div>
          <p v-if="qrData" class="hint">{{ sourceText }}</p>

          <div v-if="qrData?.warning" class="notice is-warning">
            <strong>Die App wird diesen Code ablehnen.</strong> {{ qrData.warning }}
            <router-link v-if="auth.isAdmin" to="/settings/network" class="link block mt-1">
              → Einstellungen · HTTPS &amp; Adresse
            </router-link>
          </div>

          <ol class="steps">
            <li>App öffnen und <strong>„QR-Code scannen“</strong> wählen.</li>
            <li>Diesen Code scannen. Die App meldet sich sofort an.</li>
            <li>Ohne Kamera: in der App die Adresse oben eintragen und mit Benutzername und Passwort anmelden.</li>
          </ol>

          <p class="hint">
            Der Code gilt 15 Minuten und nur einmal. Jedes neue Erzeugen macht den vorigen ungültig.
          </p>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'

const auth = useAuthStore()

const loading = ref(false)
const error = ref('')
const qrData = ref(null)
const secondsLeft = ref(0)
const copied = ref(false)
let countdownTimer = null

const SOURCE_TEXT = {
  site_url: 'Fest eingestellt über SITE_URL in der .env.',
  saved: 'Fest eingestellt unter Einstellungen · HTTPS & Adresse.',
  browser: 'Die Adresse, unter der diese Seite gerade offen ist.',
  detected: 'Die https-Adresse, unter der RIMPERIO über den externen Proxy erreichbar ist.',
  headers: 'Aus der Anfrage ermittelt.',
}
const sourceText = computed(() => SOURCE_TEXT[qrData.value?.url_source] || '')

function formatCountdown(secs) {
  const m = Math.floor(secs / 60)
  const s = secs % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

// Ab der Gueltigkeitsdauer zaehlen statt ab der Uhrzeit: so spielen weder
// Zeitzone noch eine falsch gehende Uhr im Browser eine Rolle.
function startCountdown(seconds) {
  if (countdownTimer) clearInterval(countdownTimer)
  const end = Date.now() + Math.max(0, Number(seconds) || 0) * 1000
  const update = () => {
    const diff = Math.max(0, Math.round((end - Date.now()) / 1000))
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
    const origin = encodeURIComponent(window.location.origin)
    const data = await auth.api(`/api/auth/app-qr?origin=${origin}`, { cache: 'no-store' })
    qrData.value = data
    startCountdown(data.expires_in_seconds || 900)
  } catch (e) {
    error.value = e.message || 'QR-Code konnte nicht erzeugt werden'
  } finally {
    loading.value = false
  }
}

async function copyUrl() {
  if (!qrData.value?.api_url) return
  try {
    await navigator.clipboard.writeText(qrData.value.api_url)
  } catch {
    const el = document.createElement('textarea')
    el.value = qrData.value.api_url
    document.body.appendChild(el)
    el.select()
    document.execCommand('copy')
    document.body.removeChild(el)
  }
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}

onMounted(() => generateQR())
onUnmounted(() => { if (countdownTimer) clearInterval(countdownTimer) })
</script>

<style scoped>
.app-page {
  max-width: 64rem;
}

.app-grid {
  display: grid;
  grid-template-columns: minmax(0, 20rem) minmax(0, 1fr);
  gap: 1rem;
  align-items: start;
}

@media (max-width: 768px) {
  .app-grid {
    grid-template-columns: 1fr;
  }
}

.qr-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
}

.qr-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  aspect-ratio: 1;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.qr-image {
  width: 100%;
  max-width: 17rem;
  border-radius: var(--radius);
  image-rendering: pixelated;
  background: #fff;
}

.qr-image.is-expired {
  opacity: 0.2;
  filter: grayscale(1);
}

.qr-timer {
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.is-urgent {
  color: var(--color-danger);
}

.url-box {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background-color: var(--color-surface-elevated);
}

.url-text {
  flex: 1;
  min-width: 0;
  word-break: break-all;
  font-size: 0.875rem;
  color: var(--color-text-primary);
}

.notice {
  padding: 0.75rem 1rem;
  border-radius: var(--radius);
  font-size: 0.875rem;
  line-height: 1.5;
}

.notice.is-warning {
  border: 1px solid var(--color-warning);
  background-color: var(--warning-soft);
  color: var(--color-text-primary);
}

.notice.is-error {
  border: 1px solid var(--color-danger);
  background-color: var(--danger-soft);
  color: var(--color-danger);
}

.steps {
  padding-left: 1.25rem;
  list-style: decimal;
  font-size: 0.875rem;
  color: var(--color-text-secondary);
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}
</style>
