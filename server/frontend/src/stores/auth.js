// ==============================================================================
// Name:        Auth Store
// Kontakt:     Phydran6
// Version:     2026.05.30.17.22.26
// Beschreibung: Pinia Store für Auth (Login, MFA-Login, Token, API Wrapper)
// ==============================================================================
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

// Mandant, auf den ein Betreiber umgeschaltet hat (siehe stores/tenantStore.js).
// Leer = alle. Der Server ignoriert den Kopf bei Mandanten-Konten - die sehen
// ohnehin nur ihren eigenen Mandanten.
export const TENANT_SCOPE_KEY = 'rimperio.tenantScope'
export const TENANT_HEADER = 'X-Rimperio-Tenant'

export function tenantHeaders() {
  try {
    const scope = localStorage.getItem(TENANT_SCOPE_KEY)
    return scope ? { [TENANT_HEADER]: scope } : {}
  } catch {
    return {}
  }
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || null)
  const user = ref(null)

  const isAuthenticated = computed(() => !!token.value)
  const isAdmin = computed(() => user.value?.role === 'admin')
  // Betreiber = Konto ohne Mandant. Nur Betreiber-Admins erreichen Server,
  // Updates, Sicherungen, Konsole und die Mandantenverwaltung.
  const isProvider = computed(() => !!user.value && (user.value.tenant_id ?? null) === null)
  const isSystemAdmin = computed(() => isAdmin.value && isProvider.value)

  /**
   * `detail` kann Text sein (eigene Meldungen des Servers) oder - bei
   * Prüffehlern von FastAPI (422) - eine Liste von Objekten
   * `{loc, msg, type}`. Ohne Aufbereitung stünde dann „[object Object]“ da.
   */
  function formatDetail(detail) {
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail.map(d => {
        if (typeof d === 'string') return d
        const field = (d?.loc || []).filter(x => x !== 'body').join('.')
        return field ? `${field}: ${d?.msg || ''}` : (d?.msg || JSON.stringify(d))
      }).join('; ')
    }
    if (detail && typeof detail === 'object') return detail.message || detail.msg || JSON.stringify(detail)
    return String(detail)
  }

  async function parseError(res) {
    try {
      const data = await res.json()
      if (data?.detail) return formatDetail(data.detail)
      return JSON.stringify(data)
    } catch {
      try {
        return await res.text()
      } catch {
        return `HTTP ${res.status}`
      }
    }
  }

  /**
   * Erster Login-Schritt: Passwort.
   * Liefert entweder { mfa_required: true, mfa_token, expires_in_seconds }
   * oder setzt direkt das access_token und gibt null zurück.
   */
  async function login(username, password) {
    const formData = new FormData()
    formData.append('username', username)
    formData.append('password', password)
    const res = await fetch('/api/auth/login', { method: 'POST', body: formData })
    if (!res.ok) throw new Error(await parseError(res) || 'Login fehlgeschlagen')
    const data = await res.json()
    if (data.mfa_required) {
      return { mfa_required: true, mfa_token: data.mfa_token, expires_in_seconds: data.expires_in_seconds }
    }
    token.value = data.access_token
    localStorage.setItem('token', data.access_token)
    await fetchUser()
    return null
  }

  /**
   * Zweiter Login-Schritt: TOTP-Code oder Backup-Code.
   */
  async function loginMfa(mfaToken, code) {
    const res = await fetch('/api/auth/login/mfa', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mfa_token: mfaToken, code }),
    })
    if (!res.ok) throw new Error(await parseError(res) || 'MFA-Login fehlgeschlagen')
    const data = await res.json()
    token.value = data.access_token
    localStorage.setItem('token', data.access_token)
    await fetchUser()
  }

  /**
   * Anmeldung mit Passkey (WebAuthn).
   * Zwei Schritte: Aufgabe vom Server holen, vom Gerät unterschreiben lassen,
   * Signatur zurückschicken. Kein MFA-Schritt danach - der Passkey ist bereits
   * Gerät + Entsperrung.
   */
  async function loginPasskey(username = '') {
    const { getCredential } = await import('../utils/webauthn')

    const optionsRes = await fetch('/api/auth/passkey/login/options', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username }),
    })
    if (!optionsRes.ok) throw new Error(await parseError(optionsRes) || 'Passkey-Anmeldung nicht möglich')
    const start = await optionsRes.json()

    const credential = await getCredential(start.options)

    const verifyRes = await fetch('/api/auth/passkey/login/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ challenge_handle: start.challenge_handle, credential }),
    })
    if (!verifyRes.ok) throw new Error(await parseError(verifyRes) || 'Passkey wurde abgelehnt')

    const data = await verifyRes.json()
    token.value = data.access_token
    localStorage.setItem('token', data.access_token)
    await fetchUser()
  }

  async function fetchUser() {
    user.value = await api('/api/auth/me')
  }

  /** Neue Sitzung übernehmen (z.B. nach dem Ändern des Passworts). */
  function adoptToken(value) {
    token.value = value
    localStorage.setItem('token', value)
  }

  function logout() {
    token.value = null
    user.value = null
    localStorage.removeItem('token')
  }

  async function api(url, options = {}) {
    const headers = { ...tenantHeaders(), ...options.headers }
    if (token.value) headers['Authorization'] = `Bearer ${token.value}`
    if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
      headers['Content-Type'] = 'application/json'
      options.body = JSON.stringify(options.body)
    }
    const res = await fetch(url, { ...options, headers })
    if (res.status === 401) { logout(); window.location.href = '/login'; throw new Error('Sitzung abgelaufen') }
    // Startpasswort noch nicht ersetzt: alles andere sperrt der Server, also
    // gleich auf die Seite zum Ändern.
    if (res.status === 403 && res.headers.get('X-Password-Change-Required') === '1') {
      if (window.location.pathname !== '/change-password') window.location.href = '/change-password'
      throw new Error('Bitte zuerst das Startpasswort ändern.')
    }
    if (!res.ok) throw new Error(await parseError(res) || `HTTP ${res.status}`)
    if (res.status === 204) return null
    return res.json()
  }

  /**
   * Kurzlebiges Ticket (60 s) für Wege ohne Authorization-Kopf: Terminal
   * (WebSocket), Ereignisströme (EventSource) und Download-Links. So steht nie
   * der volle Sitzungs-Token in einer Adresse - und damit nicht im
   * Browserverlauf oder in Proxy-Protokollen.
   */
  async function ticket(purpose) {
    const data = await api('/api/auth/ticket', { method: 'POST', body: { purpose } })
    return data.ticket
  }

  /**
   * EventSource mit Ticket. Bricht der Strom ab, holt sie ein frisches Ticket
   * und verbindet neu - das automatische Wiederverbinden des Browsers würde
   * sonst mit dem längst abgelaufenen Ticket anklopfen.
   */
  function eventStream(path, purpose, setup, { retryMs = 5000 } = {}) {
    let source = null
    let stopped = false
    let timer = null

    async function open() {
      if (stopped || !token.value) return
      try {
        const t = await ticket(purpose)
        if (stopped) return
        const sep = path.includes('?') ? '&' : '?'
        source = new EventSource(`${path}${sep}token=${encodeURIComponent(t)}`)
        setup(source)
        const userOnError = source.onerror
        source.onerror = (event) => {
          if (typeof userOnError === 'function') userOnError(event)
          source?.close()
          source = null
          if (!stopped) timer = setTimeout(open, retryMs)
        }
      } catch {
        if (!stopped) timer = setTimeout(open, retryMs * 2)
      }
    }

    open()
    return {
      close() {
        stopped = true
        clearTimeout(timer)
        source?.close()
        source = null
      },
    }
  }

  return { adoptToken, token, user, isAuthenticated, isAdmin, isProvider, isSystemAdmin, login, loginMfa, loginPasskey, logout, fetchUser, api,
           ticket, eventStream }
})
