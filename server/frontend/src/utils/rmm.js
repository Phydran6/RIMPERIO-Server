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
