<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Fernwartung: ein Rechner (Status, Konsole, Skripte,
                   Aufträge, Patches, Checks, Platten & Prozesse, Inventur)
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <router-link to="/rmm" class="rmm-muted">← Rechner</router-link>
        <h1 class="page-title">
          <span class="rmm-dot" :class="agent.online ? 'is-online' : 'is-offline'"></span>
          {{ agent.hostname || '…' }}
        </h1>
        <p class="page-subtitle">
          {{ agent.online ? 'online' : 'offline' }} · zuletzt {{ fmtAgo(agent.last_seen) }} ·
          {{ agent.os || PLATFORM_LABEL[agent.platform] || '' }} · Agent {{ agent.version || '–' }}
        </p>
      </div>
      <div class="rmm-actions">
        <router-link v-if="agent.hostname" :to="`/devices/${encodeURIComponent(agent.hostname)}`" class="btn btn-secondary btn-sm">
          <AppIcon name="logs" :size="16" /> Logs
        </router-link>
        <button v-if="auth.isAdmin" class="btn btn-danger btn-sm" @click="showRemove = !showRemove">Agent entfernen</button>
      </div>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>
    <p v-if="agent.uninstall_pending" class="rmm-note is-info">
      Deinstallation angefordert.
      {{ agent.online
        ? 'Der Agent führt sie beim nächsten Check-in aus (spätestens in einer Minute) und verschwindet dann aus der Liste.'
        : 'Der Rechner ist offline – der Agent deinstalliert sich, sobald er sich wieder meldet.' }}
      Kommt er nicht wieder, löscht „Agent entfernen“ ohne Häkchen den Eintrag sofort.
    </p>

    <section v-if="showRemove && auth.isAdmin" class="card mb-4">
      <div class="card-header"><h2 class="card-title">Agent entfernen</h2></div>
      <div class="card-body">
        <label class="rmm-remove-option">
          <input v-model="removeUninstall" type="checkbox">
          <span>Agent auch auf dem Rechner deinstallieren</span>
        </label>
        <p class="rmm-muted rmm-remove-text">
          <template v-if="removeUninstall">
            Der Agent bekommt beim nächsten Check-in den Auftrag, sich zu deinstallieren, und meldet sich dabei hier ab.
            Ist der Rechner aus, passiert das, sobald er wieder läuft.
          </template>
          <template v-else>
            Nur der Eintrag hier wird gelöscht. Der Agent bleibt auf dem Rechner installiert und wird abgewiesen,
            bis man ihn dort deinstalliert.
          </template>
          Befehlsverlauf, Messwerte und Patch-Stand gehen verloren. Hat das Gerät Logs, bleibt es mit ihnen unter Geräte stehen.
        </p>
        <div class="rmm-actions">
          <button class="btn btn-danger btn-sm" :disabled="busy" @click="remove">
            {{ removeUninstall ? 'Deinstallieren und entfernen' : 'Nur Eintrag entfernen' }}
          </button>
          <button class="btn btn-secondary btn-sm" @click="showRemove = false">Abbrechen</button>
        </div>
      </div>
    </section>

    <section class="card mb-4">
      <div class="card-body">
        <dl class="rmm-grid">
          <div v-for="m in metrics" :key="m.label">
            <dt>{{ m.label }}</dt>
            <dd :class="loadClass(m.value)">{{ fmtPct(m.value) }}</dd>
            <div class="rmm-bar"><span :class="loadClass(m.value)" :style="{ width: (m.value || 0) + '%' }"></span></div>
          </div>
          <div><dt>Lokale IP</dt><dd>{{ agent.local_ip || '–' }}</dd></div>
          <div><dt>Öffentliche IP</dt><dd>{{ agent.public_ip || '–' }}</dd></div>
          <div><dt>Gestartet</dt><dd>{{ fmtTime(agent.boot_time) }}</dd></div>
          <div><dt>Angemeldet seit</dt><dd>{{ fmtTime(agent.enrolled_at) }}</dd></div>
          <div>
            <dt>Standort</dt>
            <dd v-if="!auth.isAdmin">{{ agent.site }}</dd>
            <input v-else v-model="edit.site" class="input" @change="save" />
          </div>
          <div>
            <dt>Beschreibung</dt>
            <dd v-if="!auth.isAdmin">{{ agent.description || '–' }}</dd>
            <input v-else v-model="edit.description" class="input" @change="save" />
          </div>
        </dl>
      </div>
    </section>

    <nav class="rmm-tabs">
      <button v-for="t in tabs" :key="t.key" :class="{ 'is-active': tab === t.key }" @click="tab = t.key">
        {{ t.label }}<span v-if="t.key === 'jobs' && activeCount" class="badge badge-primary ad-tab-badge">{{ activeCount }}</span>
      </button>
    </nav>

    <!-- Konsole ---------------------------------------------------------------->
    <section v-if="tab === 'console'" class="card">
      <div class="card-body">
        <p class="ad-delivery" :class="deliveryClass">{{ deliveryText }}</p>

        <div ref="consoleBox" class="ad-console">
          <p v-if="!consoleCommands.length" class="ad-console-empty">Noch keine Befehle auf diesem Rechner. Unten eingeben und mit Enter abschicken.</p>
          <div v-for="c in consoleCommands" :key="c.id" class="ad-console-entry">
            <div class="ad-console-cmd">
              <span class="ad-prompt">{{ c.shell }} $</span>
              <span class="ad-console-text">{{ c.source || c.command }}</span>
              <span class="ad-console-meta">
                <span class="badge" :class="STATUS_BADGE[c.status]">{{ STATUS_LABEL[c.status] || c.status }}</span>
                <template v-if="c.exit_code !== null"> Exit {{ c.exit_code }}</template>
                · {{ c.created_by }} · {{ fmtTime(c.created_at) }}
                <template v-if="c.finished_at"> · {{ fmtDuration(c.picked_at || c.created_at, c.finished_at) }}</template>
              </span>
            </div>
            <p v-if="c.status === 'pending'" class="ad-console-wait">wartet auf den Agent …</p>
            <p v-else-if="c.status === 'running'" class="ad-console-wait">läuft seit {{ fmtDuration(c.picked_at) }} …</p>
            <template v-else>
              <pre v-if="c.stdout" class="ad-console-out">{{ c.stdout }}</pre>
              <pre v-if="c.stderr" class="ad-console-out is-bad">{{ c.stderr }}</pre>
              <p v-if="!c.stdout && !c.stderr" class="ad-console-wait">(keine Ausgabe)</p>
            </template>
          </div>
        </div>

        <form v-if="auth.isAdmin" class="ad-console-input" @submit.prevent="run">
          <select v-model="cmd.shell" class="select ad-shell" title="Shell">
            <option v-for="s in shells" :key="s.value" :value="s.value">{{ s.label }}</option>
          </select>
          <textarea ref="cmdInput" v-model="cmd.command" class="textarea rmm-code ad-cmd" :rows="cmdRows"
                    :placeholder="SHELL_PLACEHOLDER[cmd.shell] || ''" spellcheck="false" autocomplete="off"
                    @keydown="onCmdKey"></textarea>
          <button type="submit" class="btn btn-primary btn-sm" :disabled="busy || !cmd.command.trim()">Ausführen</button>
        </form>
        <p v-if="auth.isAdmin" class="rmm-muted ad-hint">
          Enter schickt ab · Umschalt+Enter neue Zeile · ↑/↓ frühere Befehle · Zeitlimit
          <input v-model.number="cmd.timeout" type="number" min="1" max="86400" class="input ad-timeout" /> s
        </p>
      </div>
    </section>

    <!-- Skripte ---------------------------------------------------------------->
    <section v-if="tab === 'scripts'" class="card">
      <div class="card-header">
        <h2 class="card-title">Skripte für {{ PLATFORM_LABEL[agent.platform] || 'diesen Rechner' }}</h2>
        <router-link to="/rmm/scripts" class="btn btn-ghost btn-sm">Bibliothek verwalten</router-link>
      </div>
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Skript</th><th>Kategorie</th><th>Shell</th><th>Zeitlimit</th><th></th></tr></thead>
          <tbody>
            <tr v-if="!platformScripts.length">
              <td colspan="5" class="rmm-muted">
                Keine passenden Skripte in der Bibliothek.
                <template v-if="hiddenScripts">({{ hiddenScripts }} weitere gehören zu einem anderen System.)</template>
                <router-link to="/rmm/scripts">Skript anlegen</router-link>
              </td>
            </tr>
            <template v-for="s in platformScripts" :key="s.id">
              <tr>
                <td><strong>{{ s.name }}</strong><div v-if="s.description" class="rmm-muted">{{ s.description }}</div></td>
                <td>{{ s.category }}</td>
                <td class="rmm-code">{{ s.shell }}</td>
                <td>{{ s.default_timeout }} s</td>
                <td>
                  <div class="rmm-actions ad-row-actions">
                    <button class="btn btn-ghost btn-sm" @click="toggleScript(s.id)">{{ openScript === s.id ? 'Ausblenden' : 'Anzeigen' }}</button>
                    <button v-if="auth.isAdmin" class="btn btn-primary btn-sm" :disabled="busy" @click="runScript(s)">Starten</button>
                  </div>
                </td>
              </tr>
              <tr v-if="openScript === s.id"><td colspan="5"><pre class="ad-console-out">{{ s.body }}</pre></td></tr>
            </template>
          </tbody>
        </table>
      </div>
      <p class="rmm-muted ad-foot">Gestartete Skripte erscheinen mit ihrer Ausgabe in der Konsole.</p>
    </section>

    <!-- Aufträge --------------------------------------------------------------->
    <section v-if="tab === 'jobs'">
      <div v-for="r in activeRuns" :key="r.id" class="card mb-4">
        <div class="card-header">
          <h2 class="card-title">
            Patch-Lauf #{{ r.id }}
            <span class="badge" :class="RUN_STATUS[r.status]?.[0]">{{ RUN_STATUS[r.status]?.[1] || r.status }}</span>
          </h2>
          <router-link :to="`/rmm/patches?tab=runs&run=${r.id}`" class="btn btn-secondary btn-sm">Details im Patch-Center</router-link>
        </div>
        <div class="card-body">
          <p class="rmm-muted">{{ r.items.length }} Update(s) · {{ r.trigger }} · {{ r.created_by }} · seit {{ fmtTime(r.started_at) }}</p>
          <ol class="ad-runlog">
            <li v-for="(e, i) in r.log.slice(-6)" :key="i" :class="`is-${e.level}`"><span class="rmm-muted">{{ fmtTime(e.ts) }}</span> {{ e.text }}</li>
          </ol>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h2 class="card-title">Aufträge</h2>
          <div class="rmm-actions">
            <button v-for="f in JOB_FILTERS" :key="f.key" class="btn btn-sm" :class="jobFilter === f.key ? 'btn-primary' : 'btn-ghost'" @click="jobFilter = f.key">{{ f.label }}</button>
          </div>
        </div>
        <div class="card-body">
          <p v-if="!shownJobs.length" class="rmm-muted">Keine Aufträge in dieser Auswahl.</p>
          <div v-for="c in shownJobs" :key="c.id" class="ad-job" :class="{ 'is-open': openJob === c.id }">
            <button class="ad-job-head" @click="openJob = openJob === c.id ? null : c.id">
              <span class="ad-job-caret">{{ openJob === c.id ? '▾' : '▸' }}</span>
              <span class="badge" :class="STATUS_BADGE[c.status]">{{ STATUS_LABEL[c.status] || c.status }}</span>
              <span class="ad-job-title">{{ jobTitle(c) }}</span>
              <span class="ad-job-kind">{{ COMMAND_KIND_LABEL[c.kind] || c.kind }}</span>
              <span class="ad-job-meta">
                {{ fmtTime(c.created_at) }}
                <template v-if="c.status === 'running'"> · läuft {{ fmtDuration(c.picked_at) }}</template>
                <template v-else-if="c.finished_at"> · {{ fmtDuration(c.picked_at || c.created_at, c.finished_at) }}</template>
                <template v-if="c.exit_code !== null"> · Exit {{ c.exit_code }}</template>
              </span>
            </button>
            <div v-if="openJob === c.id" class="ad-job-body">
              <p class="rmm-muted">Auftrag #{{ c.id }} · von {{ c.created_by }}<template v-if="c.picked_at"> · abgeholt {{ fmtTime(c.picked_at) }}</template></p>
              <pre v-if="c.kind === 'shell'" class="ad-console-out">{{ c.command }}</pre>
              <p v-if="c.status === 'pending'" class="rmm-muted">Wartet darauf, dass der Agent ihn abholt.</p>
              <p v-else-if="c.status === 'running'" class="rmm-muted">Läuft auf dem Rechner – das Ergebnis erscheint hier, sobald der Agent es meldet.</p>
              <CommandOutput v-else :kind="c.kind" :stdout="c.stdout || ''" :stderr="c.stderr || ''" />
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Patches ---------------------------------------------------------------->
    <section v-if="tab === 'patches'" class="card">
      <div class="card-body">
        <div class="rmm-actions mb-3">
          <strong>{{ patches.count }} ausstehende Updates</strong>
          <span class="rmm-muted">· geprüft {{ fmtTime(patches.scanned_at) }}</span>
          <template v-if="auth.isAdmin">
            <button class="btn btn-secondary btn-sm" :disabled="busy || scanRunning" @click="patch('scan')">{{ scanRunning ? 'Suche läuft …' : 'Nach Updates suchen' }}</button>
            <button class="btn btn-primary btn-sm" :disabled="busy || !patches.count || !!activeRuns.length" @click="patch('apply')">Updates installieren</button>
          </template>
          <button v-if="activeRuns.length" class="btn btn-ghost btn-sm" @click="tab = 'jobs'">Laufende Installation ansehen →</button>
        </div>
        <div v-if="patches.updates.length" class="rmm-table-wrap">
          <table class="table">
            <thead><tr><th>Update</th><th>Version</th><th>Kategorie</th><th></th></tr></thead>
            <tbody>
              <tr v-for="(u, i) in patches.updates" :key="u.id || i">
                <td>{{ u.package || u.title || u.name || u.id }}<div v-if="u.kb" class="rmm-muted">{{ u.kb }}</div></td>
                <td class="rmm-code">
                  <template v-if="u.current || u.available">{{ u.current || '?' }} → {{ u.available || '?' }}</template>
                  <template v-else>{{ u.version || '' }}</template>
                </td>
                <td>{{ u.category || u.severity || '' }}</td>
                <td>
                  <div class="rmm-actions">
                    <span v-if="u.security" class="badge badge-primary">Sicherheit</span>
                    <span v-if="u.reboot" class="badge badge-neutral">Neustart</span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="rmm-muted">Keine ausstehenden Updates bekannt.</p>
        <p class="rmm-muted mt-2">
          Freigaben, Regeln und KI-Einschätzung je Update: <router-link :to="`/rmm/patches?tab=items`">Patch-Center</router-link>.
        </p>
        <template v-if="patches.last_apply_output">
          <h3 class="card-title mt-4">Letzte Installation · {{ fmtTime(patches.last_apply_at) }}</h3>
          <CommandOutput kind="patch_apply" :stdout="patches.last_apply_output" />
        </template>
      </div>
    </section>

    <!-- Checks ----------------------------------------------------------------->
    <section v-if="tab === 'checks'">
      <div v-if="checkForm" class="card mb-4">
        <div class="card-header"><h2 class="card-title">{{ checkForm.id ? 'Check bearbeiten' : 'Neuer Check' }}</h2></div>
        <div class="card-body rmm-form">
          <label>Name<input v-model="checkForm.name" class="input" placeholder="z. B. Platte C: unter 90 %" /></label>
          <label>Typ
            <select v-model="checkForm.type" class="select" @change="checkForm.params = {}">
              <option v-for="(t, k) in CHECK_TYPES" :key="k" :value="k">{{ t.label }}</option>
            </select>
          </label>
          <template v-for="p in CHECK_TYPES[checkForm.type].params" :key="p.key">
            <label v-if="p.shell">{{ p.label }}
              <select v-model="checkForm.params[p.key]" class="select"><option v-for="s in shells" :key="s.value" :value="s.value">{{ s.label }}</option></select>
            </label>
            <label v-else :class="{ 'is-wide': p.key === 'body' }">{{ p.label }}
              <textarea v-if="p.key === 'body'" v-model="checkForm.params[p.key]" class="textarea rmm-code" rows="3" :placeholder="p.hint || ''"></textarea>
              <input v-else v-model="checkForm.params[p.key]" class="input" :type="p.number ? 'number' : 'text'" :placeholder="p.hint || ''" />
            </label>
          </template>
          <label>Intervall (s)<input v-model.number="checkForm.interval_sec" type="number" min="30" class="input" /></label>
          <label>Gilt für
            <select v-model="checkForm.scope_type" class="select">
              <option value="agent">nur diesen Rechner</option>
              <option value="site">Standort „{{ agent.site || 'Standard' }}“</option>
              <option value="global">alle Rechner</option>
            </select>
          </label>
          <label>Schwere
            <select v-model="checkForm.severity" class="select"><option value="warning">Warnung</option><option value="critical">kritisch</option><option value="info">Info</option></select>
          </label>
          <div class="is-wide rmm-actions">
            <button class="btn btn-primary btn-sm" :disabled="busy || !checkForm.name" @click="saveCheck">Speichern</button>
            <button class="btn btn-ghost btn-sm" @click="checkForm = null">Abbrechen</button>
            <span class="rmm-muted">Der Agent führt den Check ab seinem nächsten Check-in aus.</span>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h2 class="card-title">Checks auf diesem Rechner</h2>
          <div class="rmm-actions">
            <router-link to="/rmm/checks" class="btn btn-ghost btn-sm">Alle Checks</router-link>
            <button v-if="auth.isAdmin && !checkForm" class="btn btn-primary btn-sm" @click="openCheck()"><AppIcon name="plus" :size="16" /> Check anlegen</button>
          </div>
        </div>
        <div class="rmm-table-wrap">
          <table class="table">
            <thead><tr><th>Check</th><th>Typ</th><th>Gilt für</th><th>Status</th><th>Wert</th><th>Zeit</th><th></th></tr></thead>
            <tbody>
              <tr v-if="!agentChecks.length"><td colspan="7" class="rmm-muted">Noch keine Checks für diesen Rechner.</td></tr>
              <tr v-for="c in agentChecks" :key="c.id" :title="c.result?.output || ''" :style="c.enabled ? '' : 'opacity:.55'">
                <td><strong>{{ c.name }}</strong></td>
                <td>{{ CHECK_TYPES[c.type]?.label || c.type }}</td>
                <td>{{ c.scope_type === 'agent' ? 'dieser Rechner' : c.scope_type === 'site' ? `Standort ${c.scope_value}` : 'alle Rechner' }}</td>
                <td>
                  <span v-if="c.result" class="badge" :class="STATUS_BADGE[c.result.status]">{{ STATUS_LABEL[c.result.status] || c.result.status }}</span>
                  <span v-else class="rmm-muted">{{ c.enabled ? 'noch kein Ergebnis' : 'aus' }}</span>
                </td>
                <td>{{ c.result?.value || '' }}</td>
                <td>{{ c.result ? fmtTime(c.result.ts) : '' }}</td>
                <td>
                  <div v-if="auth.isAdmin" class="rmm-actions ad-row-actions">
                    <button class="btn btn-secondary btn-sm" @click="openCheck(c)">Bearbeiten</button>
                    <button class="btn btn-danger btn-sm" @click="removeCheck(c)">Löschen</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- Inventur --------------------------------------------------------------->
    <section v-if="tab === 'inventory'" class="card">
      <div class="card-body">
        <p v-if="!agent.inventory" class="rmm-muted">Noch keine Inventur übermittelt.</p>
        <template v-else>
          <dl class="rmm-grid mb-4">
            <div v-for="(v, k) in simpleInventory" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></div>
          </dl>
          <details v-for="(v, k) in complexInventory" :key="k" class="mb-2">
            <summary><strong>{{ k }}</strong> <span class="rmm-muted">({{ Array.isArray(v) ? v.length + ' Einträge' : 'Details' }})</span></summary>
            <table v-if="Array.isArray(v) && v.length && typeof v[0] === 'object'" class="table">
              <thead><tr><th v-for="col in Object.keys(v[0]).slice(0, 6)" :key="col">{{ col }}</th></tr></thead>
              <tbody><tr v-for="(row, i) in v" :key="i"><td v-for="col in Object.keys(v[0]).slice(0, 6)" :key="col">{{ row[col] }}</td></tr></tbody>
            </table>
            <pre v-else class="rmm-pre">{{ JSON.stringify(v, null, 2) }}</pre>
          </details>
        </template>
      </div>
    </section>

    <!-- Prozesse --------------------------------------------------------------->
    <section v-if="tab === 'procs'" class="card">
      <div class="card-body">
        <p class="rmm-muted mb-2">Stand des letzten Check-ins · Platten und Prozesse mit der höchsten Last.</p>
        <table v-if="last.disk?.length" class="table mb-4">
          <thead><tr><th>Laufwerk</th><th>Belegt</th><th>Größe</th></tr></thead>
          <tbody><tr v-for="d in last.disk" :key="d.mount"><td>{{ d.mount }}</td><td :class="loadClass(d.percent)">{{ fmtPct(d.percent) }}</td><td>{{ d.total_gb?.toFixed(0) }} GB</td></tr></tbody>
        </table>
        <table v-if="last.top_procs?.length" class="table">
          <thead><tr><th>Prozess</th><th>CPU</th><th>RAM</th></tr></thead>
          <tbody><tr v-for="(p, i) in last.top_procs" :key="i"><td>{{ p.name }}</td><td>{{ fmtPct(p.cpu) }}</td><td>{{ fmtPct(p.mem) }}</td></tr></tbody>
        </table>
        <p class="rmm-muted mt-2">Laufzeit: {{ fmtUptime(last.uptime) }}</p>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import CommandOutput from '../components/CommandOutput.vue'
