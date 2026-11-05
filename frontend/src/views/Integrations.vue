<!--
  Datei:        views/Integrations.vue
  Projekt:      LRMM — Frontend
  Autor:        Phydran6
  Version:      2026.11.05.12.00.00
  Beschreibung: Ausgehende Webhooks (z. B. n8n) und Integrations-Token für
                Automatisierungen/KI. Nur für Admins.
-->
<template>
  <div class="space-y-6 max-w-4xl">
    <!-- Webhooks -->
    <div class="card p-5 space-y-4">
      <h2 class="font-semibold">Webhooks</h2>
      <p class="text-sm text-slate-400">Bei einem Ereignis schickt LRMM eine signierte JSON-Nachricht an die URL — ideal für n8n. Signatur im Header <span class="font-mono">X-LRMM-Signature</span> (HMAC-SHA256 über den Rumpf).</p>

      <div class="grid sm:grid-cols-2 gap-3">
        <input v-model="wh.name" class="input" placeholder="Name" />
        <input v-model="wh.url" class="input" placeholder="https://n8n.example.de/webhook/lrmm" />
        <input v-model="wh.secret" class="input sm:col-span-2" placeholder="Secret (für die Signatur, optional aber empfohlen)" />
      </div>
      <div class="flex flex-wrap gap-3">
        <label v-for="e in eventTypes" :key="e" class="flex items-center gap-1.5 text-xs text-slate-400">
          <input type="checkbox" :value="e" v-model="wh.events" class="accent-brand-500" /> {{ e }}
        </label>
      </div>
      <button class="btn-primary" :disabled="!wh.name || !wh.url" @click="createWebhook">Webhook anlegen</button>

      <div class="overflow-x-auto">
        <table class="w-full">
          <thead><tr><th class="th">Name</th><th class="th">URL</th><th class="th">Ereignisse</th><th class="th">Zuletzt</th><th class="th"></th></tr></thead>
          <tbody>
            <tr v-for="w in webhooks" :key="w.id" class="hover:bg-white/5">
              <td class="td text-slate-100">{{ w.name }}</td>
              <td class="td text-slate-400 max-w-[14rem] truncate">{{ w.url }}</td>
              <td class="td text-slate-400 text-xs">{{ w.events.length ? w.events.join(', ') : 'alle' }}</td>
              <td class="td text-xs" :class="w.last_status && w.last_status < 300 ? 'text-emerald-300' : (w.last_status ? 'text-red-300' : 'text-slate-500')">
                {{ w.last_status ? ('HTTP ' + w.last_status) : '—' }}<div v-if="w.last_error" class="text-red-400/70 truncate max-w-[10rem]">{{ w.last_error }}</div>
              </td>
              <td class="td text-right space-x-2 whitespace-nowrap">
                <button class="btn-ghost !py-1 text-xs" @click="testWebhook(w)">Test</button>
                <button class="btn-danger !py-1 text-xs" @click="removeWebhook(w)">Löschen</button>
              </td>
            </tr>
            <tr v-if="!webhooks.length"><td class="td text-center text-slate-500 py-6" colspan="5">Noch keine Webhooks.</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Integrations-Token -->
    <div class="card p-5 space-y-4">
      <h2 class="font-semibold">Integrations-Token</h2>
      <p class="text-sm text-slate-400">Langlebiger Token für n8n oder eine KI, um die LRMM-API anzusprechen (als <span class="font-mono">Authorization: Bearer …</span>). Rolle bestimmt die Rechte.</p>
      <div class="grid sm:grid-cols-3 gap-3">
        <input v-model="tk.name" class="input" placeholder="Name, z. B. n8n-telegram" />
        <select v-model="tk.role" class="input"><option value="operator">operator</option><option value="readonly">readonly</option><option value="admin">admin</option></select>
        <button class="btn-primary" :disabled="!tk.name" @click="createToken">Token erzeugen</button>
      </div>
      <div v-if="newToken" class="card p-3 bg-emerald-500/10 border-emerald-500/30 space-y-2">
        <p class="text-sm text-emerald-200">Einmalig sichtbar — jetzt kopieren:</p>
        <CodeBlock :text="newToken" />
      </div>
      <div class="overflow-x-auto">
        <table class="w-full">
          <thead><tr><th class="th">Name</th><th class="th">Rolle</th><th class="th">Zuletzt genutzt</th><th class="th"></th></tr></thead>
          <tbody>
            <tr v-for="t in tokens" :key="t.id" class="hover:bg-white/5">
              <td class="td text-slate-100">{{ t.name }}</td>
              <td class="td text-slate-300">{{ t.role }}</td>
              <td class="td text-slate-500">{{ t.last_used ? fmtTime(t.last_used) : 'nie' }}</td>
              <td class="td text-right"><button class="btn-danger !py-1 text-xs" @click="removeToken(t)">Löschen</button></td>
            </tr>
            <tr v-if="!tokens.length"><td class="td text-center text-slate-500 py-6" colspan="4">Noch keine Token.</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <p v-if="error" class="text-sm text-red-300">{{ error }}</p>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { fmtTime } from '../util'
import CodeBlock from '../components/CodeBlock.vue'

const eventTypes = ref([])
const webhooks = ref([])
const tokens = ref([])
const newToken = ref('')
const error = ref('')

const wh = reactive({ name: '', url: '', secret: '', events: [] })
const tk = reactive({ name: '', role: 'operator' })

async function loadAll() {
  try {
    eventTypes.value = await api.get('/integrations/events')
    webhooks.value = await api.get('/integrations/webhooks')
    tokens.value = await api.get('/integrations/tokens')
  } catch (e) { error.value = e.message }
}

async function createWebhook() {
  error.value = ''
  try {
    await api.post('/integrations/webhooks', { name: wh.name, url: wh.url, secret: wh.secret, events: wh.events, enabled: true })
    Object.assign(wh, { name: '', url: '', secret: '', events: [] })
    await loadAll()
  } catch (e) { error.value = e.message }
}
async function testWebhook(w) {
  try { await api.post(`/integrations/webhooks/${w.id}/test`); await loadAll() }
  catch (e) { error.value = e.message }
}
async function removeWebhook(w) {
  if (!confirm(`Webhook „${w.name}" löschen?`)) return
  await api.del(`/integrations/webhooks/${w.id}`); await loadAll()
}

async function createToken() {
  error.value = ''
  newToken.value = ''
  try {
    const r = await api.post('/integrations/tokens', { name: tk.name, role: tk.role })
    newToken.value = r.token
    tk.name = ''
    await loadAll()
  } catch (e) { error.value = e.message }
}
async function removeToken(t) {
  if (!confirm(`Token „${t.name}" löschen?`)) return
  await api.del(`/integrations/tokens/${t.id}`); await loadAll()
}

onMounted(loadAll)
</script>
