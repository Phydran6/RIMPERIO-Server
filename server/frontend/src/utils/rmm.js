// ==============================================================================
// Name:        Phydran6
// Kontakt:     Phydran6
// Changelog:   docs/changelog/frontend.md
// Beschreibung: RIMPERIO - gemeinsame Helfer der RMM-Seiten (Zeit, Zahlen, Status)
// ==============================================================================

export function fmtTime(iso) {
  if (!iso) return '–'
  const d = new Date(iso)
  return isNaN(d) ? '–' : d.toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })
}

export function fmtAgo(iso) {
  if (!iso) return 'nie'
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 90) return 'gerade eben'
  if (s < 3600) return `vor ${Math.round(s / 60)} min`
  if (s < 86400) return `vor ${Math.round(s / 3600)} h`
  return `vor ${Math.round(s / 86400)} Tagen`
}

export function fmtPct(v) {
  return v === null || v === undefined ? '–' : `${Math.round(v)} %`
}

export function fmtUptime(seconds) {
  if (!seconds) return '–'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  return d ? `${d} T ${h} h` : `${h} h ${Math.floor((seconds % 3600) / 60)} min`
}

// Farbe für Auslastung: unter 70 neutral, ab 70 Warnung, ab 90 kritisch.
export function loadClass(v) {
  if (v === null || v === undefined) return ''
  return v >= 90 ? 'is-bad' : v >= 70 ? 'is-warn' : ''
}

export const STATUS_BADGE = {
  pending: 'badge-neutral', running: 'badge-primary', done: 'badge-success',
  error: 'badge-danger', timeout: 'badge-warning',
  pass: 'badge-success', fail: 'badge-danger', unknown: 'badge-neutral',
  critical: 'badge-danger', warning: 'badge-warning', info: 'badge-primary',
}

export const STATUS_LABEL = {
  pending: 'wartet', running: 'läuft', done: 'fertig', error: 'Fehler', timeout: 'Zeitüberschreitung',
  pass: 'OK', fail: 'Fehler', unknown: 'unbekannt',
  critical: 'kritisch', warning: 'Warnung', info: 'Info',
}

export const SHELLS = [
  { value: 'powershell', label: 'PowerShell (Windows)', platforms: ['windows'] },
  { value: 'cmd', label: 'cmd (Windows)', platforms: ['windows'] },
  { value: 'bash', label: 'bash (Linux)', platforms: ['linux', 'darwin'] },
  { value: 'sh', label: 'sh (Linux)', platforms: ['linux', 'darwin'] },
]

// Nur die Shells, die es auf dem System des Agents gibt. Unbekanntes System
// (noch keine Inventur): alle.
export function shellsFor(platform) {
  const p = (platform || '').toLowerCase()
  const list = SHELLS.filter(s => s.platforms.includes(p))
  return list.length ? list : SHELLS
}

export const SHELL_PLACEHOLDER = {
  powershell: 'Get-Service | Where-Object Status -eq Stopped',
  cmd: 'ipconfig /all',
  bash: 'df -h; systemctl --failed',
  sh: 'uptime',
}

export const PLATFORM_LABEL = { windows: 'Windows', linux: 'Linux', darwin: 'macOS', any: 'alle' }

// Check-Typen und ihre Parameter — so, wie der Agent sie erwartet
// (agent/internal/core/checks.go). Gemeinsam für Checks-Seite und Rechner.
export const CHECK_TYPES = {
  cpu: { label: 'CPU-Last', params: [{ key: 'max', label: 'Schwelle (%)', number: true, hint: '90' }] },
  mem: { label: 'Arbeitsspeicher', params: [{ key: 'max', label: 'Schwelle (%)', number: true, hint: '90' }] },
  disk: { label: 'Plattenplatz', params: [{ key: 'max', label: 'Schwelle (%)', number: true, hint: '90' }, { key: 'mount', label: 'Laufwerk (leer = alle)', hint: 'C:\\ oder /' }] },
  service: { label: 'Dienst läuft', params: [{ key: 'name', label: 'Dienstname', hint: 'Spooler / nginx' }] },
  port: { label: 'Port erreichbar', params: [{ key: 'host', label: 'Host', hint: '127.0.0.1' }, { key: 'port', label: 'Port', number: true, hint: '443' }] },
  ping: { label: 'Ping', params: [{ key: 'host', label: 'Host', hint: '8.8.8.8' }] },
  script: { label: 'Skript (Exit-Code prüfen)', params: [{ key: 'shell', label: 'Shell', shell: true }, { key: 'body', label: 'Skript', hint: 'exit 0' }, { key: 'expect_exit', label: 'Erwarteter Exit-Code', number: true, hint: '0' }] },
}

