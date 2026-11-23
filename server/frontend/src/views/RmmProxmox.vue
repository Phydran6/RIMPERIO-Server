<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Geraete -> Proxmox VE -> Hosts & Gaeste: Hosts verbinden,
                   VMs und Container den Rechnern zuordnen, Snapshots einsehen.

     Proxmox steht unter "Geraete": ein Proxmox-Host ist ein Geraet, das man
     anbindet, patcht und per Konsole bedient (Knoten patchen -> PveUpdates.vue).
     Das Patchmanagement NUTZT die Anbindung zusaetzlich fuer Snapshots vor dem
     Patchen der Gaeste (eingestellt je Richtlinie).
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Proxmox VE</h1>
        <p class="page-subtitle">
          Proxmox-Hosts und -Cluster anbinden. RIMPERIO ordnet Rechner ihren VMs und Containern selbst zu
          (MAC-Adresse oder Name). Das Patchmanagement nutzt die Anbindung für Snapshots vor Updates —
          einzustellen in den <router-link to="/rmm/policies">Patch-Richtlinien</router-link>.
          Die Knoten selbst patchen: <router-link to="/devices/proxmox/patching">Knoten patchen &amp; Konsole</router-link>.
        </p>
      </div>
      <button v-if="auth.isAdmin" class="btn btn-primary btn-sm" @click="form = blank()"><AppIcon name="plus" :size="16" /> Host verbinden</button>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>

    <nav class="rmm-tabs">
      <button v-for="t in TABS" :key="t.key" :class="{ 'is-active': tab === t.key }" @click="tab = t.key">
        {{ t.label }} <span class="rmm-muted">{{ t.count() }}</span>
      </button>
    </nav>

    <!-- Hosts ------------------------------------------------------------- -->
    <section v-if="tab === 'hosts'">
      <div v-if="!hosts.length" class="card">
        <div class="card-body pve-empty">
          <AppIcon name="backup" :size="28" />
          <p><strong>Noch kein Proxmox-Host verbunden.</strong></p>
          <p class="rmm-muted">Mit Adresse und root-Anmeldung verbinden — RIMPERIO legt sich in Proxmox selbst einen API-Token an.
            Ohne Host laufen Patches ohne Snapshot.</p>
          <button v-if="auth.isAdmin" class="btn btn-primary btn-sm" @click="form = blank()">Host verbinden</button>
        </div>
      </div>
      <div class="pve-hosts">
        <article v-for="h in hosts" :key="h.id" class="card pve-host" :style="h.enabled ? '' : 'opacity:.6'">
          <header class="pve-host-head">
            <span class="rmm-dot" :class="h.last_error ? 'is-offline' : (h.last_ok_at ? 'is-online' : '')" />
            <strong>{{ h.name }}</strong>
            <span v-if="!h.enabled" class="badge badge-neutral">pausiert</span>
          </header>
          <dl class="pve-facts">
            <div><dt>Adresse</dt><dd class="rmm-code">{{ h.url }}</dd></div>
            <div><dt>Anmeldung</dt><dd>{{ h.auth_type === 'token' ? h.token_id : h.username }}</dd></div>
            <div><dt>Gäste</dt><dd>{{ h.guests.length }} <span class="rmm-muted">({{ countType(h, 'qemu') }} VMs, {{ countType(h, 'lxc') }} CT)</span></dd></div>
            <div><dt>Zustand</dt><dd>
              <span v-if="h.last_error" class="badge badge-danger">Fehler</span>
              <span v-else-if="h.last_ok_at" class="badge badge-success">verbunden {{ fmtAgo(h.last_ok_at) }}</span>
              <span v-else class="badge badge-neutral">ungeprüft</span>
            </dd></div>
          </dl>
          <p v-if="h.last_error" class="rmm-muted pve-error">{{ h.last_error }}</p>
          <footer v-if="auth.isAdmin" class="rmm-actions">
            <button class="btn btn-secondary btn-sm" :disabled="busy" @click="test(h)">Testen</button>
            <button class="btn btn-secondary btn-sm" :disabled="busy" @click="refresh(h)">VMs lesen</button>
            <button class="btn btn-ghost btn-sm" @click="form = { ...h, secret: '', password: '', mode: h.auth_type }">Bearbeiten</button>
            <button class="btn btn-danger btn-sm" @click="removeHost(h)">Entfernen</button>
          </footer>
        </article>
      </div>
    </section>

    <!-- VMs & Zuordnung ----------------------------------------------------- -->
    <section v-if="tab === 'guests'" class="card">
      <div class="card-header">
        <input v-model="guestFilter" class="input" style="max-width: 18rem" placeholder="Suchen (Name, VMID, Knoten)" />
        <div class="rmm-actions">
          <button v-if="auth.isAdmin && hosts.length" class="btn btn-secondary btn-sm" :disabled="busy" @click="automatch">Automatisch zuordnen</button>
          <button v-if="auth.isAdmin && autoCandidates.length" class="btn btn-primary btn-sm" :disabled="busy" @click="importAll">
            {{ autoCandidates.length }} VMs mit Gastagent einbinden
          </button>
        </div>
      </div>
      <p v-if="auth.isAdmin && importable.length" class="card-body rmm-muted" style="padding-bottom:0">
        {{ importable.length }} Gäste sind noch nicht als Rechner eingebunden. VMs mit laufendem QEMU-Gastagent bindet
        RIMPERIO direkt über Proxmox ein; Container (LXC) über einen Befehl für die Proxmox-Shell.
      </p>
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>VM / Container</th><th>Host · Knoten</th><th>Status</th><th>IP</th><th>Rechner in RIMPERIO</th><th>erkannt über</th></tr></thead>
          <tbody>
            <tr v-if="!guests.length"><td colspan="6" class="rmm-muted">Keine Gäste gelesen. Unter „Hosts“ auf „VMs lesen“ klicken.</td></tr>
            <tr v-for="g in guests" :key="g.key">
              <td><strong>{{ g.vmid }} · {{ g.name }}</strong> <span class="badge badge-neutral">{{ g.type === 'lxc' ? 'CT' : 'VM' }}</span></td>
              <td>{{ g.hostName }} · {{ g.node }}</td>
              <td><span class="badge" :class="g.status === 'running' ? 'badge-success' : 'badge-neutral'">{{ g.status }}</span></td>
              <td class="rmm-code">{{ (g.ips || []).join(', ') || '–' }}
                <div v-if="g.type === 'qemu' && g.status === 'running' && !g.qga_ok" class="rmm-muted">ohne Gastagent</div></td>
              <td>
                <select v-if="auth.isAdmin" class="select" :value="g.agent?.agent_uid || ''" @change="assign(g, $event.target.value)">
                  <option value="">— keiner —</option>
                  <option v-for="a in agents" :key="a.agent_uid" :value="a.agent_uid">{{ a.hostname }}</option>
                </select>
                <span v-else>{{ g.agent?.hostname || '–' }}</span>
                <div v-if="auth.isAdmin && !g.agent" class="mt-1">
                  <span v-if="importState[g.key]" class="rmm-muted">{{ importState[g.key] }}</span>
                  <button v-else class="btn btn-secondary btn-sm" @click="openImport(g)">Als Rechner einbinden</button>
                </div>
              </td>
              <td class="rmm-muted">{{ MATCHED[g.link?.matched_by] || '–' }}
                <div v-if="auth.isAdmin && g.status === 'running'" class="mt-1">
                  <router-link class="btn btn-ghost btn-sm" :to="consoleLink(g)" :title="g.type === 'lxc' ? 'Konsole des Containers' : 'Serielle Konsole (braucht serial0)'">Konsole</router-link>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="unlinked.length" class="card-body rmm-muted">
        Rechner ohne VM: {{ unlinked.map(a => a.hostname).join(', ') }} — physische Rechner oder Gäste auf einem nicht verbundenen Host.
      </p>
    </section>

    <!-- Snapshots ----------------------------------------------------------- -->
    <section v-if="tab === 'snapshots'" class="card">
      <div class="card-header"><h2 class="card-title">Sicherungen vor Patch-Läufen</h2>
        <router-link to="/rmm/policies" class="btn btn-ghost btn-sm">Einstellen in den Richtlinien</router-link>
      </div>
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Rechner</th><th>Sicherung</th><th>Lauf</th><th>angelegt</th><th>wird gelöscht</th><th></th></tr></thead>
          <tbody>
            <tr v-if="!snapshots.length"><td colspan="6" class="rmm-muted">Keine offenen Sicherungen.</td></tr>
            <tr v-for="s in snapshots" :key="s.run_id">
              <td>{{ s.hostname }}</td>
              <td class="rmm-code">{{ s.name || s.volid }}<div class="rmm-muted">VM {{ s.vmid }} auf {{ s.node }} · {{ s.mode === 'backup' ? 'vzdump' : 'Snapshot' }}</div></td>
              <td><router-link :to="`/rmm/patches?tab=runs&run=${s.run_id}`">#{{ s.run_id }}</router-link> <span class="badge" :class="RUN_STATUS[s.status]?.[0]">{{ RUN_STATUS[s.status]?.[1] }}</span></td>
              <td>{{ fmtTime(s.created_at) }}</td>
              <td>{{ s.keep ? 'behalten' : (s.delete_after ? fmtTime(s.delete_after) : 'nach erfolgreicher Prüfung') }}<div v-if="s.delete_error" class="rmm-muted" style="color:var(--color-danger)">{{ s.delete_error }}</div></td>
              <td class="rmm-actions" style="justify-content:flex-end">
                <template v-if="auth.isAdmin">
                  <button v-if="s.mode === 'snapshot'" class="btn btn-danger btn-sm" :disabled="busy" @click="runAction(s, 'rollback')">Zurückrollen</button>
                  <button class="btn btn-secondary btn-sm" :disabled="busy" @click="runAction(s, s.keep ? 'unkeep' : 'keep')">{{ s.keep ? 'Löschen nach Frist' : 'Behalten' }}</button>
                  <button class="btn btn-ghost btn-sm" :disabled="busy" @click="runAction(s, 'delete')">Jetzt löschen</button>
                </template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Gast als Rechner einbinden (Fenster) ---------------------------------- -->
    <div v-if="importGuest" class="modal-backdrop" @click.self="importGuest = null">
      <div class="modal max-w-3xl">
        <div class="card-header">
          <h2 class="card-title">{{ importGuest.vmid }} · {{ importGuest.name }} als Rechner einbinden</h2>
          <button class="btn-icon" aria-label="Schließen" @click="importGuest = null"><AppIcon name="close" :size="18" /></button>
        </div>
        <div class="card-body space-y-3 text-sm overflow-y-auto">
          <p class="rmm-muted">
            {{ importGuest.type === 'lxc' ? 'Container (LXC)' : 'VM' }} auf {{ importGuest.hostName }} / {{ importGuest.node }}
            · {{ importGuest.os === 'windows' ? 'Windows' : 'Linux' }}
            <template v-if="importGuest.ips?.length"> · {{ importGuest.ips.join(', ') }}</template>.
            Der Agent meldet sich mit einer automatisch erzeugten Einladung an und wird danach dieser
            {{ importGuest.type === 'lxc' ? 'CT' : 'VM' }} selbst zugeordnet.
          </p>
          <p v-if="!invite" class="rmm-muted">Einladung wird erzeugt …</p>

          <template v-if="invite && importGuest.type === 'qemu'">
            <div v-if="importGuest.qga_ok" class="pve-way">
              <strong>Automatisch über den QEMU-Gastagent</strong>
              <p class="rmm-muted">RIMPERIO führt die Installation über Proxmox direkt in der VM aus — ohne SSH, ohne Anmeldung in der VM.</p>
              <button class="btn btn-primary btn-sm" :disabled="busy || !!importState[importGuest.key]" @click="installQga(importGuest)">Jetzt installieren</button>
              <span v-if="importState[importGuest.key]" class="rmm-muted"> {{ importState[importGuest.key] }}</span>
            </div>
            <p v-else class="rmm-note is-info">
              Für die automatische Installation braucht die VM den QEMU-Gastagent (in Proxmox unter Optionen →
              „QEMU Guest Agent“ einschalten, in der VM <code>qemu-guest-agent</code> installieren). Bis dahin: Befehl unten in der VM ausführen.
            </p>
          </template>

          <template v-if="invite">
            <div v-if="importGuest.type === 'lxc'" class="pve-way">
              <strong>Automatisch per pct exec</strong>
              <p class="rmm-muted">RIMPERIO öffnet die Shell des Knotens über die Proxmox-API und führt die Installation im
                Container aus – ohne Anmeldung im Container, ohne SSH. Fehlt curl im Container, wird es nachinstalliert.
                Braucht einen Proxmox-Zugang mit Shell-Recht (root@pam).</p>
              <button class="btn btn-primary btn-sm" :disabled="busy || !!importState[importGuest.key]" @click="installPct(importGuest)">Jetzt installieren</button>
              <span v-if="importState[importGuest.key]" class="rmm-muted"> {{ importState[importGuest.key] }}</span>
              <pre v-if="pctOutput" class="rmm-pre">{{ pctOutput }}</pre>
            </div>
            <div v-if="importGuest.type === 'lxc'" class="pve-way">
              <strong>Oder von Hand in der Proxmox-Shell von {{ importGuest.node }} ausführen</strong>
              <pre class="rmm-pre cmd-pre" @click="copy('pct', pctCommand)">{{ pctCommand }}</pre>
              <button class="btn btn-primary btn-sm" @click="copy('pct', pctCommand)">{{ copied === 'pct' ? 'Kopiert ✓' : 'Kopieren' }}</button>
            </div>
            <div class="pve-way">
              <strong>Oder direkt im Gast ({{ importGuest.os === 'windows' ? 'PowerShell als Administrator' : 'als root' }})</strong>
              <pre class="rmm-pre cmd-pre" @click="copy('one', guestCommand)">{{ guestCommand }}</pre>
              <button class="btn btn-secondary btn-sm" @click="copy('one', guestCommand)">{{ copied === 'one' ? 'Kopiert ✓' : 'Kopieren' }}</button>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- Verbinden / Bearbeiten (Fenster) ------------------------------------- -->
    <div v-if="form" class="modal-backdrop" @click.self="form = null">
      <div class="modal max-w-2xl">
        <div class="card-header">
          <h2 class="card-title">{{ form.id ? form.name : 'Proxmox-Host verbinden' }}</h2>
          <button class="btn-icon" aria-label="Abbrechen" @click="form = null"><AppIcon name="close" :size="18" /></button>
        </div>
        <div class="card-body rmm-form overflow-y-auto">
          <label>Adresse<input v-model="form.url" class="input" placeholder="192.168.1.10 oder pve.firma.local" /></label>
          <label>Name (optional)<input v-model="form.name" class="input" placeholder="leer = Name des Knotens" /></label>
          <label class="is-wide">Anmeldung
            <select v-model="form.mode" class="select">
              <option value="login">Mit Proxmox-Login verbinden (empfohlen — legt API-Token selbst an)</option>
              <option value="token">API-Token von Hand</option>
              <option value="password">Benutzer + Passwort dauerhaft speichern</option>
            </select>
          </label>
          <template v-if="form.mode === 'login'">
            <label>Benutzer<input v-model="form.username" class="input" placeholder="root@pam" /></label>
            <label>Passwort<input v-model="form.password" type="password" class="input" autocomplete="off" @keydown.enter="saveHost" /></label>
          </template>
          <template v-else>
            <label v-if="form.mode === 'token'">Token-ID<input v-model="form.token_id" class="input" placeholder="root@pam!rimperio" /></label>
            <label v-else>Benutzer<input v-model="form.username" class="input" placeholder="root@pam" /></label>
            <label>{{ form.mode === 'token' ? 'Token-Geheimnis' : 'Passwort' }}
              <input v-model="form.secret" type="password" class="input" :placeholder="form.secret_set ? 'hinterlegt — leer lassen = unverändert' : ''" /></label>
          </template>
          <details class="is-wide">
            <summary class="rmm-muted" style="cursor:pointer">Erweitert</summary>
            <div class="rmm-form mt-2">
              <label>Speicher für vzdump (optional)<input v-model="form.default_storage" class="input" placeholder="local / pbs" /></label>
              <label class="pol-check"><input v-model="form.verify_tls" type="checkbox" /> Zertifikat prüfen</label>
              <label class="pol-check"><input v-model="form.enabled" type="checkbox" /> aktiv</label>
            </div>
          </details>
          <p v-if="form.mode === 'login'" class="is-wide rmm-muted">
            Einmal mit <span class="rmm-code">root@pam</span> anmelden — RIMPERIO legt in Proxmox den API-Token
            <span class="rmm-code">root@pam!rimperio</span> an und speichert nur diesen, nicht das Passwort.
            Ohne Port gilt 8006. Bei aktiver Zwei-Faktor-Anmeldung „API-Token von Hand“ nehmen.
          </p>
          <p v-else-if="form.mode === 'token'" class="is-wide rmm-muted">
            Token anlegen: Rechenzentrum → Berechtigungen → API-Token. Rechte (Rolle am Pfad <span class="rmm-code">/vms</span>):
            VM.Snapshot, VM.Snapshot.Rollback, VM.Audit, VM.PowerMgmt; für vzdump zusätzlich VM.Backup und
            Datastore.AllocateSpace am Speicher.
          </p>
          <p v-else class="is-wide rmm-muted">Das Passwort bleibt gespeichert; Zwei-Faktor darf nicht aktiv sein.</p>
          <p v-if="formError" class="is-wide rmm-note is-error">{{ formError }}</p>
          <div class="is-wide rmm-actions">
            <button class="btn btn-primary btn-sm" :disabled="busy || !form.url" @click="saveHost">{{ busy ? 'Verbinde…' : (form.mode === 'login' ? 'Verbinden' : 'Speichern') }}</button>
            <button class="btn btn-ghost btn-sm" @click="form = null">Abbrechen</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import { fmtAgo, fmtTime, RUN_STATUS } from '../utils/rmm'

