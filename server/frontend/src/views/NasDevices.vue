<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Geraete -> NAS & Spezialsysteme -> NAS: Uebersicht und
                   Einrichten ueber die Netzwerksuche (Synology DSM, TrueNAS).

     Einrichten in drei Schritten:
       1. Weg waehlen: direkt vom Server - oder ueber einen Agent am Standort
          (NAS an einem anderen Ort, nur ausgehendes HTTPS, kein offener Port).
          Dann: Was hat die Netzwerksuche als NAS erkannt? (oder IP von Hand)
       2. Anmelden - Benutzer der Gruppe administrators; mit TOTP fragt der
          Server einmal nach dem Code und merkt sich das Geraet.
       3. Pruefliste: Zertifikat, Zwei-Faktor, Rechte, Speicher, Updates, SNMP …
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">NAS</h1>
        <p class="page-subtitle">
          NAS-Systeme über ihre HTTPS-Schnittstelle – direkt vom Server oder, an einem anderen Standort, über einen Agent dort
          (nur ausgehendes HTTPS, kein offener Port): Zustand (Speicher, Platten, Temperatur),
          DSM- und Paket-Updates von Hand oder im Wartungsfenster, SNMP und Sicherungen.
          Schwerpunkt Synology DSM; TrueNAS mit API-Schlüssel.
          Zugangsdaten liegen in <router-link to="/integrations/vault">Vaultwarden</router-link> (oder lokal verschlüsselt).
        </p>
      </div>
      <button v-if="auth.isAdmin" class="btn btn-primary btn-sm" @click="openWizard()"><AppIcon name="plus" :size="16" /> NAS verbinden</button>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>

    <div v-if="!items.length && !loading" class="card">
      <div class="card-body nas-empty">
        <AppIcon name="archive" :size="28" />
        <p><strong>Noch kein NAS verbunden.</strong></p>
        <p class="rmm-muted">Die Netzwerksuche erkennt Synology-NAS an ihrer Schnittstelle (Port 5000/5001).
          {{ discovered.length ? `${discovered.length} gefunden – gleich verbinden.` : 'Noch keins gefunden – Netzwerksuche laufen lassen oder IP angeben.' }}</p>
        <button v-if="auth.isAdmin" class="btn btn-primary btn-sm" @click="openWizard()">NAS verbinden</button>
      </div>
    </div>

    <div class="nas-grid">
      <router-link v-for="n in items" :key="n.id" :to="`/devices/nas/${n.id}`" class="card nas-card" :style="n.enabled ? '' : 'opacity:.6'">
        <header class="nas-head">
          <span class="rmm-dot" :class="dotClass(n.status)" />
          <strong>{{ n.name }}</strong>
          <span class="badge" :class="STATUS[n.status]?.[0] || 'badge-neutral'">{{ STATUS[n.status]?.[1] || n.status }}</span>
          <span v-if="n.busy" class="badge badge-primary">Vorgang läuft</span>
        </header>
        <dl class="nas-facts">
          <div><dt>System</dt><dd>{{ n.model || n.vendor_label }}<div class="rmm-muted">{{ n.firmware || '–' }}</div></dd></div>
          <div><dt>Adresse</dt><dd><span class="rmm-code">{{ n.host }}:{{ n.port }}</span>
            <div v-if="n.via_agent" class="rmm-muted" :title="n.via_agent.ready ? 'Weg offen' : 'Agent gerade nicht verbunden'">
              <span class="rmm-dot" :class="n.via_agent.ready ? 'is-online' : 'is-offline'" />
              über {{ n.via_agent.on_nas ? 'Agent auf dem NAS' : n.via_agent.hostname }} · {{ n.via_agent.site }}</div></dd></div>
          <div><dt>Updates</dt><dd>
            <span v-if="n.updates_summary.dsm" class="badge badge-warning">DSM {{ n.updates_summary.dsm }}</span>
            <span v-if="n.updates_summary.packages" class="badge badge-primary">{{ n.updates_summary.packages }} Pakete</span>
            <span v-if="!n.updates_summary.dsm && !n.updates_summary.packages" class="rmm-muted">{{ n.updates_summary.checked_at ? 'aktuell' : 'ungeprüft' }}</span>
          </dd></div>
          <div><dt>Zuletzt</dt><dd>{{ fmtAgo(n.last_ok_at) }}<span v-if="n.temperature" class="rmm-muted"> · {{ n.temperature }} °C</span></dd></div>
        </dl>
        <div v-for="v in n.volumes" :key="v.name" class="nas-vol">
          <span class="rmm-muted">{{ v.name }}</span>
          <div class="nas-bar"><div :class="barClass(v.percent)" :style="{ width: (v.percent || 0) + '%' }" /></div>
          <span>{{ v.percent != null ? Math.round(v.percent) + ' %' : '–' }}</span>
        </div>
        <ul v-if="n.problems.length" class="nas-problems">
          <li v-for="p in n.problems.slice(0, 3)" :key="p.what" :class="p.level === 'critical' ? 'is-bad' : 'is-warn'">{{ p.text }}</li>
        </ul>
      </router-link>
    </div>

    <!-- Assistent ----------------------------------------------------------- -->
    <div v-if="wizard" class="modal-backdrop" @click.self="wizard = null">
      <div class="modal max-w-3xl">
        <div class="card-header">
          <h2 class="card-title">NAS verbinden <span class="rmm-muted">· Schritt {{ wizard.step }} von 3</span></h2>
          <button class="btn-icon" aria-label="Schließen" @click="wizard = null"><AppIcon name="close" :size="18" /></button>
        </div>
        <div class="card-body space-y-3 text-sm overflow-y-auto">
          <!-- 1: Auswahl -->
          <template v-if="wizard.step === 1">
            <div class="nas-route">
              <label class="nas-route-opt" :class="{ 'is-on': wizard.route === 'direct' }">
                <input v-model="wizard.route" type="radio" value="direct" />
                <div><strong>Direkt vom RIMPERIO-Server</strong>
                  <div class="rmm-muted">Das NAS ist im selben Netz, per VPN oder über eine Portfreigabe erreichbar.</div></div>
              </label>
              <label class="nas-route-opt" :class="{ 'is-on': wizard.route === 'agent' }">
                <input v-model="wizard.route" type="radio" value="agent" @change="loadRelayAgents" />
                <div><strong>Über einen Agent am Standort</strong>
                  <div class="rmm-muted">Das NAS steht woanders. Ein Agent dort fragt es ab, nur ausgehend per HTTPS, ohne offenen Port.
                    Steht dort nur das NAS: Agent als Synology-Paket auf dem NAS selbst installieren.</div></div>
              </label>
            </div>

            <template v-if="wizard.route === 'agent'">
              <div class="rmm-form">
                <label class="is-wide">Agent am Standort
                  <select v-model="wizard.via_agent_id" class="select" @change="pickAgent">
                    <option :value="null" disabled>– Agent wählen –</option>
                    <option v-for="a in relayAgents" :key="a.id" :value="a.id" :disabled="!a.ready">
                      {{ a.hostname }} · {{ a.site }}{{ a.on_nas ? ' · läuft auf dem NAS' : '' }}{{ a.ready ? '' : (a.live ? ' · zu alt (ab 0.5.0)' : ' · nicht verbunden') }}
                    </option>
                  </select>
                  <span v-if="!relayAgents.some(a => a.ready)" class="rmm-muted">Noch kein passender Agent verbunden.</span>
                </label>
              </div>
              <div class="rmm-note is-info">
                <strong>Nur das NAS am Standort?</strong> Das Paket „RIMPERIO Agent“ herunterladen – Server-Adresse
                (<span class="rmm-code">{{ origin }}</span>) und eine Einladung sind schon eingetragen – und in DSM 7 unter
                <em>Paket-Zentrum → Manuelle Installation</em> einspielen (Vertrauensebene „Alle Herausgeber“).
                Danach erscheint das NAS hier als Agent „läuft auf dem NAS“, mit Adresse 127.0.0.1.
                <div class="rmm-actions mt-2">
                  <button class="btn btn-secondary btn-sm" :disabled="busy" @click="downloadPkg">Paket (.spk) herunterladen</button>
                  <button class="btn btn-ghost btn-sm" :disabled="busy" @click="loadRelayAgents">Agents neu laden</button>
                </div>
              </div>
              <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>
            </template>

            <template v-if="wizard.route === 'direct'">
            <p class="rmm-muted">Aus der <router-link to="/discovery">Netzwerksuche</router-link> – Systeme mit Synology-Schnittstelle
              (Port 5000/5001) oder als NAS erkannt. „Neu prüfen“ fragt jedes sofort.</p>
            <div class="rmm-actions">
              <button class="btn btn-secondary btn-sm" :disabled="busy" @click="loadDiscovered(true)">{{ busy ? 'Prüfe …' : 'Neu prüfen' }}</button>
              <router-link class="btn btn-ghost btn-sm" to="/discovery">Netzwerksuche starten</router-link>
            </div>
            <div class="rmm-table-wrap">
              <table class="table">
                <thead><tr><th>IP</th><th>Name</th><th>Erkannt</th><th></th></tr></thead>
                <tbody>
                  <tr v-if="!discovered.length"><td colspan="4" class="rmm-muted">Kein NAS in der Netzwerksuche. IP unten eintragen.</td></tr>
                  <tr v-for="d in discovered" :key="d.id">
                    <td class="rmm-code">{{ d.ip }}</td>
                    <td>{{ d.hostname || '–' }}<div class="rmm-muted">{{ d.mac || '' }}</div></td>
                    <td><span v-if="d.nas" class="badge badge-success">{{ d.nas.label }} · {{ d.nas.scheme }}:{{ d.nas.port }}</span>
                      <span v-else class="rmm-muted">{{ d.os_guess || 'Ports ' + d.ports.join(', ') }}</span></td>
                    <td style="text-align:right">
                      <span v-if="d.connected" class="badge badge-neutral">verbunden</span>
                      <button v-else class="btn btn-primary btn-sm" @click="pick(d.ip, d.nas)">Übernehmen</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            </template>
            <div class="rmm-form">
              <label>{{ wizard.route === 'agent' ? 'IP oder Name im Netz des Agents' : 'IP oder Name von Hand' }}<input v-model="wizard.host" class="input" placeholder="192.168.1.20" @keydown.enter="probeHost" /></label>
              <label>Port (optional)<input v-model.number="wizard.port" type="number" class="input" placeholder="5001" /></label>
              <div class="is-wide rmm-actions">
                <button class="btn btn-primary btn-sm" :disabled="busy || !wizard.host || (wizard.route === 'agent' && !wizard.via_agent_id)" @click="probeHost">{{ busy ? 'Prüfe …' : 'Prüfen und weiter' }}</button>
              </div>
            </div>
            <p v-if="wizard.error" class="rmm-note is-error">{{ wizard.error }}</p>
          </template>

          <!-- 2: Anmelden -->
          <template v-if="wizard.step === 2">
            <div class="nas-found">
              <AppIcon name="archive" :size="20" />
              <div>
                <strong>{{ wizard.info?.label || 'NAS' }}</strong> unter <span class="rmm-code">{{ wizard.info?.url }}</span>
                <div v-if="wizard.info?.via_agent" class="rmm-muted">gefragt vom Agent {{ wizard.info.via_agent.hostname }} ({{ wizard.info.via_agent.site }})
                  – RIMPERIO spricht mit diesem NAS nur über ihn</div>
                <div v-if="wizard.info?.certificate" class="rmm-muted">Zertifikat: {{ wizard.info.certificate.self_signed ? 'selbst signiert' : wizard.info.certificate.issuer }},
                  gültig bis {{ fmtTime(wizard.info.certificate.not_after) }}</div>
                <div v-if="wizard.info?.scheme === 'http'" class="rmm-muted" style="color:var(--color-warning)">Nur HTTP – in DSM HTTPS (Port 5001) einschalten und hier neu prüfen.</div>
              </div>
            </div>
            <div class="rmm-form">
              <label>Name (optional)<input v-model="wizard.name" class="input" placeholder="leer = Modell" /></label>
              <label>Standort<input v-model="wizard.site" class="input" /></label>
              <template v-if="wizard.info?.vendor === 'truenas'">
                <label class="is-wide">API-Schlüssel (TrueNAS → Benutzer → API-Schlüssel)<input v-model="wizard.password" type="password" class="input" autocomplete="off" /></label>
              </template>
              <template v-else>
                <label>Benutzer (Gruppe administrators)<input v-model="wizard.username" class="input" autocomplete="off" placeholder="z. B. rimperio" /></label>
                <label>Passwort<input v-model="wizard.password" type="password" class="input" autocomplete="off" @keydown.enter="connect" /></label>
                <label :class="{ 'nas-otp': wizard.needOtp }">Zwei-Faktor-Code (TOTP)
                  <input ref="otpInput" v-model="wizard.otp" class="input" inputmode="numeric" maxlength="8" placeholder="6 Ziffern aus der Authenticator-App" @keydown.enter="connect" /></label>
                <label class="pol-check"><input v-model="wizard.enable_snmp" type="checkbox" /> SNMP v2c gleich einschalten</label>
              </template>
              <details class="is-wide">
                <summary class="rmm-muted" style="cursor:pointer">Erweitert</summary>
                <div class="rmm-form mt-2">
                  <label class="is-wide">TOTP-Geheimnis (optional, Notfall)<input v-model="wizard.totp_secret" class="input" autocomplete="off" placeholder="Base32 oder otpauth://…" />
                    <span class="rmm-muted">Nur nötig, wenn DSM das vertraute Gerät verwirft – dann erzeugt RIMPERIO den Code selbst. Liegt im Vaultwarden-Eintrag als TOTP.</span></label>
                  <label class="pol-check"><input v-model="wizard.verify_tls" type="checkbox" /> Zertifikat prüfen</label>
                </div>
              </details>
              <p v-if="wizard.needOtp" class="is-wide rmm-note is-info">Für dieses Konto ist Zwei-Faktor aktiv. Code eingeben – danach vertraut DSM RIMPERIO als Gerät, spätere Anmeldungen brauchen keinen Code mehr.</p>
              <p class="is-wide rmm-muted">Tipp: eigenes Administratorkonto für RIMPERIO anlegen (nicht „admin“), mit Zwei-Faktor. RIMPERIO meldet sich als Gerät „RIMPERIO“ an.</p>
              <p v-if="wizard.error" class="is-wide rmm-note is-error">{{ wizard.error }}</p>
              <div class="is-wide rmm-actions">
                <button class="btn btn-primary btn-sm" :disabled="busy || !wizard.password" @click="connect">{{ busy ? 'Melde an und prüfe …' : 'Anmelden und prüfen' }}</button>
                <button class="btn btn-ghost btn-sm" :disabled="busy" @click="wizard.step = 1">Zurück</button>
              </div>
            </div>
          </template>

          <!-- 3: Pruefliste -->
          <template v-if="wizard.step === 3 && wizard.result">
            <p><strong>{{ wizard.result.name }}</strong> ist verbunden. Geprüft wurde:</p>
            <ul class="nas-checks">
              <li v-for="c in wizard.result.checks" :key="c.key" :class="'is-' + c.status">
                <span class="nas-check-ico">{{ ICON[c.status] || '•' }}</span>
                <div><strong>{{ c.label }}</strong><div class="rmm-muted">{{ c.detail }}</div></div>
              </li>
            </ul>
            <div class="rmm-actions">
              <router-link class="btn btn-primary btn-sm" :to="`/devices/nas/${wizard.result.id}`" @click="wizard = null">Zum NAS</router-link>
              <router-link class="btn btn-secondary btn-sm" :to="{ path: '/backups/jobs', query: { new: 'nas_config', nas: wizard.result.id } }" @click="wizard = null">Konfiguration sichern lassen</router-link>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import { fmtAgo, fmtTime } from '../utils/rmm'

