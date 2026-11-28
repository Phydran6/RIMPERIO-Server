<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Fernzugriff auf einen Rechner: Live-Shell (xterm),
                   Remotedesktop (RDP) und Live-Sitzung (Bildschirm der angemeldeten
                   Person, VNC) im Browser. Alles laeuft durch den verschluesselten
                   Tunnel des Agents (app/remote.py) - auf dem Rechner ist kein Port offen.
     ============================================================================== -->
<template>
  <div class="page rs-page" :class="{ 'is-full': full }">
    <div class="page-header">
      <div>
        <h1 class="page-title">{{ title }} <span class="rmm-muted">· {{ agent?.hostname || uid }}</span></h1>
        <p class="page-subtitle">{{ subtitle }}</p>
      </div>
      <div class="rs-actions">
        <router-link :to="`/rmm/agents/${uid}`" class="btn btn-ghost btn-sm">Zum Rechner</router-link>
        <template v-if="isWindows">
          <router-link v-for="k in ['shell', 'rdp', 'vnc']" :key="k" :to="`/rmm/agents/${uid}/remote/${k}`"
            class="btn btn-sm" :class="k === kind ? 'btn-primary' : 'btn-secondary'">{{ LABELS[k] }}</router-link>
        </template>
        <button v-if="state === 'connected' && kind !== 'shell'" class="btn btn-secondary btn-sm" @click="ctrlAltDel">Strg+Alt+Entf</button>
        <button v-if="state === 'connected'" class="btn btn-secondary btn-sm" @click="toggleFull">{{ full ? 'Verkleinern' : 'Vollbild' }}</button>
        <button v-if="state === 'connected' || state === 'connecting'" class="btn btn-danger btn-sm" @click="disconnect">Trennen</button>
        <button v-else-if="kind === 'shell' || prepared || kind === 'vnc'" class="btn btn-primary btn-sm" @click="start">Verbinden</button>
      </div>
    </div>

    <p v-if="error" class="rmm-note is-error" role="alert">{{ error }}</p>
    <p v-if="info" class="rmm-note is-info">{{ info }}</p>

    <!-- RDP: Zugangsdaten (bleiben nur fuer den Verbindungsaufbau im Speicher des Servers) -->
    <section v-if="kind === 'rdp' && state === 'idle'" class="card">
      <div class="card-header"><h2 class="card-title">Anmeldung am Remotedesktop</h2></div>
      <div class="card-body rmm-form">
        <label>Benutzer<input v-model="rdp.username" class="input" autocomplete="username" placeholder="Administrator" /></label>
        <label>Kennwort<input v-model="rdp.password" type="password" class="input" autocomplete="current-password" /></label>
        <label>Domäne (optional)<input v-model="rdp.domain" class="input" placeholder="FIRMA" /></label>
        <label>Tastatur
          <select v-model="rdp.layout" class="select">
            <option value="de-de-qwertz">Deutsch</option><option value="de-ch-qwertz">Schweiz (Deutsch)</option>
            <option value="en-us-qwerty">Englisch (US)</option><option value="en-gb-qwerty">Englisch (GB)</option>
          </select></label>
        <label>Sicherheit
          <select v-model="rdp.security" class="select">
            <option value="any">automatisch</option><option value="nla">NLA</option><option value="tls">TLS</option><option value="rdp">RDP</option>
          </select></label>
        <div class="is-wide rmm-actions">
          <button class="btn btn-primary btn-sm" @click="start">Verbinden</button>
          <button class="btn btn-ghost btn-sm" :disabled="busy" @click="enableRdp">Remotedesktop auf dem Rechner einschalten</button>
        </div>
        <p class="is-wide rmm-muted">Die Verbindung läuft durch den Agent-Tunnel – RDP muss auf dem Rechner an sein, aber nicht
          aus dem Netz erreichbar. Die Zugangsdaten werden nicht gespeichert.</p>
      </div>
    </section>

    <section v-if="kind === 'vnc' && state === 'idle'" class="card">
      <div class="card-header"><h2 class="card-title">Live-Sitzung</h2></div>
      <div class="card-body space-y-2 text-sm">
        <p>Du siehst und bedienst den Bildschirm der Person, die gerade am Rechner angemeldet ist – wie bei einer
          Fernwartung mit TeamViewer oder NinjaOne. Dafür braucht der Rechner einen VNC-Dienst, der <strong>nur von localhost</strong>
          erreichbar ist (TightVNC); RIMPERIO richtet ihn mit einem Klick ein und kommt nur über den Agent-Tunnel daran.</p>
        <div class="rmm-actions">
          <button class="btn btn-primary btn-sm" @click="start">Verbinden</button>
          <button class="btn btn-secondary btn-sm" :disabled="busy" @click="prepareLive">Einrichten bzw. reparieren</button>
        </div>
      </div>
    </section>

    <div v-show="state !== 'idle' || kind === 'shell'" ref="stage" class="rs-stage" :class="kind === 'shell' ? 'is-term' : 'is-display'">
      <div v-if="kind === 'shell'" ref="termEl" class="rs-term" />
      <div v-else ref="displayEl" class="rs-display" tabindex="0" />
      <div v-if="state === 'connecting'" class="rs-overlay">Verbinde mit {{ agent?.hostname || 'dem Rechner' }} …</div>
      <div v-if="state === 'closed'" class="rs-overlay">{{ closedText }} <button class="btn btn-primary btn-sm" @click="start">Neu verbinden</button></div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import Guacamole from 'guacamole-common-js'