import {
  CHECK_TYPES, COMMAND_KIND_LABEL, PLATFORM_LABEL, RUN_STATUS, SHELL_PLACEHOLDER, STATUS_BADGE, STATUS_LABEL,
  checkApplies, fmtAgo, fmtDuration, fmtPct, fmtTime, fmtUptime, loadClass, normalizeCheckParams, shellsFor,
} from '../utils/rmm'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const uid = route.params.uid
const base = `/api/rmm/agents/${uid}`

const TABS = [
  { key: 'console', label: 'Konsole' }, { key: 'scripts', label: 'Skripte' }, { key: 'jobs', label: 'Aufträge' },
  { key: 'patches', label: 'Patches' }, { key: 'checks', label: 'Checks' }, { key: 'procs', label: 'Platten & Prozesse' },
  { key: 'inventory', label: 'Inventur' },
]
const JOB_FILTERS = [
  { key: 'all', label: 'Alle' }, { key: 'active', label: 'Laufend' },
  { key: 'system', label: 'Patches & System' }, { key: 'shell', label: 'Befehle & Skripte' },
]
const FINAL_RUN = ['succeeded', 'failed', 'rolled_back', 'cancelled']

const agent = ref({})
const edit = reactive({ site: '', description: '' })
const commands = ref([])
const scripts = ref([])
const patches = ref({ count: 0, updates: [] })
const checks = ref([])
const checkResults = ref([])
const runs = ref([])
const last = ref({})
const error = ref('')
const notice = ref('')
const busy = ref(false)
const tab = ref(TABS.some(t => t.key === route.query.tab) ? route.query.tab : 'console')
const cmd = reactive({ shell: '', command: '', timeout: 300 })
const cmdHistory = ref([])
const historyPos = ref(-1)
const openScript = ref(null)
const openJob = ref(null)
const jobFilter = ref('all')
const checkForm = ref(null)
const showRemove = ref(false)
const removeUninstall = ref(true)
const consoleBox = ref(null)
const cmdInput = ref(null)
let timer = null
let stopped = false

