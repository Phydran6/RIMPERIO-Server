<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Einrichtungsassistent je Mail-Anbieter.

     Eine Komponente fuer alle Anbieter; was abgefragt wird, sagt `provider.kind`:
       m365      - Anmeldung bei Microsoft per Geraetecode, das Backend legt die
                   App-Registrierung selbst an (Fortschritt live).
       preset    - Vorbereitung beim Anbieter (Links), dann Adresse + Passwort.
                   Server/Port/Verschluesselung setzt das Backend fest.
       smtp      - beliebiger Mailserver, alle Felder.
       container - mitgelieferter Postfix, optional mit Smarthost.
     Gespeichert wird erst, wenn die Anmeldung geprueft ist (POST /verify).
     ============================================================================== -->

<template>
  <teleport to="body">
    <div class="modal-backdrop" @click.self="close">
      <div class="modal wizard" role="dialog" aria-modal="true" :aria-label="provider.name">
        <div class="card-header">
          <div>
            <h2 class="card-title">{{ provider.name }} einrichten</h2>
            <p class="hint mt-1">{{ provider.hint }}</p>
          </div>
          <ol class="wizard-steps" aria-label="Schritte">
            <li v-for="(label, index) in stepLabels" :key="label"
                :class="{ 'is-current': index === stepIndex, 'is-done': index < stepIndex }">
              {{ label }}
            </li>
          </ol>
        </div>

        <div class="card-body space-y-4 wizard-body">
          <p v-if="error" class="w-alert is-error">{{ error }}</p>

          <!-- =============================== Vorbereitung (Vorlagen) ===== -->
          <template v-if="step === 'prep'">
            <p class="text-sm">Bevor RIMPERIO sich anmelden kann, muss beim Anbieter
              einmal etwas eingeschaltet werden:</p>
            <ol class="prep-list">
              <li v-for="(item, index) in provider.prep" :key="index">
                <span>{{ item.text }}</span>
                <a v-if="item.link" :href="item.link" target="_blank" rel="noopener noreferrer"
                   class="btn btn-secondary btn-sm">{{ item.link_label || 'Öffnen' }} ↗</a>
              </li>
            </ol>
            <ul v-if="provider.notes?.length" class="note-list">
              <li v-for="note in provider.notes" :key="note">{{ note }}</li>
            </ul>
          </template>

          <!-- =============================== Zugangsdaten (Vorlage) ====== -->
          <template v-if="step === 'creds' && provider.kind === 'preset'">
            <div class="grid gap-3 sm:grid-cols-2">
              <div class="sm:col-span-2">
                <label class="label">E-Mail-Adresse</label>
                <input v-model.trim="form.from_address" type="email" class="input"
                       :placeholder="placeholderAddress" autocomplete="off">
              </div>
              <div class="sm:col-span-2">
                <label class="label">{{ provider.password_label || 'Passwort' }}</label>
                <input v-model="form.smtp_password" type="password" class="input"
                       autocomplete="new-password"
                       :placeholder="keepsPassword ? '•••••••• (unverändert)' : ''">
              </div>
              <div>
                <label class="label">Absendername</label>
                <input v-model="form.from_name" type="text" class="input">
              </div>
              <div>
                <label class="label">Server</label>
                <p class="fixed-value">{{ provider.host }}:{{ provider.port }} ·
                  {{ provider.encryption === 'tls' ? 'SSL/TLS' : 'STARTTLS' }}</p>
              </div>
            </div>
            <ul v-if="provider.notes?.length && !provider.prep?.length" class="note-list">
              <li v-for="note in provider.notes" :key="note">{{ note }}</li>
            </ul>
          </template>

          <!-- =============================== Zugangsdaten (frei) ========= -->
          <template v-if="step === 'creds' && provider.kind === 'smtp'">
            <div class="grid gap-3 sm:grid-cols-3">
              <div class="sm:col-span-2">
                <label class="label">Mailserver</label>
                <input v-model.trim="form.smtp_host" type="text" class="input" placeholder="mail.example.com">
              </div>
              <div>
                <label class="label">Port</label>
                <input v-model.number="form.smtp_port" type="number" min="1" max="65535" class="input">
              </div>
            </div>
            <div class="grid gap-3 sm:grid-cols-2">
              <div>
                <label class="label">Verschlüsselung</label>
                <select v-model="form.encryption" class="select" @change="suggestPort">
                  <option value="starttls">STARTTLS (üblich, Port 587)</option>
                  <option value="tls">SSL/TLS von Anfang an (Port 465)</option>
                  <option value="none">Unverschlüsselt (nur im eigenen Netz)</option>
                </select>
              </div>
              <div class="flex items-end">
                <label class="toggle">
                  <input v-model="form.verify_certificate" type="checkbox">
                  <span>Zertifikat prüfen</span>
                </label>
              </div>
              <div>
                <label class="label">Benutzer <span class="hint">(leer = ohne Anmeldung)</span></label>
                <input v-model.trim="form.smtp_user" type="text" class="input" autocomplete="off">
              </div>
              <div>
                <label class="label">Passwort</label>
                <input v-model="form.smtp_password" type="password" class="input"
                       autocomplete="new-password"
                       :placeholder="keepsPassword ? '•••••••• (unverändert)' : ''">
              </div>
              <div>
                <label class="label">Absenderadresse</label>
                <input v-model.trim="form.from_address" type="email" class="input" placeholder="rimperio@example.com">
              </div>
              <div>
                <label class="label">Absendername</label>
                <input v-model="form.from_name" type="text" class="input">
              </div>
            </div>
          </template>

          <!-- =============================== Postfix-Container =========== -->
          <template v-if="step === 'creds' && provider.kind === 'container'">
            <div class="grid gap-3 sm:grid-cols-2">
              <div>
                <label class="label">Absenderadresse</label>
                <input v-model.trim="form.from_address" type="email" class="input" placeholder="rimperio@example.com">
              </div>
              <div>
                <label class="label">Absendername</label>
                <input v-model="form.from_name" type="text" class="input">
              </div>
              <div>
                <label class="label">Eigener Hostname</label>
                <input v-model.trim="form.myhostname" type="text" class="input" placeholder="rimperio.example.com">
              </div>
              <div>
                <label class="label">Smarthost <span class="hint">(optional, empfohlen)</span></label>
                <input v-model.trim="form.relay_host" type="text" class="input" placeholder="smtp.provider.de">
              </div>
            </div>
            <div v-if="form.relay_host" class="grid gap-3 sm:grid-cols-3">
              <div>
                <label class="label">Smarthost-Port</label>
                <input v-model.number="form.relay_port" type="number" min="1" max="65535" class="input">
              </div>
              <div>
                <label class="label">Smarthost-Benutzer</label>
                <input v-model.trim="form.relay_user" type="text" class="input" autocomplete="off">
              </div>
              <div>
                <label class="label">Smarthost-Passwort</label>
                <input v-model="form.relay_password" type="password" class="input" autocomplete="new-password"
                       :placeholder="config.relay_password_set ? '•••••••• (unverändert)' : ''">
              </div>
            </div>
            <p class="hint">Ohne Smarthost stellt Postfix selbst zu. Das klappt nur mit offenem
              Port 25 und passendem PTR-Eintrag — an den meisten Anschlüssen nicht. Der Zusatzdienst
              „Postfix“ wird unter <router-link class="link" to="/stacks" @click="close">Zusatzdienste</router-link>
              eingeschaltet.</p>
          </template>

          <!-- =============================== Microsoft 365: Start ======== -->
          <template v-if="step === 'm365-intro'">
            <p class="text-sm">Du meldest dich gleich mit einem <strong>Microsoft-365-Administrator</strong>
              an (Globaler Administrator). RIMPERIO legt dann selbst an:</p>
            <ul class="check-list">
              <li>eine App-Registrierung „RIMPERIO Mailversand“ in deinem Mandanten,</li>
              <li>das Anwendungsrecht <code>Mail.Send</code> samt Admin-Zustimmung,</li>
              <li>ein Client-Geheimnis (24 Monate gültig).</li>
            </ul>
            <p class="hint">Dein Administrator-Login wird nur für diese Einrichtung benutzt und danach
              verworfen. RIMPERIO sieht dein Passwort nie — die Anmeldung läuft komplett bei Microsoft.</p>
            <div class="grid gap-3 sm:grid-cols-2">
              <div>
                <label class="label">Absenderpostfach <span class="hint">(leer = dein eigenes)</span></label>
                <input v-model.trim="form.from_address" type="email" class="input"
                       placeholder="rimperio@firma.de oder ein freigegebenes Postfach">
              </div>
              <div>
                <label class="label">Absendername</label>
                <input v-model="form.from_name" type="text" class="input">
              </div>
            </div>
            <p class="hint">Das Absenderpostfach braucht eine Exchange-Online-Lizenz oder muss ein
              freigegebenes Postfach sein (das ist kostenlos).</p>
            <button class="btn-text" type="button" @click="go('m365-manual')">
              Anmeldung per Gerätecode gesperrt? Vorhandene App-Registrierung eintragen →
            </button>
          </template>

          <!-- =============================== Microsoft 365: Login ======== -->
          <template v-if="step === 'm365-login'">
            <template v-if="flow?.status === 'waiting'">
              <div class="device-box">
                <p class="text-sm">1. Diese Seite bei Microsoft öffnen:</p>
                <a :href="flow.verification_uri" target="_blank" rel="noopener noreferrer"
                   class="btn btn-primary">{{ flow.verification_uri }} ↗</a>
                <p class="text-sm mt-3">2. Diesen Code eingeben:</p>
                <div class="device-code">
                  <span>{{ flow.user_code }}</span>
                  <button class="btn btn-ghost btn-sm" type="button" @click="copyCode">
                    {{ copied ? 'Kopiert ✓' : 'Kopieren' }}
                  </button>
                </div>
                <p class="text-sm mt-3">3. Mit dem Administrator anmelden und bei der Abfrage der
                  Berechtigungen <strong>„Im Namen Ihrer Organisation zustimmen“</strong> anhaken.</p>
              </div>
              <p class="hint waiting"><span class="spinner" /> Warte auf die Anmeldung bei Microsoft …</p>
            </template>

            <ul v-if="flow && flow.status !== 'waiting'" class="progress-list">
              <li v-for="item in flow.steps" :key="item.id" :class="`is-${item.status}`">
                <span class="mark">{{ marks[item.status] }}</span>
                <span>
                  <strong>{{ item.label }}</strong>
                  <em v-if="item.detail">{{ item.detail }}</em>
                </span>
              </li>
            </ul>
          </template>

          <!-- =============================== Microsoft 365: manuell ====== -->
          <template v-if="step === 'm365-manual'">
            <p class="text-sm">Für eine App-Registrierung, die schon existiert. Sie braucht das
              <strong>Anwendungsrecht</strong> <code>Mail.Send</code> (Microsoft Graph) mit Admin-Zustimmung.</p>
            <div class="grid gap-3 sm:grid-cols-2">
              <div>
                <label class="label">Verzeichnis-ID (Mandant)</label>
                <input v-model.trim="form.m365_tenant_id" type="text" class="input" autocomplete="off">
              </div>
              <div>
                <label class="label">Anwendungs-ID (Client)</label>
                <input v-model.trim="form.m365_client_id" type="text" class="input" autocomplete="off">
              </div>
              <div class="sm:col-span-2">
                <label class="label">Client-Geheimnis (Wert, nicht die ID)</label>
                <input v-model="form.m365_client_secret" type="password" class="input" autocomplete="new-password"
                       :placeholder="config.m365_client_secret_set ? '•••••••• (unverändert)' : ''">
              </div>
              <div>
                <label class="label">Absenderpostfach</label>
                <input v-model.trim="form.from_address" type="email" class="input">
              </div>
              <div>
                <label class="label">Absendername</label>
                <input v-model="form.from_name" type="text" class="input">
              </div>
            </div>
          </template>

          <!-- =============================== Fertig ====================== -->
          <template v-if="step === 'done'">
            <p class="w-alert is-ok">{{ doneText }}</p>
            <p v-if="readyWarning" class="w-alert is-warn">{{ readyWarning }}</p>
            <div>
              <label class="label">Probemail an</label>
              <div class="flex gap-2">
                <input v-model.trim="testRecipient" type="email" class="input" placeholder="du@example.com">
                <button class="btn btn-secondary" :disabled="busy || !testRecipient" @click="sendTest">
                  {{ busy ? 'Sende …' : 'Probemail schicken' }}
                </button>
              </div>
              <p v-if="testResult" class="w-alert is-ok mt-2">{{ testResult }}</p>
            </div>
          </template>
        </div>

        <div class="modal-actions">
          <button class="btn btn-ghost" @click="close">{{ step === 'done' ? 'Schließen' : 'Abbrechen' }}</button>
          <button v-if="canGoBack" class="btn btn-ghost" @click="back">Zurück</button>

          <button v-if="step === 'prep'" class="btn btn-primary" @click="go('creds')">Erledigt, weiter</button>
          <button v-if="step === 'creds'" class="btn btn-primary" :disabled="busy" @click="saveSmtp">
            {{ busy ? 'Prüfe …' : (provider.kind === 'container' ? 'Speichern' : 'Anmeldung prüfen und speichern') }}
          </button>
          <button v-if="step === 'm365-intro'" class="btn btn-primary" :disabled="busy" @click="startM365">
            {{ busy ? 'Einen Moment …' : 'Bei Microsoft anmelden' }}
          </button>
          <button v-if="step === 'm365-login' && flow?.status === 'error'" class="btn btn-primary" @click="restartM365">
            Nochmal versuchen
          </button>
          <button v-if="step === 'm365-manual'" class="btn btn-primary" :disabled="busy" @click="saveM365Manual">
            {{ busy ? 'Prüfe …' : 'Prüfen und speichern' }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { useAuthStore } from '../stores/auth'

const props = defineProps({
  provider: { type: Object, required: true },
  config: { type: Object, required: true },
  defaultRecipient: { type: String, default: '' },
})
const emit = defineEmits(['close', 'saved'])
const auth = useAuthStore()

const sameProvider = props.config.provider === props.provider.id && props.config.mode !== 'off'
const form = reactive({
  from_address: sameProvider ? props.config.from_address : '',
  from_name: props.config.from_name || 'RIMPERIO',
  smtp_host: sameProvider ? props.config.smtp_host : '',
  smtp_port: sameProvider ? props.config.smtp_port : 587,
  encryption: sameProvider ? props.config.encryption : 'starttls',
  verify_certificate: sameProvider ? props.config.verify_certificate : true,
  smtp_user: sameProvider ? props.config.smtp_user : '',
  smtp_password: '',
  myhostname: props.config.myhostname || '',
  relay_host: props.config.relay_host || '',
  relay_port: props.config.relay_port || 587,
  relay_user: props.config.relay_user || '',
  relay_password: '',
  m365_tenant_id: sameProvider ? props.config.m365_tenant_id : '',
  m365_client_id: sameProvider ? props.config.m365_client_id : '',
  m365_client_secret: '',
})

const keepsPassword = computed(() => sameProvider && props.config.smtp_password_set)

// ----------------------------------------------------------------- Schritte
const firstStep = {
  m365: 'm365-intro',
  preset: props.provider.prep?.length ? 'prep' : 'creds',
  smtp: 'creds',
  container: 'creds',
}[props.provider.kind]
const step = ref(firstStep)
const history = ref([])

const stepLabels = computed(() => {
  if (props.provider.kind === 'm365') return ['Absender', 'Anmelden', 'Fertig']
  if (props.provider.kind === 'preset' && props.provider.prep?.length) {
    return ['Vorbereiten', 'Zugangsdaten', 'Fertig']
  }
  return ['Einstellungen', 'Fertig']
})
const stepIndex = computed(() => {
  const order = {
    prep: 0,
    creds: props.provider.kind === 'preset' && props.provider.prep?.length ? 1 : 0,
    'm365-intro': 0,
    'm365-manual': 0,
    'm365-login': 1,
  }
  return step.value === 'done' ? stepLabels.value.length - 1 : (order[step.value] ?? 0)
})
const canGoBack = computed(() => history.value.length > 0
  && step.value !== 'done' && !(step.value === 'm365-login' && flow.value?.status === 'working'))

function go(next) {
  error.value = ''
  history.value.push(step.value)
  step.value = next
}
function back() {
  error.value = ''
  if (step.value === 'm365-login') stopM365()
  step.value = history.value.pop() || firstStep
}

const placeholderAddress = computed(() => {
  const domain = props.provider.domains?.[0]
  return domain ? `name@${domain}` : 'name@deine-domain.de'
})

function suggestPort() {
  if (form.encryption === 'tls') form.smtp_port = 465
  else if (form.encryption === 'starttls' && form.smtp_port === 465) form.smtp_port = 587
}

// ----------------------------------------------------------------- Zustand
const busy = ref(false)
const error = ref('')
const doneText = ref('')
const readyWarning = ref('')
const testRecipient = ref(props.defaultRecipient)
const testResult = ref('')

// ----------------------------------------------------------------- SMTP
function smtpBody() {
  const kind = props.provider.kind
  if (kind === 'container') {
    const body = {
      mode: 'container', provider: 'container',
      from_address: form.from_address, from_name: form.from_name,
      myhostname: form.myhostname, relay_host: form.relay_host,
      relay_port: form.relay_port, relay_user: form.relay_user,
    }
    if (form.relay_password) body.relay_password = form.relay_password
    return body
  }
  const body = {
    mode: 'relay',
    provider: props.provider.id,
    from_address: form.from_address,
    from_name: form.from_name,
  }
  if (kind === 'smtp') {
    Object.assign(body, {
      smtp_host: form.smtp_host, smtp_port: form.smtp_port, encryption: form.encryption,
      verify_certificate: form.verify_certificate, smtp_user: form.smtp_user,
    })
    // Ohne Benutzer kein Passwort - sonst bliebe ein altes stehen.
    if (!form.smtp_user) body.clear_smtp_password = true
  } else {
    body.smtp_user = form.from_address
  }
  if (form.smtp_password) body.smtp_password = form.smtp_password
  return body
}

async function saveSmtp() {
  error.value = ''
  const kind = props.provider.kind
  if (!form.from_address.includes('@')) {
    error.value = 'Bitte eine gültige E-Mail-Adresse eintragen.'
    return
  }
  if (kind === 'preset' && !form.smtp_password && !keepsPassword.value) {
    error.value = `Bitte das ${props.provider.password_label || 'Passwort'} eintragen.`
    return
  }
  if (kind === 'smtp' && !form.smtp_host) {
    error.value = 'Bitte den Mailserver eintragen.'
    return
  }
  busy.value = true
  try {
    const body = smtpBody()
    let detail = ''
    if (kind !== 'container') {
      detail = (await auth.api('/api/mail/verify', { method: 'POST', body })).detail
    }
    const saved = await auth.api('/api/mail/config', { method: 'PUT', body })
    const written = saved.config.postfix_file_written
    if (kind === 'container') {
      doneText.value = written?.written
        ? `Gespeichert. ${written.note}`
        : `Gespeichert, aber: ${written?.reason || 'Datei nicht geschrieben.'}`
    } else {
      doneText.value = `Gespeichert. ${detail}`
    }
    emit('saved')
    go('done')
  } catch (err) {
    error.value = explainSmtp(err.message)
  } finally {
    busy.value = false
  }
}

function explainSmtp(message) {
  const id = props.provider.id
  if (!/abgelehnt|535|534|authentication/i.test(message)) return message
  const hints = {
    gmx: 'Ist unter POP3 & IMAP „POP3 und IMAP Zugriff erlauben“ eingeschaltet? Bei Zwei-Faktor das App-Passwort verwenden.',
    webde: 'Ist unter POP3/IMAP Abruf „POP3 und IMAP Zugriff erlauben“ eingeschaltet? Bei Zwei-Faktor das App-Passwort verwenden.',
    google: 'Google nimmt nur ein App-Passwort an, nicht das normale Kontopasswort.',
    tonline: 'Die Telekom nimmt nur das separate E-Mail-Passwort an, nicht das Login-Passwort.',
    icloud: 'iCloud nimmt nur ein app-spezifisches Passwort an.',
    yahoo: 'Yahoo nimmt nur ein App-Passwort an.',
  }
  return hints[id] ? `${message} — ${hints[id]}` : message
}

// ----------------------------------------------------------------- M365
const flow = ref(null)
const copied = ref(false)
let pollTimer = null
const marks = { pending: '○', running: '…', ok: '✓', warn: '!', error: '✕' }

async function startM365() {
  error.value = ''
  if (form.from_address && !form.from_address.includes('@')) {
    error.value = 'Das Absenderpostfach ist keine gültige Adresse.'
    return
  }
  busy.value = true
  try {
    flow.value = await auth.api('/api/mail/m365/start', {
      method: 'POST',
      body: { from_address: form.from_address, from_name: form.from_name },
    })
    if (step.value !== 'm365-login') go('m365-login')
    pollTimer = setInterval(pollM365, 3000)
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

async function pollM365() {
  if (!flow.value) return
  try {
    const data = await auth.api(`/api/mail/m365/status/${flow.value.flow}`)
    flow.value = data
    if (data.status === 'error') {
      clearInterval(pollTimer)
      error.value = data.error
    } else if (data.status === 'done') {
      clearInterval(pollTimer)
      const result = data.result
      doneText.value = `Fertig. RIMPERIO verschickt ab jetzt über Microsoft 365 als ${result.from_address}. `
        + `App „${result.app_name}“, Geheimnis gültig bis ${String(result.secret_expires).slice(0, 10)}.`
      const ready = data.steps.find(item => item.id === 'ready')
      readyWarning.value = ready?.status === 'warn' ? ready.detail : ''
      if (!testRecipient.value) testRecipient.value = result.admin
      emit('saved')
      // Fortschritt kurz stehen lassen, dann zur Probemail.
      setTimeout(() => go('done'), 1200)
    }
  } catch (err) {
    clearInterval(pollTimer)
    error.value = err.message
    if (flow.value) flow.value = { ...flow.value, status: 'error' }
  }
}

function stopM365() {
  clearInterval(pollTimer)
  if (flow.value && !['done', 'error'].includes(flow.value.status)) {
    auth.api(`/api/mail/m365/${flow.value.flow}`, { method: 'DELETE' }).catch(() => {})
  }
  flow.value = null
}

function restartM365() {
  stopM365()
  startM365()
}

async function copyCode() {
  try {
    await navigator.clipboard.writeText(flow.value.user_code)
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  } catch {
    copied.value = false
  }
}

async function saveM365Manual() {
  error.value = ''
  busy.value = true
  try {
    const body = {
      mode: 'm365', provider: 'm365',
      from_address: form.from_address, from_name: form.from_name,
      m365_tenant_id: form.m365_tenant_id, m365_client_id: form.m365_client_id,
    }
    if (form.m365_client_secret) body.m365_client_secret = form.m365_client_secret
    const check = await auth.api('/api/mail/verify', { method: 'POST', body })
    await auth.api('/api/mail/config', { method: 'PUT', body })
    doneText.value = `Gespeichert. ${check.detail}`
    emit('saved')
    go('done')
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

// ----------------------------------------------------------------- Probemail
async function sendTest() {
  busy.value = true
  error.value = ''
  testResult.value = ''
  try {
    const result = await auth.api('/api/mail/test', {
      method: 'POST', body: { recipient: testRecipient.value },
    })
    testResult.value = `Verschickt an ${result.recipients.join(', ')} über ${result.via}. `
      + 'Kommt sie nicht an, auch im Spam-Ordner nachsehen.'
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

function close() {
  if (step.value === 'm365-login' && flow.value?.status === 'working') return
  stopM365()
  emit('close')
}

onBeforeUnmount(() => clearInterval(pollTimer))
</script>

<style scoped>
.wizard {
  max-width: 44rem;
}

.wizard .card-header {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.wizard-body {
  overflow-y: auto;
}

.wizard-steps {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
  margin: 0;
  padding: 0;
  list-style: none;
  counter-reset: step;
}

.wizard-steps li {
  counter-increment: step;
  font-size: 0.6875rem;
  padding: 0.1875rem 0.625rem;
  border-radius: 9999px;
  border: 1px solid var(--color-border);
  color: var(--color-text-muted);
}

.wizard-steps li::before {
  content: counter(step) '. ';
}

.wizard-steps li.is-current {
  border-color: var(--color-primary);
  color: var(--color-text-primary);
  background-color: var(--primary-soft);
}

.wizard-steps li.is-done {
  color: var(--color-success);
}

.prep-list {
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
  margin: 0;
  padding-left: 1.25rem;
  font-size: 0.8125rem;
}

.prep-list li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.prep-list li span {
  flex: 1 1 18rem;
}

.note-list,
.check-list {
  margin: 0;
  padding-left: 1.25rem;
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  list-style: disc;
}

.check-list {
  font-size: 0.8125rem;
}

.fixed-value {
  padding: 0.5rem 0;
  font-size: 0.8125rem;
  color: var(--color-text-secondary);
}

.toggle {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8125rem;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.btn-text {
  border: 0;
  background: none;
  padding: 0;
  font-size: 0.75rem;
  color: var(--color-primary);
  cursor: pointer;
  text-align: left;
}

.btn-text:hover {
  text-decoration: underline;
}

.device-box {
  padding: 1rem;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background-color: var(--color-surface-elevated);
}

.device-code {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-top: 0.375rem;
}

.device-code span {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 1.75rem;
  font-weight: 700;
  letter-spacing: 0.15em;
  color: var(--color-text-primary);
}

.waiting {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.spinner {
  width: 0.875rem;
  height: 0.875rem;
  border-radius: 9999px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.progress-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 0.8125rem;
}

.progress-list li {
  display: flex;
  gap: 0.625rem;
  color: var(--color-text-muted);
}

.progress-list li > span:last-child {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.progress-list em {
  font-style: normal;
  font-size: 0.75rem;
  overflow-wrap: anywhere;
}

.progress-list .mark {
  width: 1.25rem;
  flex-shrink: 0;
  text-align: center;
  font-weight: 700;
}

.progress-list .is-running { color: var(--color-text-primary); }
.progress-list .is-running .mark { animation: pulse 1s ease-in-out infinite; }
.progress-list .is-ok { color: var(--color-text-secondary); }
.progress-list .is-ok .mark { color: var(--color-success); }
.progress-list .is-warn .mark { color: var(--color-warning); }
.progress-list .is-error { color: var(--color-danger); }

@keyframes pulse {
  50% { opacity: 0.3; }
}

.w-alert {
  padding: 0.625rem 0.875rem;
  border-radius: var(--radius);
  font-size: 0.8125rem;
}

.w-alert.is-error { background-color: var(--danger-soft); color: var(--color-danger); }
.w-alert.is-ok { background-color: var(--success-soft); color: var(--color-success); }
.w-alert.is-warn { background-color: var(--warning-soft); color: var(--color-warning); }

.modal-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 0.5rem;
  padding: 0.875rem 1.25rem;
  border-top: 1px solid var(--color-border);
}

code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.75rem;
}
</style>