const auth = useAuthStore()
const route = useRoute()
const hosts = ref([])
const agents = ref([])
const links = ref({})
const snapshots = ref([])
const form = ref(null)
const formError = ref('')
const error = ref('')
const notice = ref('')
const busy = ref(false)
const guestFilter = ref('')
const tab = ref(route.query.tab || 'hosts')
const MATCHED = { mac: 'MAC-Adresse', name: 'Name', manual: 'von Hand' }

const guests = computed(() => {
  const byGuest = {}
  for (const l of Object.values(links.value)) byGuest[`${l.host_id}|${l.node}|${l.vmid}`] = l
  const f = guestFilter.value.trim().toLowerCase()
  const out = []
  for (const h of hosts.value) {
    for (const g of h.guests) {
      const link = byGuest[`${h.id}|${g.node}|${g.vmid}`]
      const row = { ...g, key: `${h.id}:${g.node}:${g.vmid}`, hostId: h.id, hostName: h.name, link,
        agent: link ? agents.value.find(a => a.agent_uid === link.agent_uid) : null }
      if (!f || [g.name, String(g.vmid), g.node, h.name, row.agent?.hostname].some(v => (v || '').toLowerCase().includes(f))) out.push(row)
    }
  }
  return out.sort((a, b) => a.vmid - b.vmid)
})
const unlinked = computed(() => agents.value.filter(a => !links.value[a.agent_uid]))

