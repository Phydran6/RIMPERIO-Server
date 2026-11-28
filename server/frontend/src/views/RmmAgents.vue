<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Fernwartung: Liste der Rechner mit RIMPERIO-Agent
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Rechner</h1>
        <p class="page-subtitle">
          Rechner mit RIMPERIO-Agent: {{ agents.length }} angebunden · {{ online }} online.
          Logs desselben Rechners hängen am selben Gerät.
        </p>
      </div>
      <div class="rmm-actions">
        <button class="btn btn-secondary btn-sm" @click="load"><AppIcon name="refresh" :size="16" /> Aktualisieren</button>
        <button v-if="auth.isAdmin" class="btn btn-primary btn-sm" @click="toggleAdd">
          <AppIcon name="plus" :size="16" /> Agent hinzufügen
        </button>
      </div>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>

    <section v-if="showAdd" class="card mb-4">
      <div class="card-header">
        <h2 class="card-title">Agent hinzufügen</h2>
        <button class="btn-icon" title="Schließen" aria-label="Schließen" @click="showAdd = false"><AppIcon name="close" :size="18" /></button>
      </div>
      <div class="card-body">
        <p class="rmm-muted mb-3">
          RIMPERIO hat dafür eben eine <strong>Einladung</strong> erzeugt (gültig {{ INVITE_DAYS }} Tage, für bis zu
          {{ INVITE_USES }} Rechner) und in die Befehle eingesetzt — einfach kopieren und auf dem Rechner ausführen.
          Ein eigener Schlüssel (etwa der Generalschlüssel) lässt sich unten eintragen.
          Alle Schlüssel: <router-link to="/agent-tokens">Zugangsschlüssel</router-link>.
        </p>
        <div class="agent-key">
          <label class="rmm-muted flex-1">Zugangsschlüssel (automatisch erzeugt, änderbar)
            <input v-model="key" class="input rmm-code" placeholder="wird erzeugt …" />
          </label>
          <button class="btn btn-secondary btn-sm" :disabled="keyBusy" @click="newInvite">{{ keyBusy ? 'Erzeuge…' : 'Neu erzeugen' }}</button>
          <button class="btn btn-secondary btn-sm" :disabled="!key" @click="copy('key', key)">{{ copied === 'key' ? 'Kopiert ✓' : 'Kopieren' }}</button>
        </div>
        <p v-if="keyNote" class="rmm-muted mt-1">{{ keyNote }}</p>
        <template v-for="c in commands" :key="c.id">
          <div class="cmd-head mt-4">
            <h3 class="card-title">{{ c.title }}</h3>
            <button class="btn btn-primary btn-sm" @click="copy(c.id, c.text)">
              <AppIcon :name="copied === c.id ? 'check' : 'copy'" :size="14" /> {{ copied === c.id ? 'Kopiert' : 'Kopieren' }}
            </button>
          </div>
          <pre class="rmm-pre cmd-pre" title="Klicken zum Kopieren" @click="copy(c.id, c.text)">{{ c.text }}</pre>
        </template>
        <p class="rmm-muted mt-2">
          Der Agent läuft unter dem Dienstkonto <code>rimperio</code>, <strong>nicht als root</strong>
          (anderes Konto: <code>--user NAME</code>; fehlt es, wird es angelegt). Alles liegt unter
          <code>/opt/RMM/rimperio</code>. Updates und Neustart spielt er über ein festes Hilfsskript per
          <code>sudo</code> ein (<code>--sudo limited</code>, Standard); <code>--sudo full</code> erlaubt
          Befehlen aus der Oberfläche zusätzlich <code>sudo</code>, <code>--sudo none</code> nimmt alle Rechte.
        </p>
      </div>
    </section>

    <section class="card">
      <div class="card-header">
        <input v-model="filter" class="input" style="max-width: 18rem" placeholder="Suchen (Name, Standort, OS, IP)" />
        <select v-model="site" class="select" style="max-width: 12rem">
          <option value="">Alle Standorte</option>
          <option v-for="s in sites" :key="s" :value="s">{{ s }}</option>
        </select>
      </div>
      <div class="rmm-table-wrap">
        <table class="table table-clickable">
          <thead>
            <tr><th></th><th>Rechner</th><th>Standort</th><th>System</th><th>CPU</th><th>RAM</th><th>Platte</th><th>Zuletzt</th></tr>
          </thead>
          <tbody>
            <tr v-if="!shown.length">
              <td colspan="8" class="rmm-muted" style="text-align:center; padding: 2rem">
                {{ loading ? 'Lade …' : 'Noch keine Rechner. Über „Agent hinzufügen“ den ersten anbinden.' }}
              </td>
            </tr>
            <tr v-for="a in shown" :key="a.agent_uid" @click="$router.push(`/rmm/agents/${a.agent_uid}`)">
              <td><span class="rmm-dot" :class="a.online ? 'is-online' : 'is-offline'" :title="a.online ? 'online' : 'offline'"></span></td>
              <td>
                <strong>{{ a.hostname }}</strong>
                <span v-if="a.uninstall_pending" class="badge badge-warning rmm-badge-removing">wird deinstalliert</span>
                <div class="rmm-muted">{{ a.description || a.local_ip || '' }}</div>
              </td>
              <td>{{ a.site }}</td>
              <td>{{ a.os || PLATFORM_LABEL[a.platform] || '–' }}</td>
              <td :class="loadClass(a.last_cpu)">{{ fmtPct(a.last_cpu) }}</td>
              <td :class="loadClass(a.last_mem)">{{ fmtPct(a.last_mem) }}</td>
              <td :class="loadClass(a.last_disk)">{{ fmtPct(a.last_disk) }}</td>
              <td :title="fmtTime(a.last_seen)">{{ fmtAgo(a.last_seen) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import { fmtAgo, fmtPct, fmtTime, loadClass, PLATFORM_LABEL } from '../utils/rmm'

const auth = useAuthStore()
const agents = ref([])
const loading = ref(true)
const error = ref('')
const filter = ref('')
const site = ref('')
const showAdd = ref(false)
const key = ref('')
const keyBusy = ref(false)
const keyNote = ref('')
const copied = ref('')
const INVITE_DAYS = 7
const INVITE_USES = 25
let timer = null

const online = computed(() => agents.value.filter(a => a.online).length)
const sites = computed(() => [...new Set(agents.value.map(a => a.site))].sort())
const shown = computed(() => {
  const f = filter.value.trim().toLowerCase()
  return agents.value.filter(a => (!site.value || a.site === site.value) &&
    (!f || [a.hostname, a.site, a.os, a.local_ip, a.public_ip, a.description]
      .some(v => (v || '').toLowerCase().includes(f))))
})

const server = window.location.origin
const raw = 'https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/agent/installer'
const k = computed(() => key.value.trim() || 'ZUGANGSSCHLÜSSEL')
const cmdWindows = computed(() =>
  `& ([scriptblock]::Create((irm ${raw}/windows/install-agent.ps1))) \`\n  -Server ${server} -Secret ${k.value} -Site Standard`)
const cmdMsi = computed(() =>
  `msiexec /i rimperio-agent.msi SERVER=${server} SECRET=${k.value} SITE=Standard /qn`)
const cmdLinux = computed(() =>
  `curl -sSL ${raw}/linux/install-agent.sh | sudo bash -s -- \\\n  --server ${server} --secret ${k.value} --site Standard --user rimperio`)

// Synology: Paket fürs Paket-Zentrum (Manuelle Installation); der Assistent
// dort fragt Adresse, Schlüssel und Standort ab.
const SPK_URL = 'https://github.com/Phydran6/RIMPERIO-Server/releases/latest/download/rimperio-agent-synology.spk'
const cmdSynology = computed(() =>
  `Paket:             ${SPK_URL}\nServer-Adresse:    ${server}\nZugangsschlüssel:  ${k.value}\nStandort:          Standard`)

const commands = computed(() => [
  { id: 'win', title: 'Windows (PowerShell als Administrator)', text: cmdWindows.value },
  { id: 'msi', title: 'Windows per MSI (GPO, Intune)', text: cmdMsi.value },
  { id: 'linux', title: 'Linux', text: cmdLinux.value },
  { id: 'synology', title: 'Synology-NAS (DSM 7: Paket-Zentrum → Manuelle Installation)', text: cmdSynology.value },
])

async function toggleAdd() {
  showAdd.value = !showAdd.value
  // Beim Öffnen gleich einen Schlüssel erzeugen - selbst ausdenken muss den niemand.
  if (showAdd.value && !key.value) await newInvite()
}

async function newInvite() {
  keyBusy.value = true
  keyNote.value = ''
  try {
    const stamp = new Date().toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })
    const data = await auth.api('/api/agent-tokens', { method: 'POST', body: {
      name: 'Rechner hinzufügen ' + stamp, kind: 'enroll',
      expires_in_hours: INVITE_DAYS * 24, max_uses: INVITE_USES,
      note: 'Automatisch beim Öffnen von „Agent hinzufügen“ erzeugt.' } })
    key.value = data.token
    keyNote.value = 'Der Schlüssel ist nur jetzt lesbar — danach steht er nur noch als Prüfsumme in der Datenbank.'
  } catch (e) {
    keyNote.value = 'Konnte keinen Schlüssel erzeugen: ' + e.message + ' — bitte einen vorhandenen eintragen.'
  } finally {
    keyBusy.value = false
  }
}

async function copy(id, text) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    // Ohne Zwischenablage-Recht (http://): über ein verstecktes Textfeld.
    const area = document.createElement('textarea')
    area.value = text
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    try { document.execCommand('copy') } catch { /* dann eben markieren */ }
    area.remove()
  }
  copied.value = id
  setTimeout(() => { if (copied.value === id) copied.value = '' }, 2000)
}

async function load() {
  try {
    agents.value = await auth.api('/api/rmm/agents')
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(() => { load(); timer = setInterval(load, 30000) })
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.agent-key { display: flex; flex-wrap: wrap; gap: .5rem; align-items: flex-end; }
.agent-key label { display: flex; flex-direction: column; gap: .25rem; min-width: 16rem; }
.cmd-head { display: flex; align-items: center; justify-content: space-between; gap: .5rem; }
.cmd-pre { cursor: copy; }
.cmd-pre:hover { border-color: var(--color-primary); }
</style>
