/**
 * Datei:        vite.config.js
 * Projekt:      RIMPERIO — Frontend
 * Autor:        Phydran6
 * Version:      2026.10.31.15.40.00
 * Beschreibung: Vite-Konfiguration. In der Entwicklung werden /api-Aufrufe an
 *               das Backend auf Port 8000 weitergereicht.
 */
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true }
    }
  },
  build: { outDir: 'dist', emptyOutDir: true }
})
