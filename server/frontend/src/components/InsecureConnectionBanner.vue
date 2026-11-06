<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.07.31.23.00.00
     Beschreibung: RIMPERIO - Hinweis, wenn die Oberflaeche ueber HTTP statt HTTPS
                   aufgerufen wird. Wegklickbar; die Entscheidung gilt fuer die
                   aktuelle Adresse, bis der Browser-Tab geschlossen wird.
     ============================================================================== -->

<template>
  <div v-if="visible" class="insecure-banner">
    <span class="grow">
      <strong>Unverschlüsselte Verbindung.</strong>
      Diese Seite wurde über <code>http://{{ host }}</code> geladen – Passwörter und Logs gehen
      im Klartext über das Netz. Bitte nach Möglichkeit HTTPS verwenden.
    </span>
    <a v-if="httpsUrl" :href="httpsUrl" class="banner-link">Zu HTTPS wechseln</a>
    <button class="banner-close" title="Hinweis ausblenden" @click="dismiss">✕</button>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'

const STORAGE_KEY = 'rimperio.insecureBannerDismissed'

const visible = ref(false)
const host = computed(() => window.location.host)

// Ueber die gleiche Adresse per HTTPS - klappt, sobald im Reverse Proxy
// ein Zertifikat (auch ein internes) fuer diese Adresse hinterlegt ist.
const httpsUrl = computed(() => {
  if (!window.location.hostname) return ''
  return `https://${window.location.hostname}${window.location.pathname}${window.location.search}`
})

onMounted(() => {
  const isHttp = window.location.protocol === 'http:'
  const isLocal = ['localhost', '127.0.0.1', '::1'].includes(window.location.hostname)
  if (!isHttp || isLocal) return

  let dismissed = false
  try {
    dismissed = sessionStorage.getItem(STORAGE_KEY) === '1'
  } catch {
    dismissed = false
  }
  visible.value = !dismissed
})

function dismiss() {
  visible.value = false
  try {
    sessionStorage.setItem(STORAGE_KEY, '1')
  } catch {
    // Ohne sessionStorage bleibt der Hinweis beim naechsten Laden wieder da.
  }
}
</script>

<style scoped>
.insecure-banner {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
  padding: 0.5rem 1rem;
  font-size: 0.8125rem;
  line-height: 1.4;
  background-color: var(--color-warning, #f59e0b);
  color: #1f2937;
}

.grow {
  flex: 1 1 320px;
}

.insecure-banner code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.banner-link {
  text-decoration: underline;
  font-weight: 600;
  white-space: nowrap;
}

.banner-close {
  padding: 0 0.25rem;
  font-weight: 700;
  line-height: 1;
}

.banner-close:hover {
  opacity: 0.7;
}
</style>
