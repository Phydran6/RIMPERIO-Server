<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.08.02.14.00.00
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Anmeldung (Passwort + optional MFA-Code)
     ============================================================================== -->

<template>
  <div class="login-page">
    <!-- Linke Haelfte: Marke. Auf schmalen Schirmen ausgeblendet. -->
    <section class="login-brand">
      <div class="login-brand-inner">
        <div class="brand-mark">
          <img v-if="logoUrl" :src="logoUrl" :alt="companyName" class="brand-logo">
          <span v-else>{{ brandInitial }}</span>
        </div>
        <h1 class="login-brand-title">{{ companyName }}</h1>
        <p class="login-brand-tagline">{{ tagline }}</p>

        <ul class="login-points">
          <li v-for="point in points" :key="point">
            <AppIcon name="check" :size="16" />
            <span>{{ point }}</span>
          </li>
        </ul>
      </div>
    </section>

    <!-- Rechte Haelfte: Formular -->
    <section class="login-form-side">
      <div class="login-card">
        <!-- Marke klein, wenn die linke Haelfte fehlt -->
        <div class="login-card-brand lg:hidden">
          <div class="brand-mark">
            <img v-if="logoUrl" :src="logoUrl" :alt="companyName" class="brand-logo">
            <span v-else>{{ brandInitial }}</span>
          </div>
          <div>
            <p class="font-semibold" style="color: var(--color-text-primary)">{{ companyName }}</p>
            <p class="text-xs" style="color: var(--color-text-muted)">{{ tagline }}</p>
          </div>
        </div>

        <h2 class="login-title">{{ mfaToken ? 'Bestätigung' : 'Anmelden' }}</h2>
        <p class="login-subtitle">
          {{ mfaToken
            ? 'Gib den Code aus deiner Authenticator-App oder einen Backup-Code ein.'
            : 'Melde dich mit deinem Konto an.' }}
        </p>

        <transition name="slide">
          <div v-if="error" class="login-error" role="alert">
            <AppIcon name="warning" :size="18" class="shrink-0" />
            <span>{{ error }}</span>
          </div>
        </transition>

        <!-- Schritt 1: Benutzername + Passwort -->
        <form v-if="!mfaToken" class="space-y-4" @submit.prevent="handleLogin">
          <div>
            <label for="login-username" class="label">Benutzername</label>
            <input
              id="login-username"
              v-model="username"
              type="text"
              class="input"
              autocomplete="username"
              required
              autofocus
            >
          </div>

          <div>
            <label for="login-password" class="label">Passwort</label>
            <div class="password-wrap">
              <input
                id="login-password"
                v-model="password"
                :type="showPassword ? 'text' : 'password'"
                class="input pr-11"
                autocomplete="current-password"
                required
              >
              <button
                type="button"
                class="password-toggle"
                :title="showPassword ? 'Passwort verbergen' : 'Passwort anzeigen'"
                :aria-label="showPassword ? 'Passwort verbergen' : 'Passwort anzeigen'"
                @click="showPassword = !showPassword"
              >
                <AppIcon :name="showPassword ? 'close' : 'lock'" :size="16" />
              </button>
            </div>
          </div>

          <button type="submit" class="btn btn-primary w-full" :disabled="loading">
            {{ loading ? 'Anmelden…' : 'Anmelden' }}
          </button>

          <!-- Passkey: Windows Hello, Face ID, Fingerabdruck, Sicherheitsschlüssel -->
          <template v-if="passkeySupported || sso.enabled">
            <div class="divider-text"><span>oder</span></div>
            <button
              v-if="passkeySupported"
              type="button"
              class="btn btn-secondary w-full"
              :disabled="passkeyBusy"
              @click="handlePasskey"
            >
              <AppIcon name="lock" :size="16" />
              {{ passkeyBusy ? 'Warte auf Gerät…' : 'Mit Passkey anmelden' }}
            </button>

            <!-- Firmenkonto (Microsoft 365 / OpenID Connect).

                 Die Anmeldung mit Benutzername und Passwort bleibt bewusst
                 daneben stehen: wäre sie weg, würde ein Fehler beim
                 Identitätsanbieter alle aussperren - auch den Administrator,
                 der ihn wieder geradeziehen müsste. -->
            <button
              v-if="sso.enabled"
              type="button"
              class="btn btn-secondary w-full"
              :disabled="ssoBusy"
              @click="startSso"
            >
              <AppIcon name="shield" :size="16" />
              {{ ssoBusy ? 'Weiterleitung…' : (sso.label || 'Mit Firmenkonto anmelden') }}
            </button>
          </template>
        </form>

        <!-- Schritt 2: MFA-Code -->
        <form v-else class="space-y-4" @submit.prevent="handleMfa">
          <div>
            <label for="mfa-code" class="label">Code</label>
            <input
              id="mfa-code"
              v-model="mfaCode"
              type="text"
              inputmode="text"
              autocomplete="one-time-code"
              class="input code-input"
              placeholder="123456"
              required
              autofocus
            >
            <p class="hint">
              Gültig noch <strong>{{ formatCountdown(mfaSecondsLeft) }}</strong> — ein Backup-Code geht auch.
            </p>
          </div>

          <button type="submit" class="btn btn-primary w-full" :disabled="loading || mfaSecondsLeft <= 0">
            {{ loading ? 'Prüfe Code…' : 'Code bestätigen' }}
          </button>
          <button type="button" class="btn btn-ghost w-full" @click="resetToStep1">Abbrechen</button>
        </form>

      </div>

      <p class="login-version">v{{ appVersion }}</p>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useThemeStore } from '../stores/themeStore'
