<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Geraete -> NAS & Spezialsysteme -> Raspberry Pi & Co.

     Ein Raspberry Pi ist ein Linux-Rechner: er bekommt den normalen Agent
     (arm64, armv7 oder armv6 - der Installer waehlt selbst) und damit Shell,
     Updates, Checks und Rechner-Sicherungen. Hier stehen zusaetzlich, was nur
     Einplatinenrechner haben: SoC-Temperatur und Drosselung (Unterspannung!).
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Raspberry Pi & Spezialsysteme</h1>
        <p class="page-subtitle">
          Einplatinenrechner (Raspberry Pi, andere ARM-Boards) laufen mit dem normalen RIMPERIO-Agent – 64 und 32 Bit,
          bis zum Pi Zero. Damit gehen Shell, Updates, Checks und <router-link to="/backups/jobs">Sicherungen von Ordnern</router-link>.
          Zusätzlich: SoC-Temperatur und ob die Firmware wegen Unterspannung oder Hitze drosselt.
          NAS-Systeme ohne Agent stehen unter <router-link to="/devices/nas">NAS</router-link>.
        </p>
      </div>
      <button class="btn btn-secondary btn-sm" :disabled="busy" @click="load"><AppIcon name="refresh" :size="15" /> Neu laden</button>
    </div>
    <p v-if="error" class="rmm-note is-error">{{ error }}</p>

    <section class="card">
      <div class="card-header"><h2 class="card-title">Mit Agent <span class="rmm-muted">{{ data.agents.length }}</span></h2></div>
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Rechner</th><th>Modell</th><th>System</th><th>Temperatur</th><th>Drosselung</th><th>Zuletzt</th><th>Aktionen</th></tr></thead>
          <tbody>
            <tr v-if="!data.agents.length"><td colspan="7" class="rmm-muted">Noch kein Einplatinenrechner mit Agent. Unten steht, wie er dazukommt.</td></tr>
            <template v-for="a in data.agents" :key="a.agent_uid">
            <tr>
              <td><span class="rmm-dot" :class="a.online ? 'is-online' : 'is-offline'" />
                <router-link :to="`/rmm/agents/${a.agent_uid}`"><strong>{{ a.hostname }}</strong></router-link>
                <div class="rmm-muted rmm-code">{{ a.ip || '' }}</div></td>
              <td>{{ a.model || '–' }}</td>
              <td>{{ a.os || '–' }} <span class="rmm-muted">{{ a.arch }}</span></td>
              <td :class="a.temp_c >= 80 ? 'is-bad' : a.temp_c >= 70 ? 'is-warn' : ''">{{ a.temp_c != null ? a.temp_c + ' °C' : '–' }}</td>
              <td>
                <span v-if="!a.throttled" class="rmm-muted">–</span>
                <span v-else-if="!(a.throttled_text || []).length" class="badge badge-success">nein</span>
                <span v-for="t in a.throttled_text || []" v-else :key="t" class="badge" :class="t.includes('jetzt') ? 'badge-danger' : 'badge-warning'">{{ t }}</span>
              </td>
              <td>{{ fmtAgo(a.last_seen) }}</td>
              <td class="sp-actions">
                <router-link class="btn btn-secondary btn-sm" :to="{ path: `/rmm/agents/${a.agent_uid}`, query: { tab: 'console' } }">Konsole</router-link>
                <router-link class="btn btn-secondary btn-sm" :to="{ path: `/rmm/agents/${a.agent_uid}`, query: { tab: 'patches' } }">
                  Updates<span v-if="a.open_patches" class="badge badge-warning sp-badge">{{ a.open_patches }}</span></router-link>
                <router-link class="btn btn-ghost btn-sm" :to="{ path: `/rmm/agents/${a.agent_uid}`, query: { tab: 'checks' } }">Checks</router-link>
                <router-link class="btn btn-ghost btn-sm" :to="{ path: '/backups/jobs', query: { new: 'files', agent: a.agent_uid } }">Sichern</router-link>
                <template v-if="auth.isAdmin">
                  <button class="btn btn-ghost btn-sm" :disabled="!a.online || busyUid === a.agent_uid" @click="piCheck(a)">Zustand prüfen</button>
                  <button class="btn btn-ghost btn-sm" :disabled="!a.online || busyUid === a.agent_uid" @click="reboot(a)">Neustart</button>
                </template>
              </td>
            </tr>
            <tr v-if="(a.hints || []).length"><td colspan="7" class="sp-hints">
              <span v-for="h in a.hints" :key="h" class="rmm-note is-info">{{ h }}</span></td></tr>
            </template>
          </tbody>
        </table>
      </div>
      <p class="card-body rmm-muted">„Unterspannung“ heißt fast immer: Netzteil zu schwach oder Kabel zu dünn – Ursache Nummer eins für kaputte SD-Karten.</p>
    </section>

    <div v-if="report" class="modal-backdrop" @click.self="report = null">
      <div class="modal max-w-2xl">
        <div class="card-header"><h2 class="card-title">Zustand: {{ report.host }}</h2>
          <button class="btn-icon" aria-label="Schließen" @click="report = null"><AppIcon name="close" :size="18" /></button></div>
        <div class="card-body">
          <p v-if="report.waiting" class="rmm-muted">Der Pi prüft … (bis zu einer Minute)</p>
          <pre v-else class="sp-pre">{{ report.text }}</pre>
        </div>
      </div>
    </div>

    <section v-if="(data.log_only || []).length" class="card">
      <div class="card-header"><h2 class="card-title">Nur Logs, ohne Fernwartung <span class="rmm-muted">{{ data.log_only.length }}</span></h2></div>
      <p class="card-body rmm-muted">Diese Pis schicken Logs (Log-Agent bzw. Syslog), haben aber den RIMPERIO-Agent nicht –
        darum gehen Konsole, Updates, Checks und Sicherungen noch nicht. Über die Netzwerksuche per SSH nachinstallieren
        oder den Befehl unter <router-link to="/rmm">Geräte → Rechner → Agent hinzufügen</router-link> auf dem Pi ausführen.</p>
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Name</th><th>IP</th><th>Zuletzt</th><th></th></tr></thead>
          <tbody>
            <tr v-for="d in data.log_only" :key="d.id">
              <td>{{ d.hostname }}<div class="rmm-muted">{{ d.mac }}</div></td><td class="rmm-code">{{ d.ip || '–' }}</td>
              <td>{{ fmtAgo(d.last_seen) }}</td>
              <td style="text-align:right"><router-link v-if="d.ip" class="btn btn-primary btn-sm" :to="{ path: '/discovery', query: { ip: d.ip } }">Agent nachinstallieren</router-link></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="card">
      <div class="card-header"><h2 class="card-title">Im Netz gefunden, ohne Agent <span class="rmm-muted">{{ data.discovered.length }}</span></h2>
        <router-link to="/discovery" class="btn btn-ghost btn-sm">Netzwerksuche</router-link></div>
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>IP</th><th>Name</th><th>Erkannt</th><th></th></tr></thead>
          <tbody>
            <tr v-if="!data.discovered.length"><td colspan="4" class="rmm-muted">Keine Raspberry Pis (an der MAC-Adresse erkannt) ohne Agent.</td></tr>
            <tr v-for="d in data.discovered" :key="d.id">
              <td class="rmm-code">{{ d.ip }}</td><td>{{ d.hostname || '–' }}<div class="rmm-muted">{{ d.mac }}</div></td>
              <td>{{ d.os_guess || 'Raspberry Pi' }}</td>
              <td style="text-align:right"><router-link class="btn btn-primary btn-sm" :to="{ path: '/discovery', query: { ip: d.ip } }">{{ d.ssh ? 'Per SSH einbinden' : 'Einbinden' }}</router-link></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="card">
      <div class="card-header"><h2 class="card-title">Agent auf einem Pi installieren</h2></div>
      <div class="card-body space-y-2 text-sm">
        <p>Wie bei jedem Linux-Rechner – der Installer erkennt die Architektur selbst
          (<span class="rmm-code">aarch64</span> → arm64, <span class="rmm-code">armv7l</span> → armv7 für Pi 2/3/4 mit 32-Bit-System,
          <span class="rmm-code">armv6l</span> → armv6 für Pi 1 und Pi Zero). Den fertigen Befehl gibt es unter
          <router-link to="/rmm">Geräte → Rechner → Agent hinzufügen</router-link>, oder ohne Handarbeit per SSH aus der
          <router-link to="/discovery">Netzwerksuche</router-link>.</p>
        <p class="rmm-muted">Drosselung liest der Agent mit <span class="rmm-code">vcgencmd</span> (Raspberry Pi OS hat es dabei). Das Dienstkonto
          <span class="rmm-code">rimperio</span> braucht dafür die Gruppe <span class="rmm-code">video</span>:
          <span class="rmm-code">sudo usermod -aG video rimperio</span>.</p>
      </div>
    </section>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import { fmtAgo } from '../utils/rmm'

