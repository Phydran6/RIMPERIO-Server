<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Patch-Richtlinien: Zeitplan, Freigaberegeln, KI, Snapshot, Prüfung, Docker
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Patch-Richtlinien</h1>
        <p class="page-subtitle">
          Wann gesucht und wann installiert wird, was automatisch läuft und was eine Freigabe braucht.
          Die Standardrichtlinie gilt für alle Rechner; eigene Richtlinien je Standort oder Rechner gehen vor.
        </p>
      </div>
      <button v-if="auth.isAdmin" class="btn btn-primary btn-sm" @click="open()"><AppIcon name="plus" :size="16" /> Neue Richtlinie</button>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>

    <section v-if="!form" class="card">
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Name</th><th>Gilt für</th><th>Suche</th><th>Fenster</th><th>KI</th><th>Sicherung</th><th></th></tr></thead>
          <tbody>
            <tr v-for="p in policies" :key="p.id" :style="p.enabled ? '' : 'opacity:.55'">
              <td><strong>{{ p.name }}</strong><span v-if="p.builtin" class="badge badge-neutral" style="margin-left:.4rem">Standard</span><div class="rmm-muted">{{ p.description }}</div></td>
              <td>{{ scopeLabel(p) }}</td>
              <td>{{ p.settings.scan.enabled ? `täglich ${p.settings.scan.time}` : 'aus' }}</td>
              <td>{{ windowLabel(p.settings) }}</td>
              <td>{{ { off: 'aus', advise: 'berät', gate: 'entscheidet mit' }[p.settings.ai.mode] }}</td>
              <td>{{ p.settings.snapshot.enabled ? `${p.settings.snapshot.mode === 'backup' ? 'vzdump' : 'Snapshot'}, ${p.settings.snapshot.retention_days} T` : 'aus' }}</td>
              <td class="rmm-actions" style="justify-content:flex-end">
                <button class="btn btn-secondary btn-sm" @click="open(p)">{{ auth.isAdmin ? 'Bearbeiten' : 'Ansehen' }}</button>
                <button v-if="auth.isAdmin && !p.builtin" class="btn btn-danger btn-sm" @click="remove(p)">Löschen</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <template v-if="form">
      <section class="card mb-4">
        <div class="card-header"><h2 class="card-title">{{ form.id ? form.name : 'Neue Richtlinie' }}</h2>
          <div class="rmm-actions">
            <button v-if="auth.isAdmin" class="btn btn-primary btn-sm" :disabled="busy || !form.name" @click="save">Speichern</button>
            <button class="btn btn-ghost btn-sm" @click="form = null">Schließen</button>
          </div>
        </div>
        <div class="card-body rmm-form">
          <label>Name<input v-model="form.name" class="input" /></label>
          <label class="is-wide">Beschreibung<input v-model="form.description" class="input" /></label>
          <label>Gilt für
            <select v-model="form.scope_type" class="select" :disabled="form.builtin">
              <option value="global">alle Rechner</option><option value="site">einen Standort</option><option value="agent">einen Rechner</option>
            </select>
          </label>
          <label v-if="form.scope_type === 'site'">Standort
            <select v-model="form.scope_value" class="select"><option v-for="s in sites" :key="s" :value="s">{{ s }}</option></select>
          </label>
          <label v-if="form.scope_type === 'agent'">Rechner
            <select v-model="form.scope_value" class="select"><option v-for="a in agents" :key="a.agent_uid" :value="a.agent_uid">{{ a.hostname }}</option></select>
          </label>
          <label>Priorität (kleiner gewinnt)<input v-model.number="form.priority" type="number" class="input" /></label>
          <label class="pol-check"><input v-model="form.enabled" type="checkbox" :disabled="form.builtin" /> aktiv</label>
          <div v-if="preview" class="is-wide rmm-muted">Gilt derzeit für: {{ preview.agents.join(', ') || 'keinen Rechner' }}</div>
        </div>
      </section>

      <section class="card mb-4">
        <div class="card-header"><h2 class="card-title">Zeitplan</h2></div>
        <div class="card-body rmm-form">
          <label class="pol-check is-wide">Plattformen:
            <span v-for="pl in ['windows','linux','docker']" :key="pl"><input v-model="s.platforms" type="checkbox" :value="pl" /> {{ pl }}</span>
          </label>
          <label class="pol-check"><input v-model="s.scan.enabled" type="checkbox" /> täglich suchen</label>
          <label>Suche um<input v-model="s.scan.time" type="time" class="input" /></label>
          <label>Zeitzone<input v-model="s.timezone" class="input" /></label>
          <label class="pol-check"><input v-model="s.window.enabled" type="checkbox" /> automatisch installieren im Wartungsfenster</label>
          <label class="pol-check is-wide">Tage:
            <span v-for="(d, i) in WEEKDAYS" :key="d"><input v-model="s.window.days" type="checkbox" :value="i" /> {{ d }}</span>
          </label>
          <label>Beginn<input v-model="s.window.start" type="time" class="input" /></label>
          <label>Dauer (Minuten)<input v-model.number="s.window.duration_minutes" type="number" min="15" class="input" /></label>
          <label>Rückstellung (Tage nach Erscheinen)<input v-model.number="s.defer_days" type="number" min="0" max="90" class="input" /></label>
          <label>Neustart
            <select v-model="s.reboot" class="select"><option value="if_needed">wenn nötig</option><option value="always">immer</option><option value="never">nie (nur melden)</option></select>
          </label>
        </div>
      </section>

      <section class="card mb-4">
        <div class="card-header"><h2 class="card-title">Freigabe-Regeln</h2>
          <button v-if="auth.isAdmin" class="btn btn-secondary btn-sm" @click="s.rules.push({ name: 'Neue Regel', platform: 'any', field: 'title', op: 'contains', value: '', action: 'approve', enabled: true })">Regel hinzufügen</button>
        </div>
        <div class="card-body">
          <p class="rmm-muted mb-2">Die erste passende Regel entscheidet. Passt keine, gilt der Standard je Plattform.
            „Hauptversion“ (<span class="rmm-code">major</span>) erkennt Sprünge wie 1.x → 2.x.</p>
          <div class="rmm-table-wrap">
            <table class="table">
              <thead><tr><th></th><th>Name</th><th>Plattform</th><th>Feld</th><th>Vergleich</th><th>Wert</th><th>Aktion</th><th></th></tr></thead>
              <tbody>
                <tr v-for="(r, i) in s.rules" :key="i">
                  <td><input v-model="r.enabled" type="checkbox" /></td>
                  <td><input v-model="r.name" class="input" /></td>
                  <td><select v-model="r.platform" class="select"><option value="any">alle</option><option>windows</option><option>linux</option><option>docker</option></select></td>
                  <td><select v-model="r.field" class="select"><option v-for="f in defaults.rule_fields" :key="f">{{ f }}</option></select></td>
                  <td><select v-model="r.op" class="select"><option v-for="o in defaults.rule_ops" :key="o">{{ o }}</option></select></td>
                  <td><input v-model="r.value" class="input rmm-code" :disabled="['is_true','is_false'].includes(r.op)" /></td>
                  <td><select v-model="r.action" class="select"><option value="auto">automatisch</option><option value="approve">Freigabe</option><option value="ignore">ignorieren</option></select></td>
                  <td class="rmm-actions">
                    <button class="btn btn-ghost btn-sm" :disabled="i === 0" @click="move(i, -1)">↑</button>
                    <button class="btn btn-ghost btn-sm" :disabled="i === s.rules.length - 1" @click="move(i, 1)">↓</button>
                    <button class="btn btn-ghost btn-sm" @click="s.rules.splice(i, 1)">×</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="rmm-form mt-4">
            <label v-for="pl in ['windows','linux','docker']" :key="pl">Standard {{ pl }}
              <select v-model="s.fallback[pl]" class="select"><option value="auto">automatisch</option><option value="approve">Freigabe</option><option value="ignore">ignorieren</option></select>
            </label>
            <div class="is-wide"><button class="btn btn-ghost btn-sm" @click="s.rules = JSON.parse(JSON.stringify(defaults.settings.rules))">Mitgelieferte Regeln wiederherstellen</button></div>
          </div>
        </div>
      </section>

      <section class="card mb-4">
        <div class="card-header"><h2 class="card-title">KI-Prüfung</h2></div>
        <div class="card-body rmm-form">
          <label>Modus
            <select v-model="s.ai.mode" class="select">
              <option value="gate">entscheidet mit (riskante Updates brauchen Freigabe)</option>
              <option value="advise">berät nur (Einschätzung anzeigen)</option>
              <option value="off">aus</option>
            </select>
          </label>
          <label class="pol-check"><input v-model="s.ai.web_search" type="checkbox" /> Websuche nach bekannten Problemen</label>
          <label class="pol-check"><input v-model="s.ai.required" type="checkbox" /> ohne KI-Einschätzung nichts automatisch</label>
          <div class="is-wide rmm-muted">Anbieter und Modell: <router-link to="/ai">KI-Auswertung</router-link>. Websuche-Anbieter: <router-link to="/rmm/sources">Update-Quellen</router-link>.</div>
        </div>
      </section>

      <section class="card mb-4">
        <div class="card-header"><h2 class="card-title">Sicherung vorher (Proxmox)</h2></div>
        <div class="card-body rmm-form">
          <label class="pol-check"><input v-model="s.snapshot.enabled" type="checkbox" /> vor jedem Lauf sichern</label>
          <label>Art
            <select v-model="s.snapshot.mode" class="select">
              <option value="snapshot">Snapshot</option><option value="backup">vzdump-Sicherung</option><option value="auto">Snapshot, sonst vzdump</option>
            </select>
          </label>
          <label>Löschen nach (Tagen, wenn alles passt)<input v-model.number="s.snapshot.retention_days" type="number" min="0" max="90" class="input" /></label>
          <label>Speicher für vzdump (leer = Standard des Hosts)<input v-model="s.snapshot.storage" class="input" /></label>
          <label class="pol-check"><input v-model="s.snapshot.include_ram" type="checkbox" /> Arbeitsspeicher mitsichern (nur VMs)</label>
          <label class="pol-check"><input v-model="s.snapshot.required" type="checkbox" /> ohne Sicherung nicht installieren</label>
          <label class="pol-check"><input v-model="s.rollback.auto" type="checkbox" /> bei Fehlschlag automatisch zurückrollen</label>
        </div>
      </section>

      <section class="card mb-4">
        <div class="card-header"><h2 class="card-title">Prüfung danach</h2></div>
        <div class="card-body rmm-form">
          <label class="pol-check"><input v-model="s.verify.enabled" type="checkbox" /> Zustand vorher/nachher vergleichen</label>
          <label>Wartezeit nach dem Update (Minuten)<input v-model.number="s.verify.settle_minutes" type="number" min="0" class="input" /></label>
          <label>Neustart darf dauern (Minuten)<input v-model.number="s.verify.reboot_timeout_minutes" type="number" min="5" class="input" /></label>
          <label class="pol-check"><input v-model="s.verify.require_checks" type="checkbox" /> fehlschlagende Checks zählen als Fehler</label>
          <label class="is-wide">Dienste ignorieren (Komma-getrennt)<input v-model="ignoreText" class="input" /></label>
        </div>
      </section>

      <section class="card mb-4">
        <div class="card-header"><h2 class="card-title">Docker</h2></div>
        <div class="card-body rmm-form">
          <label class="pol-check"><input v-model="s.docker.enabled" type="checkbox" /> Container aktualisieren</label>
          <label class="pol-check"><input v-model="s.docker.cleanup_images" type="checkbox" /> altes Image danach entfernen</label>
          <label class="pol-check"><input v-model="s.docker.rollback_on_fail" type="checkbox" /> bei Fehler alten Container zurück</label>
          <label class="pol-check"><input v-model="s.docker.prefer_compose" type="checkbox" /> Compose-Projekte über docker compose</label>
          <label>Gesundheitsprüfung (Sekunden)<input v-model.number="s.docker.health_timeout" type="number" min="15" class="input" /></label>
          <label class="is-wide">Nur diese Container (Muster, Komma-getrennt, leer = alle)<input v-model="includeText" class="input" placeholder="web-*, nextcloud" /></label>
          <label class="is-wide">Nie diese Container<input v-model="excludeText" class="input" placeholder="*-db, gitlab" /></label>
        </div>
      </section>

      <section class="card mb-4">
        <div class="card-header"><h2 class="card-title">Benachrichtigung</h2></div>
        <div class="card-body rmm-form">
          <label class="pol-check"><input v-model="s.notify.approval_needed" type="checkbox" /> Freigabe nötig</label>
          <label class="pol-check"><input v-model="s.notify.run_failed" type="checkbox" /> Lauf fehlgeschlagen</label>
          <label class="pol-check"><input v-model="s.notify.run_succeeded" type="checkbox" /> Lauf erfolgreich</label>
          <div class="is-wide rmm-muted">Kommt in der App und an der Glocke an; Mail/Telegram/Webhook unter <router-link to="/alerts">Benachrichtigungen</router-link>.</div>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import { WEEKDAYS } from '../utils/rmm'

