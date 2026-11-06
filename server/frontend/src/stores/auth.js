// ==============================================================================
// Name:        Auth Store
// Kontakt:     Phydran6
// Version:     2026.05.30.17.22.26
// Beschreibung: Pinia Store für Auth (Login, MFA-Login, Token, API Wrapper)
// ==============================================================================
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || null)
  const user = ref(null)

  const isAuthenticated = computed(() => !!token.value)
  const isAdmin = computed(() => user.value?.role === 'admin')

  async function parseError(res) {
    try {
      const data = await res.json()
      if (data?.detail) return data.detail
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

  function logout() {
    token.value = null
    user.value = null
    localStorage.removeItem('token')
  }

  async function api(url, options = {}) {
    const headers = { ...options.headers }
    if (token.value) headers['Authorization'] = `Bearer ${token.value}`
    if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
      headers['Content-Type'] = 'application/json'
      options.body = JSON.stringify(options.body)
    }
    const res = await fetch(url, { ...options, headers })
    if (res.status === 401) { logout(); window.location.href = '/login'; throw new Error('Sitzung abgelaufen') }
    if (!res.ok) throw new Error(await parseError(res) || `HTTP ${res.status}`)
    if (res.status === 204) return null
    return res.json()
  }

  return { token, user, isAuthenticated, isAdmin, login, loginMfa, loginPasskey, logout, fetchUser, api }
})