const auth = useAuthStore()
const route = useRoute()
const items = ref([])
const discovered = ref([])
const loading = ref(true)
const error = ref('')
const notice = ref('')
const busy = ref(false)
const wizard = ref(null)
const otpInput = ref(null)
const relayAgents = ref([])
const origin = window.location.origin

const STATUS = {
  ok: ['badge-success', 'in Ordnung'], warning: ['badge-warning', 'Warnung'], critical: ['badge-danger', 'kritisch'],
  down: ['badge-danger', 'nicht erreichbar'], unknown: ['badge-neutral', 'ungeprüft'],
}
const ICON = { ok: '✓', warn: '!', fail: '✗', info: 'i' }
const dotClass = s => (s === 'ok' ? 'is-online' : (s === 'down' || s === 'critical' ? 'is-offline' : ''))
const barClass = p => (p >= 90 ? 'is-bad' : p >= 80 ? 'is-warn' : '')

async function load() {
  try {
    items.value = await auth.api('/api/nas')
    if (auth.isAdmin) await loadDiscovered(false)
    error.value = ''
  } catch (e) { error.value = e.message } finally { loading.value = false }
}

async function loadDiscovered(live) {
  busy.value = live
  try { discovered.value = await auth.api('/api/nas/discovered' + (live ? '?live=1' : '')) } catch (e) { error.value = e.message } finally { busy.value = false }
}