import { useAuthStore } from '../stores/auth'

const LABELS = { shell: 'Live-Shell', rdp: 'Remotedesktop', vnc: 'Live-Sitzung' }
const auth = useAuthStore()
const route = useRoute()
const uid = computed(() => String(route.params.uid))
const kind = computed(() => String(route.params.kind || 'shell'))
const agent = ref(null)
const state = ref('idle')          // idle | connecting | connected | closed
const error = ref('')
const info = ref('')
const busy = ref(false)
const full = ref(false)
const prepared = ref(true)
const closedText = ref('Verbindung beendet.')
const rdp = ref({ username: '', password: '', domain: '', layout: 'de-de-qwertz', security: 'any' })
const stage = ref(null)
const termEl = ref(null)
const displayEl = ref(null)

const isWindows = computed(() => (agent.value?.platform || '').toLowerCase().startsWith('win'))
const title = computed(() => LABELS[kind.value] || 'Fernzugriff')
const subtitle = computed(() => ({
  shell: isWindows.value ? 'PowerShell auf dem Rechner (als SYSTEM), live und verschlüsselt über HTTPS.'
    : 'Terminal auf dem Rechner (als Dienstkonto des Agents), live und verschlüsselt über HTTPS.',
  rdp: 'Eigene Windows-Anmeldung per RDP, im Browser – durch den Agent-Tunnel.',
  vnc: 'Bildschirm der angemeldeten Person sehen und bedienen.',
})[kind.value])

let term = null
let fit = null
let socket = null
let client = null
let keyboard = null
let resizeObs = null

