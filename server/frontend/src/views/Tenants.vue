<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Mandanten (Kunden) in einer Instanz verwalten.

     Nur fuer den Betreiber (Administrator ohne Mandant). Hier wird die
     Mandantenfaehigkeit ein- und ausgeschaltet, Kunden angelegt und Geraete,
     Schluessel, Proxmox-Hosts usw. zugeordnet. Was ein Mandant sieht und was
     nicht, regelt der Server (app/tenancy.py).
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">Mandanten</h2>
        <p class="page-subtitle">
          Eine RIMPERIO-Instanz für mehrere Kunden – jeder sieht nur seine eigenen Geräte, Logs und Updates.
        </p>
      </div>
      <button class="btn btn-secondary btn-sm" :disabled="loading" @click="load">
        <AppIcon name="refresh" :size="16" />
        Neu laden
      </button>
    </div>

    <div v-if="message" class="card mb-4" :style="{ borderColor: messageError ? 'var(--color-danger)' : 'var(--color-success)' }">
      <div class="card-body text-sm" :style="{ color: messageError ? 'var(--color-danger)' : 'var(--color-success)' }">
        {{ message }}
      </div>
    </div>

    <!-- Ein / aus -->
    <div class="card mb-4">
      <div class="card-header">
        <p class="card-title">Mandantenfähigkeit</p>
        <span class="badge" :class="enabled ? 'badge-success' : 'badge-neutral'">{{ enabled ? 'eingeschaltet' : 'ausgeschaltet' }}</span>
      </div>
      <div class="card-body space-y-3 text-sm" style="color: var(--color-text-secondary)">
        <p v-if="!enabled">
          Ausgeschaltet arbeitet RIMPERIO wie bisher: alle Konten sehen alles. Eingeschaltet lassen sich Kunden
          anlegen; deren Konten sehen nur, was ihrem Mandanten gehört, und erreichen keine Server-Funktionen
          (Konsole, Updates, Sicherung, Container, Netzwerk). Der bisherige Bestand bleibt beim Betreiber, bis er
          einem Mandanten zugeordnet wird.
        </p>
        <p v-else>
          Konten ohne Mandant (Betreiber) sehen alles und können oben in der Kopfzeile auf einen Mandanten
          umschalten. Ausschalten blendet die Verwaltung aus – Daten und Zuordnungen bleiben erhalten, und
          Kunden-Konten bleiben auf ihren Mandanten beschränkt.
        </p>
        <p class="hint">
          Lieber eine eigene RIMPERIO-Instanz je Kunde? Das geht auf demselben Server mit dem Installer:
          <code>install.sh --instance kunde1 --http-port 8081 --https-port 8444 --syslog-port 1514</code>
          (siehe Installationsanleitung).
        </p>
        <button class="btn btn-sm" :class="enabled ? 'btn-secondary' : 'btn-primary'" :disabled="busy" @click="toggle">
          {{ enabled ? 'Ausschalten' : 'Einschalten' }}
        </button>
      </div>
    </div>

    <template v-if="enabled">
      <!-- Lizenz und Abrechnung: Mandanten sind frei nutzbar, lassen sich aber
           je Mandant lizenzieren und abrechnen (app/licensing.py, app/billing.py). -->
      <div v-if="license" class="card mb-4">
        <div class="card-body text-sm flex flex-wrap items-center gap-3" style="color: var(--color-text-secondary)">
          <AppIcon name="shield" :size="16" />
          <span v-if="license.model === 'per_tenant'">
            Lizenz je Mandant: <strong>{{ license.tenants_used }}</strong>
            <template v-if="license.tenants_limit"> von {{ license.tenants_limit }}</template> Mandanten
            <span v-if="license.tenants_limit && license.tenants_used > license.tenants_limit"
                  style="color: var(--color-warning)"> – über dem Kontingent (läuft trotzdem weiter)</span>
          </span>
          <span v-else>
            {{ license.edition_label }}<template v-if="license.model_label"> · {{ license.model_label }}</template> –
            Mandanten sind frei nutzbar.
          </span>
          <span class="flex-1" />
          <router-link to="/license" class="btn btn-ghost btn-sm">Lizenz</router-link>
          <router-link to="/billing" class="btn btn-ghost btn-sm">Abrechnung je Mandant</router-link>
        </div>
      </div>

      <!-- Neu anlegen -->
      <div class="card mb-4">
        <div class="card-header"><p class="card-title">Neuen Mandanten anlegen</p></div>
        <div class="card-body space-y-3">
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label class="label">Name</label>
              <input v-model="form.name" class="input" placeholder="z.B. Müller GmbH" @keyup.enter="create">
            </div>
            <div>
              <label class="label">Kurzname (optional)</label>
              <input v-model="form.slug" class="input font-mono text-sm" placeholder="mueller-gmbh">
            </div>
            <div>
              <label class="label">Ansprechpartner</label>
              <input v-model="form.contact_name" class="input">
            </div>
            <div>
              <label class="label">E-Mail des Ansprechpartners</label>
              <input v-model="form.contact_email" class="input" type="email">
            </div>
            <div class="md:col-span-2">
              <label class="label">Meldungen zusätzlich an (optional)</label>
              <input v-model="form.alert_emails" class="input" placeholder="it@kunde.de, technik@kunde.de">
              <p class="hint">Freigaben und Fehlschläge zu Geräten dieses Mandanten gehen zusätzlich per Mail hierhin
                (über den eingerichteten Mailversand).</p>
            </div>
          </div>
          <label v-if="unassignedTotal" class="flex items-start gap-2 text-sm" style="color: var(--color-text-secondary)">
            <input v-model="form.adopt_unassigned" type="checkbox" class="mt-1">
            <span>
              Den bisherigen Bestand übernehmen ({{ unassignedSummary }}) – praktisch, wenn diese Instanz bisher
              genau einem Kunden gehörte.
            </span>
          </label>
          <button class="btn btn-primary btn-sm" :disabled="busy || !form.name.trim()" @click="create">Anlegen</button>
        </div>
      </div>

      <!-- Liste -->
      <div class="card mb-4">
        <div class="card-body p-0">
          <div v-if="!items.length && !loading" class="empty-state">
            <p class="empty-state-title">Noch kein Mandant angelegt.</p>
          </div>
          <table v-else class="table">
            <thead>
              <tr>
                <th>Mandant</th>
                <th style="width: 16rem">Bestand</th>
                <th style="width: 7rem">Zustand</th>
                <th style="width: 18rem">Aktionen</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in items" :key="item.id">
                <td>
                  <template v-if="editing === item.id">
                    <input v-model="edit.name" class="input mb-1">
                    <input v-model="edit.contact_email" class="input mb-1 text-sm" placeholder="E-Mail Ansprechpartner">
                    <input v-model="edit.alert_emails" class="input text-sm" placeholder="Meldungen zusätzlich an">
                  </template>
                  <template v-else>
                    <p style="color: var(--color-text-primary)">{{ item.name }}</p>
                    <p class="text-xs font-mono" style="color: var(--color-text-muted)">{{ item.slug }}</p>
                    <p v-if="item.contact_name || item.contact_email" class="text-xs" style="color: var(--color-text-muted)">
                      {{ item.contact_name }} {{ item.contact_email }}
                    </p>
                    <p v-if="item.alert_emails" class="text-xs" style="color: var(--color-text-muted)">
                      Meldungen an: {{ item.alert_emails }}
                    </p>
                  </template>
                </td>
                <td class="text-xs" style="color: var(--color-text-secondary)">{{ countText(item.counts) }}</td>
                <td>
                  <span class="badge" :class="item.is_active ? 'badge-success' : 'badge-danger'">
                    {{ item.is_active ? 'aktiv' : 'gesperrt' }}
                  </span>
                </td>
                <td>
                  <div class="flex flex-wrap gap-1">
                    <template v-if="editing === item.id">
                      <button class="btn btn-primary btn-sm" @click="save(item)">Speichern</button>
                      <button class="btn btn-ghost btn-sm" @click="editing = null">Abbrechen</button>
                    </template>
                    <template v-else>
                      <button class="btn btn-secondary btn-sm" @click="tenantStore.setScope(item.id)">Ansehen</button>
                      <button class="btn btn-secondary btn-sm" @click="startEdit(item)">Bearbeiten</button>
                      <button class="btn btn-secondary btn-sm" @click="setActive(item, !item.is_active)">
                        {{ item.is_active ? 'Sperren' : 'Entsperren' }}
                      </button>
                      <button class="btn btn-danger btn-sm" @click="remove(item)">Löschen</button>
                    </template>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Zuordnen -->
      <div class="card">
        <div class="card-header"><p class="card-title">Zuordnen</p></div>
        <div class="card-body space-y-3">
          <p class="hint">
            Geräte, Schlüssel, Proxmox-Hosts und SNMP-Geräte gehören einem Mandanten oder dem Betreiber. Skripte,
            Checks, Richtlinien und SNMP-Vorlagen ohne Mandant gelten für alle Mandanten. Logs, Messwerte und Updates
            wandern mit ihrem Gerät.
          </p>
          <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div>
              <label class="label">Was</label>
              <select v-model="assignKind" class="select">
                <option v-for="(label, key) in KINDS" :key="key" :value="key">{{ label }}</option>
              </select>
            </div>
            <div>
              <label class="label">Filter</label>
              <input v-model="assignFilter" class="input" placeholder="Name, Standort, IP …">
            </div>
            <div>
              <label class="label">Zeigen</label>
              <select v-model="assignShow" class="select">
                <option value="all">alle</option>
                <option value="unassigned">nur ohne Mandant</option>
                <option v-for="tn in items" :key="tn.id" :value="String(tn.id)">nur {{ tn.name }}</option>
              </select>
            </div>
          </div>

          <div class="assign-list">
            <label v-for="row in assignRows" :key="row.id" class="assign-row">
              <input v-model="selected" type="checkbox" :value="row.id">
              <span class="flex-1 min-w-0 truncate" style="color: var(--color-text-primary)">{{ row.name }}</span>
              <span v-if="row.site || row.ip" class="text-xs truncate" style="color: var(--color-text-muted)">
                {{ [row.site, row.ip].filter(Boolean).join(' · ') }}
              </span>
              <span class="badge" :class="row.tenant_id ? 'badge-primary' : 'badge-neutral'">{{ tenantName(row.tenant_id) }}</span>
            </label>
            <p v-if="!assignRows.length" class="text-sm p-3" style="color: var(--color-text-muted)">Nichts gefunden.</p>
          </div>

          <div class="flex flex-wrap items-end gap-2">
            <button class="btn btn-ghost btn-sm" @click="selected = assignRows.map(r => r.id)">Alle markieren</button>
            <button class="btn btn-ghost btn-sm" @click="selected = []">Keine</button>
            <div class="flex-1" />
            <div>
              <label class="label">Zuordnen zu</label>
              <select v-model="assignTarget" class="select">
                <option :value="0">{{ ownerless }}</option>
                <option v-for="tn in items" :key="tn.id" :value="tn.id">{{ tn.name }}</option>
              </select>
            </div>
            <button class="btn btn-primary btn-sm" :disabled="busy || !selected.length" @click="assign">
              {{ selected.length }} zuordnen
            </button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useTenantStore } from '../stores/tenantStore'
