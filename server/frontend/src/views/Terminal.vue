<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - RIMPERIO-Server -> Konsole: Root-Shell auf dem Server im Browser.

     Bewusst ohne xterm.js: das waere eine weitere Abhaengigkeit (rund 300 kB)
     plus Build-Schritt, nur damit hier ein Terminal steht. Stattdessen ein
     schlanker eigener Bildschirm: Zeilenpuffer, ANSI-Farben, Cursorsteuerung
     fuer das, was eine Shell im Alltag benutzt.

     Was dieser Bildschirm NICHT kann: Programme mit voller Bildschirmsteuerung
     (top, htop, nano, vim). Das steht auch so in der Oberflaeche - lieber
     ehrlich benennen, als den Benutzer raten zu lassen, warum es zerlaeuft.
     Fuer solche Faelle gibt es weiterhin SSH.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">{{ t('terminal.title') }}</h1>
        <p class="page-subtitle">{{ t('terminal.intro') }}</p>
      </div>
      <span class="badge" :class="connected ? 'badge-success' : 'badge-neutral'">
        {{ connected ? t('terminal.connected') : t('terminal.disconnected') }}
      </span>
    </div>

    <p v-if="error" class="alert alert-danger">{{ error }}</p>

    <p v-if="status && !status.available" class="alert alert-warning">
      {{ status.reason }}
      <code v-if="status.enable_hint" class="hint-code">{{ status.enable_hint }}</code>
    </p>

    <template v-else-if="status">
      <p class="alert alert-warning">{{ t('terminal.warning') }}</p>

      <section class="card">
        <div class="card-header">
          <div class="flex flex-wrap items-center gap-2">
            <button v-if="!connected" class="btn btn-primary btn-sm" @click="connect">
              <AppIcon name="terminal" :size="15" />
              {{ t('terminal.connect') }}
            </button>
            <button v-else class="btn btn-secondary btn-sm" @click="disconnect">
              {{ t('terminal.disconnect') }}
            </button>
            <button class="btn btn-ghost btn-sm" @click="clear">{{ t('common.close') }} / Clear</button>
          </div>
          <span class="hint">
            {{ t('terminal.openSessions') }}: {{ status.open_sessions }} / {{ status.max_sessions }}
          </span>
        </div>

        <div class="card-body">
          <!-- Der Bildschirm. tabindex macht ihn fokussierbar, damit er
               Tastatureingaben bekommt, ohne dass ein Eingabefeld nötig wäre. -->
          <!-- Die Tastatur geht in ein unsichtbares Eingabefeld, nicht in den
               Bildschirm selbst: nur auf einem echten Eingabefeld loest der
               Browser bei Strg+V ein Einfuegen aus und bietet im Rechtsklick-
               Menue "Einfuegen" an. Auf einem div passiert beides nicht - so
               macht es auch xterm.js. -->
          <div
            ref="screenEl"
            class="screen"
            :class="{ 'is-focused': inputFocused }"
            role="log"
            aria-label="Terminal"
            @mousedown="onMouseDown"
            @mouseup="onMouseUp"
            @contextmenu="onContextMenu"
          >
            <textarea
              ref="inputEl"
              class="screen-input"
              aria-label="Terminal-Eingabe"
              autocapitalize="off"
              autocomplete="off"
              autocorrect="off"
              spellcheck="false"
              @keydown="onKey"
              @paste="onPaste"
              @input="onInput"
              @focus="inputFocused = true"
              @blur="inputFocused = false"
            />
            <div v-for="(line, index) in visibleLines" :key="index" class="screen-line">
              <span
                v-for="(span, spanIndex) in line"
                :key="spanIndex"
                :style="span.style"
              >{{ span.text }}</span><span
                v-if="connected && index === visibleLines.length - 1"
                class="cursor"
              >█</span>
            </div>
          </div>

          <p class="hint mt-2">
            Einfügen mit <kbd>Strg</kbd>+<kbd>V</kbd> oder Rechtsklick, kopieren durch Markieren
            und <kbd>Strg</kbd>+<kbd>C</kbd>.
            Programme mit eigener Bildschirmsteuerung (top, nano, vim) stellt dieser
            schlanke Bildschirm nicht sauber dar — dafür weiterhin SSH benutzen.
          </p>
          <!-- Diese Konsole laeuft ueber den Backend-Container. Ein Update baut
               genau den neu - und risse die Sitzung samt Update mittendrin ab. -->
          <p class="hint mt-1" style="color: var(--color-warning)">
            RIMPERIO-Updates bitte nicht hier starten: das Update baut den Container neu, über
            den diese Konsole läuft, und bräche dabei ab. Dafür
            <router-link to="/updates" style="text-decoration: underline">RIMPERIO-Server → Updates</router-link>
            oder SSH nutzen.
          </p>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useI18n } from '../i18n'
