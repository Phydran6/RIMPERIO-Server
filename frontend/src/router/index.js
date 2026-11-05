/**
 * Datei:        router/index.js
 * Projekt:      RIMPERIO — Frontend
 * Autor:        Phydran6
 * Version:      2026.10.31.15.40.00
 * Beschreibung: Routen und Zugangsschutz. Ohne Token geht es nur zur Anmeldung.
 */
import { createRouter, createWebHistory } from 'vue-router'
import { getToken } from '../api'

const routes = [
  { path: '/login', name: 'login', component: () => import('../views/Login.vue'), meta: { public: true } },
  {
    path: '/',
    component: () => import('../components/AppShell.vue'),
    meta: { requiresAuth: true },
    children: [
      { path: '', redirect: '/dashboard' },
      { path: 'dashboard', name: 'dashboard', component: () => import('../views/Dashboard.vue') },
      { path: 'agents', name: 'agents', component: () => import('../views/Agents.vue') },
      { path: 'agents/:uid', name: 'agent', component: () => import('../views/AgentDetail.vue'), props: true },
      { path: 'checks', name: 'checks', component: () => import('../views/Checks.vue') },
      { path: 'scripts', name: 'scripts', component: () => import('../views/Scripts.vue') },
      { path: 'patches', name: 'patches', component: () => import('../views/Patches.vue') },
      { path: 'alerts', name: 'alerts', component: () => import('../views/Alerts.vue') },
      { path: 'integrations', name: 'integrations', component: () => import('../views/Integrations.vue') },
      { path: 'settings', name: 'settings', component: () => import('../views/Settings.vue') },
      { path: 'users', name: 'users', component: () => import('../views/Users.vue') }
    ]
  },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to) => {
  const authed = !!getToken()
  if (to.meta.requiresAuth && !authed) return { name: 'login', query: { next: to.fullPath } }
  if (to.name === 'login' && authed) return { name: 'dashboard' }
  return true
})

export default router
