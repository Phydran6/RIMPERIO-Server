<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Patchmanagement -> DB-Updates: Datenbank-Container
                   mit Sicherung, Probelauf, KI-Pruefung, Freigabe und Rueckweg.
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">DB-Updates mit Probelauf</h1>
        <p class="page-subtitle">
          Datenbanken werden nie blind aktualisiert: Analyse → Sicherung → Probelauf mit dem neuen Image in einem
          Wegwerf-Container → Vergleich jeder Tabelle → (KI-Prüfung) → Freigabe → Ausführung mit Prüfung und
          automatischem Rückweg.
        </p>
      </div>
      <button class="btn btn-secondary btn-sm" :disabled="loading" @click="load">Aktualisieren</button>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>
    <p v-if="notice" class="rmm-note is-ok">{{ notice }}</p>

    <details class="card mb-4">
      <summary class="card-header" style="cursor:pointer"><h2 class="card-title">So funktioniert es</h2></summary>
      <div class="card-body">
        <ol class="db-steps">
          <li><strong>Analyse</strong> – Datenbank, Version alt/neu, Hauptversions-Sprung?, Größe, freier Platz, Zeilen je Tabelle als Referenz.</li>
          <li><strong>Sicherung</strong> – logischer Abzug (pg_dumpall, mariadb-dump, mongodump, RDB) auf dem Rechner unter <code>/opt/RMM/rimperio/var/db-backups</code>.</li>
          <li><strong>Probelauf</strong> – ein Wegwerf-Container mit dem <em>neuen</em> Image bekommt den Abzug eingespielt, dann wird jede Tabelle gezählt. Die echte Datenbank bleibt unberührt.</li>
          <li><strong>Prüfung</strong> – Zeilenzahlen und Fehler beim Einspielen. Passen die Daten nicht ganz zur neuen Version, kommen hier <em>Anpassungen</em> (SQL bzw. JS) ins Spiel – von Hand oder als KI-Vorschlag – und der Probelauf wird wiederholt, bis er sauber ist.</li>
          <li><strong>Freigabe</strong> – Hauptversions-Sprünge immer von Hand (auch in der App), kleine Updates nach sauberem Probelauf auf Wunsch automatisch.</li>
          <li><strong>Ausführung</strong> – optional Proxmox-Snapshot; bei Hauptversion: Projekt anhalten, letzter Abzug, Datenverzeichnis archivieren, neues Image auf leerem Verzeichnis, Abzug einspielen, Anpassungen, Zählen, Projekt starten.</li>
          <li><strong>Rückweg</strong> – schlägt nach dem Leeren etwas fehl: automatisch altes Datenverzeichnis + altes Image zurück.</li>
        </ol>
      </div>
    </details>

    <section class="card mb-4">
      <div class="card-header"><h2 class="card-title">Datenbank-Container</h2></div>
      <div class="card-body">
        <div class="rmm-table-wrap">
          <table class="table">
            <thead><tr><th>Rechner</th><th>Container</th><th>Image</th><th>Update</th><th>Vorgang</th><th></th></tr></thead>
            <tbody>
              <tr v-if="!candidates.length"><td colspan="6" class="rmm-muted">Keine Datenbank-Container gefunden (Docker-Suche der Rechner abwarten).</td></tr>
              <tr v-for="c in candidates" :key="c.agent_uid + c.container">
                <td>{{ c.hostname }}</td>
                <td><strong>{{ c.container }}</strong><br><span class="rmm-muted">{{ c.engine }} · {{ c.state }}</span></td>
                <td class="rmm-code">{{ c.image }}</td>
                <td>
                  <span v-if="c.update_available" class="badge badge-warning">verfügbar</span>
                  <span v-else class="rmm-muted">aktuell</span>
                  <span v-if="c.local_version || c.remote_version" class="rmm-muted"><br>{{ c.local_version || '?' }} → {{ c.remote_version || '?' }}</span>
                </td>
                <td>
                  <a v-if="c.migration" href="#" @click.prevent="open(c.migration.id)">#{{ c.migration.id }} {{ c.migration.status_label }}</a>
                </td>
                <td class="db-actions">
                  <template v-if="auth.isAdmin && !c.migration">
                    <input v-model="targets[c.agent_uid + c.container]" class="input input-sm" placeholder="Ziel-Image (leer = gleicher Tag)" />
                    <button class="btn btn-primary btn-sm" @click="start(c)">Prüfen &amp; Probelauf</button>
                  </template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="card mb-4">
      <div class="card-header"><h2 class="card-title">Vorgänge</h2></div>
      <div class="card-body">
        <div class="rmm-table-wrap">
          <table class="table">
            <thead><tr><th>#</th><th>Rechner / Container</th><th>Version</th><th>Status</th><th>Seit</th></tr></thead>
            <tbody>
              <tr v-if="!items.length"><td colspan="5" class="rmm-muted">Noch keine DB-Updates.</td></tr>
              <tr v-for="m in items" :key="m.id" :class="{ 'patch-row-active': current?.id === m.id }" style="cursor:pointer" @click="open(m.id)">
                <td>{{ m.id }}</td>
                <td>{{ m.hostname }} / <strong>{{ m.container }}</strong><br><span class="rmm-muted">{{ m.engine }} · {{ m.mode === 'auto' ? 'aus Patch-Lauf' : 'von Hand' }}</span></td>
                <td>{{ m.old_version || '?' }} → {{ m.new_version || '?' }}<br>
                  <span v-if="m.path" class="badge" :class="m.path === 'major' ? 'badge-warning' : 'badge-neutral'">{{ m.path === 'major' ? 'Hauptversion' : 'gleiche Hauptversion' }}</span></td>
                <td><span class="badge" :class="badge(m.status)">{{ m.status_label }}</span></td>
                <td>{{ fmtAgo(m.stage_since) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <section v-if="current" class="card">
      <div class="card-header">
        <h2 class="card-title">#{{ current.id }} · {{ current.hostname }} / {{ current.container }}
          <span class="badge" :class="badge(current.status)">{{ current.status_label }}</span></h2>
        <div v-if="auth.isAdmin" class="rmm-actions">
          <button v-if="current.can_approve" class="btn btn-primary btn-sm" @click="approve(false)">Freigeben &amp; ausführen</button>
          <button v-if="current.status === 'dryrun_failed'" class="btn btn-danger btn-sm" @click="approve(true)">Trotzdem freigeben</button>
          <button v-if="current.can_rerun" class="btn btn-secondary btn-sm" @click="rerun">Probelauf wiederholen</button>
          <button v-if="aiFixups.length && current.can_rerun" class="btn btn-secondary btn-sm" @click="adoptAi">KI-Anpassungen übernehmen &amp; Probelauf</button>
          <button v-if="current.can_rollback" class="btn btn-danger btn-sm" @click="rollback">Rückweg</button>
          <button v-if="current.can_cancel" class="btn btn-ghost btn-sm" @click="cancel">Abbrechen</button>
        </div>
      </div>
      <div class="card-body">
        <dl class="rmm-grid mb-4">
          <div><dt>Datenbank</dt><dd>{{ current.engine }}</dd></div>
          <div><dt>Version</dt><dd>{{ current.old_version || '?' }} → {{ current.new_version || '?' }}</dd></div>
          <div><dt>Weg</dt><dd>{{ current.path === 'major' ? 'Hauptversion: Abzug → neu einspielen' : 'gleiche Hauptversion: Container-Update' }}</dd></div>
          <div><dt>Daten</dt><dd>{{ fmtBytes(current.data_size) }}</dd></div>
          <div><dt>Image</dt><dd class="rmm-code">{{ current.plan?.image }}</dd></div>
          <div><dt>Ziel</dt><dd class="rmm-code">{{ current.plan?.target }}</dd></div>
          <div><dt>Compose</dt><dd>{{ current.plan?.project || '—' }}</dd></div>
          <div><dt>Sicherungen</dt><dd class="rmm-code">{{ current.plan?.backup_path || '—' }}</dd></div>
        </dl>

        <p v-if="current.error" class="rmm-note is-error">{{ current.error }}</p>
        <p v-if="(current.report?.problems || []).length" class="rmm-note is-error">{{ current.report.problems.join(' ') }}</p>

        <template v-for="step in ['dryrun', 'verify']" :key="step">
          <div v-if="current.report?.[step]" class="mb-4">
            <h3 class="card-title mb-2">{{ step === 'dryrun' ? 'Probelauf' : 'Prüfung nach dem Update' }}</h3>
            <p v-if="current.report[step].fail" class="rmm-note is-error">{{ current.report[step].fail }}</p>
            <p v-if="current.report[step].compare" class="rmm-muted">
              Tabellen {{ current.report[step].compare.tables_before }} → {{ current.report[step].compare.tables_after }},
              Zeilen {{ fmtNum(current.report[step].compare.rows_before) }} → {{ fmtNum(current.report[step].compare.rows_after) }}
              <span v-if="current.report[step].compare.ok" class="badge badge-success">identisch</span>
              <span v-else class="badge badge-danger">Abweichung</span>
            </p>
            <ul v-if="current.report[step].compare?.missing?.length" class="db-list">
              <li v-for="t in current.report[step].compare.missing" :key="t">fehlt: <code>{{ t }}</code></li>
            </ul>
            <ul v-if="current.report[step].compare?.diffs?.length" class="db-list">
              <li v-for="d in current.report[step].compare.diffs" :key="d.table"><code>{{ d.table }}</code>: {{ fmtNum(d.before) }} → {{ fmtNum(d.after) }}</li>
            </ul>
            <details v-if="(current.report[step].errors || []).length">
              <summary class="rmm-muted">{{ (current.report[step].real_errors || []).length }} echte Fehler, {{ current.report[step].errors.length - (current.report[step].real_errors || []).length }} harmlose Meldungen</summary>
              <pre class="rmm-pre">{{ current.report[step].errors.join('\n') }}</pre>
            </details>
          </div>
        </template>

        <div v-if="current.report?.ai" class="mb-4">
          <h3 class="card-title mb-2">KI-Einschätzung</h3>
          <p v-if="current.report.ai.error" class="rmm-muted">{{ current.report.ai.error }}</p>
          <p v-else>
            <span class="badge" :class="{ go: 'badge-success', caution: 'badge-warning', stop: 'badge-danger' }[current.report.ai.recommendation] || 'badge-neutral'">
              {{ { go: 'unbedenklich', caution: 'mit Vorsicht', stop: 'nicht ausführen' }[current.report.ai.recommendation] || current.report.ai.recommendation }}</span>
            {{ current.report.ai.summary }}
          </p>
          <ul v-if="current.report.ai.risks?.length" class="db-list"><li v-for="r in current.report.ai.risks" :key="r">{{ r }}</li></ul>
          <div v-for="(f, i) in aiFixups" :key="i" class="mb-2">
            <span class="rmm-muted">Vorschlag {{ i + 1 }}{{ f.database ? ' (' + f.database + ')' : '' }}: {{ f.note }}</span>
            <pre class="rmm-pre">{{ f.code }}</pre>
          </div>
        </div>

        <div v-if="auth.isAdmin && current.can_rerun" class="mb-4">
          <h3 class="card-title mb-2">Anpassungen nach dem Einspielen</h3>
          <p class="rmm-muted mb-2">Werden im Probelauf <em>und</em> bei der Ausführung direkt nach dem Einspielen ausgeführt
            ({{ current.engine === 'mongo' ? 'JavaScript in mongosh' : 'SQL' }}). Danach den Probelauf wiederholen.</p>
          <div v-for="(f, i) in fixups" :key="i" class="db-fixup">
            <input v-model="f.database" class="input input-sm" placeholder="Datenbank (optional)" />
            <textarea v-model="f.code" class="input" rows="3" placeholder="z. B. ALTER TABLE … ;" />
            <button class="btn btn-ghost btn-sm" @click="fixups.splice(i, 1)">Entfernen</button>
          </div>
          <button class="btn btn-ghost btn-sm" @click="fixups.push({ code: '', database: '', note: '' })">+ Anpassung</button>
        </div>

        <h3 class="card-title mb-2">Verlauf</h3>
        <ol class="patch-log">
          <li v-for="(l, i) in current.log" :key="i" :class="'is-' + l.level"><span class="rmm-muted">{{ fmtTime(l.ts) }}</span> {{ l.text }}</li>
        </ol>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { fmtAgo, fmtTime } from '../utils/rmm'

const auth = useAuthStore()
const route = useRoute()
const loading = ref(false)
const error = ref('')
const notice = ref('')
const candidates = ref([])
const items = ref([])
const current = ref(null)
const targets = ref({})
const fixups = ref([])
let timer = null

const ACTIVE = ['analyzing', 'backing_up', 'dryrun', 'snapshotting', 'final_backup', 'archiving', 'updating',
  'restoring', 'verifying', 'rolling_back', 'approved']
const aiFixups = computed(() => current.value?.report?.ai?.fixups || [])

function badge(s) {
  if (s === 'succeeded') return 'badge-success'
  if (['failed', 'dryrun_failed'].includes(s)) return 'badge-danger'
  if (['awaiting_approval', 'rolled_back', 'rolling_back'].includes(s)) return 'badge-warning'
  if (ACTIVE.includes(s)) return 'badge-primary'
  return 'badge-neutral'
}
const fmtNum = (n) => (n ?? 0).toLocaleString('de-DE')
function fmtBytes(n) {
  if (!n) return '–'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++ }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`
}

async function run(fn, ok = '') {
  error.value = ''
  notice.value = ''
  try {
    await fn()
    if (ok) notice.value = ok
  } catch (e) {
    error.value = e.message
  }
}

async function load() {
  loading.value = true
  await run(async () => {
    const d = await auth.api('/api/db-updates')
    candidates.value = d.candidates
    items.value = d.items
    if (current.value) await open(current.value.id, true)
  })
  loading.value = false
}

async function open(id, silent = false) {
  const d = await auth.api(`/api/db-updates/${id}`)
  if (!silent || current.value?.id !== id) fixups.value = JSON.parse(JSON.stringify(d.plan?.fixups || []))
  current.value = d
}

async function start(c) {
  await run(async () => {
    const m = await auth.api('/api/db-updates', { method: 'POST', body: {
      agent_uid: c.agent_uid, container: c.container, target_image: targets.value[c.agent_uid + c.container] || '',
    } })
    await load()
    await open(m.id)
  }, 'Analyse gestartet – Sicherung und Probelauf folgen automatisch.')
}

async function approve(force) {
  const m = current.value
  let confirmWord = ''
  if (m.path === 'major') {
    confirmWord = prompt(`Hauptversions-Update von ${m.container} (${m.old_version} → ${m.new_version}).\n\nDie Anwendung im selben Compose-Projekt wird kurz angehalten. Abzug, Archiv und Rückweg sind vorbereitet.\n\nZur Bestätigung MIGRATE eingeben:`)
    if (confirmWord !== 'MIGRATE') return
  } else if (!confirm(`${m.container} jetzt aktualisieren? Vorher wird erneut gesichert.`)) {
    return
  }
  await run(async () => {
    current.value = await auth.api(`/api/db-updates/${m.id}/approve`, { method: 'POST', body: { force, confirm: confirmWord } })
    await load()
  }, 'Freigegeben – die Ausführung läuft.')
}
async function rerun() {
  await run(async () => {
    current.value = await auth.api(`/api/db-updates/${current.value.id}/dryrun`, { method: 'POST',
      body: { fixups: fixups.value.filter(f => f.code.trim()) } })
    await load()
  }, 'Probelauf läuft.')
}
async function adoptAi() {
  await run(async () => {
    current.value = await auth.api(`/api/db-updates/${current.value.id}/adopt-ai`, { method: 'POST' })
    fixups.value = JSON.parse(JSON.stringify(current.value.plan?.fixups || []))
    await load()
  }, 'KI-Anpassungen übernommen, Probelauf läuft.')
}
async function rollback() {
  if (prompt('Datenverzeichnis und Image auf den Stand vor dem Update zurücksetzen? Änderungen seitdem gehen verloren.\n\nROLLBACK eingeben:') !== 'ROLLBACK') return
  await run(async () => {
    current.value = await auth.api(`/api/db-updates/${current.value.id}/rollback`, { method: 'POST', body: { confirm: 'ROLLBACK' } })
    await load()
  }, 'Rückweg läuft.')
}
async function cancel() {
  if (!confirm('Vorgang abbrechen? Die Datenbank bleibt, wie sie ist.')) return
  await run(async () => {
    current.value = await auth.api(`/api/db-updates/${current.value.id}/cancel`, { method: 'POST' })
    await load()
  })
}

onMounted(async () => {
  await load()
  if (route.query.id) await run(() => open(Number(route.query.id)))
  timer = setInterval(() => {
    if (current.value && ACTIVE.includes(current.value.status)) load()
    else if (items.value.some(m => ACTIVE.includes(m.status))) load()
  }, 5000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.db-steps { padding-left: 1.25rem; display: grid; gap: .35rem; font-size: .875rem; }
.db-actions { display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; }
.db-actions .input-sm, .db-fixup .input-sm { max-width: 16rem; }
.db-list { padding-left: 1.25rem; font-size: .8125rem; }
.db-fixup { display: grid; gap: .5rem; margin-bottom: .75rem; }
.input-sm { padding: .25rem .5rem; font-size: .8125rem; }
.patch-log { margin: 0; padding-left: 1.2rem; font-size: .85rem; display: flex; flex-direction: column; gap: .2rem; }
.patch-log .is-error { color: var(--color-danger); }
.patch-log .is-warning { color: var(--color-warning); }
.patch-row-active { background: var(--color-surface); }
</style>
