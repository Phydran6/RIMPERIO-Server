/**
 * Datei:        main.js
 * Projekt:      RIMPERIO — Frontend
 * Autor:        Phydran6
 * Version:      2026.10.31.15.40.00
 * Beschreibung: Einstiegspunkt. Baut die Vue-App, Pinia und Router zusammen und
 *               leitet bei abgelaufener Sitzung zurück zur Anmeldung.
 */
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router'
import App from './App.vue'
import './assets/css/main.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')

window.addEventListener('rimperio:unauthorized', () => {
  if (router.currentRoute.value.name !== 'login') router.push({ name: 'login' })
})
