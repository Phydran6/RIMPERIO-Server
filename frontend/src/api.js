/**
 * Datei:        api.js
 * Projekt:      RIMPERIO — Frontend
 * Autor:        Phydran6
 * Version:      2026.10.31.15.40.00
 * Beschreibung: Schmaler fetch-Wrapper um die RIMPERIO-API. Hängt den Sitzungs-Token
 *               an, wirft bei Fehlern eine lesbare Meldung und meldet 401 global.
 */
const TOKEN_KEY = 'rimperio_token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}
export function setToken(t) {
  if (t) localStorage.setItem(TOKEN_KEY, t)
  else localStorage.removeItem(TOKEN_KEY)
}

async function request(method, path, body) {
  const headers = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers['Authorization'] = 'Bearer ' + token

  const res = await fetch('/api' + path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined
  })

  if (res.status === 401) {
    setToken('')
    window.dispatchEvent(new CustomEvent('rimperio:unauthorized'))
  }

  const text = await res.text()
  const data = text ? JSON.parse(text) : null
  if (!res.ok) {
    const detail = data && (data.detail || data.message)
    throw new Error(typeof detail === 'string' ? detail : (detail ? JSON.stringify(detail) : res.statusText))
  }
  return data
}

export const api = {
  get: (p) => request('GET', p),
  post: (p, b) => request('POST', p, b),
  put: (p, b) => request('PUT', p, b),
  patch: (p, b) => request('PATCH', p, b),
  del: (p) => request('DELETE', p)
}