import AppIcon from '../components/AppIcon.vue'

const auth = useAuthStore()
const tenantStore = useTenantStore()

const KINDS = {
  agents: 'Geräte (Rechner und Log-Quellen)',
  tokens: 'Zugangsschlüssel',
  pve_hosts: 'Proxmox-Hosts',
  snmp_devices: 'SNMP-Geräte',
  snmp_credentials: 'SNMP-Zugangsdaten',
  scripts: 'Skripte',
  checks: 'Checks',
  policies: 'Patch-Richtlinien',
  snmp_templates: 'SNMP-Vorlagen',
  nas_devices: 'NAS',
  backup_targets: 'Sicherungsziele',
  backup_jobs: 'Sicherungsaufträge',
}
const SHARED = new Set(['scripts', 'checks', 'policies', 'snmp_templates'])
const COUNT_LABELS = {
  users: 'Benutzer', agents: 'Geräte', snmp_devices: 'SNMP', pve_hosts: 'Proxmox', nas_devices: 'NAS',
  backup_jobs: 'Sicherungen', tokens: 'Schlüssel',
}

const enabled = ref(false)
const license = ref(null)
const items = ref([])
const unassigned = ref({})
const assignable = ref({})
const loading = ref(false)
const busy = ref(false)
const message = ref('')
const messageError = ref(false)