const tabs = TABS
const shells = computed(() => shellsFor(agent.value.platform))
const metrics = computed(() => [
  { label: 'CPU', value: agent.value.last_cpu },
  { label: 'Arbeitsspeicher', value: agent.value.last_mem },
  { label: 'Platte (voll.)', value: agent.value.last_disk },
])
const simpleInventory = computed(() => Object.fromEntries(
  Object.entries(agent.value.inventory || {}).filter(([, v]) => v === null || typeof v !== 'object')))
const complexInventory = computed(() => Object.fromEntries(
  Object.entries(agent.value.inventory || {}).filter(([, v]) => v !== null && typeof v === 'object')))

const isActive = c => c.status === 'pending' || c.status === 'running'
const consoleCommands = computed(() => commands.value.filter(c => c.kind === 'shell').slice(0, 30).reverse())
const activeRuns = computed(() => runs.value.filter(r => !FINAL_RUN.includes(r.status)))
const activeCount = computed(() => commands.value.filter(isActive).length + activeRuns.value.length)
const scanRunning = computed(() => commands.value.some(c => c.kind === 'patch_scan' && isActive(c)))
const shownJobs = computed(() => commands.value.filter(c =>
  jobFilter.value === 'all' ? true
    : jobFilter.value === 'active' ? isActive(c)
      : jobFilter.value === 'shell' ? c.kind === 'shell' : c.kind !== 'shell'))
