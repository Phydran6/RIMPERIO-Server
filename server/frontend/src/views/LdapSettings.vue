<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.08.02.16.00.00
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Anmeldung gegen LDAP / Active Directory einrichten.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">Anmeldung über Verzeichnis (LDAP / AD)</h2>
        <p class="page-subtitle">
          Optional. Ist sie aktiv, wird bei einer fehlgeschlagenen lokalen Anmeldung zusätzlich
          das Verzeichnis gefragt — lokale Konten funktionieren weiterhin.
        </p>
      </div>
      <span class="badge" :class="form.enabled ? 'badge-success' : 'badge-neutral'">
        {{ form.enabled ? 'aktiv' : 'aus' }}
      </span>
    </div>

    <div v-if="message" class="card mb-4" :style="{ borderColor: messageOk ? 'var(--color-success)' : 'var(--color-danger)' }">
      <div class="card-body flex items-start gap-2 text-sm" :style="{ color: messageOk ? 'var(--color-success)' : 'var(--color-danger)' }">
        <AppIcon :name="messageOk ? 'check' : 'warning'" :size="18" class="shrink-0" />
        <span>{{ message }}</span>
      </div>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <!-- ==============================================================
           EINSTELLUNGEN
           ============================================================== -->
      <div class="card lg:col-span-2">
        <div class="card-header">
          <span class="card-title">Einstellungen</span>
          <label class="flex items-center gap-2 text-sm cursor-pointer">
            <input v-model="form.enabled" type="checkbox">
            <span>Anmeldung über Verzeichnis erlauben</span>
          </label>
        </div>

        <div class="card-body space-y-5">
          <!-- Server -->
          <section class="space-y-3">
            <p class="section-title">Server</p>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div class="sm:col-span-2">
                <label class="label">Adresse</label>
                <input v-model="form.server_uri" class="input" placeholder="ldaps://dc01.firma.local:636">
                <p class="hint">
                  <code>ldaps://…:636</code> ist verschlüsselt. Bei <code>ldap://…:389</code>
                  unbedingt „StartTLS" setzen — sonst gehen Passwörter im Klartext über das Netz.
                </p>
              </div>
              <label class="flex items-center gap-2 text-sm">
                <input v-model="form.start_tls" type="checkbox"> StartTLS verwenden
              </label>
              <label class="flex items-center gap-2 text-sm">
                <input v-model="form.verify_cert" type="checkbox"> Zertifikat prüfen
              </label>
            </div>
          </section>

          <!-- Dienstkonto -->
          <section class="space-y-3">
            <p class="section-title">Dienstkonto für die Suche</p>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label class="label">Benutzer (DN oder UPN)</label>
                <input v-model="form.bind_dn" class="input" placeholder="CN=rimperio,OU=Dienste,DC=firma,DC=local">
              </div>
              <div>
                <label class="label">Passwort</label>
                <input v-model="form.bind_password" type="password" class="input" :placeholder="passwordPlaceholder">
                <p class="hint">Leer lassen = gespeichertes Passwort behalten.</p>
              </div>
            </div>
          </section>

          <!-- Suche -->
          <section class="space-y-3">
            <p class="section-title">Benutzer finden</p>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div class="sm:col-span-2">
                <label class="label">Basis-DN</label>
                <input v-model="form.base_dn" class="input" placeholder="OU=Benutzer,DC=firma,DC=local">
              </div>
              <div class="sm:col-span-2">
                <label class="label">Suchfilter</label>
                <input v-model="form.user_filter" class="input font-mono text-xs">
                <p class="hint">
                  Muss <code>{username}</code> enthalten. Active Directory:
                  <code>(sAMAccountName={username})</code>, OpenLDAP: <code>(uid={username})</code>.
                </p>
              </div>
              <div>
                <label class="label">Attribut E-Mail</label>
                <input v-model="form.attr_email" class="input">
              </div>
              <div>
                <label class="label">Attribut Anzeigename</label>
                <input v-model="form.attr_display_name" class="input">
              </div>
              <div>
                <label class="label">Attribut Gruppen</label>
                <input v-model="form.attr_groups" class="input">
              </div>
            </div>
          </section>

          <!-- Rechte -->
          <section class="space-y-3">
            <p class="section-title">Zugang und Rollen</p>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label class="label">Erforderliche Gruppe</label>
                <input v-model="form.required_group" class="input" placeholder="RIMPERIO-Benutzer">
                <p class="hint">Leer = jeder gefundene Benutzer darf sich anmelden.</p>
              </div>
              <div>
                <label class="label">Admin-Gruppe</label>
                <input v-model="form.admin_group" class="input" placeholder="RIMPERIO-Admins">
                <p class="hint">Mitglieder bekommen Administratorrechte.</p>
              </div>
              <div>
                <label class="label">Standardrolle</label>
                <select v-model="form.default_role" class="select">
                  <option value="user">Benutzer</option>
                  <option value="admin">Administrator</option>
                </select>
              </div>
              <label class="flex items-end gap-2 text-sm pb-2">
                <input v-model="form.auto_create_users" type="checkbox">
                <span>Unbekannte Benutzer beim ersten Anmelden anlegen</span>
              </label>
            </div>
          </section>

          <div class="flex justify-end gap-2 pt-2 border-t" style="border-color: var(--color-border)">
            <button class="btn btn-secondary" :disabled="loading" @click="load">Verwerfen</button>
            <button class="btn btn-primary" :disabled="loading" @click="save">Speichern</button>
          </div>
        </div>
      </div>

      <!-- ==============================================================
           TEST
           ============================================================== -->
      <div class="card h-fit">
        <div class="card-header"><span class="card-title">Anmeldung testen</span></div>
        <div class="card-body space-y-3">
          <p class="text-sm" style="color: var(--color-text-muted)">
            Probiert eine echte Anmeldung mit den gespeicherten Einstellungen — auch wenn die
            Verzeichnis-Anmeldung noch ausgeschaltet ist. Erst speichern, dann testen.
          </p>
          <div>
            <label class="label">Benutzername</label>
            <input v-model="test.username" class="input" autocomplete="off">
          </div>
          <div>
            <label class="label">Passwort</label>
            <input v-model="test.password" type="password" class="input" autocomplete="off">
          </div>
          <button class="btn btn-secondary w-full" :disabled="testing || !test.username || !test.password" @click="runTest">
            {{ testing ? 'Prüfe…' : 'Test starten' }}
          </button>

          <div v-if="testResult" class="test-result" :class="testResult.success ? 'ok' : 'fail'">
            <p class="font-medium">{{ testResult.message }}</p>
            <dl v-if="testResult.success" class="mt-2 space-y-1 text-xs">
              <div><dt>DN</dt><dd class="font-mono break-all">{{ testResult.dn }}</dd></div>
              <div><dt>E-Mail</dt><dd>{{ testResult.email || '–' }}</dd></div>
              <div><dt>Rolle</dt><dd>{{ testResult.role === 'admin' ? 'Administrator' : 'Benutzer' }}</dd></div>
              <div><dt>Gruppen ({{ testResult.group_count }})</dt></div>
            </dl>
            <ul v-if="testResult.success && testResult.groups?.length" class="mt-1 text-xs space-y-0.5 max-h-40 overflow-auto">
              <li v-for="g in testResult.groups" :key="g" class="font-mono break-all">{{ g }}</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'

