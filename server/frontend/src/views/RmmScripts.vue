<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Fernwartung: Skript-Bibliothek
     ============================================================================== -->
<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">Skripte</h1>
        <p class="page-subtitle">Wiederverwendbare Skripte. Ausführen: in der Ansicht eines Rechners unter „Befehle &amp; Skripte“.</p>
      </div>
      <button v-if="auth.isAdmin" class="btn btn-primary btn-sm" @click="open()"><AppIcon name="plus" :size="16" /> Neues Skript</button>
    </div>

    <p v-if="error" class="rmm-note is-error">{{ error }}</p>

    <section v-if="form" class="card mb-4">
      <div class="card-header"><h2 class="card-title">{{ form.id ? 'Skript bearbeiten' : 'Neues Skript' }}</h2></div>
      <div class="card-body rmm-form">
        <label>Name<input v-model="form.name" class="input" /></label>
        <label>Kategorie<input v-model="form.category" class="input" /></label>
        <label>Plattform
          <select v-model="form.platform" class="select"><option v-for="(l, v) in PLATFORM_LABEL" :key="v" :value="v">{{ l }}</option></select>
        </label>
        <label>Shell
          <select v-model="form.shell" class="select"><option v-for="s in SHELLS" :key="s.value" :value="s.value">{{ s.label }}</option></select>
        </label>
        <label>Zeitlimit (s)<input v-model.number="form.default_timeout" type="number" min="1" class="input" /></label>
        <label class="is-wide">Beschreibung<input v-model="form.description" class="input" /></label>
        <label class="is-wide">Skript<textarea v-model="form.body" class="textarea rmm-code" rows="12"></textarea></label>
        <div class="is-wide rmm-actions">
          <button class="btn btn-primary btn-sm" :disabled="!form.name || !form.body" @click="save">Speichern</button>
          <button class="btn btn-ghost btn-sm" @click="form = null">Abbrechen</button>
        </div>
      </div>
    </section>

    <section class="card">
      <div class="rmm-table-wrap">
        <table class="table">
          <thead><tr><th>Name</th><th>Kategorie</th><th>Plattform</th><th>Shell</th><th>Geändert</th><th></th></tr></thead>
          <tbody>
            <tr v-if="!scripts.length"><td colspan="6" class="rmm-muted">Noch keine Skripte.</td></tr>
            <tr v-for="s in scripts" :key="s.id">
              <td><strong>{{ s.name }}</strong><div class="rmm-muted">{{ s.description }}</div></td>
              <td>{{ s.category }}</td><td>{{ PLATFORM_LABEL[s.platform] || s.platform }}</td>
              <td class="rmm-code">{{ s.shell }}</td><td>{{ fmtTime(s.updated_at) }}</td>
              <td class="rmm-actions" style="justify-content: flex-end">
                <button v-if="auth.isAdmin" class="btn btn-secondary btn-sm" @click="open(s)">Bearbeiten</button>
                <button v-if="auth.isAdmin" class="btn btn-danger btn-sm" @click="remove(s)">Löschen</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'
import { fmtTime, PLATFORM_LABEL, SHELLS } from '../utils/rmm'

const auth = useAuthStore()
const scripts = ref([])
const form = ref(null)
const error = ref('')

const blank = () => ({ name: '', category: 'Allgemein', platform: 'windows', shell: 'powershell',
  default_timeout: 300, description: '', body: '' })

async function load() {
  try { scripts.value = await auth.api('/api/rmm/scripts'); error.value = '' } catch (e) { error.value = e.message }
}

function open(s = null) { form.value = s ? { ...s } : blank() }

async function save() {
  const { id, created_by, created_at, updated_at, ...body } = form.value
  try {
    await auth.api(id ? `/api/rmm/scripts/${id}` : '/api/rmm/scripts', { method: id ? 'PUT' : 'POST', body })
    form.value = null
    await load()
  } catch (e) { error.value = e.message }
}

async function remove(s) {
  if (!confirm(`Skript „${s.name}“ löschen?`)) return
  try { await auth.api(`/api/rmm/scripts/${s.id}`, { method: 'DELETE' }); await load() } catch (e) { error.value = e.message }
}

onMounted(load)
</script>
