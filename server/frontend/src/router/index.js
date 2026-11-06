/**
 * ==============================================================================
 * Name:           Phydran6
 * Kontakt:        Phydran6
 * Version:        2026.08.14.12.00.00
 * Changelog:      docs/changelog/frontend.md
 * ==============================================================================
 *
 * RIMPERIO Vue Router - Navigation und Route-Definitionen
 * ======================================================
 * Fünf Bereiche, und jeder beantwortet eine andere Frage:
 *
 *   Überwachung   Was ist im Netz passiert?        Dashboard, Logs, Geräte, Systeme, Zustand
 *   Auswertung    Was mache ich damit?             KI, Webhooks, App
 *   Verwaltung    Wer darf was, und wie lange?     Benutzer, Zugang, Daten
 *   System        Womit läuft das hier?            Container, Updates, Konsole, …
 *   Hilfe         Was ist das eigentlich?          Über RIMPERIO & FAQ
 *
 * Die Trennlinie zwischen „Verwaltung" und „System" ist bewusst diese: in der
 * Verwaltung geht es um Menschen und Daten, im System um die Maschine.
 *
 * Einstellungen, die keine eigene Seite verdienen, liegen weiter unter
 * /settings als Reiter. Die alten Adressen wie /settings/ldap funktionieren
 * unverändert: der Teil hinter /settings wählt den Reiter. Angesteuert werden
 * sie aus dem linken Menü — die Reiterleiste rechts ist nur der zweite Weg.
 *
 * Was eine eigene Seite hat, hat sie, weil es ein eigenes Werkzeug ist und
 * keine Einstellung: Container, Systemtagebuch, Sicherung, Konsole,
 * KI-Auswertung, Single Sign-on, Speicherplatz, Über RIMPERIO.
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
      // Ein Ziel für alle Einstellungen. Der optionale Teil hinter /settings
      // wählt den Reiter (z.B. /settings/ldap).
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

  // Admin-Bereiche: Nicht-Admins landen auf dem Dashboard statt in einer
  // Ansicht, die ihnen ohnehin nur Fehler zeigen würde.
  if (to.meta.admin && !auth.isAdmin) return next('/')

  next()
})

export default router