const platformScripts = computed(() => {
  const allowed = shells.value.map(s => s.value)
  const p = agent.value.platform || ''
  return scripts.value.filter(s => (s.platform === 'any' || !p || s.platform === p) && allowed.includes(s.shell))
})
const hiddenScripts = computed(() => scripts.value.length - platformScripts.value.length)
const agentChecks = computed(() => {
  const byId = Object.fromEntries(checkResults.value.map(r => [r.check_id, r]))
  return checks.value.filter(c => checkApplies(c, agent.value)).map(c => ({ ...c, result: byId[c.id] || null }))
})
const cmdRows = computed(() => Math.min(8, Math.max(1, cmd.command.split('\n').length)))

const deliveryText = computed(() => {
  if (!agent.value.online) return 'Rechner offline – Befehle warten, bis der Agent sich wieder meldet.'
  if (agent.value.live) return 'Verbunden – Befehle kommen sofort beim Agent an.'
  return 'Der Agent holt Befehle beim nächsten Check-in ab (≤ 1 min). Ab Agent-Version 2026.11.21 kommen sie sofort an.'
})
const deliveryClass = computed(() => (!agent.value.online ? 'is-off' : agent.value.live ? 'is-live' : 'is-slow'))

function jobTitle(c) {
  if (c.kind === 'shell') return c.source || c.command.split('\n')[0].slice(0, 120)
  return c.source || COMMAND_KIND_LABEL[c.kind] || c.kind
}