const auth = useAuthStore()
const data = ref({ agents: [], log_only: [], discovered: [] })
const error = ref('')
const busy = ref(false)
const busyUid = ref('')
const report = ref(null)

async function waitFor(uid, id) {
  for (let i = 0; i < 40; i++) {
    await new Promise(r => setTimeout(r, 2500))
    const list = await auth.api(`/api/rmm/agents/${uid}/commands?limit=20`)
    const c = list.find(x => x.id === id)
    if (c && !['pending', 'running'].includes(c.status)) return c
  }
  return null
}
async function piCheck(a) {
  busyUid.value = a.agent_uid
  report.value = { host: a.hostname, waiting: true, text: '' }
  try {
    const cmd = await auth.api(`/api/rmm/agents/${a.agent_uid}/pi-check`, { method: 'POST' })
    const done = await waitFor(a.agent_uid, cmd.id)
    report.value = { host: a.hostname, waiting: false,
      text: done ? ((done.stdout || '') + (done.stderr ? '\n' + done.stderr : '')).trim() || '(keine Ausgabe)' : 'Keine Antwort vom Pi – ist er online?' }
  } catch (e) { report.value = { host: a.hostname, waiting: false, text: 'Fehler: ' + e.message } } finally { busyUid.value = '' }
}
async function reboot(a) {
  if (!confirm(`${a.hostname} in einer Minute neu starten?`)) return
  busyUid.value = a.agent_uid
  try {
    await auth.api(`/api/rmm/agents/${a.agent_uid}/reboot?delay=1`, { method: 'POST' })
    error.value = ''
    alert(`${a.hostname}: Neustart in einer Minute geplant.`)
  } catch (e) { error.value = e.message } finally { busyUid.value = '' }
}

async function load() {
  busy.value = true
  try { data.value = await auth.api('/api/nas/special'); error.value = '' } catch (e) { error.value = e.message } finally { busy.value = false }
}
onMounted(load)
</script>

<style scoped>
.is-bad { color: var(--color-danger); font-weight: 600; }
.is-warn { color: var(--color-warning); }
td .badge + .badge { margin-left: .25rem; }
.sp-actions { display: flex; flex-wrap: wrap; gap: .3rem; }
.sp-badge { margin-left: .3rem; }
.sp-hints { display: flex; flex-direction: column; gap: .3rem; padding-top: 0; }
.sp-pre { white-space: pre-wrap; font-size: .8rem; max-height: 60vh; overflow: auto; background: var(--color-surface-2, rgba(0,0,0,.05)); padding: .75rem; border-radius: .5rem; }
</style>
