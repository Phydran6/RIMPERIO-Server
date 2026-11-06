<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.05.13.20.58.33
     Beschreibung: RIMPERIO - Webhook-Verwaltung mit Theme-Support
     ============================================================================== -->

<template>
  <div class="p-6">
    <div class="flex justify-between items-center mb-6">
      <h1 class="text-2xl font-bold" :style="{ color: 'var(--color-text-primary)' }">Webhooks</h1>
      <!-- Anlegen und Ändern nur für Administratoren: ein Webhook gibt Logdaten
           OHNE Anmeldung heraus, der Token in der Adresse genügt. Wer einen
           anlegen darf, kann sich einen dauerhaften, offenen Abgriff bauen. -->
      <button
        v-if="authStore.isAdmin"
        @click="showCreateModal = true"
        class="text-white px-4 py-2 rounded hover:opacity-90"
        :style="{ backgroundColor: 'var(--color-primary)' }"
      >
        + Neuer Webhook
      </button>
    </div>
    
    <!-- Info Box -->
    <div class="rounded-lg p-4 mb-6" :style="infoBoxStyle">
      <h3 class="font-semibold mb-2" :style="{ color: 'var(--color-primary)' }">🔗 Webhook Integration</h3>
      <p class="text-sm" :style="{ color: 'var(--color-text-secondary)' }">
        Webhooks ermöglichen den Zugriff auf Logs ohne Login. Ideal für n8n, Make oder andere Automatisierungstools.
        Die URL kann direkt als HTTP-Request aufgerufen werden.
      </p>
    </div>
    
    <!-- Webhooks Liste -->
    <div class="space-y-4">
      <div 
        v-for="webhook in webhooks" 
        :key="webhook.id"
        class="rounded-lg shadow p-6"
        :style="cardStyle"
      >
        <div class="flex justify-between items-start mb-4">
          <div>
            <h3 class="font-semibold text-lg flex items-center gap-2" :style="{ color: 'var(--color-text-primary)' }">
              {{ webhook.name }}
              <span 
                class="px-2 py-1 text-xs rounded-full text-white"
                :class="webhook.is_active ? 'bg-green-500' : 'bg-red-500'"
              >
                {{ webhook.is_active ? 'Aktiv' : 'Inaktiv' }}
              </span>
            </h3>
            <p class="text-sm" :style="{ color: 'var(--color-text-muted)' }">{{ webhook.description || 'Keine Beschreibung' }}</p>
          </div>
          <div class="text-right text-sm" :style="{ color: 'var(--color-text-muted)' }">
            <p>{{ webhook.call_count }} Aufrufe</p>
            <p v-if="webhook.last_called_at">Zuletzt: {{ formatTime(webhook.last_called_at) }}</p>
          </div>
        </div>
        
        <!-- Webhook URL -->
        <div class="rounded p-3 mb-4" :style="codeBlockStyle">
          <label class="text-xs mb-1 block" :style="{ color: 'var(--color-text-muted)' }">Webhook URL (kein Login erforderlich)</label>
          <div class="flex gap-2">
            <code class="flex-1 text-sm px-3 py-2 rounded font-mono overflow-x-auto" :style="{ backgroundColor: 'var(--color-surface)', color: 'var(--color-text-primary)' }">
              {{ getWebhookUrl(webhook) }}
            </code>
            <button
              @click="copyUrl(webhook)"
              class="px-3 py-2 rounded hover:opacity-80 text-sm"
              :style="buttonSecondaryStyle"
            >
              Kopieren
            </button>
          </div>
        </div>
        
        <!-- Filter Anzeige -->
        <div class="grid grid-cols-3 gap-4 text-sm mb-4">
          <div>
            <label :style="{ color: 'var(--color-text-muted)' }">Hostname Filter:</label>
            <p :style="{ color: 'var(--color-text-primary)' }">{{ webhook.filters?.hostname || '-' }}</p>
          </div>
          <div>
            <label :style="{ color: 'var(--color-text-muted)' }">Source Filter:</label>
            <p :style="{ color: 'var(--color-text-primary)' }">{{ webhook.filters?.source || '-' }}</p>
          </div>
          <div>
            <label :style="{ color: 'var(--color-text-muted)' }">Level Filter:</label>
            <p :style="{ color: 'var(--color-text-primary)' }">{{ webhook.filters?.level?.join(', ') || 'Alle' }}</p>
          </div>
        </div>
        
        <div class="flex justify-between text-sm">
          <div :style="{ color: 'var(--color-text-muted)' }">
            Max. {{ webhook.max_results }} Ergebnisse | 
            {{ webhook.include_raw ? 'Mit Raw-Message' : 'Ohne Raw-Message' }}
          </div>
          <div class="flex gap-4">
            <button @click="testWebhook(webhook)" class="hover:underline" :style="{ color: 'var(--color-success)' }">Testen</button>
            <template v-if="authStore.isAdmin">
              <button @click="regenerateToken(webhook)" class="hover:underline" :style="{ color: 'var(--color-warning)' }">Token erneuern</button>
              <button @click="editWebhook(webhook)" class="hover:underline" :style="{ color: 'var(--color-primary)' }">Bearbeiten</button>
              <button @click="deleteWebhook(webhook)" class="hover:underline" :style="{ color: 'var(--color-danger)' }">Löschen</button>
            </template>
          </div>
        </div>
      </div>
      
      <div v-if="!webhooks.length" class="rounded-lg shadow p-12 text-center" :style="cardStyle">
        <p :style="{ color: 'var(--color-text-muted)' }">Keine Webhooks vorhanden</p>
        <button
          v-if="authStore.isAdmin"
          @click="showCreateModal = true"
          class="mt-4 hover:underline"
          :style="{ color: 'var(--color-primary)' }"
        >
          Ersten Webhook erstellen
        </button>
        <p v-else class="mt-2 text-sm" :style="{ color: 'var(--color-text-muted)' }">
          Anlegen darf sie ein Administrator — ein Webhook gibt Logdaten ohne
          Anmeldung heraus.
        </p>
      </div>
    </div>
    
    <!-- Create/Edit Modal -->
    <div 
      v-if="showCreateModal || editingWebhook" 
      class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50"
      @click.self="closeModal"
    >
      <div class="rounded-lg shadow-xl w-full max-w-lg max-h-[90vh] overflow-auto" :style="cardStyle">
        <div class="p-4 border-b" :style="{ borderColor: 'var(--color-border)' }">
          <h2 class="text-lg font-semibold" :style="{ color: 'var(--color-text-primary)' }">
            {{ editingWebhook ? 'Webhook bearbeiten' : 'Neuer Webhook' }}
          </h2>
        </div>
        <form @submit.prevent="saveWebhook" class="p-4 space-y-4">
          <div>
            <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Name *</label>
            <input
              v-model="form.name"
              type="text"
              required
              class="w-full rounded px-3 py-2"
              :style="inputStyle"
              placeholder="z.B. n8n Error-Alerts"
            >
          </div>
          <div>
            <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Beschreibung</label>
            <textarea
              v-model="form.description"
              class="w-full rounded px-3 py-2"
              :style="inputStyle"
              rows="2"
              placeholder="Wofür wird dieser Webhook verwendet?"
            ></textarea>
          </div>
          
          <div class="border-t pt-4" :style="{ borderColor: 'var(--color-border)' }">
            <h3 class="font-medium mb-3" :style="{ color: 'var(--color-text-primary)' }">Filter (optional)</h3>
            <div class="space-y-3">
              <div>
                <label class="block text-sm mb-1" :style="{ color: 'var(--color-text-muted)' }">Hostname (enthält)</label>
                <input
                  v-model="form.filters.hostname"
                  type="text"
                  class="w-full rounded px-3 py-2"
                  :style="inputStyle"
                  placeholder="z.B. unifi oder server01"
                >
              </div>
              <div>
                <label class="block text-sm mb-1" :style="{ color: 'var(--color-text-muted)' }">Source (enthält)</label>
                <input
                  v-model="form.filters.source"
                  type="text"
                  class="w-full rounded px-3 py-2"
                  :style="inputStyle"
                  placeholder="z.B. sshd oder nginx"
                >
              </div>
              <div>
                <label class="block text-sm mb-1" :style="{ color: 'var(--color-text-muted)' }">Log Level</label>
                <div class="flex flex-wrap gap-2">
                  <label v-for="level in allLevels" :key="level" class="flex items-center" :style="{ color: 'var(--color-text-secondary)' }">
                    <input
                      type="checkbox"
                      :value="level"
                      v-model="form.filters.level"
                      class="mr-1"
                    >
                    <span class="text-sm">{{ level }}</span>
                  </label>
                </div>
              </div>
            </div>
          </div>
          
          <div class="border-t pt-4" :style="{ borderColor: 'var(--color-border)' }">
            <h3 class="font-medium mb-3" :style="{ color: 'var(--color-text-primary)' }">Optionen</h3>
            <div class="space-y-3">
              <div>
                <label class="block text-sm mb-1" :style="{ color: 'var(--color-text-muted)' }">Max. Ergebnisse</label>
                <input
                  v-model.number="form.max_results"
                  type="number"
                  min="1"
                  max="1000"
                  class="w-full rounded px-3 py-2"
                  :style="inputStyle"
                >
              </div>
              <label class="flex items-center" :style="{ color: 'var(--color-text-secondary)' }">
                <input v-model="form.include_raw" type="checkbox" class="mr-2">
                <span class="text-sm">Raw-Message mitliefern</span>
              </label>
              <label class="flex items-center" :style="{ color: 'var(--color-text-secondary)' }">
                <input v-model="form.is_active" type="checkbox" class="mr-2">
                <span class="text-sm">Webhook aktiv</span>
              </label>
            </div>
          </div>
          
          <div class="flex justify-end gap-2 pt-4 border-t" :style="{ borderColor: 'var(--color-border)' }">
            <button
              type="button"
              @click="closeModal"
              class="px-4 py-2 rounded hover:opacity-80"
              :style="buttonSecondaryStyle"
            >
              Abbrechen
            </button>
            <button
              type="submit"
              class="px-4 py-2 text-white rounded hover:opacity-90"
              :style="{ backgroundColor: 'var(--color-primary)' }"
            >
              Speichern
            </button>
          </div>
        </form>
      </div>
    </div>
    
    <!-- Test Result Modal -->
    <div 
      v-if="testResult" 
      class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50"
      @click.self="testResult = null"
    >
      <div class="rounded-lg shadow-xl w-full max-w-2xl max-h-[80vh] overflow-auto" :style="cardStyle">
        <div class="p-4 border-b flex justify-between items-center" :style="{ borderColor: 'var(--color-border)' }">
          <h2 class="text-lg font-semibold" :style="{ color: 'var(--color-text-primary)' }">Webhook Test Ergebnis</h2>
          <button @click="testResult = null" class="hover:opacity-70" :style="{ color: 'var(--color-text-muted)' }">✕</button>
        </div>
        <div class="p-4">
          <p class="mb-2" :style="{ color: 'var(--color-text-secondary)' }">{{ testResult.length }} Logs zurückgegeben:</p>
          <pre class="p-4 rounded text-sm overflow-auto max-h-96" :style="codeBlockStyle">{{ JSON.stringify(testResult, null, 2) }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'

const authStore = useAuthStore()

const webhooks = ref([])
const showCreateModal = ref(false)
const editingWebhook = ref(null)
const testResult = ref(null)

const allLevels = ['emergency', 'alert', 'critical', 'error', 'warning', 'notice', 'info', 'debug']

const defaultForm = () => ({
  name: '',
  description: '',
  filters: { hostname: '', source: '', level: [] },
  max_results: 100,
  include_raw: false,
  is_active: true
})

const form = ref(defaultForm())

// Computed Styles
const cardStyle = computed(() => ({
  backgroundColor: 'var(--color-surface)',
  borderColor: 'var(--color-border)'
}))

const inputStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  borderColor: 'var(--color-border)',
  color: 'var(--color-text-primary)',
  border: '1px solid var(--color-border)'
}))

const buttonSecondaryStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  color: 'var(--color-text-primary)',
  border: '1px solid var(--color-border)'
}))

const codeBlockStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  color: 'var(--color-text-primary)'
}))

const infoBoxStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  border: '1px solid var(--color-primary)'
}))

onMounted(() => loadWebhooks())

async function loadWebhooks() {
  try {
    webhooks.value = await authStore.api('/api/webhooks')
  } catch (e) {
    console.error('Fehler:', e)
  }
}

function getWebhookUrl(webhook) {
  return `${window.location.origin}/api/webhook/${webhook.id}/call?token=${webhook.token}`
}

function copyUrl(webhook) {
  navigator.clipboard.writeText(getWebhookUrl(webhook))
  alert('URL kopiert!')
}

function editWebhook(webhook) {
  editingWebhook.value = webhook
  form.value = {
    name: webhook.name,
    description: webhook.description || '',
    filters: {
      hostname: webhook.filters?.hostname || '',
      source: webhook.filters?.source || '',
      level: webhook.filters?.level || []
    },
    max_results: webhook.max_results,
    include_raw: webhook.include_raw,
    is_active: webhook.is_active
  }
}

function closeModal() {
  showCreateModal.value = false
  editingWebhook.value = null
  form.value = defaultForm()
}