function flash(text) {
  notice.value = text
  setTimeout(() => { if (notice.value === text) notice.value = '' }, 6000)
}

async function guard(fn, ok = '') {
  busy.value = true
  error.value = ''
  try {
    await fn()
    if (ok) flash(ok)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

function scrollConsole() {
  nextTick(() => { if (consoleBox.value) consoleBox.value.scrollTop = consoleBox.value.scrollHeight })
}

async function loadCommands() {
  const before = consoleCommands.value.map(c => `${c.id}:${c.status}`).join()
  commands.value = await auth.api(`${base}/commands?limit=100`)
  if (consoleCommands.value.map(c => `${c.id}:${c.status}`).join() !== before) scrollConsole()
}

async function load() {
  let a
  try {
    a = await auth.api(base)
  } catch (e) {
    // Nach einer angeforderten Deinstallation meldet sich der Agent ab und
    // verschwindet — dann zurück zur Liste statt einer Fehlermeldung.
    if (agent.value.uninstall_pending) { router.push('/rmm'); return }
    error.value = e.message
    return
  }
  try {
    const [p, r, t, ck, rs] = await Promise.all([
      auth.api(`${base}/patches`), auth.api(`/api/rmm/checks/results?uid=${uid}`),
      auth.api(`${base}/telemetry?hours=1`), auth.api('/api/rmm/checks'),
      auth.api(`/api/patching/runs?agent_uid=${uid}&limit=10`).catch(() => []),
    ])
    agent.value = a
    if (document.activeElement?.tagName !== 'INPUT') Object.assign(edit, { site: a.site, description: a.description })
    patches.value = p
    checkResults.value = r
    last.value = t.length ? t[t.length - 1] : {}
    checks.value = ck
    runs.value = rs
    if (!shells.value.some(s => s.value === cmd.shell)) cmd.shell = shells.value[0].value
    await loadCommands()
  } catch (e) {
    error.value = e.message
  }
}

// Takt: solange ein Befehl aus der Konsole läuft, alle 1,5 s nachsehen —
// sonst alle 15 s alles neu laden.
async function tick() {
  if (stopped) return
  const fast = commands.value.some(c => c.kind === 'shell' && isActive(c))
  try {
    if (fast) await loadCommands()
    else await load()
  } catch { /* nächster Versuch im nächsten Takt */ }
  if (!stopped) timer = setTimeout(tick, fast ? 1500 : 15000)
}
function kick() {
  clearTimeout(timer)
  timer = setTimeout(tick, 600)
}

const save = () => guard(() => auth.api(base, { method: 'PATCH', body: { ...edit } }), 'Gespeichert.')

function onCmdKey(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    run()
    return
  }
  const single = !cmd.command.includes('\n')
  if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && single && cmdHistory.value.length) {
    e.preventDefault()
    const n = cmdHistory.value.length
    historyPos.value = e.key === 'ArrowUp'
      ? Math.min(n - 1, historyPos.value + 1)
      : Math.max(-1, historyPos.value - 1)
    cmd.command = historyPos.value < 0 ? '' : cmdHistory.value[n - 1 - historyPos.value]
  }
}

