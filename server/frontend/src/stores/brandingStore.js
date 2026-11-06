/**
 * ==============================================================================
 * Name:           Phil Fischer
 * E-Mail:         p.fischer@phytech.de
 * Version:        2026.05.13.20.58.33
 * ==============================================================================
 * 
 * RIMPERIO Branding Store
 * ======================
 * 
 * ==============================================================================
 */

import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import { useThemeStore } from './themeStore'
import { useAuthStore } from './auth'

const API_BASE = '/api/branding'

export const useBrandingStore = defineStore('branding', () => {
  
  const loading = ref(true)
  const error = ref(null)
  
  const config = reactive({
    company_name: 'RIMPERIO',
    tagline: 'Zentraler Log-Server',
    // Kein "All rights reserved" mehr: RIMPERIO steht unter der MIT-Lizenz, die
    // Rechte sind eingeräumt und nicht vorbehalten. Wer es genau wissen will,
    // findet unter "Über RIMPERIO" Herkunft, Lizenz und die Verweise ins
    // Repository.
    footer_text: 'RIMPERIO · Freie Software unter MIT-Lizenz',
    support_email: 'support@example.com',
    logo_path: null,
    favicon_path: null,
    default_theme: 'dark',
    allow_theme_toggle: true,
    
    // Markenfarben
    primary_color: '#b8860b',
    secondary_color: '#1e3a6e',
    accent_color: '#d4af37',
    success_color: '#22c55e',
    warning_color: '#f59e0b',
    danger_color: '#ef4444',
    
    // Dark Mode - Custom
    dark_scheme: {
      background: '#0b1322',
      surface: '#111c32',
      surface_elevated: '#17243f',
      border: '#24324f',
      text_primary: '#f8fafc',
      text_secondary: '#cbd5e1',
      text_muted: '#94a3b8'
    },
    
    // Light Mode
    light_scheme: {
      background: '#f1f5f9',
      surface: '#ffffff',
      surface_elevated: '#f8fafc',
      border: '#e2e8f0',
      text_primary: '#0f172a',
      text_secondary: '#334155',
      text_muted: '#64748b'
    },
    
    custom_css: ''
  })
  
  async function loadConfig() {
    loading.value = true
    error.value = null
    
    try {
      const response = await fetch(`${API_BASE}/config`)
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      
      const data = await response.json()
      Object.assign(config, data)
      
      const themeStore = useThemeStore()
      themeStore.initTheme(config.default_theme)
      
      applyCSS()
      updatePageMeta()
      
    } catch (err) {
      console.error('[Branding] Fehler beim Laden:', err)
      error.value = err.message
      
      const themeStore = useThemeStore()
      themeStore.initTheme('dark')
      applyCSS()
    } finally {
      loading.value = false
    }
  }
  
  // Schreibende Aufrufe laufen ueber den Auth-Store: die Endpunkte sind seit
  // 2026.08.02 Admins vorbehalten und brauchen den Bearer-Token.
  async function saveConfig() {
    try {
      await useAuthStore().api(`${API_BASE}/config`, {
        method: 'PUT',
        body: { ...config }
      })

      applyCSS()
      updatePageMeta()
      
      return true
    } catch (err) {
      console.error('[Branding] Fehler beim Speichern:', err)
      error.value = err.message
      return false
    }
  }
  
  async function resetToDefaults() {
    try {
      const data = await useAuthStore().api(`${API_BASE}/reset`, { method: 'POST' })
      Object.assign(config, data)
      
      applyCSS()
      updatePageMeta()
      
      return true
    } catch (err) {
      console.error('[Branding] Fehler beim Reset:', err)
      error.value = err.message
      return false
    }
  }
  
  async function uploadLogo(file) {
    const formData = new FormData()
    formData.append('file', file)

    try {
      const data = await useAuthStore().api(`${API_BASE}/upload/logo`, {
        method: 'POST',
        body: formData
      })
      config.logo_path = data.path

      return true
    } catch (err) {
      console.error('[Branding] Logo-Upload Fehler:', err)
      error.value = err.message
      return false
    }
  }
  
  async function uploadFavicon(file) {
    const formData = new FormData()
    formData.append('file', file)

    try {
      const data = await useAuthStore().api(`${API_BASE}/upload/favicon`, {
        method: 'POST',
        body: formData
      })
      config.favicon_path = data.path
      updatePageMeta()

      return true
    } catch (err) {
      console.error('[Branding] Favicon-Upload Fehler:', err)
      error.value = err.message
      return false
    }
  }
  
  function updateColor(key, value) {
    if (key.includes('.')) {
      const [scheme, prop] = key.split('.')
      if (config[scheme] && prop in config[scheme]) {
        config[scheme][prop] = value
      }
    } else if (key in config) {
      config[key] = value
    }
    applyCSS()
  }
  
  function applyCSS() {
    const root = document.documentElement
    
    root.style.setProperty('--color-primary', config.primary_color)
    root.style.setProperty('--color-secondary', config.secondary_color)
    root.style.setProperty('--color-accent', config.accent_color)
    root.style.setProperty('--color-success', config.success_color)
    root.style.setProperty('--color-warning', config.warning_color)
    root.style.setProperty('--color-danger', config.danger_color)
    
    root.style.setProperty('--dark-bg', config.dark_scheme.background)
    root.style.setProperty('--dark-surface', config.dark_scheme.surface)
    root.style.setProperty('--dark-surface-elevated', config.dark_scheme.surface_elevated)
    root.style.setProperty('--dark-border', config.dark_scheme.border)
    root.style.setProperty('--dark-text-primary', config.dark_scheme.text_primary)
    root.style.setProperty('--dark-text-secondary', config.dark_scheme.text_secondary)
    root.style.setProperty('--dark-text-muted', config.dark_scheme.text_muted)
    
    root.style.setProperty('--light-bg', config.light_scheme.background)
    root.style.setProperty('--light-surface', config.light_scheme.surface)
    root.style.setProperty('--light-surface-elevated', config.light_scheme.surface_elevated)
    root.style.setProperty('--light-border', config.light_scheme.border)
    root.style.setProperty('--light-text-primary', config.light_scheme.text_primary)
    root.style.setProperty('--light-text-secondary', config.light_scheme.text_secondary)
    root.style.setProperty('--light-text-muted', config.light_scheme.text_muted)
    
    injectCustomCSS()
  }
  
  function injectCustomCSS() {
    const styleId = 'rimperio-custom-css'
    let styleElement = document.getElementById(styleId)
    
    if (!styleElement) {
      styleElement = document.createElement('style')
      styleElement.id = styleId
      document.head.appendChild(styleElement)
    }
    
    styleElement.textContent = config.custom_css
  }
  
  function updatePageMeta() {
    document.title = config.company_name
    
    if (config.favicon_path) {
      let link = document.querySelector("link[rel~='icon']")
      if (!link) {
        link = document.createElement('link')
        link.rel = 'icon'
        document.head.appendChild(link)
      }
      link.href = `${API_BASE}/assets/${config.favicon_path}`
    }
  }
  
  function getLogoUrl() {
    return config.logo_path ? `${API_BASE}/assets/${config.logo_path}` : null
  }
  
  function getFaviconUrl() {
    return config.favicon_path ? `${API_BASE}/assets/${config.favicon_path}` : null
  }
  
  return {
    loading,
    error,
    config,
    loadConfig,
    saveConfig,
    resetToDefaults,
    uploadLogo,
    uploadFavicon,
    updateColor,
    applyCSS,
    getLogoUrl,
    getFaviconUrl
  }
})