const form = reactive({ name: '', slug: '', contact_name: '', contact_email: '', alert_emails: '', adopt_unassigned: false })
const editing = ref(null)
const edit = reactive({ name: '', contact_email: '', alert_emails: '' })

const assignKind = ref('agents')
const assignFilter = ref('')
const assignShow = ref('all')
const assignTarget = ref(0)
const selected = ref([])

const ownerless = computed(() => (SHARED.has(assignKind.value) ? 'Für alle (Betreiber)' : 'Betreiber (ohne Mandant)'))

const ADOPT_KEYS = ['agents', 'snmp_devices', 'pve_hosts', 'nas_devices', 'tokens']
const unassignedTotal = computed(() => ADOPT_KEYS
  .reduce((sum, key) => sum + (unassigned.value[key] || 0), 0))
const unassignedSummary = computed(() => countText(unassigned.value, ADOPT_KEYS))

const assignRows = computed(() => {
  const needle = assignFilter.value.trim().toLowerCase()
  return (assignable.value[assignKind.value] || []).filter((row) => {
    if (assignShow.value === 'unassigned' && row.tenant_id) return false
    if (!['all', 'unassigned'].includes(assignShow.value) && String(row.tenant_id) !== assignShow.value) return false
    if (!needle) return true
    return [row.name, row.site, row.ip].some(v => String(v || '').toLowerCase().includes(needle))
  })
})