import { useBrandingStore } from '../stores/brandingStore'
import AppIcon from '../components/AppIcon.vue'
import { isSupported as passkeyAvailable, describeError as describePasskeyError } from '../utils/webauthn'
import pkg from '../../package.json'

const router = useRouter()
const auth = useAuthStore()
const themeStore = useThemeStore()
const brandingStore = useBrandingStore()

const appVersion = pkg.version

const username = ref('')
const password = ref('')
const showPassword = ref(false)
const loading = ref(false)
const error = ref('')

// Passkey
const passkeySupported = passkeyAvailable()
const passkeyBusy = ref(false)

// MFA-Schritt 2
const mfaToken = ref(null)
const mfaCode = ref('')
const mfaSecondsLeft = ref(0)

// Single Sign-on. Der Anmeldeschirm fragt nur, OB es den Knopf gibt - Mandant
// und Anwendungs-ID gehen niemanden etwas an, der noch nicht angemeldet ist.
const sso = ref({ enabled: false, label: '' })
const ssoBusy = ref(false)
let mfaTimer = null

const companyName = computed(() => brandingStore.config?.company_name || 'RIMPERIO')
const tagline = computed(() => brandingStore.config?.tagline || 'Zentraler Log-Server')
const logoUrl = computed(() => brandingStore.getLogoUrl())
const brandInitial = computed(() => (companyName.value || 'L').trim().charAt(0).toUpperCase())

const points = [
  'Logs aller Geräte an einer Stelle',
  'Filter nach Gerät, Schweregrad und Logtyp',
  'Verschlüsselter Empfang über HTTPS-Agenten',
]

onMounted(() => {
  if (!document.documentElement.getAttribute('data-theme')) {
    themeStore.initTheme('dark')
  }
  loadSsoStatus()
  finishSso()
})

async function loadSsoStatus() {
  try {
    const res = await fetch('/api/auth/sso/status')
    if (res.ok) sso.value = await res.json()
  } catch {
    // Ohne Antwort bleibt der Knopf einfach weg - anmelden geht weiterhin.
  }
}

function startSso() {
  ssoBusy.value = true
  // Vollständiger Seitenwechsel, kein fetch: der Anbieter will den Browser
  // sehen, nicht eine Hintergrundanfrage.
  window.location.href = '/api/auth/sso/start'
}

/**
 * Rückkehr vom Anbieter.
 *
 * Der Rückweg landet auf /login?sso=<Einmal-Schlüssel>. Dieser Schlüssel wird
 * über denselben erprobten Endpunkt gegen eine Sitzung getauscht, den auch die
 * App nach dem QR-Code benutzt - ein zweiter, halb paralleler Anmeldeweg wäre
 * genau eine Stelle zu viel, an der etwas schiefgehen kann.
 *
 * Danach verschwindet der Schlüssel sofort aus der Adresszeile: er soll weder
 * im Verlauf noch in einem Lesezeichen landen.
 */
async function finishSso() {
  const params = new URLSearchParams(window.location.search)
  const handoff = params.get('sso')
  const failure = params.get('sso_error')

  if (failure) {
    error.value = failure
    window.history.replaceState({}, '', '/login')
    return
  }
  if (!handoff) return

  loading.value = true
  try {
    const res = await fetch('/api/auth/app-token/exchange', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: handoff }),
    })
    if (!res.ok) throw new Error('Die Anmeldung ist abgelaufen. Bitte noch einmal versuchen.')
    const data = await res.json()
    auth.token = data.access_token
    localStorage.setItem('token', data.access_token)
    await auth.fetchUser()
    window.history.replaceState({}, '', '/login')
    // Nur Pfade auf diesem Server - keine offene Weiterleitung über "next".
    const next = params.get('next') || '/'
    router.push(next.startsWith('/') && !next.startsWith('//') && !next.includes('\\') ? next : '/')
  } catch (e) {
    error.value = e.message
    window.history.replaceState({}, '', '/login')
  } finally {
    loading.value = false
  }
}

onUnmounted(() => {
  if (mfaTimer) clearInterval(mfaTimer)
})

