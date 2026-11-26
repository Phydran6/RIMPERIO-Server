/**
 * ==============================================================================
 * Name:           Phydran6
 * Kontakt:        Phydran6
 * Version:        2026.11.07.23.00.00
 * Changelog:      docs/changelog/frontend.md
 * ==============================================================================
 *
 * RIMPERIO Vue Router - Route-Definitionen
 * ======================================================
 * Welche Seite unter welcher Adresse liegt. WIE die Seiten im linken Menü
 * gruppiert sind, steht in src/navigation.js - nur dort.
 *
 * Die Adressen bleiben bewusst die alten (/rmm/patches, /settings/ldap, …):
 * Meldungen, Lesezeichen und Links in Mails zeigen darauf. Was umgezogen ist,
 * leitet weiter (siehe "Alte Adressen" unten).
 *
 * Einstellungsseite: /settings/<abschnitt> zeigt genau EINEN Abschnitt. Die
 * Reiterleiste in der Seite gibt es nicht mehr - jeder Abschnitt ist ein
 * eigener Menüpunkt links.
 *
 * ==============================================================================
 */

import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

// =============================================================================
// Route-Definitionen
// =============================================================================
const routes = [
  // ---------------------------------------------------------------------------
  // Öffentliche Route: Login
  // ---------------------------------------------------------------------------
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/Login.vue'),
    meta: { public: true }
  },

  // Startpasswort ersetzen - ohne Menü, bevor irgendetwas anderes geht.
  {
    path: '/change-password',
    name: 'ChangePassword',
    component: () => import('../views/ChangePassword.vue'),
  },

  // ---------------------------------------------------------------------------
  // Geschützte Routen: Hauptlayout mit Sidebar
  // ---------------------------------------------------------------------------
  {
    path: '/',
    component: () => import('../views/Layout.vue'),
    children: [
      // --- Überwachung -------------------------------------------------------
      {
        path: '',
        name: 'Dashboard',
        component: () => import('../views/Dashboard.vue')
      },
      {
        path: 'logs',
        name: 'Logs',
        component: () => import('../views/Logs.vue')
      },
      {
        path: 'agents',
        name: 'Agents',
        component: () => import('../views/Agents.vue')
      },
      {
        path: 'devices/:hostname',
        name: 'DeviceLogs',
        component: () => import('../views/DeviceLogs.vue')
      },
      // Was im Netz haengt und ob es schon liefert. Nur fuer Administratoren:
      // die Liste ist eine Landkarte des Netzes.
      {
        path: 'discovery',
        name: 'Discovery',
        component: () => import('../views/Discovery.vue'),
        meta: { admin: true }
      },

      // --- Fernwartung (RMM) --------------------------------------------------
      { path: 'rmm', name: 'RmmAgents', component: () => import('../views/RmmAgents.vue') },
      { path: 'rmm/agents/:uid', name: 'RmmAgentDetail', component: () => import('../views/RmmAgentDetail.vue') },
      { path: 'rmm/scripts', name: 'RmmScripts', component: () => import('../views/RmmScripts.vue') },
      { path: 'rmm/checks', name: 'RmmChecks', component: () => import('../views/RmmChecks.vue') },
      { path: 'rmm/patches', name: 'RmmPatches', component: () => import('../views/RmmPatches.vue') },
      { path: 'rmm/alerts', name: 'RmmAlerts', component: () => import('../views/RmmAlerts.vue') },
      { path: 'rmm/policies', name: 'RmmPolicies', component: () => import('../views/RmmPolicies.vue') },
      { path: 'rmm/sources', name: 'RmmSources', component: () => import('../views/RmmSources.vue') },
      { path: 'rmm/db-updates', name: 'RmmDbUpdates', component: () => import('../views/RmmDbUpdates.vue') },
      { path: 'logs/handling', name: 'LogHandling', component: () => import('../views/LogHandling.vue') },
      // Proxmox ist ein Geraet: anbinden, Gaeste zuordnen, Knoten patchen und
      // Konsole stehen unter "Geraete". Die alten Adressen leiten weiter.
      { path: 'devices/proxmox/patching', name: 'PveUpdates', component: () => import('../views/PveUpdates.vue') },
      { path: 'devices/proxmox', name: 'RmmProxmox', component: () => import('../views/RmmProxmox.vue') },
      { path: 'integrations/proxmox/updates', redirect: '/devices/proxmox/patching' },
      { path: 'integrations/proxmox', redirect: '/devices/proxmox' },
      { path: 'rmm/proxmox', redirect: '/devices/proxmox' },
      { path: 'rmm/snmp', name: 'RmmSnmp', component: () => import('../views/RmmSnmp.vue') },
      { path: 'devices/nas', name: 'NasDevices', component: () => import('../views/NasDevices.vue') },
      { path: 'devices/nas/:id', name: 'NasDetail', component: () => import('../views/NasDetail.vue') },
      { path: 'devices/special', name: 'SpecialSystems', component: () => import('../views/SpecialSystems.vue') },
      { path: 'backups', redirect: '/backups/jobs' },
      { path: 'backups/jobs', name: 'BackupJobs', component: () => import('../views/BackupJobs.vue') },
      { path: 'backups/targets', name: 'BackupTargets', component: () => import('../views/BackupTargets.vue') },
      { path: 'backups/runs', name: 'BackupRuns', component: () => import('../views/BackupRuns.vue') },
      { path: 'integrations/vault', name: 'VaultSettings', component: () => import('../views/VaultSettings.vue'), meta: { admin: true } },
      { path: 'rmm/snmp/:id', name: 'RmmSnmpDevice', component: () => import('../views/RmmSnmpDevice.vue') },

      // Zugangsschlüssel und Einladungen für Agents. Die Seite gab es schon,
      // sie war nur an keiner Stelle eingehängt.
      {
        path: 'agent-tokens',
        name: 'AgentTokens',
        component: () => import('../views/AgentTokens.vue'),
        meta: { admin: true }
      },

      // --- Auswertung --------------------------------------------------------
      {
        path: 'webhooks',
        name: 'Webhooks',
        component: () => import('../views/Webhooks.vue')
      },
      {
        path: 'app',
        name: 'AppQR',
        component: () => import('../views/AppQR.vue')
      },

      // --- Verwaltung --------------------------------------------------------
      {
        path: 'users',
        name: 'Users',
        component: () => import('../views/Users.vue'),
        meta: { admin: true }
      },
      // Single Sign-on hat eine eigene Seite statt eines Reiters: dort steht
      // eine Einrichtungsanleitung mit Rückadresse zum Kopieren, und das
      // braucht Platz.
      {
        path: 'sso',
        name: 'SsoSettings',
        component: () => import('../views/SsoSettings.vue'),
        meta: { admin: true }
      },
      {
        path: 'storage',
        name: 'Storage',
        component: () => import('../views/Storage.vue'),
        meta: { admin: true }
      },

      // --- System ------------------------------------------------------------
      // Alte Adressen, die umgezogen sind
      { path: 'settings/agents', redirect: '/agent-tokens' },
      { path: 'settings/https', redirect: '/settings/network' },
      { path: 'devices', redirect: '/rmm' },

      // Ein Ziel für alle Einstellungen. Der Teil hinter /settings wählt den
      // Abschnitt (z.B. /settings/ldap, /settings/dns).
      {
        path: 'settings/:tab?',
        name: 'Settings',
        component: () => import('../views/Settings.vue')
      },
      {
        path: 'health',
        name: 'Health',
        component: () => import('../views/Health.vue')
      },
      {
        path: 'updates',
        name: 'Updates',
        component: () => import('../views/Updates.vue'),
        meta: { admin: true }
      },
      {
        path: 'backup',
        name: 'Backup',
        component: () => import('../views/Backup.vue'),
        meta: { admin: true }
      },
      {
        path: 'ai',
        name: 'AiSettings',
        component: () => import('../views/AiSettings.vue'),
        meta: { admin: true }
      },
      {
        path: 'stacks',
        name: 'Stacks',
        component: () => import('../views/Stacks.vue'),
        meta: { admin: true }
      },
      {
        path: 'mail',
        name: 'Mail',
        component: () => import('../views/MailSettings.vue'),
        meta: { admin: true }
      },
      {
        path: 'alerts',
        name: 'Alerts',
        component: () => import('../views/Alerts.vue'),
        meta: { admin: true }
      },
      {
        path: 'terminal',
        name: 'Terminal',
        component: () => import('../views/Terminal.vue'),
        meta: { admin: true }
      },
      {
        path: 'containers',
        name: 'Containers',
        component: () => import('../views/Containers.vue'),
        meta: { admin: true }
      },
      {
        path: 'journal',
        name: 'Journal',
        component: () => import('../views/Journal.vue'),
        meta: { admin: true }
      },

      // --- Hilfe -------------------------------------------------------------
      // Bewusst für alle erreichbar, nicht nur für Administratoren: die Frage
      // "was ist das hier eigentlich und wo kommt es her?" hat jeder Benutzer.
      {
        path: 'about',
        name: 'About',
        component: () => import('../views/About.vue')
      },
    ]
  },

  // ---------------------------------------------------------------------------
  // Unbekannte Adresse: zurück auf das Dashboard statt leere Seite
  // ---------------------------------------------------------------------------
  {
    path: '/:pathMatch(.*)*',
    redirect: '/'
  }
]

// =============================================================================
// Router-Instanz erstellen
// =============================================================================
const router = createRouter({
  history: createWebHistory(),
  routes
})

// =============================================================================
// Navigation Guard - Auth-Prüfung
// =============================================================================
router.beforeEach(async (to, from, next) => {
  const auth = useAuthStore()

  // Öffentliche Routen durchlassen
  if (to.meta.public) return next()

  // Kein Token = zum Login
  if (!auth.token) return next('/login')

  // User-Daten laden falls noch nicht vorhanden
  if (!auth.user) {
    try {
      await auth.fetchUser()
    } catch {
      return next('/login')
    }
  }

  // Startpasswort: erst ein eigenes setzen, dann geht es weiter.
  if (auth.user?.must_change_password && to.name !== 'ChangePassword') return next('/change-password')
  if (!auth.user?.must_change_password && to.name === 'ChangePassword') return next('/')

  // Admin-Bereiche: Nicht-Admins landen auf dem Dashboard statt in einer
  // Ansicht, die ihnen ohnehin nur Fehler zeigen würde.
  if (to.meta.admin && !auth.isAdmin) return next('/')

  next()
})

export default router
