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
  { value: 'powershell', label: 'PowerShell (Windows)' },
  { value: 'cmd', label: 'cmd (Windows)' },
  { value: 'bash', label: 'bash (Linux)' },
  { value: 'sh', label: 'sh (Linux)' },
]

export const PLATFORM_LABEL = { windows: 'Windows', linux: 'Linux', darwin: 'macOS', any: 'alle' }

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
