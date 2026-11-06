<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.05.13.20.58.33
     Beschreibung: RIMPERIO - Branding Einstellungen mit Theme-Support
     ============================================================================== -->

<template>
  <div class="p-6">
    <!-- Header -->
    <div class="mb-6">
      <h1 class="text-2xl font-bold" :style="{ color: 'var(--color-text-primary)' }">🎨 Branding Einstellungen</h1>
      <p :style="{ color: 'var(--color-text-muted)' }">Passe das Erscheinungsbild von RIMPERIO an deine Marke an.</p>
    </div>

    <!-- Loading -->
    <div v-if="brandingStore.loading" class="text-center py-12" :style="{ color: 'var(--color-text-muted)' }">
      Lade Einstellungen...
    </div>

    <!-- Settings Grid -->
    <div v-else class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      
      <!-- Allgemeine Einstellungen -->
      <div class="rounded-lg shadow p-6" :style="cardStyle">
        <h2 class="text-lg font-semibold mb-4 pb-2 border-b" :style="{ color: 'var(--color-text-primary)', borderColor: 'var(--color-border)' }">Allgemein</h2>
        
        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Firmenname</label>
            <input v-model="brandingStore.config.company_name" type="text" class="w-full px-3 py-2 rounded" :style="inputStyle" />
          </div>

          <div>
            <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Tagline</label>
            <input v-model="brandingStore.config.tagline" type="text" class="w-full px-3 py-2 rounded" :style="inputStyle" />
          </div>

          <div>
            <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Footer Text</label>
            <input v-model="brandingStore.config.footer_text" type="text" class="w-full px-3 py-2 rounded" :style="inputStyle" />
          </div>

          <div>
            <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Support E-Mail</label>
            <input v-model="brandingStore.config.support_email" type="email" class="w-full px-3 py-2 rounded" :style="inputStyle" />
          </div>
        </div>
      </div>

      <!-- Logo & Favicon -->
      <div class="rounded-lg shadow p-6" :style="cardStyle">
        <h2 class="text-lg font-semibold mb-4 pb-2 border-b" :style="{ color: 'var(--color-text-primary)', borderColor: 'var(--color-border)' }">Logo & Favicon</h2>
        
        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium mb-2" :style="{ color: 'var(--color-text-secondary)' }">Logo</label>
            <div class="flex items-center gap-4">
              <div class="w-20 h-20 rounded-lg flex items-center justify-center overflow-hidden" :style="previewStyle">
                <img v-if="brandingStore.getLogoUrl()" :src="brandingStore.getLogoUrl()" alt="Logo" class="max-w-full max-h-full object-contain" />
                <span v-else class="text-xs" :style="{ color: 'var(--color-text-muted)' }">Kein Logo</span>
              </div>
              <input type="file" accept=".png,.jpg,.jpeg,.svg,.webp" @change="handleLogoUpload" ref="logoInput" class="hidden" />
              <button @click="$refs.logoInput.click()" class="px-4 py-2 rounded hover:opacity-80" :style="buttonSecondaryStyle">
                Logo hochladen
              </button>
            </div>
          </div>

          <div>
            <label class="block text-sm font-medium mb-2" :style="{ color: 'var(--color-text-secondary)' }">Favicon</label>
            <div class="flex items-center gap-4">
              <div class="w-12 h-12 rounded-lg flex items-center justify-center overflow-hidden" :style="previewStyle">
                <img v-if="brandingStore.getFaviconUrl()" :src="brandingStore.getFaviconUrl()" alt="Favicon" class="max-w-full max-h-full object-contain" />
                <span v-else class="text-xs" :style="{ color: 'var(--color-text-muted)' }">—</span>
              </div>
              <input type="file" accept=".ico,.png,.svg" @change="handleFaviconUpload" ref="faviconInput" class="hidden" />
              <button @click="$refs.faviconInput.click()" class="px-4 py-2 rounded hover:opacity-80" :style="buttonSecondaryStyle">
                Favicon hochladen
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Theme Einstellungen -->
      <div class="rounded-lg shadow p-6" :style="cardStyle">
        <h2 class="text-lg font-semibold mb-4 pb-2 border-b" :style="{ color: 'var(--color-text-primary)', borderColor: 'var(--color-border)' }">Theme</h2>
        
        <div class="space-y-4">
          <div>
            <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Standard Theme</label>
            <select v-model="brandingStore.config.default_theme" class="w-full px-3 py-2 rounded" :style="inputStyle">
              <option value="dark">Dark Mode</option>
              <option value="light">Light Mode</option>
            </select>
          </div>

          <div class="flex items-center gap-2">
            <input type="checkbox" id="allow_toggle" v-model="brandingStore.config.allow_theme_toggle" class="w-4 h-4 rounded" />
            <label for="allow_toggle" class="text-sm" :style="{ color: 'var(--color-text-secondary)' }">Theme-Wechsel erlauben</label>
          </div>
        </div>
      </div>

      <!-- Markenfarben -->
      <div class="rounded-lg shadow p-6" :style="cardStyle">
        <h2 class="text-lg font-semibold mb-4 pb-2 border-b" :style="{ color: 'var(--color-text-primary)', borderColor: 'var(--color-border)' }">Markenfarben</h2>
        
        <div class="grid grid-cols-3 gap-4">
          <div>
            <label class="block text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Primär</label>
            <input type="color" v-model="brandingStore.config.primary_color" @input="brandingStore.applyCSS()" class="w-full h-10 rounded cursor-pointer" :style="colorInputStyle" />
            <span class="text-xs font-mono" :style="{ color: 'var(--color-text-muted)' }">{{ brandingStore.config.primary_color }}</span>
          </div>
          <div>
            <label class="block text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Sekundär</label>
            <input type="color" v-model="brandingStore.config.secondary_color" @input="brandingStore.applyCSS()" class="w-full h-10 rounded cursor-pointer" :style="colorInputStyle" />
            <span class="text-xs font-mono" :style="{ color: 'var(--color-text-muted)' }">{{ brandingStore.config.secondary_color }}</span>
          </div>
          <div>
            <label class="block text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Akzent</label>
            <input type="color" v-model="brandingStore.config.accent_color" @input="brandingStore.applyCSS()" class="w-full h-10 rounded cursor-pointer" :style="colorInputStyle" />
            <span class="text-xs font-mono" :style="{ color: 'var(--color-text-muted)' }">{{ brandingStore.config.accent_color }}</span>
          </div>
          <div>
            <label class="block text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Erfolg</label>
            <input type="color" v-model="brandingStore.config.success_color" @input="brandingStore.applyCSS()" class="w-full h-10 rounded cursor-pointer" :style="colorInputStyle" />
            <span class="text-xs font-mono" :style="{ color: 'var(--color-text-muted)' }">{{ brandingStore.config.success_color }}</span>
          </div>
          <div>
            <label class="block text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Warnung</label>
            <input type="color" v-model="brandingStore.config.warning_color" @input="brandingStore.applyCSS()" class="w-full h-10 rounded cursor-pointer" :style="colorInputStyle" />
            <span class="text-xs font-mono" :style="{ color: 'var(--color-text-muted)' }">{{ brandingStore.config.warning_color }}</span>
          </div>
          <div>
            <label class="block text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Fehler</label>
            <input type="color" v-model="brandingStore.config.danger_color" @input="brandingStore.applyCSS()" class="w-full h-10 rounded cursor-pointer" :style="colorInputStyle" />
            <span class="text-xs font-mono" :style="{ color: 'var(--color-text-muted)' }">{{ brandingStore.config.danger_color }}</span>
          </div>
        </div>
      </div>

      <!-- Dark Mode Farben -->
      <div class="rounded-lg shadow p-6" :style="cardStyle">
        <h2 class="text-lg font-semibold mb-4 pb-2 border-b" :style="{ color: 'var(--color-text-primary)', borderColor: 'var(--color-border)' }">Dark Mode Farben</h2>
        
        <div class="grid grid-cols-3 gap-4">
          <div>
            <label class="block text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Hintergrund</label>
            <input type="color" v-model="brandingStore.config.dark_scheme.background" @input="brandingStore.applyCSS()" class="w-full h-10 rounded cursor-pointer" :style="colorInputStyle" />
            <span class="text-xs font-mono" :style="{ color: 'var(--color-text-muted)' }">{{ brandingStore.config.dark_scheme.background }}</span>
          </div>
          <div>
            <label class="block text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Oberfläche</label>
            <input type="color" v-model="brandingStore.config.dark_scheme.surface" @input="brandingStore.applyCSS()" class="w-full h-10 rounded cursor-pointer" :style="colorInputStyle" />
            <span class="text-xs font-mono" :style="{ color: 'var(--color-text-muted)' }">{{ brandingStore.config.dark_scheme.surface }}</span>
          </div>
          <div>
            <label class="block text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Rahmen</label>
            <input type="color" v-model="brandingStore.config.dark_scheme.border" @input="brandingStore.applyCSS()" class="w-full h-10 rounded cursor-pointer" :style="colorInputStyle" />
            <span class="text-xs font-mono" :style="{ color: 'var(--color-text-muted)' }">{{ brandingStore.config.dark_scheme.border }}</span>
          </div>
          <div>
            <label class="block text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Text Primär</label>
            <input type="color" v-model="brandingStore.config.dark_scheme.text_primary" @input="brandingStore.applyCSS()" class="w-full h-10 rounded cursor-pointer" :style="colorInputStyle" />
            <span class="text-xs font-mono" :style="{ color: 'var(--color-text-muted)' }">{{ brandingStore.config.dark_scheme.text_primary }}</span>
          </div>
          <div>
            <label class="block text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Text Sekundär</label>
            <input type="color" v-model="brandingStore.config.dark_scheme.text_secondary" @input="brandingStore.applyCSS()" class="w-full h-10 rounded cursor-pointer" :style="colorInputStyle" />
            <span class="text-xs font-mono" :style="{ color: 'var(--color-text-muted)' }">{{ brandingStore.config.dark_scheme.text_secondary }}</span>
          </div>
          <div>
            <label class="block text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Text Muted</label>
            <input type="color" v-model="brandingStore.config.dark_scheme.text_muted" @input="brandingStore.applyCSS()" class="w-full h-10 rounded cursor-pointer" :style="colorInputStyle" />
            <span class="text-xs font-mono" :style="{ color: 'var(--color-text-muted)' }">{{ brandingStore.config.dark_scheme.text_muted }}</span>
          </div>
        </div>
      </div>

      <!-- Light Mode Farben -->
      <div class="rounded-lg shadow p-6" :style="cardStyle">
        <h2 class="text-lg font-semibold mb-4 pb-2 border-b" :style="{ color: 'var(--color-text-primary)', borderColor: 'var(--color-border)' }">Light Mode Farben</h2>
        
        <div class="grid grid-cols-3 gap-4">
          <div>
            <label class="block text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Hintergrund</label>
            <input type="color" v-model="brandingStore.config.light_scheme.background" @input="brandingStore.applyCSS()" class="w-full h-10 rounded cursor-pointer" :style="colorInputStyle" />
            <span class="text-xs font-mono" :style="{ color: 'var(--color-text-muted)' }">{{ brandingStore.config.light_scheme.background }}</span>
          </div>
          <div>
            <label class="block text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Oberfläche</label>
            <input type="color" v-model="brandingStore.config.light_scheme.surface" @input="brandingStore.applyCSS()" class="w-full h-10 rounded cursor-pointer" :style="colorInputStyle" />
            <span class="text-xs font-mono" :style="{ color: 'var(--color-text-muted)' }">{{ brandingStore.config.light_scheme.surface }}</span>
          </div>
          <div>
            <label class="block text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Rahmen</label>
            <input type="color" v-model="brandingStore.config.light_scheme.border" @input="brandingStore.applyCSS()" class="w-full h-10 rounded cursor-pointer" :style="colorInputStyle" />
            <span class="text-xs font-mono" :style="{ color: 'var(--color-text-muted)' }">{{ brandingStore.config.light_scheme.border }}</span>
          </div>
          <div>
            <label class="block text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Text Primär</label>
            <input type="color" v-model="brandingStore.config.light_scheme.text_primary" @input="brandingStore.applyCSS()" class="w-full h-10 rounded cursor-pointer" :style="colorInputStyle" />
            <span class="text-xs font-mono" :style="{ color: 'var(--color-text-muted)' }">{{ brandingStore.config.light_scheme.text_primary }}</span>
          </div>
          <div>
            <label class="block text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Text Sekundär</label>
            <input type="color" v-model="brandingStore.config.light_scheme.text_secondary" @input="brandingStore.applyCSS()" class="w-full h-10 rounded cursor-pointer" :style="colorInputStyle" />
            <span class="text-xs font-mono" :style="{ color: 'var(--color-text-muted)' }">{{ brandingStore.config.light_scheme.text_secondary }}</span>
          </div>
          <div>
            <label class="block text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Text Muted</label>
            <input type="color" v-model="brandingStore.config.light_scheme.text_muted" @input="brandingStore.applyCSS()" class="w-full h-10 rounded cursor-pointer" :style="colorInputStyle" />
            <span class="text-xs font-mono" :style="{ color: 'var(--color-text-muted)' }">{{ brandingStore.config.light_scheme.text_muted }}</span>
          </div>
        </div>
      </div>

      <!-- Custom CSS -->
      <div class="rounded-lg shadow p-6 lg:col-span-2" :style="cardStyle">
        <h2 class="text-lg font-semibold mb-4 pb-2 border-b" :style="{ color: 'var(--color-text-primary)', borderColor: 'var(--color-border)' }">Custom CSS</h2>
        <textarea v-model="brandingStore.config.custom_css" rows="5" @input="brandingStore.applyCSS()"
          placeholder="/* Eigene CSS-Regeln hier einfügen */"
          class="w-full px-3 py-2 rounded font-mono text-sm"
          :style="inputStyle"></textarea>
      </div>
    </div>

    <!-- Action Buttons -->
    <div class="flex justify-end gap-4 mt-6 pt-6 border-t" :style="{ borderColor: 'var(--color-border)' }">
      <button @click="handleReset" :disabled="saving" class="px-6 py-2 rounded hover:opacity-80 disabled:opacity-50" :style="buttonSecondaryStyle">
        Zurücksetzen
      </button>
      <button @click="handleSave" :disabled="saving" class="px-6 py-2 text-white rounded hover:opacity-90 disabled:opacity-50" :style="{ backgroundColor: 'var(--color-primary)' }">
        {{ saving ? 'Speichert...' : 'Speichern' }}
      </button>
    </div>

    <!-- Toast -->
    <div v-if="toast.show" class="fixed bottom-6 right-6 px-6 py-3 rounded-lg text-white font-medium shadow-lg z-50"
      :style="{ backgroundColor: toast.type === 'success' ? 'var(--color-success)' : 'var(--color-danger)' }">
      {{ toast.message }}
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useBrandingStore } from '../stores/brandingStore'