import AppIcon from '../components/AppIcon.vue'

const auth = useAuthStore()
const { t } = useI18n()

const status = ref(null)
const error = ref('')
const connected = ref(false)
const screenEl = ref(null)

// So viele Zeilen bleiben im Puffer. Darüber hinaus wird vorne abgeschnitten -
// sonst wächst die Seite bei einem `find /` ins Unermessliche.
const MAX_LINES = 2000

/** Der Bildschirm als Liste von Zeilen; jede Zeile ist eine Liste von Abschnitten. */
const lines = ref([[]])
let socket = null

const visibleLines = computed(() => lines.value)

// =============================================================================
// ANSI: nur das, was eine Shell im Alltag wirklich schickt
// =============================================================================
const COLORS = [
  '#3b3b46', '#f87171', '#4ade80', '#fbbf24',
  '#60a5fa', '#c084fc', '#22d3ee', '#e5e7eb',
]
const BRIGHT = [
  '#6b7280', '#fca5a5', '#86efac', '#fde047',
  '#93c5fd', '#d8b4fe', '#67e8f9', '#ffffff',
]

let currentStyle = {}

function resetStyle() {
  currentStyle = {}
}

/** Wertet eine SGR-Folge aus (Farben, fett, zurücksetzen). */
function applySgr(codes) {
  for (const raw of codes) {
    const code = Number(raw)
    if (Number.isNaN(code) || code === 0) {
      resetStyle()
    } else if (code === 1) {
      currentStyle.fontWeight = '700'
    } else if (code === 2) {
      currentStyle.opacity = '0.7'
    } else if (code === 4) {
      currentStyle.textDecoration = 'underline'
    } else if (code === 7) {
      // Invertiert: für Auswahlbalken. Grob, aber besser als gar nichts.
      currentStyle.filter = 'invert(1)'
    } else if (code >= 30 && code <= 37) {
      currentStyle.color = COLORS[code - 30]
    } else if (code >= 90 && code <= 97) {
      currentStyle.color = BRIGHT[code - 90]
    } else if (code === 39) {
      delete currentStyle.color
    } else if (code >= 40 && code <= 47) {
      currentStyle.backgroundColor = COLORS[code - 40]
    } else if (code === 49) {
      delete currentStyle.backgroundColor
    }
  }
}

function pushText(text) {
  if (!text) return
  const line = lines.value[lines.value.length - 1]
  const last = line[line.length - 1]
  // Gleicher Stil wie eben? Dann anhängen statt einen neuen Abschnitt anlegen -
  // sonst entstehen bei jedem Zeichen eigene DOM-Knoten.
  if (last && last.styleKey === styleKey()) {
    last.text += text
  } else {
    line.push({ text, style: { ...currentStyle }, styleKey: styleKey() })
  }
}

function styleKey() {
  return JSON.stringify(currentStyle)
}

function newLine() {
  lines.value.push([])
  if (lines.value.length > MAX_LINES) {
    lines.value.splice(0, lines.value.length - MAX_LINES)
  }
}

function backspace() {
  const line = lines.value[lines.value.length - 1]
  for (let index = line.length - 1; index >= 0; index -= 1) {
    if (line[index].text.length) {
      line[index].text = line[index].text.slice(0, -1)
      if (!line[index].text) line.splice(index, 1)
      return
    }
  }
}