function formatCountdown(secs) {
  if (secs <= 0) return 'abgelaufen'
  const m = Math.floor(secs / 60)
  const s = secs % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

function startMfaCountdown(seconds) {
  if (mfaTimer) clearInterval(mfaTimer)
  mfaSecondsLeft.value = seconds
  mfaTimer = setInterval(() => {
    mfaSecondsLeft.value = Math.max(0, mfaSecondsLeft.value - 1)
    if (mfaSecondsLeft.value === 0) clearInterval(mfaTimer)
  }, 1000)
}

function resetToStep1() {
  mfaToken.value = null
  mfaCode.value = ''
  mfaSecondsLeft.value = 0
  if (mfaTimer) clearInterval(mfaTimer)
  error.value = ''
}

async function handleLogin() {
  loading.value = true
  error.value = ''
  try {
    const result = await auth.login(username.value, password.value)
    if (result?.mfa_required) {
      mfaToken.value = result.mfa_token
      startMfaCountdown(result.expires_in_seconds || 300)
    } else {
      router.push('/')
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function handlePasskey() {
  passkeyBusy.value = true
  error.value = ''
  try {
    // Benutzername ist optional: ist das Feld leer, sucht der Browser selbst
    // einen passenden Passkey für diese Adresse heraus.
    await auth.loginPasskey(username.value.trim())
    router.push('/')
  } catch (e) {
    error.value = e.name ? describePasskeyError(e) : e.message
  } finally {
    passkeyBusy.value = false
  }
}

async function handleMfa() {
  loading.value = true
  error.value = ''
  try {
    await auth.loginMfa(mfaToken.value, mfaCode.value.trim())
    router.push('/')
  } catch (e) {
    error.value = e.message
    mfaCode.value = ''
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 1fr;
  background-color: var(--color-bg);
}

@media (min-width: 1024px) {
  .login-page {
    grid-template-columns: 1.1fr 1fr;
  }
}

/* ------------------------------------------------------------------- Marke */
.login-brand {
  display: none;
  position: relative;
  overflow: hidden;
  padding: 3rem;
  align-items: center;
  background:
    radial-gradient(120% 120% at 0% 0%, var(--primary-soft-strong) 0%, transparent 55%),
    var(--color-surface);
  border-right: 1px solid var(--color-border);
}

@media (min-width: 1024px) {
  .login-brand {
    display: flex;
  }
}

.login-brand-inner {
  max-width: 26rem;
}

.login-brand-title {
  margin-top: 1.5rem;
  font-size: 2rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

.login-brand-tagline {
  margin-top: 0.5rem;
  font-size: 1rem;
  color: var(--color-text-muted);
}

.login-points {
  margin-top: 2rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.login-points li {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  font-size: 0.875rem;
  color: var(--color-text-secondary);
}

.login-points svg {
  color: var(--color-accent);
  flex-shrink: 0;
}

.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 3rem;
  height: 3rem;
  border-radius: var(--radius-lg);
  background-color: var(--primary-soft);
  color: var(--color-primary);
  font-size: 1.25rem;
  font-weight: 700;
  overflow: hidden;
  flex-shrink: 0;
}

.brand-logo {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

/* ---------------------------------------------------------------- Formular */
.login-form-side {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  padding: 2rem 1.25rem;
}

.login-card {
  width: 100%;
  max-width: 24rem;
  padding: 1.75rem;
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
}

.login-card-brand {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1.5rem;
}

.login-title {
  font-size: 1.375rem;
  font-weight: 700;
  color: var(--color-text-primary);
}

.login-subtitle {
  margin-top: 0.25rem;
  margin-bottom: 1.5rem;
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.login-error {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  margin-bottom: 1rem;
  padding: 0.75rem 0.875rem;
  border-radius: var(--radius);
  background-color: var(--danger-soft);
  color: var(--color-danger);
  font-size: 0.875rem;
}

.password-wrap {
  position: relative;
}

.password-toggle {
  position: absolute;
  top: 50%;
  right: 0.5rem;
  transform: translateY(-50%);
  padding: 0.375rem;
  border-radius: var(--radius-sm);
  color: var(--color-text-muted);
}

.password-toggle:hover {
  color: var(--color-text-primary);
  background-color: var(--hover-surface);
}

/* "oder"-Trenner zwischen Passwort- und Passkey-Anmeldung */
.divider-text {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.divider-text::before,
.divider-text::after {
  content: "";
  flex: 1;
  height: 1px;
  background-color: var(--color-border);
}

.code-input {
  text-align: center;
  letter-spacing: 0.35em;
  font-size: 1.125rem;
  font-family: var(--font-mono);
}

.login-version {
  font-size: 0.6875rem;
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}

/* ----------------------------------------------------------------- Übergang */
.slide-enter-active,
.slide-leave-active {
  transition: opacity var(--duration) var(--ease), transform var(--duration) var(--ease);
}

.slide-enter-from,
.slide-leave-to {
  opacity: 0;
  transform: translateY(-0.25rem);
}
</style>
