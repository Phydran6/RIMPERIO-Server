/**
 * Datei:        util.js
 * Projekt:      RIMPERIO — Frontend
 * Autor:        Phydran6
 * Version:      2026.10.31.15.40.00
 * Beschreibung: Kleine Helfer für Zeit- und Zahlenformate (deutsch).
 */
export function relTime(iso) {
  if (!iso) return 'nie'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 0) return 'gerade eben'
  if (s < 60) return `vor ${Math.floor(s)} s`
  if (s < 3600) return `vor ${Math.floor(s / 60)} min`
  if (s < 86400) return `vor ${Math.floor(s / 3600)} h`
  return `vor ${Math.floor(s / 86400)} d`
}

export function fmtTime(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('de-DE')
}

export function fmtUptime(sec) {
  if (!sec) return '—'
  const d = Math.floor(sec / 86400)
  const h = Math.floor((sec % 86400) / 3600)
  const m = Math.floor((sec % 3600) / 60)
  return `${d ? d + ' d ' : ''}${h} h ${m} min`
}

export function pct(n) {
  return (n === null || n === undefined) ? '—' : `${Math.round(n)} %`
}
