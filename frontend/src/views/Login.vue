<!--
  Datei:        views/Login.vue
  Projekt:      RIMPERIO — Frontend
  Autor:        Phydran6
  Version:      2026.10.31.15.40.00
  Beschreibung: Anmeldeseite.
-->
<template>
  <div class="min-h-screen flex items-center justify-center p-4">
    <div class="w-full max-w-sm">
      <div class="flex items-center justify-center gap-2 mb-6">
        <img src="/favicon.svg" alt="RIMPERIO" class="w-10 h-10 rounded-xl" />
        <span class="text-xl font-semibold tracking-tight">RIMPERIO</span>
      </div>

      <form class="card p-6 space-y-4" @submit.prevent="submit">
        <h1 class="text-lg font-semibold">Anmelden</h1>

        <div>
          <label class="label">Benutzername</label>
          <input v-model="username" class="input" autocomplete="username" autofocus />
        </div>
        <div>
          <label class="label">Passwort</label>
          <input v-model="password" type="password" class="input" autocomplete="current-password" />
        </div>

        <p v-if="error" class="text-sm text-red-300">{{ error }}</p>

        <button class="btn-primary w-full" :disabled="loading">
          {{ loading ? 'Einen Moment …' : 'Anmelden' }}
        </button>

        <p class="text-xs text-slate-500 text-center">
          Erststart: <span class="font-mono">admin / admin</span> — danach bitte sofort ändern.
        </p>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from '../stores/auth'

const auth = useAuth()
const router = useRouter()
const route = useRoute()

const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    await auth.login(username.value, password.value)
    router.push(route.query.next || { name: 'dashboard' })
  } catch (e) {
    error.value = e.message || 'Anmeldung fehlgeschlagen.'
  } finally {
    loading.value = false
  }
}
</script>