// ---------------------------------------------------------------- Einbinden
const RAW = 'https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/agent/installer'
const SERVER = window.location.origin
const invite = ref('')
const importGuest = ref(null)
const importState = ref({})
const copied = ref('')
const importable = computed(() => guests.value.filter(g => !g.agent && g.status === 'running' && g.os !== 'other'))
const autoCandidates = computed(() => importable.value.filter(g => g.type === 'qemu' && g.qga_ok && !importState.value[g.key]))

function linuxCommand() {
  const url = RAW + '/linux/install-agent.sh'
  return '(curl -fsSL ' + url + ' || wget -qO- ' + url + ') | bash -s -- --server ' + SERVER + ' --secret ' + invite.value + " --site 'Standard' --user rimperio"
}
const guestCommand = computed(() => {
  if (!importGuest.value) return ''
  if (importGuest.value.os === 'windows') {
    return '& ([scriptblock]::Create((irm ' + RAW + '/windows/install-agent.ps1))) -Server ' + SERVER + ' -Secret ' + invite.value + " -Site 'Standard'"
  }
  return linuxCommand()
})
const pctCommand = computed(() => importGuest.value ? 'pct exec ' + importGuest.value.vmid + ' -- bash -c "' + linuxCommand() + '"' : '')

async function ensureInvite() {
  if (invite.value) return invite.value
  const stamp = new Date().toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })
  const data = await auth.api('/api/agent-tokens', { method: 'POST', body: {
    name: 'Proxmox-Import ' + stamp, kind: 'enroll', expires_in_hours: 7 * 24, max_uses: 200,
    note: 'Automatisch beim Einbinden von Proxmox-Gästen erzeugt.' } })
  invite.value = data.token
  return invite.value
}

