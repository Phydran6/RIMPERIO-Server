/**
 * Datei:        tailwind.config.js
 * Projekt:      RIMPERIO — Frontend
 * Autor:        Phydran6
 * Version:      2026.10.31.15.40.00
 * Beschreibung: Tailwind-Konfiguration samt Markenfarben.
 */
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,js}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#eef4ff', 100: '#dbe6ff', 200: '#bdd2ff', 300: '#90b2ff',
          400: '#5b86fb', 500: '#1f6feb', 600: '#1557c7', 700: '#1345a0',
          800: '#143b80', 900: '#153566', 950: '#0f2143'
        },
        ink: {
          800: '#161b22', 850: '#11161d', 900: '#0d1117', 950: '#090c10'
        }
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Consolas', 'monospace']
      }
    }
  },
  plugins: []
}
