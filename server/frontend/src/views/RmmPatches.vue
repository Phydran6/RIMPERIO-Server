<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Patch-Center: Stand, Freigaben (mit KI), Updates, Läufe, Docker
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Patch-Center</h1>
        <p class="page-subtitle">
          Windows, Linux und Docker. Gesucht wird täglich, installiert nur im Wartungsfenster der
          <router-link to="/rmm/policies">Richtlinie</router-link>. Was eine Freigabe braucht, steht unter „Freigaben“ —
          und in der App.
        </p>
      </div>
      <div v-if="auth.isAdmin" class="rmm-actions">
        <button class="btn btn-secondary btn-sm" :disabled="busy" @click="scanAll">Jetzt überall suchen</button>
      </div>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>

    <div class="patch-kpis">
      <button class="card patch-kpi" @click="tab = 'approvals'"><strong>{{ totals.needs_approval || 0 }}</strong><span>brauchen Freigabe</span></button>
      <button class="card patch-kpi" @click="setFilter('auto,approved')"><strong>{{ (totals.auto || 0) + (totals.approved || 0) }}</strong><span>installieren im nächsten Fenster</span></button>
      <button class="card patch-kpi" @click="setFilter('review')"><strong>{{ totals.review || 0 }}</strong><span>KI prüft gerade</span></button>
      <button class="card patch-kpi" @click="setFilter('failed')"><strong>{{ totals.failed || 0 }}</strong><span>fehlgeschlagen</span></button>
      <button class="card patch-kpi" @click="tab = 'runs'"><strong>{{ activeRuns }}</strong><span>Läufe aktiv</span></button>
    </div>

    <nav class="rmm-tabs">
      <button v-for="t in TABS" :key="t.key" :class="{ 'is-active': tab === t.key }" @click="tab = t.key">
        {{ t.label }}<span v-if="t.key === 'approvals' && approvals.length" class="badge badge-warning" style="margin-left:.35rem">{{ approvals.length }}</span>
      </button>
    </nav>

    <!-- Übersicht ---------------------------------------------------------- -->
    <section v-if="tab === 'overview'" class="card">
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Rechner</th><th>Richtlinie</th><th>Freigabe</th><th>Automatisch</th><th>Fehler</th><th>Fenster</th><th>Sicherung</th><th>Letzter Lauf</th><th></th></tr></thead>
          <tbody>
            <tr v-if="!agents.length"><td colspan="9" class="rmm-muted">Noch keine Rechner mit Agent.</td></tr>
            <tr v-for="a in agents" :key="a.agent_uid">
              <td>
                <span class="rmm-dot" :class="a.online ? 'is-online' : 'is-offline'" />
                <router-link :to="`/rmm/agents/${a.agent_uid}`"><strong>{{ a.hostname }}</strong></router-link>
                <div class="rmm-muted">{{ a.os || a.platform }}<span v-if="a.docker"> · Docker</span><span v-if="a.reboot_pending"> · Neustart ausstehend</span></div>
              </td>
              <td>{{ a.policy?.name || '–' }}</td>
              <td><span class="badge" :class="c(a, 'needs_approval') ? 'badge-warning' : 'badge-neutral'">{{ c(a, 'needs_approval') }}</span></td>
              <td>{{ c(a, 'auto') + c(a, 'approved') }}<span v-if="c(a, 'review')" class="rmm-muted"> (+{{ c(a, 'review') }} in Prüfung)</span></td>
              <td><span v-if="c(a, 'failed')" class="badge badge-danger">{{ c(a, 'failed') }}</span><span v-else>–</span></td>
              <td>
                <span v-if="a.window_open" class="badge badge-primary">offen bis {{ fmtTime(a.window_end) }}</span>
                <span v-else class="rmm-muted">{{ a.window_next ? fmtTime(a.window_next) : 'kein Fenster' }}</span>
              </td>
              <td><span v-if="a.pve" class="badge badge-success" :title="`VM ${a.pve.vmid} auf ${a.pve.node}`">Proxmox {{ a.pve.vmid }}</span><span v-else class="rmm-muted">keine</span></td>
              <td>
                <template v-if="a.active_run"><span class="badge" :class="RUN_STATUS[a.active_run.status]?.[0]">{{ RUN_STATUS[a.active_run.status]?.[1] }}</span></template>
                <template v-else-if="a.last_run"><a href="#" @click.prevent="openRun(a.last_run.id)"><span class="badge" :class="RUN_STATUS[a.last_run.status]?.[0]">{{ RUN_STATUS[a.last_run.status]?.[1] }}</span></a></template>
                <span v-else class="rmm-muted">–</span>
              </td>
              <td class="rmm-actions" style="justify-content:flex-end">
                <button class="btn btn-ghost btn-sm" @click="showAgent(a)">Updates</button>
                <template v-if="auth.isAdmin">
                  <button class="btn btn-secondary btn-sm" :disabled="busy" @click="scan(a)">Suchen</button>
                  <button class="btn btn-primary btn-sm" :disabled="busy || !(c(a,'auto') + c(a,'approved')) || !!a.active_run" @click="installNow(a)">Jetzt installieren</button>
                </template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Freigaben ---------------------------------------------------------- -->
    <section v-if="tab === 'approvals'">
      <p v-if="!approvals.length" class="rmm-note is-info">Nichts wartet auf eine Freigabe. Alles Weitere läuft automatisch nach Zeitplan.</p>
      <div v-else class="rmm-actions mb-4">
        <label class="rmm-muted"><input type="checkbox" :checked="allSelected" @change="toggleAll" /> alle wählen</label>
        <template v-if="auth.isAdmin">
          <button class="btn btn-primary btn-sm" :disabled="!selected.length || busy" @click="decide('approve')">Freigeben ({{ selected.length }})</button>
          <button class="btn btn-secondary btn-sm" :disabled="!selected.length || busy" @click="decide('defer')">7 Tage zurückstellen</button>
          <button class="btn btn-danger btn-sm" :disabled="!selected.length || busy" @click="decide('reject')">Ablehnen</button>
        </template>
      </div>
      <div class="patch-cards">
        <article v-for="it in approvals" :key="it.id" class="card patch-card" :class="{ 'is-selected': selected.includes(it.id) }">
          <header>
            <label><input v-model="selected" type="checkbox" :value="it.id" /></label>
            <div style="flex:1;min-width:0">
              <div class="patch-title">{{ it.title }}</div>
              <div class="rmm-muted">
                <router-link :to="`/rmm/agents/${it.agent_uid}`">{{ it.hostname }}</router-link> · {{ it.platform }}
                <span v-if="it.kb"> · {{ it.kb }}</span>
                <span v-if="it.version_from || it.version_to"> · {{ it.version_from || '?' }} → {{ it.version_to || '?' }}</span>
              </div>
            </div>
            <span v-if="it.urgent" class="badge badge-danger" title="behebt eine aktiv ausgenutzte Lücke (CISA KEV)">dringend</span>
            <span v-if="it.security" class="badge badge-primary">Sicherheit</span>
            <span v-if="it.reboot" class="badge badge-neutral">Neustart</span>
          </header>
          <p class="patch-reason"><strong>Warum Freigabe:</strong> {{ it.reason }}</p>
          <div v-if="it.ai && !it.ai.error" class="patch-ai">
            <span class="badge" :class="RISK[it.ai.risk]?.[0] || 'badge-neutral'">{{ RISK[it.ai.risk]?.[1] || 'KI' }}</span>
            <span>{{ it.ai.summary }}</span>
            <ul v-if="it.ai.known_issues?.length"><li v-for="k in it.ai.known_issues" :key="k">{{ k }}</li></ul>
            <div v-if="it.ai.sources?.length" class="rmm-muted">Quellen: <a v-for="(s, i) in it.ai.sources" :key="s" :href="s" target="_blank" rel="noopener">[{{ i + 1 }}] </a></div>
          </div>
          <p v-else-if="it.ai?.error" class="rmm-muted">KI: {{ it.ai.error }}</p>
          <details v-if="it.advisories?.length">
            <summary class="rmm-muted">{{ it.advisories.length }} Meldung(en) aus den Quellen</summary>
            <ul class="patch-adv"><li v-for="a in it.advisories" :key="a.id"><a :href="a.link" target="_blank" rel="noopener">{{ a.title }}</a> <span class="rmm-muted">— {{ a.source }}</span></li></ul>
          </details>
          <footer v-if="auth.isAdmin" class="rmm-actions">
            <button class="btn btn-primary btn-sm" :disabled="busy" @click="decide('approve', [it.id])">Freigeben</button>
            <button class="btn btn-secondary btn-sm" :disabled="busy" @click="decide('reject', [it.id])">Ablehnen</button>
            <button class="btn btn-ghost btn-sm" :disabled="busy" @click="reassess([it.id])">KI neu fragen</button>
          </footer>
        </article>
      </div>
    </section>

    <!-- Alle Updates ------------------------------------------------------ -->
    <section v-if="tab === 'items'" class="card">
      <div class="card-header rmm-actions">
        <input v-model="q" class="input" placeholder="Suche (Titel, KB, Paket, Rechner)" style="max-width:18rem" @keyup.enter="loadItems" />
        <select v-model="statusFilter" class="select" style="max-width:14rem" @change="loadItems">
          <option value="">offene</option>
          <option v-for="(v, k) in PATCH_STATUS" :key="k" :value="k">{{ v[1] }}</option>
          <option value="all">alle</option>
        </select>
        <select v-model="kindFilter" class="select" style="max-width:10rem" @change="loadItems">
          <option value="">System + Docker</option><option value="os">nur System</option><option value="docker">nur Docker</option>
        </select>
        <span v-if="agentFilter" class="badge badge-primary">{{ agentFilterName }} <a href="#" @click.prevent="agentFilter = ''; loadItems()">×</a></span>
        <template v-if="auth.isAdmin && selectedItems.length">
          <button class="btn btn-primary btn-sm" @click="decide('approve', selectedItems)">Freigeben</button>
          <button class="btn btn-danger btn-sm" @click="decide('reject', selectedItems)">Ablehnen</button>
          <button class="btn btn-ghost btn-sm" @click="decide('reset', selectedItems)">Entscheidung zurücksetzen</button>
        </template>
      </div>
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th></th><th>Update</th><th>Rechner</th><th>Version</th><th>Status</th><th>Regel / Grund</th><th>KI</th></tr></thead>
          <tbody>
            <tr v-if="!items.length"><td colspan="7" class="rmm-muted">Keine Updates in dieser Auswahl.</td></tr>
            <tr v-for="it in items" :key="it.id">
              <td><input v-model="selectedItems" type="checkbox" :value="it.id" /></td>
              <td>
                <div>{{ it.title }}</div>
                <div class="rmm-muted">{{ it.platform }}<span v-if="it.category"> · {{ it.category }}</span><span v-if="it.kb"> · {{ it.kb }}</span>
                  <span v-if="it.urgent" class="badge badge-danger">dringend</span><span v-if="it.security" class="badge badge-primary">Sicherheit</span></div>
              </td>
              <td>{{ it.hostname }}</td>
              <td class="rmm-code">{{ it.version_from || '' }}<span v-if="it.version_to"> → {{ it.version_to }}</span></td>
              <td>
                <span class="badge" :class="PATCH_STATUS[it.status]?.[0]">{{ PATCH_STATUS[it.status]?.[1] || it.status }}</span>
                <div v-if="it.not_before && ['auto','approved'].includes(it.status)" class="rmm-muted">ab {{ fmtTime(it.not_before) }}</div>
                <div v-if="it.decided_by" class="rmm-muted">{{ it.decided_by }}</div>
              </td>
              <td class="rmm-muted" style="max-width:22rem">{{ it.reason }}<div v-if="it.error" style="color:var(--color-danger)">{{ it.error }}</div></td>
              <td><span v-if="it.ai && !it.ai.error" class="badge" :class="RISK[it.ai.risk]?.[0]" :title="it.ai.summary">{{ it.ai.risk || '?' }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Läufe ------------------------------------------------------------- -->
    <section v-if="tab === 'runs'">
      <div class="card mb-4">
        <div class="rmm-table-wrap">
          <table class="table">
            <thead><tr><th>#</th><th>Rechner</th><th>Status</th><th>Auslöser</th><th>Updates</th><th>Sicherung</th><th>Start</th><th>Ende</th><th></th></tr></thead>
            <tbody>
              <tr v-if="!runs.length"><td colspan="9" class="rmm-muted">Noch keine Läufe.</td></tr>
              <tr v-for="r in runs" :key="r.id" :class="{ 'patch-row-active': run?.id === r.id }">
                <td>{{ r.id }}</td><td>{{ r.hostname }}</td>
                <td><span class="badge" :class="RUN_STATUS[r.status]?.[0]">{{ RUN_STATUS[r.status]?.[1] || r.status }}</span></td>
                <td>{{ r.trigger }}<div class="rmm-muted">{{ r.created_by }}</div></td>
                <td>{{ r.items.length }}</td>
                <td>
                  <span v-if="r.snapshot?.deleted_at" class="rmm-muted">gelöscht</span>
                  <span v-else-if="r.snapshot?.name || r.snapshot?.volid" class="badge badge-success" :title="r.snapshot.name || r.snapshot.volid">{{ r.snapshot.mode === 'backup' ? 'vzdump' : 'Snapshot' }}</span>
                  <span v-else-if="r.snapshot?.failed" class="badge badge-danger" :title="r.snapshot.failed">fehlgeschlagen</span>
                  <span v-else class="rmm-muted">–</span>
                  <div v-if="r.snapshot?.delete_after && !r.snapshot?.deleted_at && !r.snapshot?.keep" class="rmm-muted">löscht {{ fmtTime(r.snapshot.delete_after) }}</div>
                  <div v-if="r.snapshot?.keep" class="rmm-muted">wird behalten</div>
                </td>
                <td>{{ fmtTime(r.started_at) }}</td><td>{{ fmtTime(r.finished_at) }}</td>
                <td><button class="btn btn-ghost btn-sm" @click="openRun(r.id)">Details</button></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <section v-if="run" class="card">
        <div class="card-header">
          <h2 class="card-title">Lauf #{{ run.id }} — {{ run.hostname }}</h2>
          <div v-if="auth.isAdmin" class="rmm-actions">
            <button v-if="canRollback(run)" class="btn btn-danger btn-sm" :disabled="busy" @click="runAction(run, 'rollback')">Auf Snapshot zurückrollen</button>
            <button v-if="hasSnapshot(run) && !run.snapshot.keep" class="btn btn-secondary btn-sm" :disabled="busy" @click="runAction(run, 'keep')">Sicherung behalten</button>
            <button v-if="hasSnapshot(run) && run.snapshot.keep" class="btn btn-secondary btn-sm" :disabled="busy" @click="runAction(run, 'unkeep')">Löschen nach Frist</button>
            <button v-if="hasSnapshot(run)" class="btn btn-ghost btn-sm" :disabled="busy" @click="runAction(run, 'delete')">Sicherung jetzt löschen</button>
            <button v-if="['pending','snapshotting','baseline'].includes(run.status)" class="btn btn-ghost btn-sm" @click="runAction(run, 'cancel')">Abbrechen</button>
          </div>
        </div>
        <div class="card-body">
          <dl class="rmm-grid">
            <div><dt>Status</dt><dd><span class="badge" :class="RUN_STATUS[run.status]?.[0]">{{ RUN_STATUS[run.status]?.[1] }}</span></dd></div>
            <div><dt>Sicherung</dt><dd>{{ run.snapshot?.name || run.snapshot?.volid || '–' }}<span v-if="run.snapshot?.vmid" class="rmm-muted"> (VM {{ run.snapshot.vmid }} auf {{ run.snapshot.node }})</span></dd></div>
            <div><dt>Gesundheitsbild</dt><dd>vorher {{ run.baseline ? '✓' : '–' }} · nachher {{ run.after ? '✓' : '–' }}</dd></div>
            <div v-if="run.verify?.problems"><dt>Prüfung</dt><dd>{{ run.verify.problems.length ? run.verify.problems.join(' · ') : 'ohne Befund' }}</dd></div>
          </dl>
          <h3 class="mt-4 mb-2" style="font-weight:600">Updates</h3>
          <ul class="patch-adv">
            <li v-for="it in run.item_details || []" :key="it.id">
              <span class="badge" :class="PATCH_STATUS[it.status]?.[0]">{{ PATCH_STATUS[it.status]?.[1] }}</span> {{ it.title }}
              <span v-if="it.error" style="color:var(--color-danger)"> — {{ it.error }}</span>
            </li>
          </ul>
          <h3 class="mt-4 mb-2" style="font-weight:600">Ablauf</h3>
          <ol class="patch-log">
            <li v-for="(l, i) in run.log" :key="i" :class="`is-${l.level}`"><span class="rmm-muted">{{ fmtTime(l.ts) }}</span> {{ l.text }}</li>
          </ol>
        </div>
      </section>
    </section>

    <!-- Docker ------------------------------------------------------------ -->
    <section v-if="tab === 'docker'">
      <p class="rmm-note is-info">
        Container mit <span class="rmm-code">com.centurylinklabs.watchtower.enable=false</span> oder
        <span class="rmm-code">de.rimperio.update=off</span> bleiben unberührt, mit
        <span class="rmm-code">…monitor-only=true</span> bzw. <span class="rmm-code">de.rimperio.update=monitor</span>
        werden sie nur beobachtet. Compose-Container werden über <span class="rmm-code">docker compose</span> erneuert,
        alle anderen wie bei Watchtower — mit Gesundheitsprüfung und Zurückrollen.
      </p>
      <div v-for="a in dockerHosts" :key="a.agent_uid" class="card mb-4">
        <div class="card-header">
          <h2 class="card-title">{{ a.hostname }}</h2>
          <span class="rmm-muted">geprüft {{ fmtAgo(a.docker_scanned_at) }} · Docker {{ dockerInv[a.agent_uid]?.engine_version || '?' }}<span v-if="dockerInv[a.agent_uid]?.compose"> · Compose</span></span>
        </div>
        <div class="rmm-table-wrap">
          <table class="table">
            <thead><tr><th>Container</th><th>Image</th><th>Zustand</th><th>Version</th><th>Update</th></tr></thead>
            <tbody>
              <tr v-for="ct in dockerInv[a.agent_uid]?.containers || []" :key="ct.id">
                <td><strong>{{ ct.name }}</strong><div v-if="ct.compose_project" class="rmm-muted">{{ ct.compose_project }}/{{ ct.compose_service }}</div></td>
                <td class="rmm-code">{{ ct.image }}</td>
                <td>{{ ct.state }}<span v-if="ct.health" class="rmm-muted"> ({{ ct.health }})</span></td>
                <td class="rmm-code">{{ ct.local_version || (ct.local_digest || '').slice(7, 19) }}<span v-if="ct.update_available"> → {{ ct.remote_version || (ct.remote_digest || '').slice(7, 19) }}</span></td>
                <td>
                  <span v-if="ct.excluded" class="badge badge-neutral" :title="ct.exclude_reason">ausgenommen</span>
                  <span v-else-if="ct.monitor_only && ct.update_available" class="badge badge-warning">Update (nur beobachten)</span>
                  <span v-else-if="ct.update_available" class="badge badge-warning">Update verfügbar</span>
                  <span v-else-if="ct.error" class="rmm-muted" :title="ct.error">nicht prüfbar</span>
                  <span v-else class="badge badge-success">aktuell</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <p v-if="!dockerHosts.length" class="rmm-muted">Kein Rechner meldet bisher Docker. Die Suche läuft täglich mit der Patch-Suche (Linux-Agents mit Docker).</p>
    </section>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { fmtAgo, fmtTime, PATCH_STATUS, RISK, RUN_STATUS } from '../utils/rmm'

const TABS = [
  { key: 'overview', label: 'Übersicht' }, { key: 'approvals', label: 'Freigaben' },
  { key: 'items', label: 'Alle Updates' }, { key: 'runs', label: 'Läufe' }, { key: 'docker', label: 'Docker' },
]

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const tab = ref(route.query.tab || 'overview')
const agents = ref([])
const totals = ref({})
const approvals = ref([])
const selected = ref([])
const items = ref([])
const selectedItems = ref([])
const runs = ref([])
const run = ref(null)
const dockerInv = ref({})
const error = ref('')
const notice = ref('')
const busy = ref(false)
const q = ref('')
const statusFilter = ref('')
const kindFilter = ref('')
const agentFilter = ref('')
let timer = null

const c = (a, s) => a.counts?.[s] || 0
const activeRuns = computed(() => agents.value.filter(a => a.active_run).length)
const allSelected = computed(() => approvals.value.length && selected.value.length === approvals.value.length)
const dockerHosts = computed(() => agents.value.filter(a => a.docker))
const agentFilterName = computed(() => agents.value.find(a => a.agent_uid === agentFilter.value)?.hostname || '')

function flash(text) { notice.value = text; error.value = ''; setTimeout(() => { if (notice.value === text) notice.value = '' }, 6000) }
async function guard(fn) {
  busy.value = true
  try { await fn() } catch (e) { error.value = e.message } finally { busy.value = false }
}

async function loadOverview() {
  const data = await auth.api('/api/patching/overview')
  agents.value = data.agents
  totals.value = data.totals
}
async function loadApprovals() {
  approvals.value = await auth.api('/api/patching/items?status=needs_approval')
  selected.value = selected.value.filter(id => approvals.value.some(a => a.id === id))
}
async function loadItems() {
  const p = new URLSearchParams()
  const st = statusFilter.value === 'all' ? '' : (statusFilter.value || 'review,auto,needs_approval,approved,installing,failed')
  if (st) p.set('status', st)
  if (kindFilter.value) p.set('kind', kindFilter.value)
  if (agentFilter.value) p.set('agent_uid', agentFilter.value)
  if (q.value) p.set('q', q.value)
  items.value = await auth.api(`/api/patching/items?${p}`)
}
async function loadRuns() { runs.value = await auth.api('/api/patching/runs?limit=100') }
async function loadDocker() {
  const out = {}
  await Promise.all(dockerHosts.value.map(async a => { out[a.agent_uid] = await auth.api(`/api/patching/agents/${a.agent_uid}/docker`) }))
  dockerInv.value = out
}

async function load() {
  try {
    await loadOverview()
    if (tab.value === 'approvals') await loadApprovals()
    else await loadApprovals().catch(() => {})
    if (tab.value === 'items') await loadItems()
    if (tab.value === 'runs') { await loadRuns(); if (run.value) await openRun(run.value.id, false) }
    if (tab.value === 'docker') await loadDocker()
    error.value = ''
  } catch (e) { error.value = e.message }
}

function setFilter(st) { statusFilter.value = st; tab.value = 'items' }
function showAgent(a) { agentFilter.value = a.agent_uid; statusFilter.value = 'all'; tab.value = 'items' }
function toggleAll() { selected.value = allSelected.value ? [] : approvals.value.map(a => a.id) }

async function decide(decision, ids = selected.value) {
  if (!ids.length) return
  if (decision === 'reject' && !confirm(`${ids.length} Update(s) ablehnen? Sie werden nicht installiert, bis die Entscheidung zurückgesetzt wird.`)) return
  await guard(async () => {
    const r = await auth.api('/api/patching/items/decision', { method: 'POST', body: { ids, decision, defer_days: 7 } })
    flash(`${r.changed} Update(s): ${{ approve: 'freigegeben', reject: 'abgelehnt', defer: 'zurückgestellt', reset: 'neu eingestuft' }[decision]}.`)
    selected.value = []; selectedItems.value = []
    await load()
  })
}
async function reassess(ids) {
  await guard(async () => { await auth.api('/api/patching/items/reassess', { method: 'POST', body: { ids } }); flash('Die KI schaut sich das Update erneut an.'); await load() })
}
async function scan(a) {
  await guard(async () => { await auth.api(`/api/patching/agents/${a.agent_uid}/scan`, { method: 'POST' }); flash(`${a.hostname}: Suche eingereiht.`) })
}
async function scanAll() {
  await guard(async () => { const r = await auth.api('/api/patching/scan-all', { method: 'POST' }); flash(`Suche auf ${r.agents} Rechner(n) eingereiht.`) })
}
async function installNow(a) {
  const n = c(a, 'auto') + c(a, 'approved')
  const hint = a.pve ? 'Vorher wird ein Proxmox-Snapshot angelegt (laut Richtlinie).' : 'Keine Proxmox-VM zugeordnet – ohne Snapshot.'
  if (!confirm(`${n} Update(s) auf ${a.hostname} jetzt installieren? ${hint}`)) return
  await guard(async () => {
    const r = await auth.api(`/api/patching/agents/${a.agent_uid}/install`, { method: 'POST', body: { item_ids: [] } })
    flash(`Lauf #${r.id} gestartet.`); tab.value = 'runs'; await openRun(r.id)
  })
}
async function openRun(id, switchTab = true) {
  if (switchTab) tab.value = 'runs'
  try { run.value = await auth.api(`/api/patching/runs/${id}`) } catch (e) { error.value = e.message }
}
const hasSnapshot = r => !!(r.snapshot?.name || r.snapshot?.volid) && !r.snapshot?.deleted_at
const canRollback = r => hasSnapshot(r) && r.snapshot.mode === 'snapshot' && ['failed', 'succeeded', 'verifying'].includes(r.status)
async function runAction(r, what) {
  const texts = { rollback: `VM ${r.snapshot?.vmid} auf den Snapshot ${r.snapshot?.name} zurückrollen? Alles seit dem Snapshot geht verloren.`,
    delete: 'Sicherung jetzt löschen?', cancel: 'Lauf abbrechen?' }
  if (texts[what] && !confirm(texts[what])) return
  await guard(async () => {
    const path = { rollback: 'rollback', delete: 'snapshot/delete', keep: 'snapshot/keep?keep=true', unkeep: 'snapshot/keep?keep=false', cancel: 'cancel' }[what]
    await auth.api(`/api/patching/runs/${r.id}/${path}`, { method: 'POST' })
    flash('Erledigt.'); await loadRuns(); await openRun(r.id, false)
  })
}

watch(tab, t => { router.replace({ query: { ...route.query, tab: t } }); load() })
onMounted(() => { load(); timer = setInterval(load, 20000) })
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.patch-kpis { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); margin-bottom: 1rem; }
.patch-kpi { display: flex; flex-direction: column; align-items: flex-start; padding: .9rem 1rem; text-align: left; cursor: pointer; border: 1px solid var(--color-border); }
.patch-kpi strong { font-size: 1.5rem; line-height: 1.2; }
.patch-kpi span { color: var(--color-text-muted); font-size: .8rem; }
.patch-cards { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fill, minmax(min(100%, 26rem), 1fr)); }
.patch-card { padding: 1rem; display: flex; flex-direction: column; gap: .5rem; }
.patch-card.is-selected { outline: 2px solid var(--color-primary); }
.patch-card header { display: flex; gap: .5rem; align-items: flex-start; flex-wrap: wrap; }
.patch-title { font-weight: 600; overflow-wrap: anywhere; }
.patch-reason { font-size: .85rem; margin: 0; }
.patch-ai { font-size: .85rem; background: var(--color-surface); border-radius: var(--radius-sm); padding: .5rem .65rem; display: flex; flex-direction: column; gap: .3rem; }
.patch-ai ul, .patch-adv { margin: 0; padding-left: 1.1rem; font-size: .85rem; }
.patch-log { margin: 0; padding-left: 1.2rem; font-size: .85rem; display: flex; flex-direction: column; gap: .2rem; }
.patch-log .is-error { color: var(--color-danger); }
.patch-log .is-warning { color: var(--color-warning); }
.patch-row-active { background: var(--color-surface); }
</style>