async function openImport(g) {
  importGuest.value = g
  try { await ensureInvite() } catch (e) { error.value = 'Einladung konnte nicht erzeugt werden: ' + e.message }
}

function consoleLink(g) {
  return { path: '/terminal', query: { pve: g.hostId, node: g.node, kind: g.type, vmid: g.vmid, name: g.name } }
}

const pctOutput = ref('')
async function installPct(g) {
  const key = g.key
  importState.value = { ...importState.value, [key]: 'läuft (bis zu einigen Minuten) …' }
  pctOutput.value = ''
  try {
    await ensureInvite()
    const r = await auth.api('/api/pve/hosts/' + g.hostId + '/lxc/' + g.vmid + '/install-agent', { method: 'POST', body: {
      node: g.node, secret: invite.value, site: 'Standard' } })
    pctOutput.value = (r.output || '').split('\n').slice(-25).join('\n')
    importState.value = { ...importState.value, [key]: r.ok ? 'installiert ✓ – der Rechner erscheint gleich' : 'fehlgeschlagen (Ausgabe unten)' }
    if (r.ok) setTimeout(load, 8000)
  } catch (e) {
    importState.value = { ...importState.value, [key]: 'Fehler: ' + e.message }
  }
}

async function installQga(g) {
  const key = g.key
  importState.value = { ...importState.value, [key]: 'startet …' }
  try {
    await ensureInvite()
    const r = await auth.api('/api/patching/pve/hosts/' + g.hostId + '/guests/install', { method: 'POST', body: {
      node: g.node, vmid: Number(g.vmid), os: g.os === 'windows' ? 'windows' : 'linux', secret: invite.value } })
    importState.value = { ...importState.value, [key]: 'installiert …' }
    watchExec(g, r.pid, 0)
  } catch (e) {
    importState.value = { ...importState.value, [key]: 'Fehler: ' + e.message }
  }
}

