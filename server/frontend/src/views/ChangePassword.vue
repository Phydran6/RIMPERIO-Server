<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Startpasswort ersetzen. Kommt nach der ersten
                   Anmeldung mit dem Startpasswort (oder einem vom Admin
                   vergebenen) - bevor irgendetwas anderes geht.
     ============================================================================== -->
<template>
  <div class="cp-page">
    <form class="cp-card" @submit.prevent="save">
      <div class="cp-icon"><AppIcon name="key" :size="22" /></div>
      <h1 class="cp-title">Eigenes Passwort setzen</h1>
      <p class="cp-text">
        Du bist mit einem Startpasswort angemeldet. Das gilt nur für diese eine Anmeldung —
        bitte jetzt ein eigenes Passwort setzen (mindestens 10 Zeichen). Danach geht es direkt weiter.
      </p>

      <label class="label" for="cp-current">Aktuelles (Start-)Passwort</label>
      <input id="cp-current" v-model="current" type="password" class="input" autocomplete="current-password" required>

      <label class="label" for="cp-new">Neues Passwort</label>
      <input id="cp-new" v-model="next" type="password" class="input" autocomplete="new-password" minlength="10" required>

      <label class="label" for="cp-repeat">Neues Passwort wiederholen</label>
      <input id="cp-repeat" v-model="repeat" type="password" class="input" autocomplete="new-password" required>

      <p v-if="error" class="cp-error" role="alert">{{ error }}</p>

      <button class="btn btn-primary w-full" :disabled="busy">{{ busy ? 'Speichere…' : 'Passwort setzen und weiter' }}</button>
      <button type="button" class="btn btn-ghost w-full" @click="logout">Abmelden</button>
    </form>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'

const auth = useAuthStore()
const router = useRouter()
const current = ref('')
const next = ref('')
const repeat = ref('')
const error = ref('')
const busy = ref(false)

async function save() {
  error.value = ''
  if (next.value.length < 10) { error.value = 'Das neue Passwort braucht mindestens 10 Zeichen.'; return }
  if (next.value !== repeat.value) { error.value = 'Die beiden neuen Passwörter stimmen nicht überein.'; return }
  busy.value = true
  try {
    const data = await auth.api('/api/auth/change-password', {
      method: 'POST', body: { current_password: current.value, new_password: next.value },
    })
    auth.adoptToken(data.access_token)
    await auth.fetchUser()
    router.replace('/')
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

function logout() {
  auth.logout()
  router.replace('/login')
}
</script>

<style scoped>
.cp-page { min-height: 100%; display: flex; align-items: center; justify-content: center; padding: 1rem; background: var(--color-bg); }
.cp-card { width: 100%; max-width: 26rem; display: flex; flex-direction: column; gap: .5rem; padding: 1.75rem; border: 1px solid var(--color-border); border-radius: var(--radius-xl); background: var(--color-surface); box-shadow: var(--shadow-lg); }
.cp-icon { width: 2.75rem; height: 2.75rem; display: flex; align-items: center; justify-content: center; border-radius: var(--radius); background: var(--primary-soft); color: var(--color-primary); }
.cp-title { font-size: 1.25rem; font-weight: 700; color: var(--color-text-primary); margin-top: .25rem; }
.cp-text { font-size: .875rem; color: var(--color-text-secondary); margin-bottom: .5rem; }
.cp-error { font-size: .8125rem; color: var(--color-danger); }
.label { margin-top: .25rem; }
</style>