const run = () => {
  const text = cmd.command.trim()
  if (!text || busy.value) return
  return guard(async () => {
    await auth.api(`${base}/commands`, { method: 'POST', body: { shell: cmd.shell, command: text, timeout: cmd.timeout || 300 } })
    cmdHistory.value = [...cmdHistory.value.filter(h => h !== text), text].slice(-50)
    historyPos.value = -1
    cmd.command = ''
    await loadCommands()
    scrollConsole()
    kick()
    nextTick(() => cmdInput.value?.focus())
  })
}

function toggleScript(id) { openScript.value = openScript.value === id ? null : id }

const runScript = s => guard(async () => {
  await auth.api(`${base}/run-script/${s.id}`, { method: 'POST' })
  await loadCommands()
  tab.value = 'console'
  scrollConsole()
  kick()
}, `Skript „${s.name}“ gestartet.`)

const patch = (action) => guard(async () => {
  if (action === 'apply' && !confirm(`${patches.value.count} Updates auf ${agent.value.hostname} installieren? Ein Neustart kann nötig sein.`)) return
  const r = await auth.api(`${base}/patches/${action}`, { method: 'POST' })
  await load()
  if (action === 'apply') {
    tab.value = 'jobs'
    flash(`Patch-Lauf #${r.id} gestartet – den Fortschritt siehst du hier unter „Aufträge“.`)
  } else {
    flash('Suche nach Updates gestartet. Das Ergebnis erscheint hier, sobald der Agent fertig ist (siehe „Aufträge“).')
  }
})