function watchExec(g, pid, round) {
  if (!pid || round > 45) {
    importState.value = { ...importState.value, [g.key]: 'gestartet — der Rechner erscheint in Kürze' }
    return
  }
  setTimeout(async () => {
    try {
      const st = await auth.api('/api/patching/pve/hosts/' + g.hostId + '/guests/exec-status?node=' + encodeURIComponent(g.node) + '&vmid=' + g.vmid + '&pid=' + pid)
      if (st.exited) {
        importState.value = { ...importState.value, [g.key]: st.exitcode === 0 ? 'installiert ✓ — wird zugeordnet' : 'Fehler (Code ' + st.exitcode + '): ' + (st.err || st.out || '').slice(-200) }
        if (st.exitcode === 0) setTimeout(async () => { await auth.api('/api/patching/pve/automatch', { method: 'POST' }).catch(() => {}); await load() }, 15000)
        return
      }
    } catch { /* weiter fragen */ }
    watchExec(g, pid, round + 1)
  }, 4000)
}

async function importAll() {
  const list = [...autoCandidates.value]
  if (!confirm(list.length + ' VMs mit QEMU-Gastagent als Rechner einbinden? Der RIMPERIO-Agent wird darin installiert.')) return
  for (const g of list) await installQga(g)
}

async function copy(id, text) {
  try { await navigator.clipboard.writeText(text) } catch {
    const area = document.createElement('textarea')
    area.value = text; area.style.position = 'fixed'; area.style.opacity = '0'
    document.body.appendChild(area); area.select()
    try { document.execCommand('copy') } catch { /* markieren geht immer */ }
    area.remove()
  }
  copied.value = id
  setTimeout(() => { if (copied.value === id) copied.value = '' }, 2000)
}

