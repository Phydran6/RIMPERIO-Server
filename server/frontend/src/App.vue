<!--
================================================================================
Name:           Phil Fischer
E-Mail:         p.fischer@phytech.de & Phydran6
Version:        2026.05.13.20.58.33
================================================================================

RIMPERIO App.vue - Hauptkomponente mit Branding
=============================================
Lädt beim Start die Branding-Konfiguration vom Backend.
Tailwind-kompatibel - überschreibt keine bestehenden Styles.

================================================================================
-->

<template>
  <!-- Spalte: oben der HTTP-Hinweis (falls noetig), darunter die eigentliche App -->
  <div class="app-shell">
    <InsecureConnectionBanner />
    <div class="app-content">
      <router-view />
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useBrandingStore } from './stores/brandingStore'
import { useThemeStore } from './stores/themeStore'
import InsecureConnectionBanner from './components/InsecureConnectionBanner.vue'

const brandingStore = useBrandingStore()
const themeStore = useThemeStore()

onMounted(async () => {
  try {
    // Branding-Config vom Backend laden
    await brandingStore.loadConfig()
  } catch (error) {
    console.error('[App] Branding laden fehlgeschlagen:', error)
    // Fallback: Dark Mode als Default
    themeStore.initTheme('dark')
  }
})
</script>

<style>
/* Rahmen fuer die gesamte App: Banner oben, Inhalt fuellt den Rest. */
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
}

.app-content {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
</style>