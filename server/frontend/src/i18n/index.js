/**
 * ==============================================================================
 * Name:           Phydran6
 * Kontakt:        Phydran6
 * Changelog:      docs/changelog/frontend.md
 * ==============================================================================
 *
 * RIMPERIO - Sprachen
 * =================
 * Bewusst ohne vue-i18n: gebraucht wird ein Wörterbuch, eine reaktive Auswahl
 * und `t()`. Das sind fünfzig Zeilen. Eine weitere Abhängigkeit im Build wäre
 * dafür zu viel — und der Container baut so auch ohne Netz durch.
 *
 * Verwendung in einer Komponente:
 *
 *     import { useI18n } from '../i18n'
 *     const { t, locale, setLocale } = useI18n()
 *     ...
 *     <h1>{{ t('nav.dashboard') }}</h1>
 *
 * Fehlt ein Schlüssel in der gewählten Sprache, greift Deutsch. Fehlt er auch
 * dort, steht der Schlüssel selbst da — das fällt beim Testen sofort auf,
 * statt still eine leere Stelle zu hinterlassen.
 * ==============================================================================
 */

import { computed, ref, watch } from 'vue'
import de from './de'
import en from './en'

const MESSAGES = { de, en }

export const LOCALES = [
  { code: 'de', label: 'Deutsch', english: 'German', flag: '🇩🇪' },
  { code: 'en', label: 'English', english: 'English', flag: '🇬🇧' },
]

const STORAGE_KEY = 'rimperio.locale'
const FALLBACK = 'de'

/** Erste sinnvolle Sprache: gemerkte Wahl, sonst die des Browsers, sonst Deutsch. */
function detectLocale() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored && MESSAGES[stored]) return stored
  } catch {
    // Kein localStorage (Privat-Modus) - dann eben der Browser.
  }
  const browser = (navigator?.language || '').slice(0, 2).toLowerCase()
  return MESSAGES[browser] ? browser : FALLBACK
}

// Eine einzige Quelle für die ganze App: alle Komponenten teilen sich diese ref.
const locale = ref(detectLocale())

watch(locale, (value) => {
  try {
    localStorage.setItem(STORAGE_KEY, value)
  } catch {
    // Merken geht nicht - die Wahl gilt dann nur für diese Sitzung.
  }
  // Wichtig für Vorlesehilfen und die Silbentrennung des Browsers.
  if (typeof document !== 'undefined') document.documentElement.lang = value
}, { immediate: true })

/** Holt 'a.b.c' aus einem verschachtelten Objekt. */
function lookup(dictionary, path) {
  return path.split('.').reduce((node, key) => (
    node && typeof node === 'object' ? node[key] : undefined
  ), dictionary)
}

/**
 * Übersetzt einen Schlüssel. Platzhalter werden als {name} geschrieben:
 *   t('backup.created', { name: 'rimperio-backup-...' })
 */
export function translate(key, params = {}) {
  let text = lookup(MESSAGES[locale.value], key)
  if (text === undefined) text = lookup(MESSAGES[FALLBACK], key)
  if (text === undefined) return key
  if (typeof text !== 'string') return key

  return text.replace(/\{(\w+)\}/g, (match, name) => (
    params[name] !== undefined ? String(params[name]) : match
  ))
}

export function setLocale(code) {
  if (MESSAGES[code]) locale.value = code
}

export function useI18n() {
  return {
    t: translate,
    locale,
    // Nur lesen: die Anzeige der aktuellen Sprache in der Oberfläche.
    localeInfo: computed(() => LOCALES.find(l => l.code === locale.value) || LOCALES[0]),
    locales: LOCALES,
    setLocale,
  }
}

/**
 * Als Vue-Plugin einbinden, damit `$t` auch in Vorlagen ohne Import da ist.
 * Nützlich für die vielen bestehenden Ansichten, die sonst alle angefasst
 * werden müssten.
 */
export default {
  install(app) {
    app.config.globalProperties.$t = translate
    app.provide('i18n', { t: translate, locale, setLocale })
  },
}
