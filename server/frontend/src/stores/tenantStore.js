// ==============================================================================
// Name:        Tenant Store
// Kontakt:     Phydran6
// Version:     2026.11.27.22.00.00
// Beschreibung: Mandanten - Kontext (an/aus, eigener Mandant, Umschalter)
// ==============================================================================
// Der Server liefert unter /api/tenants/context, ob die Mandantenfaehigkeit an
// ist, ob dieses Konto ein Betreiber ist und wohin es umschalten kann. Die
// Auswahl liegt in localStorage und geht als Kopf X-Rimperio-Tenant bei jeder
// Anfrage mit (stores/auth.js). Umschalten laedt die Seite neu - so zeigt jede
// Ansicht sofort nur noch die Daten des gewaehlten Mandanten.
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useAuthStore, TENANT_SCOPE_KEY } from './auth'

function readScope() {
  try {
    return localStorage.getItem(TENANT_SCOPE_KEY) || ''
  } catch {
    return ''
  }
}

export const useTenantStore = defineStore('tenant', () => {
  const context = ref(null)
  const scope = ref(readScope())

  const enabled = computed(() => !!context.value?.enabled)
  const isProvider = computed(() => context.value ? !!context.value.is_provider : true)
  const ownTenant = computed(() => context.value?.tenant || null)
  const tenants = computed(() => context.value?.tenants || [])
  const scopeLabel = computed(() => {
    if (!scope.value) return ''
    if (scope.value === '0') return 'Betreiber'
    return tenants.value.find(t => String(t.id) === scope.value)?.name || `#${scope.value}`
  })

  async function load() {
    const auth = useAuthStore()
    try {
      context.value = await auth.api('/api/tenants/context')
    } catch {
      context.value = { enabled: false, is_provider: true, tenant: null, tenants: [] }
    }
    // Gewaehlter Mandant gibt es nicht mehr (geloescht) oder Funktion aus:
    // zurueck auf "alle".
    if (scope.value && (!enabled.value || !isProvider.value
        || (scope.value !== '0' && !tenants.value.some(t => String(t.id) === scope.value)))) {
      setScope('', false)
    }
    return context.value
  }

  function setScope(value, reload = true) {
    scope.value = value ? String(value) : ''
    try {
      if (scope.value) localStorage.setItem(TENANT_SCOPE_KEY, scope.value)
      else localStorage.removeItem(TENANT_SCOPE_KEY)
    } catch {
      // ohne localStorage gilt die Auswahl nur bis zum Neuladen
    }
    if (reload) window.location.reload()
  }

  return { context, scope, enabled, isProvider, ownTenant, tenants, scopeLabel, load, setScope }
})