async function saveWebhook() {
  try {
    const data = {
      ...form.value,
      filters: {
        hostname: form.value.filters.hostname || null,
        source: form.value.filters.source || null,
        level: form.value.filters.level.length ? form.value.filters.level : null
      }
    }
    
    if (editingWebhook.value) {
      await authStore.api(`/api/webhooks/${editingWebhook.value.id}`, {
        method: 'PUT',
        body: data
      })
    } else {
      await authStore.api('/api/webhooks', {
        method: 'POST',
        body: data
      })
    }
    closeModal()
    loadWebhooks()
  } catch (e) {
    alert('Fehler: ' + e.message)
  }
}

async function regenerateToken(webhook) {
  if (!confirm('Token wirklich erneuern? Der alte Token wird ungültig!')) return
  
  try {
    await authStore.api(`/api/webhooks/${webhook.id}/regenerate-token`, { method: 'POST' })
    loadWebhooks()
  } catch (e) {
    alert('Fehler: ' + e.message)
  }
}

async function testWebhook(webhook) {
  try {
    const response = await fetch(getWebhookUrl(webhook))
    testResult.value = await response.json()
  } catch (e) {
    alert('Fehler: ' + e.message)
  }
}

async function deleteWebhook(webhook) {
  if (!confirm(`Webhook "${webhook.name}" wirklich löschen?`)) return
  
  try {
    await authStore.api(`/api/webhooks/${webhook.id}`, { method: 'DELETE' })
    loadWebhooks()
  } catch (e) {
    alert('Fehler: ' + e.message)
  }
}

function formatTime(timestamp) {
  if (!timestamp) return '-'
  return new Date(timestamp).toLocaleString('de-DE')
}
</script>