watch(assignKind, () => { selected.value = [] })

function say(text, isError = false) {
  message.value = text
  messageError.value = isError
  if (!isError) setTimeout(() => { if (message.value === text) message.value = '' }, 6000)
}

function tenantName(id) {
  if (!id) return SHARED.has(assignKind.value) ? 'für alle' : 'Betreiber'
  return items.value.find(t => t.id === id)?.name || `#${id}`
}

function countText(counts = {}, keys = Object.keys(COUNT_LABELS)) {
  const parts = keys.filter(k => counts[k]).map(k => `${counts[k]} ${COUNT_LABELS[k]}`)
  return parts.length ? parts.join(' · ') : 'leer'
}

async function load() {
  loading.value = true
  try {
    const data = await auth.api('/api/tenants')
    enabled.value = !!data.enabled
    items.value = data.items || []
    unassigned.value = data.unassigned || {}
    if (enabled.value) assignable.value = await auth.api('/api/tenants/assignable')
    license.value = await auth.api('/api/license/summary').catch(() => null)
  } catch (e) {
    say(e.message, true)
  } finally {
    loading.value = false
  }
}

async function refreshAll() {
  await load()
  await tenantStore.load()
}

async function toggle() {
  const next = !enabled.value
  if (!next && !confirm('Mandantenfähigkeit ausschalten? Daten und Zuordnungen bleiben erhalten.')) return
  busy.value = true
  try {
    await auth.api('/api/tenants/enable', { method: 'POST', body: { enabled: next } })
    say(next ? 'Mandantenfähigkeit eingeschaltet.' : 'Mandantenfähigkeit ausgeschaltet.')
    if (!next) tenantStore.setScope('', false)
    await refreshAll()
  } catch (e) {
    say(e.message, true)
  } finally {
    busy.value = false
  }
}

