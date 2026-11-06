<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Herkunft, Lizenz und haeufige Fragen.

     Was hier an die Stelle von etwas anderem tritt: In der Fusszeile stand
     "© 2026 RIMPERIO. All rights reserved." Das war schlicht falsch - RIMPERIO
     steht unter der MIT-Lizenz, die Rechte sind also gerade NICHT vorbehalten,
     sondern eingeraeumt. Und es war nutzlos: es beantwortet keine einzige
     Frage, die ein Betreiber tatsaechlich hat.

     Diese Seite beantwortet sie: was ist das, wo kommt es her, wem gehoert es,
     was darf ich damit, und wie pruefe ich, dass hier wirklich das laeuft, was
     auf GitHub steht.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">Über RIMPERIO</h2>
        <p class="page-subtitle">Woher das hier kommt, was erlaubt ist — und die Fragen, die immer wieder kommen.</p>
      </div>
    </div>

    <div v-if="error" class="card mb-4" style="border-color: var(--color-danger)">
      <div class="card-body text-sm" style="color: var(--color-danger)">{{ error }}</div>
    </div>

    <template v-if="about">
      <!-- Steckbrief -->
      <div class="card mb-4">
        <div class="card-body">
          <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1 mb-2">
            <span class="text-xl font-bold" style="color: var(--color-text-primary)">{{ about.name }}</span>
            <span class="badge badge-primary">v{{ about.version }}</span>
            <span class="badge badge-neutral">{{ about.license.id }}</span>
          </div>
          <p class="text-sm mb-3" style="color: var(--color-text-secondary)">{{ about.tagline }}</p>
          <dl class="text-sm grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1">
            <div class="flex gap-2">
              <dt class="w-28 shrink-0" style="color: var(--color-text-muted)">Autor</dt>
              <dd style="color: var(--color-text-secondary)">{{ about.author }}</dd>
            </div>
            <div class="flex gap-2">
              <dt class="w-28 shrink-0" style="color: var(--color-text-muted)">Repository</dt>
              <dd>
                <a :href="about.repository.url" target="_blank" rel="noopener noreferrer" class="link">
                  {{ about.repository.slug }}
                </a>
              </dd>
            </div>
          </dl>
        </div>
      </div>

      <!-- Lizenz - der Punkt, an dem das "All rights reserved" stand -->
      <div class="card mb-4">
        <div class="card-header"><p class="card-title">Lizenz: {{ about.license.name }}</p></div>
        <div class="card-body">
          <p class="text-sm mb-2" style="color: var(--color-text-secondary)">{{ about.license.summary }}</p>
          <p class="hint mb-3">{{ about.license.note }}</p>
          <a :href="about.license.url" target="_blank" rel="noopener noreferrer" class="btn btn-secondary btn-sm">
            Lizenztext lesen
          </a>
        </div>
      </div>

      <!-- Verweise -->
      <div class="card mb-4">
        <div class="card-header">
          <p class="card-title">Wo alles nachzulesen ist</p>
        </div>
        <div class="card-body grid grid-cols-1 md:grid-cols-2 gap-3">
          <a
            v-for="link in about.links"
            :key="link.key"
            :href="link.url"
            target="_blank"
            rel="noopener noreferrer"
            class="link-card"
          >
            <span class="font-medium" style="color: var(--color-primary)">{{ link.label }}</span>
            <span class="text-xs mt-0.5 block" style="color: var(--color-text-muted)">{{ link.hint }}</span>
          </a>
        </div>
      </div>

      <!-- FAQ -->
      <div class="mb-4">
        <h3 class="section-title">Häufige Fragen</h3>
        <div class="flex flex-wrap gap-2 mb-3">
          <button
            class="btn btn-sm"
            :class="activeCategory === '' ? 'btn-primary' : 'btn-secondary'"
            @click="activeCategory = ''"
          >
            Alle
          </button>
          <button
            v-for="category in about.categories"
            :key="category"
            class="btn btn-sm"
            :class="activeCategory === category ? 'btn-primary' : 'btn-secondary'"
            @click="activeCategory = category"
          >
            {{ category }}
          </button>
        </div>

        <div class="space-y-2">
          <div v-for="entry in visibleFaq" :key="entry.key" class="card">
            <button class="faq-question" @click="toggle(entry.key)">
              <span style="color: var(--color-text-primary)">{{ entry.question }}</span>
              <AppIcon
                name="chevronDown"
                :size="16"
                class="shrink-0 transition-transform"
                :style="{ transform: open === entry.key ? 'rotate(180deg)' : 'none' }"
              />
            </button>
            <div v-if="open === entry.key" class="card-body pt-0">
              <p
                v-for="(paragraph, index) in entry.answer.split('\n\n')"
                :key="index"
                class="text-sm mb-2 last:mb-0"
                style="color: var(--color-text-secondary)"
              >{{ paragraph }}</p>
            </div>
          </div>
        </div>
      </div>
    </template>

    <div v-else-if="!error" class="skeleton" style="height: 12rem" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'

const auth = useAuthStore()

const about = ref(null)
const error = ref('')
const open = ref('')
const activeCategory = ref('')

const visibleFaq = computed(() => {
  const entries = about.value?.faq || []
  if (!activeCategory.value) return entries
  return entries.filter(entry => entry.category === activeCategory.value)
})

function toggle(key) {
  open.value = open.value === key ? '' : key
}

onMounted(async () => {
  try {
    about.value = await auth.api('/api/about')
  } catch (e) {
    error.value = e.message
  }
})
</script>

<style scoped>
.link-card {
  display: block;
  padding: 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  text-decoration: none;
  transition: border-color 0.15s ease, background-color 0.15s ease;
}

.link-card:hover {
  border-color: var(--color-primary);
  background-color: var(--color-surface-elevated);
}

.faq-question {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  width: 100%;
  padding: 0.875rem 1rem;
  text-align: left;
  font-weight: 500;
  background: none;
  border: none;
  cursor: pointer;
  color: inherit;
}
</style>