const auth = useAuthStore()
const policies = ref([])
const agents = ref([])
const defaults = ref({ settings: { rules: [] }, rule_fields: [], rule_ops: [] })
const form = ref(null)
const preview = ref(null)
const error = ref('')
const notice = ref('')
const busy = ref(false)

const s = computed(() => form.value?.settings)
const sites = computed(() => [...new Set(agents.value.map(a => a.site || 'Standard'))])
const listText = (obj, key) => computed({
  get: () => (obj()?.[key] || []).join(', '),
  set: v => { obj()[key] = v.split(',').map(x => x.trim()).filter(Boolean) },
})
const ignoreText = listText(() => s.value?.verify, 'ignore_services')
const includeText = listText(() => s.value?.docker, 'include')
const excludeText = listText(() => s.value?.docker, 'exclude')

function scopeLabel(p) {
  if (p.scope_type === 'global') return 'alle Rechner'
  if (p.scope_type === 'site') return `Standort ${p.scope_value}`
  return agents.value.find(a => a.agent_uid === p.scope_value)?.hostname || 'ein Rechner'
}
function windowLabel(st) {
  if (!st.window.enabled) return 'nur von Hand'
  const days = st.window.days.length === 7 ? 'täglich' : st.window.days.map(d => WEEKDAYS[d]).join(', ')
  return `${days} ${st.window.start} (${st.window.duration_minutes} min)`
}