// Zahlenfelder eines Checks als Zahl schicken (Formularfelder liefern Text).
export function normalizeCheckParams(type, params) {
  const out = { ...params }
  for (const p of CHECK_TYPES[type]?.params || []) {
    if (p.number && out[p.key] !== undefined && out[p.key] !== '') out[p.key] = Number(out[p.key])
  }
  return out
}

// Gilt ein Check für diesen Rechner?
export function checkApplies(check, agent) {
  if (check.scope_type === 'global') return true
  if (check.scope_type === 'site') return check.scope_value === (agent.site || 'Standard')
  return check.scope_value === agent.agent_uid
}

// --- Aufträge ------------------------------------------------------------------
export const COMMAND_KIND_LABEL = {
  shell: 'Befehl', patch_scan: 'Patch-Suche', patch_apply: 'Installation', docker_scan: 'Docker-Suche',
  docker_update: 'Container-Update', health_snapshot: 'Zustandsaufnahme', reboot: 'Neustart',
  software_inventory: 'Software-Inventur', uninstall: 'Deinstallation',
}

export function parseJSON(text) {
  if (!text || !/^\s*[[{]/.test(text)) return null
  try { return JSON.parse(text) } catch { return null }
}

export function fmtDuration(fromIso, toIso) {
  if (!fromIso) return ''
  const s = Math.max(0, Math.round(((toIso ? new Date(toIso) : new Date()) - new Date(fromIso)) / 1000))
  if (s < 60) return `${s} s`
  if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60} s`
  return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`
}

// --- Patchmanagement -----------------------------------------------------------
export const PATCH_STATUS = {
  review: ['badge-primary', 'wird geprüft'], auto: ['badge-success', 'automatisch'],
  needs_approval: ['badge-warning', 'Freigabe nötig'], approved: ['badge-success', 'freigegeben'],
  rejected: ['badge-neutral', 'abgelehnt'], ignored: ['badge-neutral', 'ignoriert'],
  monitor: ['badge-neutral', 'nur beobachten'], installing: ['badge-primary', 'wird installiert'],
  installed: ['badge-success', 'installiert'], failed: ['badge-danger', 'fehlgeschlagen'],
  superseded: ['badge-neutral', 'erledigt'], new: ['badge-neutral', 'neu'],
}

export const RUN_STATUS = {
  pending: ['badge-neutral', 'wartet'], snapshotting: ['badge-primary', 'Sicherung'],
  baseline: ['badge-primary', 'Zustand vorher'], installing: ['badge-primary', 'installiert'],
  rebooting: ['badge-primary', 'Neustart'], settling: ['badge-primary', 'Wartezeit'],
  verifying: ['badge-primary', 'Prüfung'], succeeded: ['badge-success', 'erfolgreich'],
  failed: ['badge-danger', 'fehlgeschlagen'], rolling_back: ['badge-warning', 'rollt zurück'],
  rolled_back: ['badge-warning', 'zurückgerollt'], cancelled: ['badge-neutral', 'abgebrochen'],
}

export const RISK = { low: ['badge-success', 'geringes Risiko'], medium: ['badge-warning', 'mittleres Risiko'], high: ['badge-danger', 'hohes Risiko'] }

export const WEEKDAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']

export function fmtRate(v, unit) {
  if (v === null || v === undefined) return '–'
  if (unit === 'bit/s') {
    const steps = [['Gbit/s', 1e9], ['Mbit/s', 1e6], ['kbit/s', 1e3]]
    for (const [u, f] of steps) if (Math.abs(v) >= f) return `${(v / f).toFixed(1)} ${u}`
    return `${Math.round(v)} bit/s`
  }
  const n = Math.abs(v) >= 100 ? Math.round(v) : Math.round(v * 100) / 100
  return `${n.toLocaleString('de-DE')}${unit ? ' ' + unit : ''}`
}

export const SNMP_STATUS = {
  up: ['badge-success', 'OK'], warning: ['badge-warning', 'Warnung'], critical: ['badge-danger', 'kritisch'],
  down: ['badge-danger', 'nicht erreichbar'], unknown: ['badge-neutral', 'unbekannt'],
  ok: ['badge-success', 'OK'],
}
