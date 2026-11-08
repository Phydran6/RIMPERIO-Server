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
        <button v-if="auth.isAdmin" class="btn btn-primary btn-sm" @click="showAdd = !showAdd">
          <AppIcon name="plus" :size="16" /> Agent hinzufügen
        </button>
      </div>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>

    <section v-if="showAdd" class="card mb-4">
      <div class="card-header"><h2 class="card-title">Agent hinzufügen</h2></div>
      <div class="card-body">
        <p class="rmm-muted mb-3">
          Zur Anmeldung braucht der Agent einen <strong>Zugangsschlüssel</strong> – am besten eine
          <router-link to="/settings/agents">Einladung</router-link> (zählbar, läuft ab), alternativ den Generalschlüssel.
          Denselben Schlüssel nutzen auch die Log-Agents.
        </p>
        <label class="rmm-muted">Zugangsschlüssel für die Befehle unten
          <input v-model="key" class="input rmm-code" placeholder="ZUGANGSSCHLÜSSEL" />
        </label>
        <h3 class="card-title mt-4">Windows (PowerShell als Administrator)</h3>
        <pre class="rmm-pre">{{ cmdWindows }}</pre>
        <h3 class="card-title mt-4">Windows per MSI (GPO, Intune)</h3>
        <pre class="rmm-pre">{{ cmdMsi }}</pre>
        <h3 class="card-title mt-4">Linux</h3>
        <pre class="rmm-pre">{{ cmdLinux }}</pre>
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