function clearLine() {
  lines.value[lines.value.length - 1] = []
}

/** Zerlegt den Datenstrom und baut daraus den Bildschirm. */
function write(chunk) {
  let index = 0
  while (index < chunk.length) {
    const char = chunk[index]

    if (char === '\x1b') {
      // CSI-Folge: ESC [ ... Buchstabe
      const match = /^\x1b\[([0-9;?]*)([A-Za-z])/.exec(chunk.slice(index))
      if (match) {
        const [, params, command] = match
        if (command === 'm') {
          applySgr((params || '0').split(';'))
        } else if (command === 'K') {
          // Zeile ab Cursor löschen - genau das macht die Shell beim Editieren.
          if (params === '' || params === '0' || params === '2') clearLine()
        } else if (command === 'J' && (params === '2' || params === '3')) {
          lines.value = [[]]
        }
        // Alles andere (Cursor bewegen, scrollen) wird verworfen: dieser
        // Bildschirm führt keine Cursorposition, er hängt hinten an.
        index += match[0].length
        continue
      }
      // OSC-Folge (Fenstertitel): ESC ] ... BEL/ST - komplett überspringen.
      const osc = /^\x1b\][^\x07\x1b]*(\x07|\x1b\\)/.exec(chunk.slice(index))
      if (osc) {
        index += osc[0].length
        continue
      }
      index += 1
      continue
    }

    if (char === '\n') {
      newLine()
    } else if (char === '\r') {
      // Wagenrücklauf ohne Zeilenvorschub: die Shell überschreibt die Zeile.
      const next = chunk[index + 1]
      if (next !== '\n') clearLine()
    } else if (char === '\b') {
      backspace()
    } else if (char === '\x07') {
      // Klingel - stillschweigend schlucken.
    } else if (char === '\t') {
      pushText('    ')
    } else if (char >= ' ' || char === ' ') {
      pushText(char)
    }
    index += 1
  }

  nextTick(scrollToBottom)
}

function scrollToBottom() {
  if (screenEl.value) screenEl.value.scrollTop = screenEl.value.scrollHeight
}

function focusScreen() {
  inputEl.value?.focus({ preventScroll: true })
}

// =============================================================================
// Maus: Klick = Fokus (Markieren zum Kopieren bleibt moeglich),
// Rechtsklick = Einfuegen wie in PuTTY
// =============================================================================
const inputEl = ref(null)
const inputFocused = ref(false)

// Darf die Seite die Zwischenablage selbst lesen? Nur ueber HTTPS/localhost,
// und nur wenn der Nutzer es nicht abgelehnt hat. Sonst bleibt das Menue des
// Browsers - mit "Einfuegen", weil es auf dem Eingabefeld aufgeht.
let clipboardUsable = window.isSecureContext && !!navigator.clipboard?.readText

onMounted(async () => {
  if (!clipboardUsable || !navigator.permissions?.query) return
  try {
    const state = await navigator.permissions.query({ name: 'clipboard-read' })
    clipboardUsable = state.state !== 'denied'
    state.onchange = () => { clipboardUsable = state.state !== 'denied' }
  } catch {
    // Firefox kennt 'clipboard-read' nicht - dann einfach versuchen.
  }
})

function onMouseDown(event) {
  if (event.button !== 2) return
  // Rechtsklick: das Eingabefeld SOFORT unter den Mauszeiger schieben - direkt
  // am Element, nicht ueber Vue. Ueber Vue kam die neue Position erst nach dem
  // Rechtsklick-Menue an: der Browser sah dann nur Text und bot "Kopieren" an,
  // aber kein "Einfuegen". Genau so macht es auch xterm.js.
  const input = inputEl.value
  const screen = screenEl.value
  if (!input || !screen) return
  const box = screen.getBoundingClientRect()
  input.style.left = `${event.clientX - box.left + screen.scrollLeft - 20}px`
  input.style.top = `${event.clientY - box.top + screen.scrollTop - 20}px`
  input.style.width = '40px'
  input.style.height = '40px'
  input.style.zIndex = '1000'
  focusScreen()
}