function wsBase() {
  return (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host
}

async function loadAgent() {
  try { agent.value = await auth.api(`/api/rmm/agents/${uid.value}`) } catch (e) { error.value = e.message }
}

async function start() {
  error.value = ''
  info.value = ''
  cleanup()
  state.value = 'connecting'
  try {
    const body = { kind: kind.value }
    if (kind.value === 'shell') {
      await nextTick()
      setupTerm()
      body.cols = term.cols; body.rows = term.rows
    }
    if (kind.value === 'rdp') Object.assign(body, rdp.value)
    const session = await auth.api(`/api/remote/${uid.value}/sessions`, { method: 'POST', body })
    rdp.value.password = ''
    if (!session.online) info.value = 'Der Rechner wirkt offline – der Aufbau kann bis zu 40 s dauern oder scheitern.'
    const ticket = await auth.ticket('console')
    if (kind.value === 'shell') openShell(session, ticket)
    else openDisplay(session, ticket)
  } catch (e) {
    state.value = 'idle'
    error.value = e.message
  }
}

// ------------------------------------------------------------------ Shell
function setupTerm() {
  if (term) return
  term = new Terminal({ cursorBlink: true, fontSize: 14, scrollback: 5000, convertEol: false,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
    theme: { background: '#0b0f17', foreground: '#e5e7eb' } })
  fit = new FitAddon()
  term.loadAddon(fit)
  term.open(termEl.value)
  fit.fit()
  term.onData(d => send({ type: 'input', data: d }))
  term.onResize(({ cols, rows }) => send({ type: 'resize', cols, rows }))
  resizeObs = new ResizeObserver(() => { try { fit.fit() } catch { /* unsichtbar */ } })
  resizeObs.observe(termEl.value)
}
function send(obj) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(obj))
}
function openShell(session, ticket) {
  socket = new WebSocket(`${wsBase()}${session.ws}?token=${encodeURIComponent(ticket)}`)
  socket.binaryType = 'arraybuffer'
  const decoder = new TextDecoder()
  socket.onmessage = ev => {
    if (typeof ev.data !== 'string') { term.write(new Uint8Array(ev.data)); return }
    const m = JSON.parse(ev.data)
    if (m.type === 'ready') { state.value = 'connected'; term.focus(); send({ type: 'resize', cols: term.cols, rows: term.rows }) }
    else if (m.type === 'status') term.writeln('\x1b[90m' + m.message + '\x1b[0m')
    else if (m.type === 'error') { error.value = m.message; term.writeln('\r\n\x1b[31m' + m.message + '\x1b[0m') }
    else if (m.type === 'closed') { closedText.value = m.message; state.value = 'closed' }
  }
  socket.onclose = () => { if (state.value !== 'idle') state.value = 'closed' }
  void decoder
}

