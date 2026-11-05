<!--
  Datei:        views/Users.vue
  Projekt:      RIMPERIO — Frontend
  Autor:        Phydran6
  Version:      2026.10.31.15.40.00
  Beschreibung: Benutzerverwaltung (nur Admin): anlegen, Rolle/Sperre ändern,
                Passwort zurücksetzen, löschen.
-->
<template>
  <div class="space-y-6 max-w-4xl">
    <!-- Neu anlegen -->
    <div class="card p-5 space-y-4">
      <h2 class="font-semibold">Benutzer anlegen</h2>
      <div class="grid sm:grid-cols-4 gap-3">
        <input v-model="nu.username" class="input" placeholder="Benutzername" />
        <input v-model="nu.password" type="password" class="input" placeholder="Passwort (min. 6)" />
        <select v-model="nu.role" class="input">
          <option value="admin">admin</option>
          <option value="operator">operator</option>
          <option value="readonly">readonly</option>
        </select>
        <button class="btn-primary" :disabled="!nu.username || nu.password.length < 6" @click="create">Anlegen</button>
      </div>
      <p v-if="err" class="text-sm text-red-300">{{ err }}</p>
    </div>

    <!-- Liste -->
    <div class="card overflow-hidden">
      <div class="overflow-x-auto">
        <table class="w-full">
          <thead><tr><th class="th">Benutzer</th><th class="th">Rolle</th><th class="th">Aktiv</th><th class="th">Zuletzt angemeldet</th><th class="th"></th></tr></thead>
          <tbody>
            <tr v-for="u in users" :key="u.id" class="hover:bg-white/5">
              <td class="td font-medium text-slate-100">{{ u.username }}</td>
              <td class="td">
                <select :value="u.role" class="input !py-1 max-w-[10rem]" @change="setRole(u, $event.target.value)">
                  <option value="admin">admin</option>
                  <option value="operator">operator</option>
                  <option value="readonly">readonly</option>
                </select>
              </td>
              <td class="td">
                <button class="badge" :class="u.is_active ? 'badge-online' : 'badge-offline'" @click="toggleActive(u)">
                  {{ u.is_active ? 'aktiv' : 'gesperrt' }}
                </button>
              </td>
              <td class="td text-slate-500">{{ fmtTime(u.last_login) }}</td>
              <td class="td text-right space-x-2 whitespace-nowrap">
                <button class="btn-ghost !py-1 text-xs" @click="resetPw(u)">Passwort</button>
                <button class="btn-danger !py-1 text-xs" @click="remove(u)">Löschen</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <p v-if="actionMsg" class="text-sm text-emerald-300">{{ actionMsg }}</p>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { fmtTime } from '../util'

const users = ref([])
const nu = reactive({ username: '', password: '', role: 'operator' })
const err = ref('')
const actionMsg = ref('')

function flash(m) { actionMsg.value = m; setTimeout(() => (actionMsg.value = ''), 2500) }

async function load() {
  try { users.value = await api.get('/users') } catch (e) { err.value = e.message }
}
async function create() {
  err.value = ''
  try {
    await api.post('/users', { username: nu.username, password: nu.password, role: nu.role })
    nu.username = ''; nu.password = ''; nu.role = 'operator'
    await load(); flash('Benutzer angelegt.')
  } catch (e) { err.value = e.message }
}
async function setRole(u, role) {
  try { await api.patch(`/users/${u.id}`, { role }); await load(); flash('Rolle geändert.') }
  catch (e) { err.value = e.message; await load() }
}
async function toggleActive(u) {
  try { await api.patch(`/users/${u.id}`, { is_active: !u.is_active }); await load() }
  catch (e) { err.value = e.message }
}
async function resetPw(u) {
  const p = prompt(`Neues Passwort für ${u.username} (min. 6 Zeichen):`)
  if (!p) return
  try { await api.patch(`/users/${u.id}`, { new_password: p }); flash('Passwort gesetzt.') }
  catch (e) { err.value = e.message }
}
async function remove(u) {
  if (!confirm(`Benutzer ${u.username} löschen?`)) return
  try { await api.del(`/users/${u.id}`); await load(); flash('Benutzer gelöscht.') }
  catch (e) { err.value = e.message }
}

onMounted(load)
</script>
