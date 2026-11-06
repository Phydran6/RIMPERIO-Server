// Changelog: docs/changelog/frontend.md
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import i18n from './i18n'
import './assets/css/main.css'

// i18n als Plugin: damit steht `$t` auch in Vorlagen bereit, die es nicht
// eigens importieren. Siehe src/i18n/index.js.
createApp(App).use(createPinia()).use(router).use(i18n).mount('#app')
