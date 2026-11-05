<!--
  Datei:        views/Settings.vue
  Projekt:      RIMPERIO — Frontend
  Autor:        Phydran6
  Version:      2026.10.31.15.40.00
  Beschreibung: Laufzeiteinstellungen: Alarm-Schwellen, Enrollment-Geheimnis,
                eigenes Passwort. Alles im Browser.
-->
<template>
  <div class="space-y-6 max-w-3xl">
    <!-- Schwellen -->
    <div class="card p-5 space-y-4">
      <h2 class="font-semibold">Alarm-Schwellen</h2>
      <p class="text-sm text-slate-400">Über diesen Werten meldet RIMPERIO einen Alarm; fällt der Wert wieder, löst er sich selbst auf.</p>
      <div class="grid sm:grid-cols-2 gap-4">
        <div><label class="label">CPU (%)</label><input v-model.number="th.cpu_percent" type="number" class="input" :disabled="!isAdmin" /></div>
        <div><label class="label">RAM (%)</label><input v-model.number="th.mem_percent" type="number" class="input" :disabled="!isAdmin" /></div>
        <div><label class="label">Platte (%)</label><input v-model.number="th.disk_percent" type="number" class="input" :disabled="!isAdmin" /></div>
        <div><label class="label">Offline nach (s)</label><input v-model.number="th.offline_after" type="number" class="input" :disabled="!isAdmin" /></div>
      </div>
      <div class="flex items-center gap-3">
        <button v-if="isAdmin" class="btn-primary" @click="saveThresholds">Schwellen speichern</button>
        <span v-if="thMsg" class="text-sm text-emerald-300">{{ thMsg }}</span>
      </div>
    </div>

    <!-- Enrollment-Geheimnis -->
    <div v-if="isAdmin" class="card p-5 space-y-4">
      <h2 class="font-semibold">Enrollment-Geheimnis</h2>
      <p class="text-sm text-slate-400">Mit diesem Geheimnis melden sich neue Agents an. Beim Wechsel bleiben bereits verbundene Agents verbunden.</p>
      <div class="flex gap-3">
        <input v-model="secret" class="input font-mono" />
        <button class="btn-ghost" @click="rotate">Wechseln</button>
      </div>
      <span v-if="secretMsg" class="text-sm text-emerald-300">{{ secretMsg }}</span>
    </div>

    <!-- Passwort ändern -->
    <div class="card p-5 space-y-4">
      <h2 class="font-semibold">Eigenes Passwort</h2>
      <div class="grid sm:grid-cols-2 gap-4">
        <div><label class="label">Altes Passwort</label><input v-model="pw.old" type="password" class="input" /></div>
        <div><label class="label">Neues Passwort</label><input v-model="pw.new" type="password" class="input" /></div>
      </div>
      <div class="flex items-center gap-3">
        <button class="btn-primary" :disabled="!pw.old || pw.new.length < 6" @click="changePw">Passwort ändern</button>
        <span v-if="pwMsg" class="text-sm" :class="pwOk ? 'text-emerald-300' : 'text-red-300'">{{ pwMsg }}</span>
      </div>
    </div>

    <div class="text-xs text-slate-500">
      Check-in-Takt: {{ checkin }} s · Version {{ version }}
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { useAuth } from '../stores/auth'

const auth = useAuth()
const isAdmin = computed(() => auth.isAdmin)

const th = reactive({ cpu_percent: 90, mem_percent: 90, disk_percent: 90, offline_after: 300 })
const checkin = ref(60)
const version = ref('')
const thMsg = ref('')

const secret = ref('')
const secretMsg = ref('')

const pw = reactive({ old: '', new: '' })
const pwMsg = ref('')
const pwOk = ref(false)

async function load() {
  try {
    const s = await api.get('/settings')
    Object.assign(th, s.thresholds)
    checkin.value = s.checkin_interval
    version.value = s.version
  } catch { /* */ }
  if (isAdmin.value) {
    try { const r = await api.get('/settings/enroll-secret'); secret.value = r.secret } catch { /* */ }
  }
}
async function saveThresholds() {
  thMsg.value = ''
  await api.put('/settings/thresholds', th)
  thMsg.value = 'Gespeichert.'
  setTimeout(() => (thMsg.value = ''), 2000)
}
async function rotate() {
  secretMsg.value = ''
  const r = await api.post('/settings/enroll-secret', { secret: secret.value })
  secretMsg.value = r.detail || 'Gewechselt.'
  setTimeout(() => (secretMsg.value = ''), 3000)
}
async function changePw() {
  pwMsg.value = ''
  try {
    await api.post('/auth/password', { old_password: pw.old, new_password: pw.new })
    pwOk.value = true; pwMsg.value = 'Passwort geändert.'
    pw.old = ''; pw.new = ''
  } catch (e) {
    pwOk.value = false; pwMsg.value = e.message
  }
}

onMounted(load)
</script>