async function create() {
  if (!form.name.trim()) return
  busy.value = true
  try {
    const body = { ...form, slug: form.slug.trim() || null }
    const result = await auth.api('/api/tenants', { method: 'POST', body })
    const adopted = Object.values(result.adopted || {}).reduce((a, b) => a + b, 0)
    say(`Mandant „${result.name}“ angelegt` + (adopted ? ` – ${adopted} Einträge übernommen.` : '.'))
    Object.assign(form, { name: '', slug: '', contact_name: '', contact_email: '', alert_emails: '', adopt_unassigned: false })
    await refreshAll()
  } catch (e) {
    say(e.message, true)
  } finally {
    busy.value = false
  }
}

function startEdit(item) {
  editing.value = item.id
  Object.assign(edit, { name: item.name, contact_email: item.contact_email, alert_emails: item.alert_emails })
}

async function save(item) {
  busy.value = true
  try {
    await auth.api(`/api/tenants/${item.id}`, { method: 'PUT', body: { ...edit } })
    editing.value = null
    say('Gespeichert.')
    await refreshAll()
  } catch (e) {
    say(e.message, true)
  } finally {
    busy.value = false
  }
}

async function setActive(item, active) {
  if (!active && !confirm(`„${item.name}“ sperren? Die Konten dieses Mandanten kommen dann nicht mehr hinein; die Geräte liefern weiter.`)) return
  busy.value = true
  try {
    await auth.api(`/api/tenants/${item.id}`, { method: 'PUT', body: { is_active: active } })
    await refreshAll()
  } catch (e) {
    say(e.message, true)
  } finally {
    busy.value = false
  }
}

async function remove(item) {
  const others = items.value.filter(t => t.id !== item.id)
  const hint = others.length
    ? `\n\nDer Bestand geht an den Betreiber. Stattdessen an einen anderen Mandanten? Dessen Kurzname eingeben (leer = Betreiber):\n${others.map(t => t.slug).join(', ')}`
    : '\n\nDer Bestand (Geräte, Schlüssel …) geht an den Betreiber.'
  const answer = prompt(`Mandant „${item.name}“ löschen?${hint}`, '')
  if (answer === null) return
  const target = answer.trim() ? others.find(t => t.slug === answer.trim()) : null
  if (answer.trim() && !target) { say(`Unbekannter Kurzname „${answer.trim()}“.`, true); return }
  const users = item.counts?.users || 0
  if (users && !confirm(`Der Mandant hat ${users} Benutzer. Diese Konten werden mitgelöscht. Weiter?`)) return
  busy.value = true
  try {
    const q = new URLSearchParams({ move_to: String(target?.id || 0), delete_users: users ? 'true' : 'false' })
    await auth.api(`/api/tenants/${item.id}?${q}`, { method: 'DELETE' })
    if (tenantStore.scope === String(item.id)) tenantStore.setScope('', false)
    say(`Mandant „${item.name}“ gelöscht.`)
    await refreshAll()
  } catch (e) {
    say(e.message, true)
  } finally {
    busy.value = false
  }
}

async function assign() {
  busy.value = true
  try {
    const result = await auth.api('/api/tenants/assign', {
      method: 'POST',
      body: { tenant_id: Number(assignTarget.value) || 0, items: { [assignKind.value]: selected.value } },
    })
    const moved = Object.values(result.moved || {}).reduce((a, b) => a + b, 0)
    say(`${moved} Einträge zugeordnet.`)
    selected.value = []
    await load()
  } catch (e) {
    say(e.message, true)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.assign-list {
  max-height: 22rem;
  overflow: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 0.5rem);
}

.assign-row {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--color-border);
  font-size: 0.875rem;
  cursor: pointer;
}

.assign-row:last-child {
  border-bottom: 0;
}

.assign-row:hover {
  background-color: var(--color-surface-elevated);
}
</style>
