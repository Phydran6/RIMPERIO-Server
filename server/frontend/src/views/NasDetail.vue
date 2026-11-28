<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Ein NAS im Detail: Gesamtzustand, Speicher, Platten,
                   Updates (DSM und Pakete) samt Automatik, Hyper Backup, SNMP,
                   Einrichtungs-Pruefung und Vorgaenge.
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <router-link to="/devices/nas" class="rmm-muted">← NAS</router-link>
        <h1 class="page-title">
          <span class="rmm-dot" :class="n && n.status === 'ok' ? 'is-online' : (n && ['down', 'critical'].includes(n.status) ? 'is-offline' : '')" />
          {{ n?.name || 'NAS' }}
          <span v-if="n" class="badge" :class="STATUS[n.status]?.[0]">{{ STATUS[n.status]?.[1] || n.status }}</span>
        </h1>
        <p v-if="n" class="page-subtitle">{{ n.model || n.vendor_label }} · {{ n.firmware || '–' }} ·
          <span v-if="n.via_agent_id" class="rmm-code">{{ n.url }}</span>
          <a v-else :href="n.url" target="_blank" rel="noopener" class="rmm-code">{{ n.url }}</a>
          <template v-if="n.via_agent"> · über Agent <router-link :to="`/rmm/agents/${n.via_agent.agent_uid}`">{{ n.via_agent.hostname }}</router-link></template>
          · gelesen {{ fmtAgo(n.last_ok_at) }}</p>
      </div>
      <div v-if="n && auth.isAdmin" class="rmm-actions">
        <button class="btn btn-secondary btn-sm" :disabled="busy" @click="refresh(false)"><AppIcon name="refresh" :size="15" /> Neu lesen</button>
        <button class="btn btn-secondary btn-sm" :disabled="busy" @click="refresh(true)">Updates prüfen</button>
        <button class="btn btn-ghost btn-sm" @click="openEdit">Einstellungen</button>
      </div>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>
    <p v-if="n?.last_error" class="rmm-note is-error">{{ n.last_error }}
      <button v-if="auth.isAdmin" class="btn btn-secondary btn-sm" style="margin-left:.5rem" @click="relogin = { otp: '', error: '' }">Neu anmelden</button></p>

    <template v-if="n">
      <section v-if="n.problems.length" class="card">
        <div class="card-header"><h2 class="card-title">Probleme</h2></div>
        <ul class="card-body nd-problems">
          <li v-for="p in n.problems" :key="p.what"><span class="badge" :class="p.level === 'critical' ? 'badge-danger' : 'badge-warning'">{{ p.level === 'critical' ? 'kritisch' : 'Warnung' }}</span> {{ p.text }}</li>
        </ul>
      </section>

      <div class="nd-grid">
        <!-- System -->
        <section class="card">
          <div class="card-header"><h2 class="card-title">System</h2></div>
          <dl class="card-body nd-facts">
            <div><dt>Modell</dt><dd>{{ sys.model || '–' }}</dd></div>
            <div><dt>Seriennummer</dt><dd class="rmm-code">{{ sys.serial || '–' }}</dd></div>
            <div><dt>Firmware</dt><dd>{{ sys.firmware || '–' }}</dd></div>
            <div><dt>Laufzeit</dt><dd>{{ sys.uptime || '–' }}</dd></div>
            <div><dt>Temperatur</dt><dd :class="sys.temperature_warning ? 'is-bad' : ''">{{ sys.temperature != null ? sys.temperature + ' °C' : '–' }}</dd></div>
            <div><dt>CPU</dt><dd>{{ load.cpu != null ? Math.round(load.cpu) + ' %' : '–' }} <span class="rmm-muted">{{ sys.cpu }}</span></dd></div>
            <div><dt>RAM</dt><dd>{{ load.mem != null ? Math.round(load.mem) + ' %' : '–' }} <span class="rmm-muted">{{ sys.ram_mb ? Math.round(sys.ram_mb / 1024) + ' GB' : '' }}</span></dd></div>
            <div><dt>Zertifikat</dt><dd>{{ cert ? (cert.self_signed ? 'selbst signiert' : 'noch ' + cert.days_left + ' Tage') : (n.scheme === 'https' ? '–' : 'nur HTTP') }}</dd></div>
            <div><dt>SNMP</dt><dd>
              <router-link v-if="n.snmp_device_id" :to="`/rmm/snmp/${n.snmp_device_id}`">überwacht</router-link>
              <span v-else class="rmm-muted">{{ st.snmp_enabled ? 'an, nicht überwacht' : 'aus' }}</span>
              <button v-if="auth.isAdmin && n.vendor === 'synology'" class="btn btn-ghost btn-sm" :disabled="busy" @click="enableSnmp">{{ n.snmp_device_id ? 'neu einrichten' : 'einschalten' }}</button>
            </dd></div>
            <div><dt>Zugang</dt><dd>{{ d.secret ? (d.secret.backend === 'vaultwarden' ? 'Vaultwarden' : 'lokal verschlüsselt') : '–' }}
              <span class="rmm-muted">{{ d.secret?.username }}</span></dd></div>
            <div><dt>Weg</dt><dd>
              <template v-if="n.via_agent">
                <span class="rmm-dot" :class="n.via_agent.ready ? 'is-online' : 'is-offline'" />
                über {{ n.via_agent.on_nas ? 'den Agent auf dem NAS' : 'den Agent ' + n.via_agent.hostname }}
                <div class="rmm-muted">{{ n.via_agent.site }} · {{ n.via_agent.ready ? 'verbunden' : (n.via_agent.live ? 'Agent zu alt (ab 0.4.0)' : 'Agent nicht verbunden') }}<template
                  v-if="n.via_agent.last_ms != null"> · zuletzt {{ n.via_agent.last_ms }} ms</template></div>
              </template>
              <span v-else-if="n.via_agent_id" class="rmm-muted">über einen Agent (entfernt?)</span>
              <span v-else>direkt vom Server</span>
            </dd></div>
          </dl>
        </section>

        <!-- Speicher -->
        <section class="card">
          <div class="card-header"><h2 class="card-title">Speicher</h2></div>
          <div class="card-body space-y-2">
            <div v-for="v in st.volumes || []" :key="v.id" class="nd-vol">
              <div><strong>{{ v.name }}</strong> <span class="rmm-muted">{{ v.fs }} · {{ v.status }}</span></div>
              <div class="nd-bar"><div :class="v.percent >= 90 ? 'is-bad' : v.percent >= 80 ? 'is-warn' : ''" :style="{ width: (v.percent || 0) + '%' }" /></div>
              <div class="rmm-muted">{{ fmtBytes(v.used) }} von {{ fmtBytes(v.total) }} ({{ v.percent != null ? Math.round(v.percent) + ' %' : '–' }})</div>
            </div>
            <div v-for="p in st.pools || []" :key="p.id" class="rmm-muted">Pool {{ p.name }}: {{ p.status }} · {{ p.raid }}</div>
            <p v-if="!(st.volumes || []).length" class="rmm-muted">Keine Volumes gelesen.</p>
          </div>
        </section>
      </div>

      <!-- Platten -->
      <section class="card">
        <div class="card-header"><h2 class="card-title">Platten</h2></div>
        <div class="rmm-table-wrap">
          <table class="table">
            <thead><tr><th>Platte</th><th>Modell</th><th>Größe</th><th>Zustand</th><th>S.M.A.R.T.</th><th>Temperatur</th></tr></thead>
            <tbody>
              <tr v-if="!(st.disks || []).length"><td colspan="6" class="rmm-muted">Keine Platten gelesen.</td></tr>
              <tr v-for="x in st.disks || []" :key="x.id">
                <td>{{ x.name }}</td><td>{{ x.vendor }} {{ x.model }}<div class="rmm-muted rmm-code">{{ x.serial }}</div></td>
                <td>{{ fmtBytes(x.size) }}</td>
                <td><span class="badge" :class="['normal', 'initialized'].includes(String(x.status).toLowerCase()) ? 'badge-success' : 'badge-warning'">{{ x.status || '–' }}</span></td>
                <td>{{ x.smart || '–' }}</td><td>{{ x.temp != null ? x.temp + ' °C' : '–' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Updates -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">Updates <span class="rmm-muted">geprüft {{ fmtAgo(upd.checked_at) }}</span></h2>
          <span v-if="d.busy" class="badge badge-primary">Vorgang läuft …</span>
        </div>
        <div class="card-body space-y-3">
          <p v-for="(e, k) in upd.errors || {}" :key="k" class="rmm-note is-error">{{ k }}: {{ e }}</p>
          <div class="nd-way">
            <div>
              <strong>{{ n.vendor === 'synology' ? 'DSM' : 'Firmware' }}</strong>
              <span v-if="upd.dsm?.available"> → <span class="badge badge-warning">{{ upd.dsm.version }}</span>
                <span class="rmm-muted"> {{ upd.dsm.reboot ? 'mit Neustart' : '' }} {{ upd.dsm.major ? '· Hauptversion' : '' }}</span></span>
              <span v-else class="rmm-muted"> aktuell ({{ n.firmware || '–' }})</span>
            </div>
            <button v-if="auth.isAdmin && upd.dsm?.available" class="btn btn-danger btn-sm" :disabled="busy || d.busy" @click="runDsm">Jetzt installieren (Neustart)</button>
          </div>
          <div class="rmm-table-wrap" v-if="(upd.packages || []).length">
            <table class="table">
              <thead><tr><th style="width:2rem"><input type="checkbox" :checked="selected.length === upd.packages.length" @change="selected = $event.target.checked ? upd.packages.map(p => p.id) : []" /></th>
                <th>Paket</th><th>installiert</th><th>neu</th><th></th></tr></thead>
              <tbody>
                <tr v-for="p in upd.packages" :key="p.id">
                  <td><input v-model="selected" type="checkbox" :value="p.id" /></td>
                  <td><strong>{{ p.name }}</strong> <span class="rmm-muted rmm-code">{{ p.id }}</span>
                    <span v-if="auto.exclude?.includes(p.id)" class="badge badge-neutral">nie automatisch</span></td>
                  <td class="rmm-code">{{ p.installed }}</td><td class="rmm-code">{{ p.available }}</td>
                  <td><details v-if="p.changelog"><summary class="rmm-muted" style="cursor:pointer">Änderungen</summary><div class="nd-changelog" v-text="stripHtml(p.changelog)" /></details></td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="rmm-muted">Keine Paket-Updates.</p>
          <div v-if="auth.isAdmin && (upd.packages || []).length" class="rmm-actions">
            <button class="btn btn-primary btn-sm" :disabled="busy || d.busy || !selected.length" @click="runPackages">{{ selected.length }} Paket(e) aktualisieren</button>
          </div>
        </div>
      </section>

      <!-- Automatik -->
      <section class="card">
        <div class="card-header"><h2 class="card-title">Automatik & Wartungsfenster</h2></div>
        <div class="card-body rmm-form">
          <label>DSM-Updates
            <select v-model="autoForm.dsm" class="select" :disabled="!auth.isAdmin">
              <option value="off">aus</option><option value="notify">nur melden</option><option value="auto">im Fenster installieren</option>
            </select></label>
          <label>Paket-Updates
            <select v-model="autoForm.packages" class="select" :disabled="!auth.isAdmin">
              <option value="off">aus</option><option value="notify">nur melden</option><option value="auto">im Fenster installieren</option>
            </select></label>
          <div class="is-wide nd-days">
            <span class="rmm-muted">Tage:</span>
            <label v-for="(dn, i) in DAYS" :key="i" class="pol-check"><input v-model="autoForm.days" type="checkbox" :value="i" :disabled="!auth.isAdmin" /> {{ dn }}</label>
          </div>
          <label>Beginn<input v-model="autoForm.start" type="time" class="input" :disabled="!auth.isAdmin" /></label>
          <label>Dauer (Minuten)<input v-model.number="autoForm.duration_min" type="number" min="30" class="input" :disabled="!auth.isAdmin" /></label>
          <label class="pol-check"><input v-model="autoForm.dsm_major" type="checkbox" :disabled="!auth.isAdmin" /> auch Hauptversionen automatisch (nicht empfohlen)</label>
          <label class="pol-check"><input v-model="autoForm.backup_config_first" type="checkbox" :disabled="!auth.isAdmin" /> vorher Konfiguration sichern (Backup-Auftrag „NAS-Konfiguration“)</label>
          <label class="is-wide">Pakete nie automatisch (IDs, Komma)<input v-model="excludeText" class="input" placeholder="z. B. Docker, SynologyDrive" :disabled="!auth.isAdmin" /></label>
          <p class="is-wide rmm-muted">Reihenfolge im Fenster: zuerst Pakete, an einem späteren Fenster-Tag DSM. Nach einem DSM-Update wartet RIMPERIO
            den Neustart ab und prüft die Version. Ergebnis als Meldung (auch in der App).</p>
          <div v-if="auth.isAdmin" class="is-wide rmm-actions"><button class="btn btn-primary btn-sm" :disabled="busy" @click="saveAuto">Speichern</button></div>
        </div>
      </section>

      <!-- Hyper Backup -->
      <section v-if="n.vendor === 'synology'" class="card">
        <div class="card-header"><h2 class="card-title">Hyper Backup auf dem NAS</h2>
          <router-link :to="{ path: '/backups/jobs', query: { new: 'nas_config', nas: n.id } }" class="btn btn-ghost btn-sm">Konfiguration sichern lassen</router-link></div>
        <div class="rmm-table-wrap">
          <table class="table">
            <thead><tr><th>Aufgabe</th><th>Zustand</th><th>Letzter Lauf</th><th>Nächster</th><th></th></tr></thead>
            <tbody>
              <tr v-if="!(st.backup_tasks || []).length"><td colspan="5" class="rmm-muted">Keine Hyper-Backup-Aufgaben (oder Paket nicht installiert).</td></tr>
              <tr v-for="t in st.backup_tasks || []" :key="t.id">
                <td><strong>{{ t.name }}</strong> <span class="rmm-muted">#{{ t.id }}</span></td>
                <td>{{ t.state || t.status || '–' }}</td>
                <td>{{ t.last_time || '–' }} <span class="badge" :class="['done', 'success'].includes(String(t.last_result).toLowerCase()) ? 'badge-success' : 'badge-warning'">{{ t.last_result || '–' }}</span></td>
                <td>{{ t.next_time || '–' }}</td>
                <td class="rmm-actions" style="justify-content:flex-end">
                  <template v-if="auth.isAdmin">
                    <button class="btn btn-secondary btn-sm" :disabled="busy" @click="runTask(t)">Jetzt sichern</button>
                    <router-link class="btn btn-ghost btn-sm" :to="{ path: '/backups/jobs', query: { new: 'nas_task', nas: n.id, task: t.id, task_name: t.name } }">Als Auftrag überwachen</router-link>
                  </template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Pakete -->
      <section v-if="(st.packages || []).length" class="card">
        <div class="card-header"><h2 class="card-title">Installierte Pakete <span class="rmm-muted">{{ st.packages.length }}</span></h2></div>
        <div class="card-body nd-pkgs">
          <span v-for="p in st.packages" :key="p.id" class="badge" :class="p.status === 'running' ? 'badge-success' : 'badge-neutral'" :title="p.id">{{ p.name }} {{ p.version }}</span>
        </div>
      </section>

      <!-- Pruefung -->
      <section v-if="d.checks?.length" class="card">
        <div class="card-header"><h2 class="card-title">Prüfung beim Einrichten</h2></div>
        <ul class="card-body nd-checks">
          <li v-for="c in d.checks" :key="c.key"><span class="badge" :class="CHECK[c.status]">{{ c.status }}</span> <strong>{{ c.label }}</strong> – <span class="rmm-muted">{{ c.detail }}</span></li>
        </ul>
      </section>

      <!-- Vorgaenge -->
      <section class="card">
        <div class="card-header"><h2 class="card-title">Vorgänge</h2></div>
        <div class="rmm-table-wrap">
          <table class="table">
            <thead><tr><th>Wann</th><th>Was</th><th>Status</th><th>Protokoll</th></tr></thead>
            <tbody>
              <tr v-if="!(d.jobs || []).length"><td colspan="4" class="rmm-muted">Noch keine.</td></tr>
              <tr v-for="j in d.jobs" :key="j.id">
                <td>{{ fmtTime(j.started_at) }}<div class="rmm-muted">{{ j.created_by }} · {{ j.trigger === 'schedule' ? 'Zeitplan' : 'von Hand' }}</div></td>
                <td>{{ JOB[j.kind] || j.kind }}</td>
                <td><span class="badge" :class="j.status === 'done' ? 'badge-success' : j.status === 'error' ? 'badge-danger' : 'badge-primary'">{{ j.status === 'done' ? 'fertig' : j.status === 'error' ? 'Fehler' : 'läuft' }}</span>
                  <div v-if="j.error" class="rmm-muted" style="color:var(--color-danger)">{{ j.error }}</div></td>
                <td><details><summary class="rmm-muted" style="cursor:pointer">{{ (j.log || []).length }} Zeilen</summary>
                  <pre class="rmm-pre">{{ (j.log || []).map(l => l.at.slice(11, 19) + '  ' + l.text).join('\n') }}</pre></details></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>

    <!-- Einstellungen -->
    <div v-if="edit" class="modal-backdrop" @click.self="edit = null">
      <div class="modal max-w-2xl">
        <div class="card-header"><h2 class="card-title">Einstellungen</h2>
          <button class="btn-icon" aria-label="Schließen" @click="edit = null"><AppIcon name="close" :size="18" /></button></div>
        <div class="card-body rmm-form">
          <label>Name<input v-model="edit.name" class="input" /></label>
          <label>Standort<input v-model="edit.site" class="input" /></label>
          <label>Abfrage alle (Sekunden)<input v-model.number="edit.poll_interval_sec" type="number" min="60" class="input" /></label>
          <label class="is-wide">Weg zum NAS
            <select v-model="edit.via_agent_id" class="select">
              <option :value="null">direkt vom RIMPERIO-Server</option>
              <option v-for="a in relayAgents" :key="a.id" :value="a.id">über Agent {{ a.hostname }} · {{ a.site }}{{ a.on_nas ? ' · läuft auf dem NAS' : '' }}{{ a.ready ? '' : ' · gerade nicht verbunden' }}</option>
            </select>
            <span class="rmm-muted">Für ein NAS an einem anderen Standort: Ein Agent dort fragt es ab, nur ausgehend per HTTPS. Die Adresse
              ({{ n.host }}:{{ n.port }}) gilt dann im Netz des Agents.</span></label>
          <label class="pol-check"><input v-model="edit.verify_tls" type="checkbox" /> Zertifikat prüfen</label>
          <label class="pol-check"><input v-model="edit.enabled" type="checkbox" /> aktiv</label>
          <div class="is-wide rmm-actions">
            <button class="btn btn-primary btn-sm" :disabled="busy" @click="saveEdit">Speichern</button>
            <button class="btn btn-secondary btn-sm" @click="edit = null; relogin = { otp: '', error: '' }">Neu anmelden …</button>
            <button class="btn btn-danger btn-sm" @click="remove">Entfernen</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Neu anmelden -->
    <div v-if="relogin" class="modal-backdrop" @click.self="relogin = null">
      <div class="modal max-w-2xl">
        <div class="card-header"><h2 class="card-title">Neu anmelden</h2>
          <button class="btn-icon" aria-label="Schließen" @click="relogin = null"><AppIcon name="close" :size="18" /></button></div>
        <div class="card-body rmm-form">
          <p class="is-wide rmm-muted">Mit dem hinterlegten Benutzer und Passwort. Hat DSM das vertraute Gerät verworfen, den aktuellen Zwei-Faktor-Code eingeben.
            Anderes Passwort? Unten eintragen (landet in Vaultwarden bzw. lokal).</p>
          <label>Zwei-Faktor-Code<input v-model="relogin.otp" class="input" inputmode="numeric" maxlength="8" /></label>
          <label>Neues Passwort (optional)<input v-model="relogin.password" type="password" class="input" autocomplete="off" /></label>
          <label>Benutzer (optional)<input v-model="relogin.username" class="input" autocomplete="off" /></label>
          <p v-if="relogin.error" class="is-wide rmm-note is-error">{{ relogin.error }}</p>
          <div class="is-wide rmm-actions"><button class="btn btn-primary btn-sm" :disabled="busy" @click="doRelogin">Anmelden und prüfen</button></div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import { fmtAgo, fmtTime } from '../utils/rmm'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const d = ref({})
const error = ref('')
const notice = ref('')
const busy = ref(false)
const selected = ref([])
const edit = ref(null)
const relogin = ref(null)
const autoForm = ref({})
const relayAgents = ref([])
const excludeText = ref('')
let timer = null

const STATUS = {
  ok: ['badge-success', 'in Ordnung'], warning: ['badge-warning', 'Warnung'], critical: ['badge-danger', 'kritisch'],
  down: ['badge-danger', 'nicht erreichbar'], unknown: ['badge-neutral', 'ungeprüft'],
}
const CHECK = { ok: 'badge-success', warn: 'badge-warning', fail: 'badge-danger', info: 'badge-primary' }
const JOB = { dsm_update: 'DSM-Update', package_update: 'Paket-Updates', snmp_enable: 'SNMP einschalten', config_backup: 'Konfiguration sichern' }
const DAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']

const n = computed(() => d.value?.id ? d.value : null)
const st = computed(() => d.value?.state || {})
const sys = computed(() => st.value.system || {})
const load = computed(() => st.value.load || {})
const cert = computed(() => st.value.certificate || null)
const upd = computed(() => d.value?.updates || {})
const auto = computed(() => d.value?.automation || {})

function fmtBytes(v) {
  if (v == null || isNaN(v)) return '–'
  const u = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let i = 0; let x = Number(v)
  while (x >= 1024 && i < u.length - 1) { x /= 1024; i++ }
  return (i >= 3 ? x.toFixed(1) : Math.round(x)) + ' ' + u[i]
}
const stripHtml = s => String(s || '').replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]+>/g, '').trim()