const brandingStore = useBrandingStore()
const saving = ref(false)
const logoInput = ref(null)
const faviconInput = ref(null)

const toast = reactive({ show: false, message: '', type: 'success' })

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

const previewStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  border: '1px solid var(--color-border)'
}))

const colorInputStyle = computed(() => ({
  border: '1px solid var(--color-border)'
}))

function showToast(message, type = 'success') {
  toast.message = message
  toast.type = type
  toast.show = true
  setTimeout(() => { toast.show = false }, 3000)
}

async function handleSave() {
  saving.value = true
  const success = await brandingStore.saveConfig()
  showToast(success ? 'Gespeichert!' : 'Fehler beim Speichern!', success ? 'success' : 'error')
  saving.value = false
}

async function handleReset() {
  if (!confirm('Wirklich alle Einstellungen zurücksetzen?')) return
  saving.value = true
  const success = await brandingStore.resetToDefaults()
  showToast(success ? 'Zurückgesetzt!' : 'Fehler!', success ? 'success' : 'error')
  saving.value = false
}

async function handleLogoUpload(event) {
  const file = event.target.files[0]
  if (!file) return
  const success = await brandingStore.uploadLogo(file)
  showToast(success ? 'Logo hochgeladen!' : 'Fehler!', success ? 'success' : 'error')
  event.target.value = ''
}

async function handleFaviconUpload(event) {
  const file = event.target.files[0]
  if (!file) return
  const success = await brandingStore.uploadFavicon(file)
  showToast(success ? 'Favicon hochgeladen!' : 'Fehler!', success ? 'success' : 'error')
  event.target.value = ''
}

onMounted(() => {
  if (brandingStore.loading) {
    brandingStore.loadConfig()
  }
})
</script>