function onMouseUp(event) {
  if (event.button !== 0) return
  resetInputPosition()
  // Wer gerade Text markiert hat, will kopieren - Fokus erst danach holen,
  // sonst geht die Markierung verloren.
  if (window.getSelection()?.toString()) return
  focusScreen()
}

async function onContextMenu(event) {
  if (!connected.value) return
  // Rechtsklick = Einfuegen, wie in PuTTY und vielen Linux-Terminals - wenn
  // der Browser die Zwischenablage herausgibt (HTTPS oder localhost).
  if (clipboardUsable) {
    event.preventDefault()
    try {
      const text = await navigator.clipboard.readText()
      if (text) send(normalizePaste(text))
    } catch {
      // Abgelehnt: ab jetzt das Menue des Browsers (mit "Einfuegen").
      clipboardUsable = false
    }
    resetInputPosition()
    focusScreen()
    return
  }
  // Sonst (HTTP): das Browsermenue geht auf dem Eingabefeld auf, das
  // onMouseDown unter den Zeiger geschoben hat - dort steht "Einfuegen".
  // Zurueckgeschoben wird beim Einfuegen bzw. beim naechsten Linksklick.
}

function resetInputPosition() {
  const input = inputEl.value
  if (!input) return
  for (const prop of ['left', 'top', 'width', 'height', 'zIndex']) input.style[prop] = ''
}

/** Zeilenenden aus der Zwischenablage so, wie eine Shell sie erwartet. */
function normalizePaste(text) {
  return text.replace(/\r\n/g, '\r').replace(/\n/g, '\r')
}

/** Was nicht ueber keydown kam (Handy-Tastatur, Eingabehilfen), steht hier. */
function onInput(event) {
  const value = event.target.value
  event.target.value = ''
  if (value && connected.value) send(value)
}

function clear() {
  lines.value = [[]]
  resetStyle()
}

// =============================================================================
// Verbindung
// =============================================================================
async function loadStatus() {
  try {
    status.value = await auth.api('/api/shell/status')
  } catch (err) {
    error.value = err.message
  }
}

async function connect() {
  error.value = ''
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const rows = 32
  const cols = estimateColumns()
  // Ein Ticket fuer genau diese eine Verbindung (60 s gueltig) - nicht der
  // Sitzungs-Token, der sonst in Proxy-Protokollen stuende.
  let ticket
  try {
    ticket = await auth.ticket('shell')
  } catch (err) {
    error.value = err.message
    return
  }
  const url = `${protocol}://${window.location.host}/api/shell/ws`
    + `?token=${encodeURIComponent(ticket)}&rows=${rows}&cols=${cols}`

  socket = new WebSocket(url)

  socket.onmessage = (event) => {
    let message
    try {
      message = JSON.parse(event.data)
    } catch {
      write(event.data)
      return
    }
    if (message.type === 'output') {
      write(message.data)
    } else if (message.type === 'ready') {
      connected.value = true
      write(`\x1b[32m[RIMPERIO] Verbunden als ${message.user}.\x1b[0m\r\n`)
      nextTick(focusScreen)
    } else if (message.type === 'error') {
      error.value = message.message
    } else if (message.type === 'closed') {
      write(`\r\n\x1b[33m[RIMPERIO] ${message.reason}\x1b[0m\r\n`)
    }
  }

  socket.onclose = () => {
    connected.value = false
    loadStatus()
  }
  socket.onerror = () => {
    error.value = 'Die Verbindung zum Terminal ist fehlgeschlagen.'
    connected.value = false
  }
}

function disconnect() {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: 'close' }))
  }
  socket?.close()
  socket = null
  connected.value = false
}

/** Wie viele Zeichen passen nebeneinander? Grob aus der Breite geschätzt. */
function estimateColumns() {
  const width = screenEl.value?.clientWidth || 800
  return Math.max(40, Math.min(Math.floor(width / 7.7), 400))
}

function send(data) {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: 'input', data }))
  }
}