function openCheck(c = null) {
  checkForm.value = c
    ? { ...c, params: { ...c.params } }
    : { name: '', type: 'disk', params: {}, interval_sec: 300, scope_type: 'agent', scope_value: uid, severity: 'warning', enabled: true }
}

watch(() => checkForm.value?.type, t => {
  if (t === 'script' && checkForm.value && !checkForm.value.params.shell) checkForm.value.params.shell = shells.value[0].value
})

const saveCheck = () => guard(async () => {
  const { id, created_at, result, ...body } = checkForm.value
  body.params = normalizeCheckParams(body.type, body.params)
  body.scope_value = body.scope_type === 'agent' ? uid : body.scope_type === 'site' ? (agent.value.site || 'Standard') : ''
  await auth.api(id ? `/api/rmm/checks/${id}` : '/api/rmm/checks', { method: id ? 'PUT' : 'POST', body })
  checkForm.value = null
  await load()
}, 'Check gespeichert.')

const removeCheck = c => guard(async () => {
  const where = c.scope_type === 'agent' ? '' : ' Er gilt nicht nur für diesen Rechner und verschwindet überall.'
  if (!confirm(`Check „${c.name}“ löschen?${where}`)) return
  await auth.api(`/api/rmm/checks/${c.id}`, { method: 'DELETE' })
  await load()
})

