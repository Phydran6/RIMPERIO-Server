<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Anmeldung ueber Microsoft 365 (OpenID Connect).

     Warum eine eigene Seite und kein Reiter: hier steht die Rueckadresse, die
     man im Entra-Portal eintragen muss, und eine Schritt-fuer-Schritt-Anleitung.
     Das braucht Platz, und man hat es beim Einrichten neben dem anderen
     Browserfenster offen.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">Microsoft 365 / Entra ID</h2>
        <p class="page-subtitle">
          Anmelden mit dem Firmenkonto (Single Sign-on über Microsoft 365) — ohne Zusatzkosten, mit jedem Tarif. Automatisch per Microsoft-Anmeldung einrichten oder die App-Registrierung von Hand eintragen.
        </p>
      </div>
      <span class="badge" :class="config.enabled ? 'badge-success' : 'badge-neutral'">
        {{ config.enabled ? 'eingeschaltet' : 'aus' }}
      </span>
    </div>

    <div v-if="message" class="card mb-4" :style="{ borderColor: messageError ? 'var(--color-danger)' : 'var(--color-success)' }">
      <div class="card-body text-sm" :style="{ color: messageError ? 'var(--color-danger)' : 'var(--color-success)' }">
        {{ message }}
      </div>
    </div>

    <!-- Automatisch einrichten ------------------------------------------------ -->
    <div class="card mb-4">
      <div class="card-header">
        <p class="card-title">Automatisch einrichten (empfohlen)</p>
        <span v-if="config.setup_mode === 'auto' && config.client_id" class="badge badge-success">eingerichtet</span>
      </div>
      <div class="card-body space-y-3 text-sm" style="color: var(--color-text-secondary)">
        <p>
          Ein Fenster mit der Microsoft-Anmeldung geht auf. Mit einem Konto anmelden, das in Entra ID
          App-Registrierungen anlegen und Admin-Zustimmungen erteilen darf (Globaler Administrator) —
          RIMPERIO legt sich dann selbst die App-Registrierung an, trägt die Rückadresse ein, erteilt die
          Zustimmung für alle Benutzer, macht Sie zum RIMPERIO-Administrator und schaltet die Anmeldung ein.
          Ihr Microsoft-Token wird danach verworfen.
        </p>
        <p v-if="config.setup_mode === 'auto' && config.setup_at" class="hint">
          Zuletzt eingerichtet {{ new Date(config.setup_at).toLocaleString('de-DE') }} von {{ config.setup_by }}
          <template v-if="config.secret_expires"> · Geheimnis gültig bis {{ config.secret_expires.slice(0, 10) }}</template>
        </p>

        <div v-if="!flow" class="flex flex-wrap gap-2">
          <button class="btn btn-primary btn-sm" :disabled="flowBusy" @click="startAuto">
            {{ flowBusy ? 'Starte…' : (config.setup_mode === 'auto' && config.client_id ? 'Neu einrichten' : 'Mit Microsoft 365 anmelden und einrichten') }}
          </button>
        </div>

        <div v-else class="auto-flow">
          <template v-if="flow.status === 'waiting'">
            <p>
              Im Microsoft-Fenster diesen Code eingeben (er ist schon in der Zwischenablage),
              dann anmelden und zustimmen:
            </p>
            <div class="flex flex-wrap items-center gap-2">
              <span class="device-code">{{ flow.user_code }}</span>
              <button class="btn btn-secondary btn-sm" @click="copy(flow.user_code)">{{ copied ? 'Kopiert' : 'Code kopieren' }}</button>
              <button class="btn btn-secondary btn-sm" @click="openMicrosoft">Microsoft-Fenster öffnen ↗</button>
            </div>
            <p class="hint">Wartet auf die Anmeldung …</p>
          </template>
          <ul class="flow-steps">
            <li v-for="st in flow.steps" :key="st.id" :class="'is-' + st.status">
              <span class="flow-mark">{{ MARKS[st.status] }}</span>
              <span>{{ st.label }}<span v-if="st.detail" class="hint"> — {{ st.detail }}</span></span>
            </li>
          </ul>
          <p v-if="flow.status === 'done'" style="color: var(--color-success)">
            Fertig. Die Anmeldung mit Microsoft 365 ist eingeschaltet — zum Prüfen abmelden und den
            Microsoft-Knopf auf der Anmeldeseite benutzen.
          </p>
          <p v-if="flow.error" style="color: var(--color-danger)">{{ flow.error }}</p>
          <div class="flex gap-2">
            <button v-if="['waiting', 'working'].includes(flow.status)" class="btn btn-ghost btn-sm" @click="cancelAuto">Abbrechen</button>
            <button v-else class="btn btn-secondary btn-sm" @click="flow = null">Schließen</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Warum OIDC und nicht SAML - das fragt jeder, der SAML erwartet hat -->
    <details class="card mb-4">
      <summary class="card-header cursor-pointer"><p class="card-title">Warum OpenID Connect und nicht SAML?</p></summary>
      <div class="card-body text-sm space-y-2" style="color: var(--color-text-secondary)">
        <p>
          Beides erledigt dieselbe Aufgabe. Der Unterschied liegt in der Rechnung:
          <strong>SAML-Anmeldung für eine eigene, nicht im Katalog gelistete Anwendung
          verlangt bei Microsoft einen kostenpflichtigen Entra-ID-Plan.</strong>
          Eine App-Registrierung mit OpenID Connect — also OAuth 2.0 mit
          Identitätsschicht obendrauf — ist in jedem Microsoft-365-Tarif enthalten,
          auch im kostenlosen.
        </p>
        <p>
          Sicherheitstechnisch gelten beide als gleichwertig; OIDC ist der neuere Weg und
          der, den Microsoft selbst empfiehlt. Praktisch heißt das: das hier funktioniert
          mit dem Tarif, den Sie ohnehin haben.
        </p>
        <p>
          Andere Anbieter gehen über dieselbe Einstellung: Keycloak, Authentik,
          Google Workspace, Okta — alles, was ein Discovery-Dokument hat.
        </p>
      </div>
    </details>

    <!-- Einrichtung im Portal -->
    <details v-if="guide" class="card mb-4">
      <summary class="card-header cursor-pointer"><p class="card-title">Von Hand einrichten: Schritt für Schritt im Entra-Portal</p></summary>
      <div class="card-body">
        <div class="mb-3">
          <label class="label">Diese Rückadresse dort eintragen (Umleitungs-URI, Typ „Web")</label>
          <div class="flex gap-2">
            <input :value="guide.redirect_uri" class="input font-mono text-xs" readonly>
            <button class="btn btn-secondary btn-sm shrink-0" @click="copy(guide.redirect_uri)">
              <AppIcon name="check" v-if="copied" :size="14" />
              {{ copied ? 'Kopiert' : 'Kopieren' }}
            </button>
          </div>
        </div>
        <ol class="text-sm space-y-1.5 list-decimal pl-5" style="color: var(--color-text-secondary)">
          <li v-for="(step, index) in guide.steps" :key="index">{{ step }}</li>
        </ol>
        <p class="hint mt-3">{{ guide.note }}</p>
      </div>
    </details>

    <!-- Einstellung -->
    <div class="card mb-4">
      <div class="card-header"><p class="card-title">Einstellung (auch für die manuelle Einrichtung)</p></div>
      <div class="card-body space-y-4">
        <label class="flex items-start gap-3">
          <input v-model="config.enabled" type="checkbox" class="mt-1">
          <span>
            <strong style="color: var(--color-text-primary)">Anmeldung mit Microsoft 365 / OpenID Connect einschalten</strong>
            <span class="hint block">
              Auf dem Anmeldeschirm erscheint dann zusätzlich ein Knopf. Die Anmeldung mit
              Benutzername und Passwort bleibt daneben bestehen — sonst könnte ein Fehler
              in der Einrichtung alle aussperren.
            </span>
          </span>
        </label>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label class="label">Anbieter</label>
            <select v-model="config.provider" class="select">
              <option v-for="provider in providers" :key="provider.id" :value="provider.id">
                {{ provider.label }}
              </option>
            </select>
            <p class="hint">{{ providerHint }}</p>
          </div>
          <div>
            <label class="label">Beschriftung des Knopfes</label>
            <input v-model="config.label" class="input" placeholder="Mit Microsoft 365 anmelden">
          </div>

          <div v-if="config.provider === 'entra'">
            <label class="label">Verzeichnis-ID (Mandant)</label>
            <input v-model="config.tenant_id" class="input font-mono text-sm" placeholder="00000000-0000-0000-0000-000000000000">
          </div>
          <div v-else>
            <label class="label">Discovery-Dokument</label>
            <input v-model="config.discovery_url" class="input font-mono text-sm"
                   placeholder="https://…/.well-known/openid-configuration">
          </div>

          <div>
            <label class="label">Anwendungs-ID (Client)</label>
            <input v-model="config.client_id" class="input font-mono text-sm">
          </div>

          <div>
            <label class="label">
              Client-Geheimnis
              <span v-if="config.client_secret_set" class="badge badge-success ml-1">hinterlegt</span>
            </label>
            <input v-model="secret" type="password" class="input"
                   :placeholder="config.client_secret_set ? 'Unverändert lassen' : 'Aus dem Portal einfügen'">
            <p class="hint">
              Wird nie wieder angezeigt — auch hier nicht. Leer lassen behält das
              vorhandene Geheimnis.
            </p>
          </div>

          <div>
            <label class="label">Rückadresse (optional)</label>
            <input v-model="config.redirect_uri" class="input font-mono text-xs"
                   :placeholder="guide?.redirect_uri || ''">
            <p class="hint">Leer lassen: wird aus SITE_URL bzw. der Anfrage bestimmt.</p>
          </div>
        </div>

        <div class="divider" />

        <p class="section-title">Wer darf herein, und als was?</p>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label class="label">Zugelassene Domänen</label>
            <input v-model="config.allowed_domains" class="input" placeholder="firma.de, firma.com">
            <p class="hint">Leer = alle Konten des Mandanten.</p>
          </div>
          <div>
            <label class="label">Standardrolle</label>
            <select v-model="config.default_role" class="select">
              <option value="user">Benutzer (empfohlen)</option>
              <option value="admin">Administrator</option>
            </select>
          </div>
          <div>
            <label class="label">Admin-Gruppen (Objekt-IDs)</label>
            <input v-model="config.admin_groups" class="input font-mono text-xs"
                   placeholder="0000…, 1111…">
            <p class="hint">
              Wer in einer dieser Gruppen ist, wird Administrator. Dafür muss im Portal
              unter „Tokenkonfiguration" der Gruppenanspruch aktiviert sein.
            </p>
          </div>
          <div>
            <label class="label">Oder: App-Rolle für Administratoren</label>
            <input v-model="config.admin_role_claim" class="input font-mono text-sm" placeholder="RIMPERIO.Admin">
          </div>
        </div>

        <label class="flex items-start gap-3">
          <input v-model="config.auto_create_users" type="checkbox" class="mt-1">
          <span>
            <strong style="color: var(--color-text-primary)">Konten beim ersten Anmelden anlegen</strong>
            <span class="hint block">
              Aus: Es kommt nur herein, wer hier schon ein Konto hat.
            </span>
          </span>
        </label>

        <div class="flex flex-wrap gap-2">
          <button class="btn btn-primary btn-sm" :disabled="saving" @click="save">
            {{ saving ? 'Wird gespeichert…' : 'Speichern' }}
          </button>
          <button class="btn btn-secondary btn-sm" :disabled="testing" @click="test">
            {{ testing ? 'Prüfe…' : 'Verbindung prüfen' }}
          </button>
        </div>

        <div v-if="testResult" class="rounded p-3 text-sm"
             :style="{ backgroundColor: testResult.ok ? 'var(--success-soft)' : 'var(--danger-soft)' }">
          <p :style="{ color: testResult.ok ? 'var(--color-success)' : 'var(--color-danger)' }">
            {{ testResult.message }}
          </p>
          <p v-if="testResult.issuer" class="text-xs mt-1 break-all" style="color: var(--color-text-muted)">
            Aussteller: {{ testResult.issuer }} · {{ testResult.keys }} Schlüssel geladen
          </p>
        </div>
      </div>
    </div>

    <p class="hint">
      Jede Anmeldung — und jede abgewiesene — steht im
      <router-link to="/journal" class="link">Systemtagebuch</router-link>.
    </p>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'

const auth = useAuthStore()

const config = reactive({
  enabled: false,
  provider: 'entra',
  label: '',
  tenant_id: '',
  client_id: '',
  client_secret_set: false,
  discovery_url: '',
  redirect_uri: '',
  auto_create_users: true,
  default_role: 'user',
  admin_groups: '',
  admin_role_claim: '',
  allowed_domains: '',
  setup_mode: 'manual',
  setup_at: '',
  setup_by: '',
  secret_expires: '',
})

// ---------------------------------------------------------------- Automatik
const MARKS = { ok: '✓', error: '✕', warn: '!', running: '…', pending: '·' }
const flow = ref(null)
const flowBusy = ref(false)
let flowTimer = null
let msWindow = null

function openMicrosoft() {
  const url = flow.value?.verification_uri || 'https://microsoft.com/devicelogin'
  msWindow = window.open(url, 'rimperio-m365', 'width=520,height=720')
}

async function startAuto() {
  flowBusy.value = true
  message.value = ''
  // Fenster sofort öffnen (noch im Klick), sonst blockt der Browser das Popup.
  msWindow = window.open('about:blank', 'rimperio-m365', 'width=520,height=720')
  try {
    flow.value = await auth.api('/api/sso/m365/start', { method: 'POST' })
    if (msWindow && !msWindow.closed) msWindow.location.href = flow.value.verification_uri
    try { await navigator.clipboard.writeText(flow.value.user_code) } catch { /* Knopf zum Kopieren ist da */ }
    pollAuto()
  } catch (e) {
    if (msWindow && !msWindow.closed) msWindow.close()
    message.value = e.message
    messageError.value = true
  } finally {
    flowBusy.value = false
  }
}

function pollAuto() {
  clearTimeout(flowTimer)
  flowTimer = setTimeout(async () => {
    if (!flow.value) return
    try {
      flow.value = await auth.api('/api/sso/m365/status/' + flow.value.flow)
    } catch (e) {
      flow.value = { ...flow.value, status: 'error', error: e.message }
    }
    if (['waiting', 'working'].includes(flow.value.status)) {
      pollAuto()
    } else {
      if (msWindow && !msWindow.closed) msWindow.close()
      await load()
    }
  }, 3000)
}

async function cancelAuto() {
  clearTimeout(flowTimer)
  if (flow.value) await auth.api('/api/sso/m365/' + flow.value.flow, { method: 'DELETE' }).catch(() => {})
  if (msWindow && !msWindow.closed) msWindow.close()
  flow.value = null
}

const providers = ref([])
const guide = ref(null)
const secret = ref('')
const saving = ref(false)
const testing = ref(false)
const testResult = ref(null)
const message = ref('')
const messageError = ref(false)
const copied = ref(false)

const providerHint = computed(() => {
  const found = providers.value.find(entry => entry.id === config.provider)
  return found?.hint || ''
})

function apply(incoming) {
  Object.keys(config).forEach((key) => {
    if (incoming[key] !== undefined) config[key] = incoming[key]
  })
}

async function load() {
  try {
    const data = await auth.api('/api/sso/config')
    apply(data.config)
    providers.value = data.providers || []
    guide.value = data.guide
  } catch (e) {
    message.value = e.message
    messageError.value = true
  }
}

async function save() {
  saving.value = true
  message.value = ''
  try {
    const body = { ...config }
    delete body.client_secret_set
    if (secret.value) body.client_secret = secret.value
    const data = await auth.api('/api/sso/config', { method: 'PUT', body })
    apply(data.config)
    guide.value = data.guide
    secret.value = ''
    message.value = 'Gespeichert.'
    messageError.value = false
  } catch (e) {
    message.value = e.message
    messageError.value = true
  } finally {
    saving.value = false
  }
}

async function test() {
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await auth.api('/api/sso/test', { method: 'POST' })
  } catch (e) {
    testResult.value = { ok: false, message: e.message }
  } finally {
    testing.value = false
  }
}

async function copy(value) {
  try {
    await navigator.clipboard.writeText(value)
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  } catch {
    // Ohne Zwischenablage-Recht bleibt das Feld zum Markieren da.
  }
}

onMounted(load)
onBeforeUnmount(() => clearTimeout(flowTimer))
</script>

<style scoped>
.auto-flow { display: flex; flex-direction: column; gap: .75rem; }
.device-code { font-family: ui-monospace, Consolas, monospace; font-size: 1.5rem; font-weight: 700; letter-spacing: .15em; padding: .25rem .75rem; border-radius: var(--radius); background: var(--primary-soft); color: var(--color-primary); }
.flow-steps { display: flex; flex-direction: column; gap: .3rem; }
.flow-steps li { display: flex; gap: .5rem; }
.flow-mark { width: 1rem; text-align: center; font-weight: 700; }
.flow-steps li.is-ok .flow-mark { color: var(--color-success); }
.flow-steps li.is-error .flow-mark { color: var(--color-danger); }
.flow-steps li.is-warn .flow-mark { color: var(--color-warning); }
.flow-steps li.is-pending { opacity: .6; }
details > summary { list-style: none; }
details > summary::-webkit-details-marker { display: none; }
</style>
