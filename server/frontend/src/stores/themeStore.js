/**
 * ==============================================================================
 * Name:           Phil Fischer
 * E-Mail:         p.fischer@phytech.de
 * Version:        2026.05.13.20.58.33
 * ==============================================================================
 * 
 * RIMPERIO Theme Store - Pinia Store für Dark/Light Mode
 * =====================================================
 * Verwaltet das aktuelle Theme (dark/light) und speichert
 * die Benutzer-Präferenz im localStorage.
 * 
 * Features:
 * - Automatische Erkennung der System-Präferenz
 * - Persistierung im localStorage
 * - Reaktives Umschalten ohne Seiten-Reload
 * 
 * ==============================================================================
 */

import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

// =============================================================================
// Theme Store Definition
// =============================================================================
export const useThemeStore = defineStore('theme', () => {
  
  // ===========================================================================
  // State
  // ===========================================================================
  
  /**
   * Aktuelles Theme: 'dark' oder 'light'
   * Wird beim Laden aus localStorage oder System-Präferenz initialisiert
   */
  const currentTheme = ref('dark')
  
  // ===========================================================================
  // Initialisierung
  // ===========================================================================
  
  /**
   * Ermittelt das initiale Theme basierend auf:
   * 1. localStorage (falls vorhanden)
   * 2. System-Präferenz (prefers-color-scheme)
   * 3. Fallback: 'dark'
   */
  function initTheme(defaultTheme = 'dark') {
    // Prüfe localStorage für gespeicherte Präferenz
    const savedTheme = localStorage.getItem('rimperio-theme')
    
    if (savedTheme && ['dark', 'light'].includes(savedTheme)) {
      // Benutzer hat bereits eine Wahl getroffen
      currentTheme.value = savedTheme
    } else if (window.matchMedia) {
      // Keine gespeicherte Präferenz - System-Einstellung prüfen
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
      currentTheme.value = prefersDark ? 'dark' : 'light'
    } else {
      // Fallback auf übergebenes Default-Theme
      currentTheme.value = defaultTheme
    }
    
    // Theme auf HTML-Element anwenden
    applyTheme()
  }
  
  // ===========================================================================
  // Actions
  // ===========================================================================
  
  /**
   * Wendet das aktuelle Theme auf das HTML-Element an
   * Setzt das data-theme Attribut für CSS-Selektoren
   */
  function applyTheme() {
    // data-theme Attribut auf <html> setzen
    document.documentElement.setAttribute('data-theme', currentTheme.value)
    
    // Meta theme-color für mobile Browser aktualisieren
    const metaThemeColor = document.querySelector('meta[name="theme-color"]')
    if (metaThemeColor) {
      // Hintergrundfarbe je nach Theme
      metaThemeColor.setAttribute(
        'content', 
        currentTheme.value === 'dark' ? '#0a0a0f' : '#f8fafc'
      )
    }
  }
  
  /**
   * Wechselt zwischen Dark und Light Mode
   */
  function toggleTheme() {
    currentTheme.value = currentTheme.value === 'dark' ? 'light' : 'dark'
  }
  
  /**
   * Setzt ein spezifisches Theme
   * @param {string} theme - 'dark' oder 'light'
   */
  function setTheme(theme) {
    if (['dark', 'light'].includes(theme)) {
      currentTheme.value = theme
    }
  }
  
  // ===========================================================================
  // Watchers
  // ===========================================================================
  
  /**
   * Beobachtet Theme-Änderungen und:
   * - Speichert im localStorage
   * - Wendet CSS-Klassen an
   */
  watch(currentTheme, (newTheme) => {
    // Im localStorage speichern
    localStorage.setItem('rimperio-theme', newTheme)
    
    // Theme anwenden
    applyTheme()
  })
  
  // ===========================================================================
  // Getters (computed)
  // ===========================================================================
  
  /**
   * Prüft ob aktuell Dark Mode aktiv ist
   */
  const isDark = () => currentTheme.value === 'dark'
  
  /**
   * Prüft ob aktuell Light Mode aktiv ist
   */
  const isLight = () => currentTheme.value === 'light'
  
  // ===========================================================================
  // Public API
  // ===========================================================================
  return {
    // State
    currentTheme,
    
    // Actions
    initTheme,
    toggleTheme,
    setTheme,
    applyTheme,
    
    // Getters
    isDark,
    isLight
  }
})