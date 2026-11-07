<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.08.14.12.00.00
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Updates: Stand gegen GitHub prüfen, einspielen,
                   zurückfallen. Inklusive Warnung zum Datenbestand.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">Updates</h2>
        <p class="page-subtitle">
          Vergleicht den installierten Stand mit dem Stand auf GitHub und spielt ihn auf Wunsch ein.
        </p>
      </div>
      <button class="btn btn-secondary btn-sm" :disabled="checking || isRunning" @click="check(true)">
        <AppIcon name="refresh" :size="16" :class="checking ? 'spin' : ''" />
        {{ checking ? 'Prüfe…' : 'Auf Updates prüfen' }}
      </button>
    </div>

    <p v-if="error" class="note note-fail">{{ error }}</p>

    <!-- ================================================================
         TERMINAL
         Wie die Upgrade-Konsole in Proxmox: Die Befehlszeile steht fertig
         da, Enter startet sie, und man sieht live, was auf dem Server
         passiert. Bewusst KEINE echte Shell - das Update baut genau den
         Container neu, in dem eine solche Shell laufen wuerde, und risse sie
         mitten im Lauf ab. Der Lauf selbst haengt am Host (systemd-run), die
         Ausgabe kommt per Server-Sent Events und ueberlebt den Neubau.
         ================================================================ -->
    <div class="term mb-4" :class="{ 'is-running': isRunning }">
      <div class="term-bar">
        <span class="term-dots" aria-hidden="true"><i /><i /><i /></span>
        <span class="term-title">{{ termTitle }}</span>
        <span v-if="isRunning" class="term-chip" :class="liveConnected ? 'is-live' : 'is-off'">
          {{ liveConnected ? 'live' : (offline ? 'Server baut neu…' : 'verbinde…') }}
        </span>
        <label class="term-follow">
          <input v-model="followOutput" type="checkbox">
          mitscrollen
        </label>
      </div>
      <div v-if="isRunning" class="term-progress">
        <div :style="{ width: (run?.progress || 0) + '%' }" />
      </div>

      <div
        ref="liveBox"
        class="term-body"
        tabindex="0"
        role="log"
        aria-label="Update-Terminal"
        @keydown="onTermKey"
        @focus="termFocused = true"
        @blur="termFocused = false"
      >
        <template v-if="historyLines.length">
          <div class="term-line term-muted"># ── Protokoll des letzten Laufs ──</div>
          <div v-for="(line, i) in historyLines" :key="'h' + i" class="term-line term-muted">{{ line }}</div>
          <div class="term-line">&nbsp;</div>
        </template>
        <div v-for="(line, i) in echoLines" :key="'e' + i" class="term-line" :class="line.cls">
          <span v-if="line.prompt" class="term-prompt">{{ line.prompt }}</span>{{ line.text }}
        </div>
        <div v-for="(line, i) in liveLines" :key="'l' + i" class="term-line">{{ line }}</div>

        <!-- Laufender Vorgang: Ausgabe kommt gleich -->
        <div v-if="isRunning && !liveLines.length" class="term-line term-muted">
          Warte auf die erste Ausgabe vom Server…
        </div>

        <!-- Ergebnis, sobald der Lauf durch ist -->
        <div v-if="!isRunning && finishedLine" class="term-line" :class="finishedLine.cls">{{ finishedLine.text }}</div>

        <!-- Eingabezeile -->
        <template v-if="!isRunning && !starting">
          <div v-if="termMode === 'ask'" class="term-line term-warn">
            Vorher sichern (RIMPERIO-Sicherung + Datenbank-Abzug)? [J/n] {{ termAnswer }}<span class="term-cursor" :class="{ 'is-on': termFocused }" />
          </div>
          <div v-else class="term-line">
            <span class="term-prompt">{{ promptText }}</span>{{ termBlocked ? '' : commandText }}<span class="term-cursor" :class="{ 'is-on': termFocused }" />
          </div>
        </template>
        <div v-else-if="starting" class="term-line term-muted">Wird gestartet…</div>
      </div>

      <div class="term-foot">
        <span class="term-hint">
          <template v-if="isRunning && offline">
            Der Server baut gerade neu und ist kurz weg. Das ist normal, die Ausgabe läuft gleich weiter.
          </template>
          <template v-else-if="isRunning">
            {{ run?.step || 'Vorbereitung' }} – bitte das Fenster offen lassen.
          </template>
          <template v-else-if="termBlocked">{{ termBlocked }}</template>
          <template v-else-if="termMode === 'ask'">J + Enter = mit Sicherung · n + Enter = ohne · Esc = abbrechen</template>
          <template v-else>Ins Terminal klicken und <kbd>Enter</kbd> drücken, um das Update zu starten.</template>
        </span>
        <span class="term-actions">
          <template v-if="!isRunning && !starting && termMode === 'ask'">
            <button class="btn btn-primary btn-sm" @click="answerAsk('j')">Ja, sichern</button>
            <button class="btn btn-secondary btn-sm" @click="answerAsk('n')">Ohne</button>
            <button class="btn btn-ghost btn-sm" @click="cancelAsk">Abbrechen</button>
          </template>
          <button
            v-else-if="!isRunning && !starting && !termBlocked"
            class="btn btn-primary btn-sm"
            @click="beginAsk"
          >
            ⏎ Enter
          </button>
          <button
            v-if="!isRunning && run && (run.status === 'success' || run.status === 'failed')"
            class="btn btn-secondary btn-sm"
            @click="reloadPage"
          >
            Seite neu laden
          </button>
        </span>
      </div>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <!-- ==============================================================
           STAND
           ============================================================== -->
      <div class="card lg:col-span-2">
        <div class="card-header">
          <span class="card-title">Versionsstand</span>
          <span v-if="status" class="badge" :class="stateBadge">
            {{ stateLabel }}
          </span>
        </div>
        <div class="card-body space-y-2 text-sm">
          <div class="info-row">
            <span>Installiert</span>
            <span class="font-mono">{{ status?.local?.version || '–' }}</span>
          </div>
          <div v-if="status?.local?.commit_short" class="info-row">
            <span>Stand (Commit)</span>
            <span class="font-mono">{{ status.local.commit_short }} · {{ formatTime(status.local.commit_date) }}</span>
          </div>
          <div class="info-row">
            <span>Verzeichnis auf dem Server</span>
            <span class="font-mono">{{ status?.local?.install_dir || '–' }}</span>
          </div>
          <div class="divider my-2" />
          <div class="info-row">
            <span>Auf GitHub</span>
            <span class="font-mono">
              {{ status?.remote?.version || status?.remote?.commit_short || '–' }}
            </span>
          </div>
          <div v-if="status?.remote?.commit_date" class="info-row">
            <span>Veröffentlicht</span>
            <span>{{ formatTime(status.remote.commit_date) }}</span>
          </div>
          <div v-if="status?.remote?.commit_message" class="info-row">
            <span>Letzte Änderung</span>
            <span class="truncate" :title="status.remote.commit_message">{{ status.remote.commit_message }}</span>
          </div>
          <!-- Gegen genau diesen Bezugspunkt wird geprueft - beim Kanal
               "Stabil" ist das das neueste Release-Tag, nicht der Zweig. -->
          <div class="info-row">
            <span>Geprüft gegen</span>
            <span class="font-mono">{{ status?.remote?.repo }}@{{ status?.target_ref || status?.remote?.branch }}</span>
          </div>

          <p v-if="status?.reason" class="text-xs pt-1" style="color: var(--color-text-muted)">
            {{ status.reason }}
          </p>

          <!-- Auf dem Zweig liegt schon mehr, nur ohne Release: sonst stuende
               hier "entspricht GitHub", obwohl GitHub laengst weiter ist. -->
          <div v-if="status?.unreleased" class="downgrade-note">
            <p class="font-semibold mb-1">
              Auf <span class="font-mono">{{ status.unreleased.branch }}</span> liegt schon
              <span class="font-mono">{{ status.unreleased.version }}</span> – noch ohne Release.
            </p>
            <p>
              Der Kanal <strong>{{ channelLabel }}</strong> wartet auf ein veröffentlichtes Release.
              Wer den Stand jetzt will: unten den Kanal auf
              <strong>Aktuell ({{ status.unreleased.branch }})</strong> stellen – oder auf GitHub ein
              Release dafür anlegen.
            </p>
          </div>

          <!-- Der Kanal zeigt auf einen aelteren Stand als den installierten.
               Meist fehlt einfach ein Release fuer den aktuellen Stand. Hier
               steht deshalb kein Update-Knopf, sondern was zu tun ist. -->
          <div v-if="status?.is_downgrade" class="downgrade-note">
            <p class="font-semibold mb-1">Einspielen wäre hier ein Rückschritt.</p>
            <p>
              Der eingestellte Kanal <strong>{{ channelLabel }}</strong> zeigt auf
              <span class="font-mono">{{ status?.remote?.version || status?.target_ref }}</span> —
              installiert ist aber schon
              <span class="font-mono">{{ status?.local?.version }}</span>.
            </p>
            <p class="mt-1">Zwei Wege:</p>
            <ul class="downgrade-list">
              <li>Auf GitHub ein Release für den aktuellen Stand anlegen — dann stimmt „Stabil“ wieder.</li>
              <li>Oder oben den Kanal auf <strong>Aktuell ({{ status?.remote?.branch }})</strong> stellen.</li>
            </ul>
          </div>
          <p v-if="status?.local?.note" class="note note-warn">{{ status.local.note }}</p>
          <p v-if="status && !status.can_update" class="note note-warn">
            Dieser Server lässt sich nicht über die Oberfläche aktualisieren – dem Backend fehlt
            der Zugriff auf den Host. Nutze dafür den Befehl weiter unten.
          </p>

          <div class="pt-2">
            <button
              class="btn btn-primary"
              :disabled="!canStart"
              @click="askUpdate"
              title="Startet das Update im Terminal oben"
            >
              <AppIcon name="download" :size="16" />
              {{ applyLabel }}
            </button>
          </div>
        </div>
      </div>

      <!-- ==============================================================
           SICHERUNGEN / RÜCKFALL
           ============================================================== -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Sicherungen</span>
          <span class="badge badge-neutral">{{ backups.length }}</span>
        </div>
        <div class="card-body space-y-3 text-sm">
          <p style="color: var(--color-text-muted)">
            Vor jedem Update wird der bisherige Stand gesichert. Der Rückfall spielt genau
            diese Sicherung wieder ein.
          </p>

          <div v-if="!backups.length" class="empty-state">
            <p class="empty-state-title">Noch keine Sicherung</p>
            <p class="text-sm" style="color: var(--color-text-muted)">
              Sie entsteht beim ersten Update über diese Seite.
            </p>
          </div>

          <div v-for="backup in backups" :key="backup.name" class="backup-row">
            <div class="min-w-0">
              <!-- Der Name ist ein Zeitstempel in Serverzeit (meist UTC).
                   Daneben steht die Uhrzeit in der Zeit dieses Browsers -
                   sonst liegen Name und "Update vom" scheinbar Stunden
                   auseinander. -->
              <p class="text-sm">
                <span class="font-medium">{{ backup.created_at ? formatTime(backup.created_at) : backup.name }}</span>
                <span v-if="backup.created_at" class="font-mono text-xs ml-1" style="color: var(--color-text-muted)">{{ backup.name }}</span>
              </p>
              <p class="text-xs" style="color: var(--color-text-muted)">
                Stand davor: {{ backup.version || 'ohne Versionsangabe' }}
                <template v-if="backup.size"> · {{ backup.size }}</template>
                · {{ backup.database_dump ? 'mit Datenbank' : 'ohne Datenbank' }}
              </p>
            </div>
            <button
              class="btn btn-secondary btn-sm"
              :disabled="!canStart"
              @click="askRollback(backup)"
            >
              Zurück
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- ================================================================
         WELCHER STAND? (Kanal und Release)
         ================================================================ -->
    <div class="card mt-4">
      <div class="card-header">
        <span class="card-title">Welchen Stand soll dieser Server fahren?</span>
      </div>
      <div class="card-body space-y-3">
        <p class="text-sm" style="color: var(--color-text-muted)">
          Nicht jeder will immer den letzten Commit. Hier steht, woran sich dieser
          Server hält — die Update-Prüfung oben richtet sich danach.
        </p>

        <div class="channel-grid">
          <label
            v-for="option in channels"
            :key="option.id"
            class="channel"
            :class="{ 'is-picked': channel.channel === option.id }"
          >
            <input v-model="channel.channel" type="radio" :value="option.id" @change="channelTouched = true">
            <span>
              <strong>{{ option.label }}</strong>
              <em>{{ option.hint }}</em>
            </span>
          </label>
        </div>

        <!-- Nur bei "festgelegte Version": welche denn? -->
        <div v-if="channel.channel === 'pinned'">
          <label class="label">Release oder Tag</label>
          <select v-if="releases.length" v-model="channel.ref" class="select">
            <option value="">– bitte wählen –</option>
            <option v-for="release in releases" :key="release.tag" :value="release.tag">
              {{ release.name }}{{ release.prerelease ? ' (Vorabversion)' : '' }}
            </option>
          </select>
          <input v-else v-model="channel.ref" type="text" class="input"
                 placeholder="z.B. v2026.08.14 oder ein Commit">
          <p class="text-xs mt-1" style="color: var(--color-text-muted)">
            Der Server bleibt auf diesem Stand stehen, bis hier etwas anderes gewählt wird.
          </p>
        </div>

        <div class="flex flex-wrap items-center gap-2">
          <button class="btn btn-primary btn-sm" :disabled="channelSaving" @click="saveChannel">
            {{ channelSaving ? 'Wird gespeichert…' : 'Übernehmen' }}
          </button>
          <span v-if="status?.target_ref" class="text-xs" style="color: var(--color-text-muted)">
            Zielpunkt: <code class="font-mono">{{ status.target_ref }}</code>
          </span>
        </div>

        <!-- Sofortmeldung: wie der Server von einem Push erfährt -->
        <div v-if="status?.webhook" class="webhook-box">
          <p class="text-sm font-semibold" style="color: var(--color-text-primary)">
            Sofort erfahren, wenn etwas gepusht wird
          </p>
          <p class="text-xs mt-1" style="color: var(--color-text-muted)">
            Dieser Server sieht von selbst alle
            {{ status.watcher?.interval_seconds || 120 }} Sekunden nach und meldet einen
            neuen Stand sofort in jedes offene Fenster.
            <template v-if="!status.webhook.configured">
              Noch schneller geht es mit einem Webhook — {{ status.webhook.hint }}
            </template>
            <template v-else>
              Der GitHub-Webhook ist eingerichtet: <code class="font-mono">{{ status.webhook.url || status.webhook.path }}</code>
            </template>
          </p>
        </div>
      </div>
    </div>

    <!-- ================================================================
         UPDATE PER KOMMANDOZEILE
         ================================================================ -->
    <div class="card mt-4">
      <div class="card-header"><span class="card-title">Update ohne Oberfläche</span></div>
      <div class="card-body">
        <p class="text-sm mb-2" style="color: var(--color-text-muted)">
          Derselbe Vorgang als Einzeiler – falls die Oberfläche nicht erreichbar ist:
        </p>
        <div class="flex items-center gap-2">
          <code class="oneliner">{{ status?.oneliner || '–' }}</code>
          <button class="btn btn-secondary btn-sm" :disabled="!status?.oneliner" @click="copyOneliner">
            {{ copied ? 'Kopiert' : 'Kopieren' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ================================================================
         RÜCKFRAGE VOR DEM EINGRIFF
         ================================================================ -->
    <div v-if="dialog" class="modal-backdrop" @click.self="dialog = null">
      <div class="modal max-w-lg">
        <div class="card-header">
          <span class="card-title">Wirklich zurückfallen?</span>
        </div>
        <div class="card-body space-y-3 text-sm overflow-y-auto">
          <div class="warn-box">
            <p class="font-semibold mb-1">Das kann Logdaten kosten.</p>
            <p>
              Normalerweise bleibt die Datenbank erhalten – sie liegt in einem eigenen
              Docker-Volume. Verlassen sollte man sich darauf nicht: ändert sich das
              Datenbankschema oder schlägt der Vorgang mitten im Lauf fehl, können Logs
              verloren gehen. <strong>Wer die Logs braucht, sichert sie vorher.</strong>
            </p>
          </div>

          <p>
            Wiederhergestellt wird die Sicherung
            <strong class="font-mono">{{ dialog.backup?.name }}</strong>
            <template v-if="dialog.backup?.version"> (Version {{ dialog.backup.version }})</template>.
            <template v-if="dialog.backup?.database_dump">
              Der mitgesicherte Datenbankstand wird dabei eingespielt – <strong>alle Logs, die
              seit dieser Sicherung dazugekommen sind, gehen dabei verloren.</strong>
            </template>
            <template v-else>
              Diese Sicherung enthält keinen Datenbankabzug – die Datenbank bleibt, wie sie ist.
            </template>
          </p>

          <label class="flex items-start gap-2 cursor-pointer">
            <input v-model="understood" type="checkbox" class="mt-1">
            <span>Ich habe verstanden, dass dabei Logdaten verloren gehen können.</span>
          </label>
        </div>
        <div class="modal-actions">
          <button class="btn btn-ghost" @click="dialog = null">Abbrechen</button>
          <button class="btn btn-danger" :disabled="!understood || starting" @click="confirmDialog">
            {{ starting ? 'Wird gestartet…' : 'Weiter' }}
          </button>
        </div>
      </div>
    </div>

    <!-- Letzter Schritt vor dem Eingriff: die Sicherungsfrage. Ohne beantwortete
         Frage weist der Server den Aufruf ab (backend app/guard.py). -->
    <BackupPrompt
      :open="promptOpen"
      :operation="promptOperation"
      :warning="promptWarning"
      :scopes="backupScopes"
      @confirm="startRun"
      @cancel="promptOpen = false"
    />
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import BackupPrompt from '../components/BackupPrompt.vue'

const auth = useAuthStore()

// Release-Auswahl und Sicherungsfrage
const releases = ref([])
// Die drei Kanaele stehen auch hier fest: die Auswahl darf nie davon abhaengen,
// ob die Release-Liste von GitHub gerade geladen werden konnte.
const FALLBACK_CHANNELS = [
  { id: 'stable', label: 'Stabil (letztes Release)', hint: 'Nur veröffentlichte Releases. Empfohlen für den Regelbetrieb.' },
  { id: 'edge', label: 'Aktuell (main)', hint: 'Jeder Push landet sofort als Angebot hier. Für Test- und Entwicklungsserver.' },
  { id: 'pinned', label: 'Festgelegte Version', hint: 'Ein bestimmtes Release oder Tag. Der Server bleibt darauf stehen.' },
]
const channels = ref(FALLBACK_CHANNELS)
const channel = ref({ channel: 'stable', ref: '', auto_offer: true })
const channelSaving = ref(false)
// Hat jemand einen Kanal angeklickt, aber noch nicht uebernommen, darf das
// Nachladen des Status die Auswahl nicht zuruecksetzen.
let channelTouched = false
const backupScopes = ref([])
const promptOpen = ref(false)
const promptOperation = ref('')
const promptWarning = ref('')

const status = ref(null)
const checking = ref(false)
const starting = ref(false)
const error = ref('')
const offline = ref(false)
const dialog = ref(null)
const understood = ref(false)
const copied = ref(false)

// Terminal: Eingabezustand und die Zeilen, die nicht vom Server kommen
// (eingegebener Befehl, Rueckfrage, Antwort) - wie in einer echten Shell
// bleiben sie im Verlauf stehen.
const termFocused = ref(false)
const termMode = ref('ready')          // 'ready' | 'ask'
const termAnswer = ref('')
const echoLines = ref([])              // { prompt?, text, cls? }
const historyLines = ref([])           // Protokoll des letzten Laufs beim Öffnen

// Mitlaufende Ausgabe des Wartungslaufs (Server-Sent Events).
const liveLines = ref([])
const liveConnected = ref(false)
const followOutput = ref(true)
const liveBox = ref(null)
let outputStream = null

// Wie viele Zeilen im Fenster stehen bleiben. Ein Neubau erzeugt schnell
// einige tausend - alle zu behalten macht den Browser langsam, ohne dass es
// jemandem hilft. Das vollstaendige Protokoll steht weiterhin unter
// „Protokoll anzeigen".
const MAX_LIVE_LINES = 2000

let pollTimer = null
let pendingSince = 0

// Zwischen "Start abgeschickt" und dem ersten Lebenszeichen des Skripts vergehen
// ein paar Sekunden. Solange gilt der Lauf als laufend - sonst zeigt die Seite
// noch das Ergebnis des *vorherigen* Laufs und hört zu früh auf zu warten.
const pendingStart = ref(false)
const pendingAction = ref('apply')
const PENDING_GRACE_MS = 45000

const serverRun = computed(() => status.value?.run || null)
const backups = computed(() => status.value?.backups || [])
const isRunning = computed(() => serverRun.value?.status === 'running' || pendingStart.value)

/** Was angezeigt wird: der echte Zustand, ersatzweise der Startvermerk. */
const run = computed(() => {
  if (serverRun.value?.status === 'running') return serverRun.value
  if (pendingStart.value) {
    return {
      status: 'running',
      action: pendingAction.value,
      step: 'Start',
      message: 'Der Wartungslauf wird auf dem Server gestartet…',
      progress: 2,
    }
  }
  return serverRun.value
})

const canStart = computed(() => (
  !!status.value?.can_update && !isRunning.value && !starting.value
  // Bei einem Rueckschritt bleibt der Knopf aus: der Server weist das ohnehin
  // ab (HTTP 409), und ein Knopf, der nur eine Fehlermeldung erzeugt, hilft
  // niemandem.
  && !status.value?.is_downgrade
))

/** Drei Zustaende statt zwei: hinterher, gleichauf, oder voraus. */
const stateBadge = computed(() => {
  if (status.value?.is_downgrade) return 'badge-neutral'
  return status.value?.update_available ? 'badge-warning' : 'badge-success'
})

const stateLabel = computed(() => {
  if (status.value?.is_downgrade) return 'Server ist voraus'
  return status.value?.update_available ? 'Update verfügbar' : 'aktuell'
})

const applyLabel = computed(() => (
  status.value?.update_available ? 'Update einspielen' : 'Neu installieren (gleicher Stand)'
))

const channelLabel = computed(() => {
  const key = status.value?.channel?.channel
  return channels.value.find(c => c.id === key)?.label || key || '—'
})

const promptText = computed(() => {
  const host = (typeof window !== 'undefined' && window.location.hostname) || 'rimperio'
  return `root@${host}:${status.value?.install_dir || '/opt/rimperio'}# `
})

/** Genau die Zeile, die nach dem Enter auf dem Host läuft (kommt vom Server). */
const commandText = computed(() => (status.value?.command || []).join(' '))

/** Warum gerade nichts gestartet werden kann - oder '' wenn es geht. */
const termBlocked = computed(() => {
  if (!status.value) return 'Lade den Stand vom Server…'
  if (!status.value.can_update) {
    return 'Dem Backend fehlt der Zugriff auf den Host – Update nur per Einzeiler (unten).'
  }
  if (status.value.is_downgrade) return 'Einspielen wäre ein Rückschritt – siehe Hinweis unter „Versionsstand“.'
  if (!commandText.value) return 'Der Server kennt diese Funktion noch nicht – bitte einmal per Einzeiler aktualisieren.'
  return ''
})

const termTitle = computed(() => {
  if (isRunning.value) {
    const what = run.value?.action === 'rollback' ? 'Rückfall' : 'Update'
    return `${what} läuft – ${run.value?.step || 'Vorbereitung'} (${run.value?.progress || 0} %)`
  }
  return 'Update-Konsole'
})

/** Abschlusszeile nach einem Lauf, so wie eine Shell den Exit-Status meldet. */
const finishedLine = computed(() => {
  const r = serverRun.value
  if (!r || !['success', 'failed'].includes(r.status)) return null
  // Nur zeigen, wenn es in diesem Fenster etwas zu dem Lauf gibt - sonst
  // stuende eine alte Erfolgsmeldung ueber einer frischen Eingabezeile.
  if (!liveLines.value.length && !historyLines.value.length) return null
  const ok = r.status === 'success'
  return {
    cls: ok ? 'term-ok' : 'term-fail',
    text: `${ok ? '✔ Fertig' : '✘ Fehlgeschlagen'}: ${r.message || ''}`
      + (r.rolled_back ? ' (auf den vorherigen Stand zurückgesetzt)' : ''),
  }
})

onMounted(async () => {
  await load()
  loadReleases()
  loadBackupScopes()
  // Wie beim Öffnen einer Konsole: der letzte Lauf steht noch im Verlauf.
  if (!isRunning.value) loadLog()
})

onUnmounted(() => {
  stopPolling()
  stopOutputStream()
})

// Sobald ein Lauf beginnt, haengt sich die Seite an die Ausgabe - und loest
// sich wieder, wenn er durch ist. Ohne das liefe ein offener Kanal weiter,
// an dem niemand mehr haengt.
watch(isRunning, (running) => {
  if (running) startOutputStream()
  else stopOutputStream()
}, { immediate: true })

async function load(force = false) {
  try {
    status.value = await auth.api(`/api/updates/status${force ? '?force=true' : ''}`)
    offline.value = false
    error.value = ''
    // Gespeicherten Kanal anhaken - auch wenn die Release-Liste (GitHub)
    // gerade nicht zu laden war. Nur solange nichts geaendert, aber noch
    // nicht uebernommen wurde, bleibt die Auswahl des Nutzers stehen.
    if (status.value?.channel && !channelSaving.value && !channelTouched) {
      channel.value = { ...status.value.channel }
    }
    if (isRunning.value) {
      startPolling()
      startOutputStream()
    }
  } catch (e) {
    error.value = e.message || 'Der Update-Stand konnte nicht geladen werden.'
  }
}

async function check(force) {
  checking.value = true
  try {
    await load(force)
  } finally {
    checking.value = false
  }
}

function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(poll, 3000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

/** Während des Updates ist der Server zeitweise weg - das ist kein Fehler. */
async function poll() {
  try {
    status.value = await auth.api('/api/updates/status')
    offline.value = false

    // Das Skript hat sich gemeldet - der Startvermerk hat seinen Zweck erfüllt.
    if (serverRun.value?.status === 'running') {
      pendingStart.value = false
      return
    }

    // Kein "läuft" mehr, aber vielleicht schon ein Ergebnis dieses Laufs -
    // ein Vorlauf kann auch sofort scheitern (falsches Verzeichnis o. ä.).
    if (pendingStart.value) {
      const stamp = Date.parse(serverRun.value?.updated_at || '')
      const fresh = !Number.isNaN(stamp) && stamp >= pendingSince - 5000
      if (!fresh && Date.now() - pendingSince < PENDING_GRACE_MS) return
      pendingStart.value = false
      if (!fresh) {
        error.value = 'Der Wartungslauf hat sich nicht gemeldet. Bitte das Protokoll ansehen.'
      }
    }

    stopPolling()
    await loadLog()
  } catch {
    // Server gerade weg (Neubau läuft) - weiter warten.
    offline.value = true
  }
}

/** Der Knopf unter „Versionsstand" führt ins Terminal - dort läuft das Update. */
function askUpdate() {
  liveBox.value?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  liveBox.value?.focus({ preventScroll: true })
  beginAsk()
}

// =============================================================================
// Terminal
// =============================================================================
function onTermKey(event) {
  if (isRunning.value || starting.value) return
  const key = event.key
  if (termMode.value === 'ready') {
    if (key === 'Enter') {
      event.preventDefault()
      beginAsk()
    }
    return
  }
  // Rueckfrage: einzelne Zeichen sammeln wie an einer echten Eingabeaufforderung.
  if (key === 'Enter') {
    event.preventDefault()
    answerAsk()
  } else if (key === 'Escape' || (event.ctrlKey && key.toLowerCase() === 'c')) {
    event.preventDefault()
    cancelAsk()
  } else if (key === 'Backspace') {
    event.preventDefault()
    termAnswer.value = termAnswer.value.slice(0, -1)
  } else if (key.length === 1 && !event.ctrlKey && !event.metaKey && termAnswer.value.length < 4) {
    event.preventDefault()
    termAnswer.value += key
  }
}

function echo(text, cls = '', prompt = '') {
  echoLines.value = [...echoLines.value, { text, cls, prompt }]
  scrollToEnd()
}

function beginAsk() {
  if (isRunning.value || starting.value || termMode.value === 'ask') return
  echo(termBlocked.value ? '' : commandText.value, '', promptText.value)
  if (termBlocked.value) {
    echo(`# ${termBlocked.value}`, 'term-fail')
    return
  }
  const from = status.value?.local?.version || status.value?.local?.commit_short || '?'
  const to = status.value?.remote?.version || status.value?.remote?.commit_short || status.value?.target_ref || '?'
  echo(`Stand: ${from} → ${to}  (${status.value?.target_ref || status.value?.remote?.branch || ''})`, 'term-muted')
  echo('Alle Container werden neu gebaut. Die Oberfläche ist dabei ein paar Minuten weg.', 'term-warn')
  echo('Normalerweise bleiben die Logs erhalten – verlassen sollte man sich darauf nicht.', 'term-warn')
  termAnswer.value = ''
  termMode.value = 'ask'
  scrollToEnd()
}

function cancelAsk() {
  echo(`Vorher sichern (RIMPERIO-Sicherung + Datenbank-Abzug)? [J/n] ${termAnswer.value}^C`, 'term-warn')
  echo('Abgebrochen.', 'term-muted')
  termAnswer.value = ''
  termMode.value = 'ready'
}

async function answerAsk(forced) {
  const raw = (forced ?? termAnswer.value).trim().toLowerCase()
  echo(`Vorher sichern (RIMPERIO-Sicherung + Datenbank-Abzug)? [J/n] ${forced ?? termAnswer.value}`, 'term-warn')
  termAnswer.value = ''
  let withBackup
  if (['', 'j', 'ja', 'y', 'yes'].includes(raw)) withBackup = true
  else if (['n', 'nein', 'no'].includes(raw)) withBackup = false
  else {
    echo('Bitte „j" oder „n" eingeben.', 'term-muted')
    return
  }
  termMode.value = 'ready'
  echo(withBackup
    ? 'Sicherung wird vorher angelegt, dann startet das Update…'
    : 'Ohne Sicherung – das Update startet…', 'term-muted')
  // Die Sicherungsfrage ist serverseitig Pflicht (app/guard.py): die Antwort
  // hier ist genau diese Entscheidung, nur eben im Terminal gestellt.
  await launch('update', { create: withBackup }, { databaseBackup: withBackup })
}

function askRollback(backup) {
  understood.value = false
  dialog.value = { mode: 'rollback', backup }
}

/**
 * Der alte Dialog erklaert nur noch, was passiert. Losgeschickt wird erst nach
 * der Sicherungsfrage - die ist Pflicht und wird auch serverseitig verlangt.
 */
function confirmDialog() {
  if (!dialog.value || !understood.value) return
  const mode = dialog.value.mode
  promptOperation.value = mode === 'update'
    ? `Update auf ${status.value?.remote?.version || status.value?.remote?.commit_short || 'den neuen Stand'}`
    : `Rückfall auf ${dialog.value.backup?.name || 'die letzte Sicherung'}`
  promptWarning.value = mode === 'rollback' && dialog.value.backup?.database_dump
    ? 'Der mitgesicherte Datenbankstand wird eingespielt — alle Logs seit dieser Sicherung gehen verloren.'
    : ''
  promptOpen.value = true
}

/** Rückfall: startet, sobald die Sicherungsfrage beantwortet ist. */
async function startRun(decision) {
  promptOpen.value = false
  if (!dialog.value) return
  const backupName = dialog.value.backup?.name || ''
  dialog.value = null
  echo(`${commandText.value.replace(/ apply .*$/, ' rollback')}${backupName ? ' --backup ' + backupName : ''}`,
    '', promptText.value)
  await launch('rollback', decision, { backupName })
}

/** Schickt Update oder Rückfall los. Die Ausgabe landet im Terminal. */
async function launch(mode, decision, { databaseBackup = true, backupName = '' } = {}) {
  starting.value = true
  error.value = ''
  try {
    if (mode === 'update') {
      await auth.api('/api/updates/apply', {
        method: 'POST',
        body: {
          confirm: 'UPDATE',
          database_backup: databaseBackup,
          ref: status.value?.target_ref || '',
          backup: decision,
        },
      })
    } else {
      await auth.api('/api/updates/rollback', {
        method: 'POST',
        body: {
          confirm: 'ROLLBACK',
          // Die Sicherung des Wartungsskripts auf dem Host - nicht zu
          // verwechseln mit der ZIP-Sicherung aus der Rueckfrage oben.
          backup_dir: backupName,
          backup: decision,
        },
      })
    }
    // Neuer Lauf, neue Ausgabe - der alte Verlauf hat ausgedient.
    historyLines.value = []
    liveLines.value = []
    // Der Lauf braucht einen Moment, bis er den ersten Zustand schreibt.
    pendingAction.value = mode === 'update' ? 'apply' : 'rollback'
    pendingSince = Date.now()
    pendingStart.value = true
    startPolling()
  } catch (e) {
    error.value = e.message || 'Der Vorgang konnte nicht gestartet werden.'
    echo(`✘ ${error.value}`, 'term-fail')
  } finally {
    starting.value = false
  }
}

// =============================================================================
// Release-Auswahl
// =============================================================================
async function loadReleases() {
  try {
    const data = await auth.api('/api/updates/releases')
    releases.value = data.releases || []
    if (data.channels?.length) channels.value = data.channels
    if (data.current_channel) channel.value = { ...data.current_channel }
  } catch (e) {
    // Ohne GitHub-Antwort bleibt die Liste leer - der Kanal laesst sich
    // trotzdem umstellen, nur ohne Auswahlhilfe.
    releases.value = []
  }
}

async function saveChannel() {
  channelSaving.value = true
  error.value = ''
  try {
    const data = await auth.api('/api/updates/channel', {
      method: 'PUT',
      body: {
        channel: channel.value.channel,
        ref: channel.value.ref || '',
        auto_offer: channel.value.auto_offer !== false,
      },
    })
    status.value = data.status
    channelTouched = false
  } catch (e) {
    error.value = e.message
  } finally {
    channelSaving.value = false
  }
}

async function loadBackupScopes() {
  try {
    const data = await auth.api('/api/backup/overview')
    backupScopes.value = data.scopes
  } catch {
    backupScopes.value = []
  }
}

/**
 * Haengt sich an die laufende Ausgabe.
 *
 * Bewusst ein eigener Strom statt des allgemeinen Ereigniskanals: hier kommen
 * je nach Lauf hunderte Zeilen pro Sekunde, und die haben in dem Kanal, ueber
 * den sonst nur Hinweise laufen, nichts verloren.
 */
function startOutputStream() {
  if (outputStream || typeof EventSource === 'undefined') return
  // Ticket statt Sitzungs-Token in der Adresse. Waehrend des Updates wird der
  // Container neu gebaut - eventStream verbindet danach mit frischem Ticket neu.
  outputStream = auth.eventStream('/api/updates/log/stream', 'update-log', (source) => {
    source.addEventListener('update.output', (event) => {
      liveConnected.value = true
      try {
        const data = JSON.parse(event.data)
        if (data.initial) liveLines.value = []
        liveLines.value = [...liveLines.value, ...(data.lines || [])].slice(-MAX_LIVE_LINES)
        scrollToEnd()
      } catch {
        // Eine unlesbare Zeile ist kein Grund, den Strom aufzugeben.
      }
    })

    source.addEventListener('update.state', (event) => {
      try {
        const state = JSON.parse(event.data)
        if (status.value) status.value.run = state
      } catch {
        // Zustand kommt beim naechsten Takt ohnehin wieder.
      }
    })

    source.addEventListener('update.done', () => {
      liveConnected.value = false
      stopOutputStream()
      load()
    })

    source.onopen = () => { liveConnected.value = true }
    // Bricht die Verbindung ab (der Container wird gerade neu gebaut - genau
    // das ist ja der Vorgang), versucht der Browser es von selbst erneut.
    source.onerror = () => { liveConnected.value = false }
  })
}

function stopOutputStream() {
  outputStream?.close()
  outputStream = null
}

function scrollToEnd() {
  if (!followOutput.value) return
  nextTick(() => {
    const box = liveBox.value
    if (box) box.scrollTop = box.scrollHeight
  })
}

/** Verlauf des letzten Laufs - nur, solange das Terminal nichts Eigenes zeigt. */
async function loadLog() {
  if (liveLines.value.length) return
  try {
    const data = await auth.api('/api/updates/log?lines=300')
    historyLines.value = data.lines || []
    scrollToEnd()
  } catch {
    historyLines.value = []
  }
}

function reloadPage() {
  window.location.reload()
}

async function copyOneliner() {
  if (!status.value?.oneliner) return
  try {
    await navigator.clipboard.writeText(status.value.oneliner)
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  } catch {
    error.value = 'Kopieren hat nicht geklappt – bitte von Hand markieren.'
  }
}

function formatTime(value) {
  if (!value) return '–'
  const date = new Date(value)
  return isNaN(date.getTime()) ? value : date.toLocaleString('de-DE')
}
</script>

<style scoped>
.downgrade-note {
  margin-top: 0.625rem;
  padding: 0.625rem 0.75rem;
  border-radius: var(--radius);
  border: 1px solid var(--color-warning);
  background-color: var(--primary-soft);
  color: var(--color-text-secondary);
  font-size: 0.75rem;
}

.downgrade-list {
  margin-top: 0.25rem;
  padding-left: 1.125rem;
  list-style: disc;
}

.channel-grid {
  display: grid;
  gap: 0.5rem;
}

@media (min-width: 768px) {
  .channel-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

.channel {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.625rem 0.75rem;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  cursor: pointer;
  font-size: 0.8125rem;
}

.channel:hover {
  background-color: var(--hover-surface);
}

.channel.is-picked {
  border-color: var(--color-primary);
  background-color: var(--primary-soft);
}

.channel input {
  margin-top: 0.1875rem;
  flex-shrink: 0;
}

.channel span {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
  min-width: 0;
}

.channel strong {
  color: var(--color-text-primary);
  font-weight: 600;
}

.channel em {
  font-style: normal;
  font-size: 0.6875rem;
  color: var(--color-text-muted);
}

.webhook-box {
  padding: 0.625rem 0.75rem;
  border-radius: var(--radius);
  background-color: var(--color-surface-elevated);
  border: 1px solid var(--color-border);
}

.info-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.info-row > span:first-child {
  color: var(--color-text-muted);
}

.info-row > span:last-child {
  color: var(--color-text-primary);
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 60%;
}

/* Terminal: dunkel auch im hellen Design - eine Konsole liest sich so besser,
   und man verwechselt sie nicht mit dem Rest der Seite. */
.term {
  border-radius: var(--radius);
  border: 1px solid #1e293b;
  background-color: #0b1220;
  overflow: hidden;
}

.term.is-running {
  border-color: var(--color-primary);
}

.term-bar {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  padding: 0.4375rem 0.75rem;
  background-color: #111a2e;
  border-bottom: 1px solid #1e293b;
  color: #94a3b8;
  font-size: 0.75rem;
}

.term-dots {
  display: inline-flex;
  gap: 0.3125rem;
  flex-shrink: 0;
}

.term-dots i {
  width: 0.625rem;
  height: 0.625rem;
  border-radius: 50%;
  background-color: #334155;
}

.term-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #e2e8f0;
  font-weight: 500;
}

.term-chip {
  flex-shrink: 0;
  padding: 0.0625rem 0.5rem;
  border-radius: var(--radius-full);
  font-size: 0.6875rem;
}

.term-chip.is-live { background-color: rgba(34, 197, 94, 0.18); color: #4ade80; }
.term-chip.is-off { background-color: rgba(234, 179, 8, 0.18); color: #facc15; }

.term-follow {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  flex-shrink: 0;
  cursor: pointer;
}

.term-progress {
  height: 3px;
  background-color: #1e293b;
}

.term-progress > div {
  height: 100%;
  background-color: var(--color-primary);
  transition: width var(--duration) var(--ease);
}

.term-body {
  max-height: 28rem;
  min-height: 9rem;
  overflow: auto;
  padding: 0.75rem;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  line-height: 1.55;
  color: #cbd5e1;
  cursor: text;
  outline: none;
}

.term-body:focus-visible {
  box-shadow: inset 0 0 0 1px var(--color-primary);
}

.term-line {
  white-space: pre-wrap;
  word-break: break-word;
}

.term-prompt { color: #4ade80; }
.term-muted { color: #64748b; }
.term-warn { color: #facc15; }
.term-ok { color: #4ade80; font-weight: 600; }
.term-fail { color: #f87171; font-weight: 600; }

/* Block-Cursor: blinkt nur, solange das Terminal den Fokus hat - wie echt. */
.term-cursor {
  display: inline-block;
  width: 0.55em;
  height: 1.1em;
  margin-left: 1px;
  vertical-align: text-bottom;
  border: 1px solid #94a3b8;
}

.term-cursor.is-on {
  background-color: #e2e8f0;
  border-color: #e2e8f0;
  animation: term-blink 1s step-end infinite;
}

@keyframes term-blink {
  50% { opacity: 0; }
}

.term-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  border-top: 1px solid #1e293b;
  background-color: #111a2e;
}

.term-hint {
  color: #94a3b8;
  font-size: 0.75rem;
}

.term-hint kbd {
  padding: 0 0.3125rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  font-family: var(--font-mono);
  color: #e2e8f0;
}

.term-actions {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 0.375rem;
}

.backup-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.5rem 0.625rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
}

.note {
  padding: 0.625rem 0.75rem;
  border-radius: var(--radius);
  font-size: 0.8125rem;
}

.note-fail {
  background-color: var(--danger-soft);
  color: var(--color-danger);
  margin-bottom: 1rem;
}

.note-warn {
  background-color: var(--warning-soft);
  color: var(--color-warning);
}

.warn-box {
  padding: 0.75rem;
  border-radius: var(--radius);
  background-color: var(--warning-soft);
  color: var(--color-warning);
  line-height: 1.5;
}

.oneliner {
  flex: 1;
  min-width: 0;
  padding: 0.5rem 0.625rem;
  border-radius: var(--radius);
  background-color: var(--color-surface-elevated);
  border: 1px solid var(--color-border);
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--color-text-primary);
  overflow-x: auto;
  white-space: nowrap;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  padding: 0.875rem 1.25rem;
  border-top: 1px solid var(--color-border);
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
