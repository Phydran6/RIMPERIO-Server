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
        <h2 class="page-title">Single Sign-on</h2>
        <p class="page-subtitle">
          Anmelden mit dem Firmenkonto — ohne Zusatzkosten, mit jedem Microsoft-365-Tarif.
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

    <!-- Warum OIDC und nicht SAML - das fragt jeder, der SAML erwartet hat -->
    <div class="card mb-4">
      <div class="card-header"><p class="card-title">Warum OpenID Connect und nicht SAML?</p></div>
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
    </div>

    <!-- Einrichtung im Portal -->
    <div v-if="guide" class="card mb-4">
      <div class="card-header"><p class="card-title">Schritt für Schritt im Entra-Portal</p></div>
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
    </div>

    <!-- Einstellung -->
    <div class="card mb-4">
      <div class="card-header"><p class="card-title">Einstellung</p></div>
      <div class="card-body space-y-4">
        <label class="flex items-start gap-3">
          <input v-model="config.enabled" type="checkbox" class="mt-1">
          <span>
            <strong style="color: var(--color-text-primary)">Single Sign-on einschalten</strong>
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
import { ref, reactive, computed, onMounted } from 'vue'
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
})

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
</script>