async function loadData() {
  try {
    d.value = await auth.api(`/api/nas/${route.params.id}`)
    autoForm.value = { ...d.value.automation, days: [...(d.value.automation?.days || [])] }
    excludeText.value = (d.value.automation?.exclude || []).join(', ')
    error.value = ''
    clearTimeout(timer)
    if (d.value.busy || (d.value.jobs || []).some(j => j.status === 'running')) timer = setTimeout(loadData, 5000)
  } catch (e) { error.value = e.message }
}
async function guard(fn) {
  busy.value = true
  try { await fn(); error.value = '' } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function refresh(updates) {
  await guard(async () => { await auth.api(`/api/nas/${n.value.id}/refresh${updates ? '?updates=true' : ''}`, { method: 'POST' }); notice.value = 'Zustand gelesen.'; await loadData() })
}
async function enableSnmp() {
  await guard(async () => {
    const r = await auth.api(`/api/nas/${n.value.id}/snmp`, { method: 'POST' })
    if (r.result.status === 'ok') notice.value = r.result.detail; else error.value = r.result.detail
    await loadData()
  })
}
async function runDsm() {
  const word = prompt(`DSM auf „${n.value.name}“ auf ${upd.value.dsm.version} aktualisieren? Das NAS startet neu und ist einige Minuten nicht erreichbar.\n\nZum Bestätigen UPDATE eingeben:`)
  if (word !== 'UPDATE') return
  await guard(async () => { await auth.api(`/api/nas/${n.value.id}/update`, { method: 'POST', body: { what: 'dsm', confirm: 'UPDATE' } }); notice.value = 'DSM-Update gestartet – Fortschritt unter Vorgänge.'; await loadData() })
}
async function runPackages() {
  if (!confirm(`${selected.value.length} Paket(e) auf „${n.value.name}“ aktualisieren? Betroffene Dienste starten dabei kurz neu.`)) return
  await guard(async () => { await auth.api(`/api/nas/${n.value.id}/update`, { method: 'POST', body: { what: 'packages', packages: selected.value } }); notice.value = 'Paket-Updates gestartet.'; selected.value = []; await loadData() })
}
async function runTask(t) {
  await guard(async () => { await auth.api(`/api/nas/${n.value.id}/backup-tasks/${t.id}/run`, { method: 'POST' }); notice.value = `Hyper Backup „${t.name}“ gestartet.` })
}
async function saveAuto() {
  await guard(async () => {
    const automation = { ...autoForm.value, exclude: excludeText.value.split(',').map(s => s.trim()).filter(Boolean) }
    d.value = { ...d.value, ...(await auth.api(`/api/nas/${n.value.id}`, { method: 'PUT', body: {
      name: n.value.name, site: n.value.site, verify_tls: n.value.verify_tls, enabled: n.value.enabled,
      poll_interval_sec: n.value.poll_interval_sec, automation } })) }
    notice.value = 'Automatik gespeichert.'
  })
}
async function openEdit() {
  edit.value = { name: n.value.name, site: n.value.site, poll_interval_sec: n.value.poll_interval_sec, verify_tls: n.value.verify_tls,
    enabled: n.value.enabled, via_agent_id: n.value.via_agent_id ?? null }
  try { relayAgents.value = await auth.api('/api/nas/relay-agents') } catch (e) { error.value = e.message }
}
async function saveEdit() {
  await guard(async () => {
    await auth.api(`/api/nas/${n.value.id}`, { method: 'PUT', body: { ...edit.value, automation: n.value.automation } })
    edit.value = null; await loadData()
  })
}
async function remove() {
  if (!confirm(`NAS „${n.value.name}“ entfernen? Auch der Zugang (in Vaultwarden: in den Papierkorb) wird entfernt. Am NAS selbst ändert sich nichts.`)) return
  await guard(async () => { await auth.api(`/api/nas/${n.value.id}`, { method: 'DELETE' }); router.push('/devices/nas') })
}
async function doRelogin() {
  relogin.value.error = ''
  busy.value = true
  try {
    await auth.api('/api/nas/connect', { method: 'POST', body: {
      nas_id: n.value.id, host: n.value.host, port: n.value.port, scheme: n.value.scheme, name: n.value.name,
      username: relogin.value.username || d.value.secret?.username || '', password: relogin.value.password || '',
      otp: (relogin.value.otp || '').trim(), verify_tls: n.value.verify_tls, enable_snmp: false, site: n.value.site, vendor: n.value.vendor,
      via_agent_id: n.value.via_agent_id ?? null } })
    relogin.value = null
    notice.value = 'Angemeldet.'
    await loadData()
  } catch (e) {
    const msg = String(e.message || '')
    relogin.value.error = msg.startsWith('need_otp') ? 'Zwei-Faktor-Code nötig bzw. abgelehnt – aktuellen Code eingeben.' : msg
  } finally { busy.value = false }
}

onMounted(loadData)
onBeforeUnmount(() => clearTimeout(timer))
</script>

<style scoped>
.pol-check { flex-direction: row !important; align-items: center; gap: .4rem !important; }
.nd-grid { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fit, minmax(min(100%, 24rem), 1fr)); }
.nd-facts { display: grid; grid-template-columns: 1fr 1fr; gap: .6rem 1rem; margin: 0; font-size: .875rem; }
.nd-facts dt { color: var(--color-text-muted); font-size: .72rem; text-transform: uppercase; letter-spacing: .03em; }
.nd-facts dd { margin: 0; overflow-wrap: anywhere; }
.nd-facts .is-bad { color: var(--color-danger); }
.nd-problems, .nd-checks { margin: 0; padding-left: 0; list-style: none; display: flex; flex-direction: column; gap: .35rem; font-size: .875rem; }
.nd-vol { display: flex; flex-direction: column; gap: .25rem; font-size: .85rem; }
.nd-bar { height: .5rem; background: var(--color-border); border-radius: 999px; overflow: hidden; }
.nd-bar > div { height: 100%; background: var(--color-success, #16a34a); }
.nd-bar > div.is-warn { background: var(--color-warning, #d97706); }
.nd-bar > div.is-bad { background: var(--color-danger, #dc2626); }
.nd-way { display: flex; justify-content: space-between; align-items: center; gap: .75rem; flex-wrap: wrap; padding: .75rem; border: 1px solid var(--color-border); border-radius: var(--radius); }
.nd-days { display: flex; gap: .75rem; flex-wrap: wrap; align-items: center; }
.nd-pkgs { display: flex; flex-wrap: wrap; gap: .35rem; }
.nd-changelog { white-space: pre-wrap; font-size: .8rem; max-width: 36rem; max-height: 12rem; overflow: auto; }
</style>