const remove = () => guard(async () => {
  if (removeUninstall.value) {
    await auth.api(`${base}?uninstall=true`, { method: 'DELETE' })
    showRemove.value = false
    await load()
    return
  }
  if (!confirm(`Eintrag von ${agent.value.hostname} entfernen? Der Agent auf dem Rechner bleibt installiert.`)) return
  await auth.api(base, { method: 'DELETE' })
  router.push('/rmm')
}, removeUninstall.value ? 'Deinstallation eingereiht.' : '')

watch(tab, t => {
  router.replace({ query: { ...route.query, tab: t } })
  if (t === 'console') scrollConsole()
})

onMounted(async () => {
  await load()
  scrollConsole()
  try { scripts.value = await auth.api('/api/rmm/scripts') } catch { /* nur Komfort */ }
  timer = setTimeout(tick, 15000)
})
onUnmounted(() => { stopped = true; clearTimeout(timer) })
</script>

<style scoped>
.ad-tab-badge { margin-left: .35rem; }
.ad-delivery { font-size: .8125rem; margin: 0 0 .75rem; display: flex; align-items: center; gap: .4rem; color: var(--color-text-muted); }
.ad-delivery::before { content: ''; width: .5rem; height: .5rem; border-radius: 9999px; background: var(--color-text-muted); flex: none; }
.ad-delivery.is-live::before { background: var(--color-success); }
.ad-delivery.is-slow::before { background: var(--color-warning); }
.ad-delivery.is-off::before { background: var(--color-danger); }

.ad-console {
  background: var(--color-bg); border: 1px solid var(--color-border); border-radius: var(--radius-sm);
  padding: .75rem 1rem; max-height: 32rem; min-height: 10rem; overflow: auto;
  font-family: ui-monospace, Consolas, monospace; font-size: .8125rem; line-height: 1.55;
}
.ad-console-empty { color: var(--color-text-muted); margin: 0; }
.ad-console-entry + .ad-console-entry { margin-top: .9rem; padding-top: .9rem; border-top: 1px dashed var(--color-border); }
.ad-console-cmd { display: flex; flex-wrap: wrap; gap: .25rem .6rem; align-items: baseline; }
.ad-prompt { color: var(--color-primary); font-weight: 600; flex: none; }
.ad-console-text { color: var(--color-text-primary); white-space: pre-wrap; overflow-wrap: anywhere; flex: 1 1 20rem; }
.ad-console-meta { color: var(--color-text-muted); font-family: inherit; font-size: .75rem; }
.ad-console-wait { color: var(--color-text-muted); margin: .35rem 0 0; font-style: italic; }
.ad-console-out {
  margin: .4rem 0 0; color: var(--color-text-secondary); white-space: pre-wrap; overflow-wrap: anywhere;
  font-family: ui-monospace, Consolas, monospace; font-size: .8rem; line-height: 1.55; font-weight: 400;
}
.ad-console-out.is-bad { color: var(--color-danger); }

.ad-console-input { display: flex; gap: .5rem; align-items: flex-start; margin-top: .75rem; flex-wrap: wrap; }
.ad-shell { width: auto; flex: 0 0 auto; min-width: 9rem; }
.ad-cmd { flex: 1 1 18rem; resize: vertical; min-height: 2.4rem; }
.ad-hint { margin-top: .4rem; display: flex; flex-wrap: wrap; align-items: center; gap: .35rem; }
.ad-timeout { width: 5.5rem; padding: .15rem .4rem; height: auto; }
.ad-row-actions { justify-content: flex-end; flex-wrap: nowrap; }
.ad-foot { padding: .75rem 1rem; margin: 0; }

.ad-runlog { margin: .5rem 0 0; padding-left: 1.2rem; font-size: .85rem; display: flex; flex-direction: column; gap: .2rem; }
.ad-runlog .is-error { color: var(--color-danger); }
.ad-runlog .is-warning { color: var(--color-warning); }

.ad-job { border: 1px solid var(--color-border); border-radius: var(--radius-sm); margin-bottom: .5rem; }
.ad-job.is-open { background: var(--color-surface); }
.ad-job-head {
  display: flex; flex-wrap: wrap; align-items: center; gap: .3rem .6rem; width: 100%;
  padding: .55rem .75rem; background: none; border: 0; color: inherit; text-align: left; cursor: pointer; font-size: .875rem;
}
.ad-job-caret { color: var(--color-text-muted); width: .8rem; }
.ad-job-title { font-weight: 600; font-family: ui-monospace, Consolas, monospace; font-size: .8125rem; overflow-wrap: anywhere; }
.ad-job-kind { color: var(--color-text-secondary); font-size: .8125rem; }
.ad-job-meta { color: var(--color-text-muted); font-size: .8125rem; margin-left: auto; }
.ad-job-body { padding: 0 .75rem .75rem; }
</style>