// Tasten, die eine Shell als Steuerzeichen erwartet.
const KEY_MAP = {
  Enter: '\r',
  Backspace: '\x7f',
  Tab: '\t',
  Escape: '\x1b',
  ArrowUp: '\x1b[A',
  ArrowDown: '\x1b[B',
  ArrowRight: '\x1b[C',
  ArrowLeft: '\x1b[D',
  Home: '\x1b[H',
  End: '\x1b[F',
  Delete: '\x1b[3~',
  PageUp: '\x1b[5~',
  PageDown: '\x1b[6~',
}

function onKey(event) {
  if (!connected.value) return

  // Kopieren muss der Browser behalten dürfen - sonst kommt man an die Ausgabe
  // nicht heran. Strg+C zum Abbrechen geht weiterhin, wenn nichts markiert ist.
  const key = event.key.toLowerCase()
  if (event.ctrlKey && key === 'c' && window.getSelection()?.toString()) return
  // Einfuegen: Strg+V, Strg+Umschalt+V und Umschalt+Einfg ueberlaesst man dem
  // Browser - er loest dann auf dem Eingabefeld das paste-Ereignis aus.
  if ((event.ctrlKey && key === 'v') || (event.shiftKey && event.key === 'Insert')) return
  // Strg+Umschalt+C: kopieren wie in Linux-Terminals.
  if (event.ctrlKey && event.shiftKey && key === 'c') return
  // Handy-Tastatur und Eingabehilfen: kein echter Tastenname - der Text kommt
  // gleich ueber das input-Ereignis (onInput).
  if (event.isComposing || event.key === 'Unidentified' || event.key === 'Process') return

  event.preventDefault()

  if (event.ctrlKey && event.key.length === 1) {
    const code = event.key.toUpperCase().charCodeAt(0)
    if (code >= 64 && code <= 95) {
      send(String.fromCharCode(code - 64))     // Strg+C -> \x03, Strg+D -> \x04
      return
    }
  }

  const mapped = KEY_MAP[event.key]
  if (mapped) {
    send(mapped)
  } else if (event.key.length === 1) {
    send(event.key)
  }
}

function onPaste(event) {
  event.preventDefault()
  resetInputPosition()
  if (!connected.value) return
  const text = event.clipboardData?.getData('text') || ''
  if (text) send(normalizePaste(text))
}

onMounted(loadStatus)
onBeforeUnmount(disconnect)
</script>

<style scoped>
.screen {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.8125rem;
  line-height: 1.45;
  background-color: #12121a;
  color: #e5e7eb;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  padding: 0.75rem;
  height: 28rem;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
  cursor: text;
}

.screen {
  position: relative;
}

.screen.is-focused {
  outline: 2px solid var(--color-primary);
  outline-offset: 1px;
}

/* Unsichtbar, aber fokussierbar und an seinem Platz - display:none oder
   visibility:hidden wuerden Tastatur und Zwischenablage abschalten. */
.screen-input {
  position: absolute;
  left: 0;
  top: 0;
  width: 1px;
  height: 1px;
  padding: 0;
  border: 0;
  margin: 0;
  opacity: 0;
  resize: none;
  overflow: hidden;
  white-space: nowrap;
  background: transparent;
  color: transparent;
  caret-color: transparent;
}

.screen-line {
  min-height: 1.45em;
}

.cursor {
  animation: blink 1.1s step-end infinite;
  color: var(--color-primary);
}

@keyframes blink {
  50% { opacity: 0; }
}

.hint-code {
  display: inline-block;
  margin-left: 0.375rem;
  padding: 0.0625rem 0.375rem;
  border-radius: var(--radius-sm, 4px);
  background-color: var(--color-surface-elevated);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.75rem;
}

.alert {
  padding: 0.625rem 0.875rem;
  border-radius: var(--radius);
  font-size: 0.8125rem;
  margin-bottom: 0.75rem;
  border: 1px solid var(--color-border);
}

.alert-danger {
  background-color: var(--danger-soft);
  color: var(--color-danger);
  border-color: var(--color-danger);
}

.alert-warning {
  background-color: var(--primary-soft);
  color: var(--color-warning);
  border-color: var(--color-warning);
}
</style>
