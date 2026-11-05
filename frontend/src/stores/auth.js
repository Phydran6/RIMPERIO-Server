/**
 * Datei:        stores/auth.js
 * Projekt:      RIMPERIO — Frontend
 * Autor:        Phydran6
 * Version:      2026.10.31.15.40.00
 * Beschreibung: Anmeldezustand: Token, angemeldeter Benutzer, Rollenprüfungen.
 */
import { defineStore } from 'pinia'
import { api, getToken, setToken } from '../api'

export const useAuth = defineStore('auth', {
  state: () => ({
    user: null,
    token: getToken()
  }),
  getters: {
    isAuthed: (s) => !!s.token,
    isAdmin: (s) => s.user?.role === 'admin',
    canOperate: (s) => !!s.user && (s.user.role === 'admin' || s.user.role === 'operator')
  },
  actions: {
    async login(username, password) {
      const data = await api.post('/auth/login', { username, password })
      this.token = data.token
      setToken(data.token)
      this.user = data.user
    },
    async fetchMe() {
      if (!this.token) return
      try {
        this.user = await api.get('/auth/me')
      } catch {
        this.logout()
      }
    },
    logout() {
      this.token = ''
      this.user = null
      setToken('')
    }
  }
})