async function load() {
  try {
    ;[policies.value, agents.value, defaults.value] = await Promise.all([
      auth.api('/api/patching/policies'), auth.api('/api/rmm/agents'), auth.api('/api/patching/policies/defaults')])
  } catch (e) { error.value = e.message }
}

async function open(p = null) {
  preview.value = null
  form.value = p ? JSON.parse(JSON.stringify(p)) : {
    name: '', description: '', enabled: true, priority: 100, scope_type: 'site', scope_value: sites.value[0] || 'Standard',
    settings: JSON.parse(JSON.stringify(defaults.value.settings)),
  }
  for (const r of form.value.settings.rules) r.enabled = r.enabled !== false
  if (p?.id) preview.value = await auth.api(`/api/patching/policies/${p.id}/preview`).catch(() => null)
}

function move(i, d) {
  const r = s.value.rules
  ;[r[i], r[i + d]] = [r[i + d], r[i]]
}

async function save() {
  busy.value = true
  try {
    const { id, builtin, created_at, updated_at, ...body } = form.value
    const saved = await auth.api(id ? `/api/patching/policies/${id}` : '/api/patching/policies', { method: id ? 'PUT' : 'POST', body })
    notice.value = `„${saved.name}“ gespeichert. Offene Updates wurden neu eingestuft.`
    error.value = ''
    await load()
    await open(policies.value.find(p => p.id === saved.id))
  } catch (e) { error.value = e.message } finally { busy.value = false }
}

async function remove(p) {
  if (!confirm(`Richtlinie „${p.name}“ löschen? Betroffene Rechner fallen auf die nächste passende Richtlinie zurück.`)) return
  try { await auth.api(`/api/patching/policies/${p.id}`, { method: 'DELETE' }); await load() } catch (e) { error.value = e.message }
}

onMounted(load)
</script>

<style scoped>
.pol-check { flex-direction: row !important; align-items: center; gap: .5rem !important; flex-wrap: wrap; }
.pol-check span { display: inline-flex; gap: .25rem; align-items: center; margin-right: .5rem; }
</style>