const auth = useAuthStore()

const DEFAULTS = {
  enabled: false,
  server_uri: '',
  start_tls: false,
  verify_cert: true,
  bind_dn: '',
  bind_password: '',
  base_dn: '',
  user_filter: '(sAMAccountName={username})',
  attr_email: 'mail',
  attr_display_name: 'displayName',
  attr_groups: 'memberOf',
  required_group: '',
  admin_group: '',
  default_role: 'user',
  auto_create_users: true,
}

const form = ref({ ...DEFAULTS })
const passwordSet = ref(false)
const loading = ref(false)
const message = ref('')
const messageOk = ref(true)

const test = ref({ username: '', password: '' })
const testing = ref(false)
const testResult = ref(null)

const passwordPlaceholder = computed(() => (passwordSet.value ? '•••••••• (gespeichert)' : 'Passwort'))

onMounted(load)

async function load() {
  loading.value = true
  message.value = ''
  try {
    const data = await auth.api('/api/ldap/config')
    passwordSet.value = !!data.bind_password_set
    form.value = { ...DEFAULTS, ...data, bind_password: '' }
  } catch (e) {
    show(e.message, false)
  } finally {
    loading.value = false
  }
}

async function save() {
  loading.value = true
  try {
    const data = await auth.api('/api/ldap/config', { method: 'PUT', body: { ...form.value } })
    passwordSet.value = !!data.bind_password_set
    form.value.bind_password = ''
    show('Einstellungen gespeichert.', true)
  } catch (e) {
    show(e.message, false)
  } finally {
    loading.value = false
  }
}

async function runTest() {
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await auth.api('/api/ldap/test', { method: 'POST', body: { ...test.value } })
  } catch (e) {
    testResult.value = { success: false, message: e.message }
  } finally {
    testing.value = false
    test.value.password = ''
  }
}

function show(text, ok) {
  message.value = text
  messageOk.value = ok
  setTimeout(() => { message.value = '' }, 6000)
}
</script>

<style scoped>
.test-result {
  padding: 0.75rem;
  border-radius: var(--radius);
  font-size: 0.8125rem;
}

.test-result.ok {
  background-color: var(--success-soft);
  color: var(--color-success);
}

.test-result.fail {
  background-color: var(--danger-soft);
  color: var(--color-danger);
}

.test-result dt {
  color: var(--color-text-muted);
}

.test-result dd {
  color: var(--color-text-primary);
}
</style>