// -------------------------------------------------------- RDP / VNC (Guacamole)
function openDisplay(session, ticket) {
  const el = displayEl.value
  el.innerHTML = ''
  const rect = stage.value.getBoundingClientRect()
  const width = Math.max(800, Math.round(rect.width || 1280))
  const height = Math.max(600, Math.round((full.value ? window.innerHeight : window.innerHeight - 220)))
  const tunnel = new Guacamole.WebSocketTunnel(session.ws)
  client = new Guacamole.Client(tunnel)
  const display = client.getDisplay()
  el.appendChild(display.getElement())
  client.onstatechange = s => {
    if (s === 3) { state.value = 'connected'; el.focus() }           // CONNECTED
    if (s === 5) { if (state.value !== 'idle') state.value = 'closed' } // DISCONNECTED
  }
  client.onerror = st => {
    error.value = st.message || 'Verbindung fehlgeschlagen.'
    if (kind.value === 'vnc' && /5900|VNC|antwortet nicht/.test(error.value)) error.value += ' – „Einrichten bzw. reparieren“ ausführen.'
    if (kind.value === 'rdp' && /3389/.test(error.value)) error.value += ' – „Remotedesktop auf dem Rechner einschalten“ ausführen.'
  }
  tunnel.onerror = st => { error.value = st.message || error.value || 'Tunnel unterbrochen.' }
  client.onclipboard = (stream, mimetype) => {
    if (!/^text\//.test(mimetype)) return
    const reader = new Guacamole.StringReader(stream)
    let text = ''
    reader.ontext = t => { text += t }
    reader.onend = () => { navigator.clipboard?.writeText(text).catch(() => {}) }
  }
  const mouse = new Guacamole.Mouse(display.getElement())
  const sendMouse = ms => client.sendMouseState(ms, true)
  mouse.onEach(['mousedown', 'mouseup', 'mousemove'], ev => sendMouse(ev.state))
  keyboard = new Guacamole.Keyboard(el)
  keyboard.onkeydown = k => { client.sendKeyEvent(1, k); return false }
  keyboard.onkeyup = k => client.sendKeyEvent(0, k)
  client.connect(`token=${encodeURIComponent(ticket)}&width=${width}&height=${height}&dpi=96`)
  resizeObs = new ResizeObserver(() => {
    const r = stage.value?.getBoundingClientRect()
    if (client && r && state.value === 'connected') client.sendSize(Math.round(r.width), Math.round(full.value ? window.innerHeight : r.height))
  })
  resizeObs.observe(stage.value)
}
function ctrlAltDel() {
  if (!client) return
  for (const k of [0xFFE3, 0xFFE9, 0xFFFF]) client.sendKeyEvent(1, k)
  for (const k of [0xFFFF, 0xFFE9, 0xFFE3]) client.sendKeyEvent(0, k)
}

// ---------------------------------------------------------------- Helfer
async function waitCommand(id, okText) {
  for (let i = 0; i < 120; i++) {
    await new Promise(r => setTimeout(r, 3000))
    const st = await auth.api(`/api/remote/commands/${id}`)
    if (!['pending', 'running'].includes(st.status)) {
      if (st.ready) { info.value = okText; return true }
      error.value = 'Auf dem Rechner fehlgeschlagen: ' + (st.output || st.status).slice(-600)
      return false
    }
  }
  error.value = 'Keine Rückmeldung vom Rechner.'
  return false
}
async function enableRdp() {
  busy.value = true; error.value = ''; info.value = 'Schalte Remotedesktop ein …'
  try { const r = await auth.api(`/api/remote/${uid.value}/rdp/enable`, { method: 'POST' }); await waitCommand(r.command_id, 'Remotedesktop ist an.') }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
async function prepareLive() {
  busy.value = true; error.value = ''; info.value = 'Richte die Live-Sitzung ein (lädt TightVNC, bis zu 2 Minuten) …'
  try { const r = await auth.api(`/api/remote/${uid.value}/live/prepare`, { method: 'POST' }); await waitCommand(r.command_id, 'Live-Sitzung bereit – jetzt verbinden.') }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
function toggleFull() {
  full.value = !full.value
  if (full.value) stage.value?.requestFullscreen?.().catch(() => {})
  else if (document.fullscreenElement) document.exitFullscreen().catch(() => {})
  nextTick(() => { try { fit?.fit() } catch { /* */ } })
}
function cleanup() {
  try { socket?.close() } catch { /* */ }
  try { client?.disconnect() } catch { /* */ }
  if (keyboard) { keyboard.onkeydown = null; keyboard.onkeyup = null; keyboard = null }
  resizeObs?.disconnect()
  socket = null; client = null
}
function disconnect() {
  cleanup()
  state.value = kind.value === 'shell' ? 'closed' : 'idle'
  closedText.value = 'Getrennt.'
}

watch(kind, () => { disconnect(); term?.dispose(); term = null; state.value = 'idle'; error.value = ''; info.value = '' })
onMounted(async () => { await loadAgent(); if (kind.value === 'shell') start() })
onBeforeUnmount(() => { cleanup(); term?.dispose() })
</script>

<style scoped>
.rs-actions { display: flex; flex-wrap: wrap; gap: .4rem; align-items: center; }
.rs-stage { position: relative; border-radius: .6rem; overflow: hidden; border: 1px solid var(--color-border); background: #0b0f17; }
.rs-stage.is-term { height: calc(100vh - 230px); min-height: 22rem; padding: .4rem; }
.rs-stage.is-display { min-height: 60vh; display: flex; justify-content: center; align-items: flex-start; }
.rs-term { width: 100%; height: 100%; }
.rs-display { outline: none; cursor: none; position: relative; isolation: isolate; z-index: 0; }
.rs-display:focus { box-shadow: inset 0 0 0 2px var(--color-primary); }
.rs-overlay { position: absolute; inset: 0; display: flex; gap: .75rem; align-items: center; justify-content: center; color: #e5e7eb; background: rgba(11, 15, 23, .75); font-size: .95rem; }
.is-full .rs-stage { position: fixed; inset: 0; z-index: 60; border-radius: 0; height: 100vh; }
</style>