const TABS = [
  { key: 'hosts', label: 'Hosts', count: () => hosts.value.length },
  { key: 'guests', label: 'VMs & Zuordnung', count: () => hosts.value.reduce((n, h) => n + h.guests.length, 0) },
  { key: 'snapshots', label: 'Snapshots', count: () => snapshots.value.length },
]

const blank = () => ({ name: '', url: '', mode: 'login', token_id: '', username: 'root@pam', password: '', secret: '', verify_tls: false, enabled: true, default_storage: '' })
const countType = (h, type) => h.guests.filter(g => (g.type || 'qemu') === type).length

async function guard(fn) {
  busy.value = true
  try { await fn(); error.value = '' } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function load() {
  await guard(async () => {
    const [h, a, l, s] = await Promise.all([auth.api('/api/patching/pve/hosts'), auth.api('/api/rmm/agents'),
      auth.api('/api/patching/pve/links'), auth.api('/api/patching/pve/snapshots')])
    hosts.value = h; agents.value = a; snapshots.value = s
    links.value = Object.fromEntries(l.map(x => [x.agent_uid, x]))
  })
}

async function saveHost() {
  formError.value = ''
  busy.value = true
  try {
    const f = form.value
    if (f.mode === 'login') {
      const r = await auth.api('/api/patching/pve/hosts/connect', { method: 'POST', body: {
        host_id: f.id ?? null, name: f.name, url: f.url, username: f.username || 'root@pam', password: f.password,
        verify_tls: f.verify_tls, enabled: f.enabled, default_storage: f.default_storage || '' } })
      notice.value = r.message
      form.value = null
      await load()
      if (importable.value.length) {
        notice.value = r.message + ' ' + importable.value.length + ' laufende Gäste sind noch keine Rechner — unten „Als Rechner einbinden“.'
        tab.value = 'guests'
      }
      return
    } else {
      const { id, guests: _g, guests_at, last_ok_at, last_error, secret_set, mode, password, ...body } = f
      body.auth_type = mode
      body.name = (body.name || '').trim() || body.url
      await auth.api(id ? `/api/patching/pve/hosts/${id}` : '/api/patching/pve/hosts', { method: id ? 'PUT' : 'POST', body })
      notice.value = 'Gespeichert.'
    }
    form.value = null
    await load()
  } catch (e) {
    formError.value = e.message
  } finally {
    busy.value = false
  }
}
async function test(h) {
  await guard(async () => { const r = await auth.api(`/api/patching/pve/hosts/${h.id}/test`, { method: 'POST' }); if (r.ok) notice.value = r.message; else error.value = r.message; await load() })
}
async function refresh(h) {
  await guard(async () => { const r = await auth.api(`/api/patching/pve/hosts/${h.id}/refresh`, { method: 'POST' }); notice.value = `${r.guests.length} Gäste gelesen, ${r.matched} neu zugeordnet.`; await load() })
}
async function removeHost(h) {
  if (!confirm(`Proxmox-Host „${h.name}“ entfernen? Zuordnungen gehen verloren, Snapshots bleiben auf dem Host.`)) return
  await guard(async () => { await auth.api(`/api/patching/pve/hosts/${h.id}`, { method: 'DELETE' }); await load() })
}
async function automatch() {
  await guard(async () => { const r = await auth.api('/api/patching/pve/automatch', { method: 'POST' }); notice.value = `${r.matched} Rechner neu zugeordnet.`; await load() })
}
// Zuordnung von der VM aus: welcher Rechner läuft in diesem Gast?
async function assign(g, agentUid) {
  await guard(async () => {
    if (g.agent && g.agent.agent_uid !== agentUid) await auth.api(`/api/patching/pve/links/${g.agent.agent_uid}`, { method: 'DELETE' })
    if (agentUid) {
      await auth.api(`/api/patching/pve/links/${agentUid}`, { method: 'PUT', body: {
        host_id: g.hostId, node: g.node, vmid: Number(g.vmid), guest_type: g.type, guest_name: g.name } })
    }
    await load()
  })
}
async function runAction(s, what) {
  if (what === 'rollback' && !confirm(`VM ${s.vmid} auf ${s.name} zurückrollen? Alles seit dem Snapshot geht verloren.`)) return
  if (what === 'delete' && !confirm('Sicherung jetzt löschen?')) return
  await guard(async () => {
    const path = { rollback: 'rollback', delete: 'snapshot/delete', keep: 'snapshot/keep?keep=true', unkeep: 'snapshot/keep?keep=false' }[what]
    await auth.api(`/api/patching/runs/${s.run_id}/${path}`, { method: 'POST' })
    notice.value = 'Erledigt.'; await load()
  })
}

function onKey(e) {
  if (e.key !== 'Escape') return
  if (importGuest.value) importGuest.value = null
  else if (form.value) form.value = null
}
onMounted(() => { window.addEventListener('keydown', onKey); load() })
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<style scoped>
.pol-check { flex-direction: row !important; align-items: center; gap: .5rem !important; }
.pve-empty { display: flex; flex-direction: column; align-items: center; gap: .5rem; text-align: center; padding: 2rem 1rem; color: var(--color-text-secondary); }
.pve-hosts { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fill, minmax(min(100%, 22rem), 1fr)); }
.pve-host { padding: 1rem; display: flex; flex-direction: column; gap: .75rem; }
.pve-host-head { display: flex; align-items: center; gap: .5rem; font-size: 1rem; }
.pve-facts { display: grid; grid-template-columns: 1fr 1fr; gap: .5rem 1rem; margin: 0; font-size: .875rem; }
.pve-facts dt { color: var(--color-text-muted); font-size: .72rem; text-transform: uppercase; letter-spacing: .03em; }
.pve-facts dd { margin: 0; overflow-wrap: anywhere; }
.pve-error { color: var(--color-danger); }
.pve-way { display: flex; flex-direction: column; gap: .4rem; align-items: flex-start; padding: .75rem; border: 1px solid var(--color-border); border-radius: var(--radius); }
.pve-way pre { width: 100%; }
.cmd-pre { cursor: copy; }
</style>
