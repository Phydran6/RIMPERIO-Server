<!--
  Datei:        views/Scripts.vue
  Projekt:      LRMM — Frontend
  Autor:        Phydran6
  Version:      2026.11.05.12.00.00
  Beschreibung: Skript-Bibliothek: Skripte anlegen, bearbeiten, löschen.
                Ausgeführt werden sie auf einem Agent (siehe Agent-Detail).
-->
<template>
  <div class="space-y-5">
    <div class="flex items-center justify-between">
      <p class="text-sm text-slate-400">Wiederverwendbare Skripte. Ausführen: in der Agent-Ansicht unter „Skript ausführen".</p>
      <button v-if="canEdit" class="btn-primary" @click="openNew">Neues Skript</button>
    </div>

    <!-- Formular -->
    <div v-if="editing" class="card p-5 space-y-3">
      <h2 class="font-semibold">{{ form.id ? 'Skript bearbeiten' : 'Neues Skript' }}</h2>
      <div class="grid sm:grid-cols-3 gap-3">
        <div><label class="label">Name</label><input v-model="form.name" class="input" /></div>
        <div><label class="label">Kategorie</label><input v-model="form.category" class="input" /></div>
        <div>
          <label class="label">Plattform</label>
          <select v-model="form.platform" class="input"><option>any</option><option>windows</option><option>linux</option></select>
        </div>
        <div>
          <label class="label">Shell</label>
          <select v-model="form.shell" class="input"><option>powershell</option><option>cmd</option><option>bash</option><option>sh</option></select>
        </div>
        <div><label class="label">Zeitlimit (s)</label><input v-model.number="form.default_timeout" type="number" class="input" /></div>
        <div class="sm:col-span-3"><label class="label">Beschreibung</label><input v-model="form.description" class="input" /></div>
      </div>
      <div><label class="label">Skript</label><textarea v-model="form.body" rows="8" class="input font-mono"></textarea></div>
      <p v-if="error" class="text-sm text-red-300">{{ error }}</p>
      <div class="flex gap-2">
        <button class="btn-primary" :disabled="!form.name || !form.body" @click="save">Speichern</button>
        <button class="btn-ghost" @click="editing = false">Abbrechen</button>
      </div>
    </div>

    <!-- Liste -->
    <div class="card overflow-hidden">
      <div class="overflow-x-auto">
        <table class="w-full">
          <thead><tr><th class="th">Name</th><th class="th">Kategorie</th><th class="th">Shell</th><th class="th">Plattform</th><th class="th"></th></tr></thead>
          <tbody>
            <tr v-for="s in scripts" :key="s.id" class="hover:bg-white/5">
              <td class="td font-medium text-slate-100">{{ s.name }}<div class="text-xs text-slate-500">{{ s.description }}</div></td>
              <td class="td text-slate-300">{{ s.category }}</td>
              <td class="td text-slate-300">{{ s.shell }}</td>
              <td class="td text-slate-300">{{ s.platform }}</td>
              <td class="td text-right space-x-2 whitespace-nowrap">
                <button v-if="canEdit" class="btn-ghost !py-1 text-xs" @click="openEdit(s)">Bearbeiten</button>
                <button v-if="canEdit" class="btn-danger !py-1 text-xs" @click="remove(s)">Löschen</button>
              </td>
            </tr>
            <tr v-if="!scripts.length"><td class="td text-center text-slate-500 py-10" colspan="5">Noch keine Skripte.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { useAuth } from '../stores/auth'

const auth = useAuth()
const canEdit = computed(() => auth.canOperate)
const scripts = ref([])
const editing = ref(false)
const error = ref('')
const blank = { id: null, name: '', category: 'Allgemein', platform: 'any', shell: 'powershell', default_timeout: 300, description: '', body: '' }
const form = reactive({ ...blank })

async function load() { try { scripts.value = await api.get('/scripts') } catch (e) { error.value = e.message } }
function openNew() { Object.assign(form, blank); editing.value = true; error.value = '' }
function openEdit(s) { Object.assign(form, s); editing.value = true; error.value = '' }

async function save() {
  error.value = ''
  const payload = { name: form.name, description: form.description, shell: form.shell, body: form.body, default_timeout: form.default_timeout, platform: form.platform, category: form.category }
  try {
    if (form.id) await api.put(`/scripts/${form.id}`, payload)
    else await api.post('/scripts', payload)
    editing.value = false
    await load()
  } catch (e) { error.value = e.message }
}
async function remove(s) {
  if (!confirm(`Skript „${s.name}" löschen?`)) return
  await api.del(`/scripts/${s.id}`); await load()
}

onMounted(load)
</script>