function blank() {
  return { step: 1, route: 'direct', via_agent_id: null, host: '', port: null, info: null, name: '', site: 'Standard',
    username: '', password: '', otp: '', totp_secret: '', verify_tls: false, enable_snmp: true, needOtp: false, error: '', result: null }
}
async function loadRelayAgents() {
  try { relayAgents.value = await auth.api('/api/nas/relay-agents') } catch (e) { wizard.value.error = e.message }
  const w = wizard.value
  if (w && !w.via_agent_id) {
    const first = relayAgents.value.find(a => a.ready)
    if (first) { w.via_agent_id = first.id; pickAgent() }
  }
}
// Paket "RIMPERIO Agent" fuer DSM 7 - baut der Server, mit Adresse und Einladung (POST /api/nas/dsm-package).
async function downloadPkg() {
  const w = wizard.value
  w.error = ''
  busy.value = true
  try {
    const a = relayAgents.value.find(x => x.id === w.via_agent_id)
    const res = await fetch('/api/nas/dsm-package', { method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${auth.token}` },
      body: JSON.stringify({ server: '', site: (w.site && w.site !== 'Standard') ? w.site : (a?.site || 'Standard') }) })
    if (!res.ok) { let m = `HTTP ${res.status}`; try { m = (await res.json()).detail || m } catch { /* Text */ } throw new Error(m) }
    const name = (res.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/)?.[1] || 'RIMPERIO-Agent.spk'
    const url = URL.createObjectURL(await res.blob())
    const link = document.createElement('a'); link.href = url; link.download = name; document.body.appendChild(link); link.click(); link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 5000)
    notice.value = `${name} heruntergeladen – im DSM-Paketzentrum über „Manuelle Installation“ einspielen.`
  } catch (e) { w.error = e.message } finally { busy.value = false }
}
// Agent laeuft auf dem NAS selbst: dann ist DSM unter 127.0.0.1:5001, Standort wie der Agent.
function pickAgent() {
  const w = wizard.value
  const a = relayAgents.value.find(x => x.id === w.via_agent_id)
  if (!a) return
  if (a.on_nas) { w.host = '127.0.0.1'; w.port = 5001 } else if (w.host === '127.0.0.1') { w.host = ''; w.port = null }
  if (!w.site || w.site === 'Standard') w.site = a.site || 'Standard'
  if (!w.name && a.on_nas) w.name = a.hostname
}
function openWizard(host) {
  wizard.value = blank()
  if (host) { wizard.value.host = host; probeHost() }
}
function pick(ip, info) {
  wizard.value.host = ip
  if (info) { wizard.value.info = info; wizard.value.port = info.port; wizard.value.step = 2; return }
  probeHost()
}
async function probeHost() {
  const w = wizard.value
  w.error = ''
  busy.value = true
  try {
    const r = await auth.api('/api/nas/probe', { method: 'POST', body: { host: w.host.trim(), port: w.port || null,
      via_agent_id: w.route === 'agent' ? w.via_agent_id : null } })
    if (!r.found) { w.error = r.message; return }
    w.info = r
    w.port = r.port
    w.step = 2
  } catch (e) { w.error = e.message } finally { busy.value = false }
}
async function connect() {
  const w = wizard.value
  w.error = ''
  busy.value = true
  try {
    const r = await auth.api('/api/nas/connect', { method: 'POST', body: {
      host: w.host.trim(), port: w.info?.port || w.port || null, scheme: w.info?.scheme || null, name: w.name,
      username: w.info?.vendor === 'truenas' ? 'api' : w.username, password: w.password, otp: w.otp.trim(),
      totp_secret: w.totp_secret.trim(), verify_tls: w.verify_tls, enable_snmp: w.enable_snmp, site: w.site,
      vendor: w.info?.vendor === 'synology' || w.info?.vendor === 'truenas' ? w.info.vendor : 'generic',
      via_agent_id: w.route === 'agent' ? w.via_agent_id : null } })
    w.result = r
    w.step = 3
    w.password = ''
    await load()
  } catch (e) {
    const msg = String(e.message || '')
    if (msg.startsWith('need_otp')) {
      w.needOtp = true
      w.error = w.otp ? 'Code abgelehnt – bitte den aktuellen Code eingeben.' : ''
      w.otp = ''
      await nextTick()
      otpInput.value?.focus()
    } else {
      w.error = msg
    }
  } finally { busy.value = false }
}

function onKey(e) { if (e.key === 'Escape' && wizard.value && !busy.value) wizard.value = null }
onMounted(async () => {
  window.addEventListener('keydown', onKey)
  await load()
  if (route.query.connect && auth.isAdmin) openWizard(String(route.query.connect))
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<style scoped>
.pol-check { flex-direction: row !important; align-items: center; gap: .5rem !important; }
.nas-empty { display: flex; flex-direction: column; align-items: center; gap: .5rem; text-align: center; padding: 2rem 1rem; color: var(--color-text-secondary); }
.nas-grid { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fill, minmax(min(100%, 22rem), 1fr)); }
.nas-card { padding: 1rem; display: flex; flex-direction: column; gap: .6rem; color: inherit; text-decoration: none; }
.nas-card:hover { border-color: var(--color-primary); }
.nas-head { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; font-size: 1rem; }
.nas-facts { display: grid; grid-template-columns: 1fr 1fr; gap: .5rem 1rem; margin: 0; font-size: .875rem; }
.nas-facts dt { color: var(--color-text-muted); font-size: .72rem; text-transform: uppercase; letter-spacing: .03em; }
.nas-facts dd { margin: 0; overflow-wrap: anywhere; }
.nas-vol { display: grid; grid-template-columns: minmax(5rem, auto) 1fr 3rem; gap: .5rem; align-items: center; font-size: .8rem; }
.nas-bar { height: .45rem; background: var(--color-border); border-radius: 999px; overflow: hidden; }
.nas-bar > div { height: 100%; background: var(--color-success, #16a34a); }
.nas-bar > div.is-warn { background: var(--color-warning, #d97706); }
.nas-bar > div.is-bad { background: var(--color-danger, #dc2626); }
.nas-problems { margin: 0; padding-left: 1rem; font-size: .8rem; }
.nas-problems .is-bad { color: var(--color-danger); }
.nas-problems .is-warn { color: var(--color-warning); }
.nas-found { display: flex; gap: .75rem; align-items: flex-start; padding: .75rem; border: 1px solid var(--color-border); border-radius: var(--radius); }
.nas-otp input { border-color: var(--color-primary); }
.nas-route { display: grid; gap: .5rem; grid-template-columns: repeat(auto-fit, minmax(min(100%, 16rem), 1fr)); }
.nas-route-opt { display: flex; gap: .6rem; align-items: flex-start; padding: .75rem; border: 1px solid var(--color-border); border-radius: var(--radius); cursor: pointer; }
.nas-route-opt.is-on { border-color: var(--color-primary); background: var(--primary-soft); }
.nas-route-opt input { margin-top: .2rem; }
.nas-checks { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: .4rem; }
.nas-checks li { display: flex; gap: .6rem; align-items: flex-start; padding: .5rem .6rem; border: 1px solid var(--color-border); border-radius: var(--radius); }
.nas-check-ico { width: 1.4rem; height: 1.4rem; border-radius: 999px; display: inline-flex; align-items: center; justify-content: center; font-weight: 700; flex: none; background: var(--color-border); }
.nas-checks .is-ok .nas-check-ico { background: var(--color-success, #16a34a); color: #fff; }
.nas-checks .is-warn .nas-check-ico { background: var(--color-warning, #d97706); color: #fff; }
.nas-checks .is-fail .nas-check-ico { background: var(--color-danger, #dc2626); color: #fff; }
.nas-checks .is-info .nas-check-ico { background: var(--color-primary); color: #fff; }
</style>
