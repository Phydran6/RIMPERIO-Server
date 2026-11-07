<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.11.07.23.00.00
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Hauptlayout: linkes Menue, Kopfzeile, Inhalt.

     Was sich gegenueber der Vorgaenger-Fassung geaendert hat:

     * Das Menue ist nach RMM-Arbeitsbereichen gegliedert (src/navigation.js),
       nicht mehr nach der Herkunft aus dem Log-Server. Jeder Menuepunkt ist
       genau eine Seite; die Reiterleisten in der Einstellungsseite sind weg.
     * Nur noch zwei Ebenen: Bereich -> Seite. Vorher drei, und es war immer
       nur ein Bereich offen - beim Wechsel klappte der andere weg.
     * Bereiche lassen sich einzeln zuklappen, das merkt sich der Browser.
       Der Bereich der aktuellen Seite ist immer offen.
     * Suchfeld oben: tippt man "dns", "patch" oder "app", bleiben nur die
       passenden Punkte stehen, Enter oeffnet den ersten.
     * Eingeklappt bleibt eine Icon-Leiste mit allen Seiten (Tooltip = Name).
     ============================================================================== -->

<template>
  <div class="flex h-full" style="background-color: var(--color-bg)">
    <!-- Mobil: Overlay hinter der Sidebar -->
    <transition name="fade">
      <div
        v-if="sidebarOpen"
        class="fixed inset-0 z-20 md:hidden"
        style="background-color: var(--overlay)"
        @click="sidebarOpen = false"
      />
    </transition>

    <!-- ================================================================
         SEITENMENÜ
         ================================================================ -->
    <aside
      class="sidebar"
      :class="[sidebarOpen ? 'is-open' : '', isCollapsed ? 'is-collapsed' : '']"
    >
      <!-- Marke -->
      <div class="sidebar-brand">
        <router-link to="/" class="brand-mark" :title="companyName" @click="closeMobile">
          <img v-if="logoUrl" :src="logoUrl" :alt="companyName" class="brand-logo">
          <span v-else class="brand-initial">{{ brandInitial }}</span>
        </router-link>

        <div v-if="!isCollapsed" class="brand-text">
          <p class="brand-name">{{ companyName }}</p>
          <p class="brand-version">v{{ appVersion }}</p>
        </div>

        <button
          class="btn-icon md:hidden"
          :title="t('nav.closeMenu')"
          :aria-label="t('nav.closeMenu')"
          @click="sidebarOpen = false"
        >
          <AppIcon name="close" :size="18" />
        </button>
      </div>

      <!-- Suche im Menü -->
      <div v-if="!isCollapsed" class="sidebar-search">
        <AppIcon name="search" :size="15" class="search-icon" />
        <input
          ref="searchInput"
          v-model="query"
          type="search"
          class="search-input"
          :placeholder="t('nav.searchMenu')"
          :aria-label="t('nav.searchMenu')"
          @keydown.enter.prevent="openFirstMatch"
          @keydown.esc="query = ''"
        >
      </div>

      <!-- ============================================================
           NAVIGATION - Bereich -> Seite
           ============================================================ -->
      <nav class="sidebar-nav" aria-label="Hauptmenü">
        <template v-for="section in visibleSections" :key="section.key">
          <div class="nav-section" :class="{ 'is-flat': section.flat }">
            <!-- Bereichsüberschrift (aufgeklappt) bzw. Trennlinie (eingeklappt) -->
            <button
              v-if="!section.flat && !isCollapsed"
              class="section-head"
              :class="{ 'has-active': active.section?.key === section.key }"
              :aria-expanded="isSectionOpen(section)"
              @click="toggleSection(section.key)"
            >
              <span class="section-title">{{ t('nav.' + section.title) }}</span>
              <AppIcon
                v-if="!searching"
                name="chevronDown"
                :size="14"
                class="section-chevron"
                :class="{ 'is-open': isSectionOpen(section) }"
              />
            </button>
            <div v-else-if="!section.flat" class="section-divider" />

            <ul v-if="isSectionOpen(section)" class="nav-list">
              <li v-for="item in section.items" :key="item.key">
                <router-link
                  :to="item.to"
                  class="nav-row nav-link"
                  :class="{ active: isEntryActive(item, route) }"
                  :title="isCollapsed ? t('nav.' + item.label) : null"
                  @click="onNavigate"
                >
                  <AppIcon :name="item.icon" :size="17" class="shrink-0" />
                  <span v-if="!isCollapsed" class="nav-label">{{ t('nav.' + item.label) }}</span>
                  <span
                    v-if="badgeFor(item.key)"
                    class="nav-badge"
                    :class="badgeFor(item.key).tone"
                    :title="badgeFor(item.key).title"
                  >{{ badgeFor(item.key).value }}</span>
                </router-link>
              </li>
            </ul>
          </div>
        </template>
        <p v-if="searching && !visibleSections.length" class="nav-empty">{{ t('nav.noMatch') }}</p>
      </nav>

      <!-- Fußbereich -->
      <div class="sidebar-footer">
        <button
          class="nav-row footer-row collapse-btn hidden md:flex"
          :title="isCollapsed ? t('nav.expand') : t('nav.collapse')"
          :aria-label="isCollapsed ? t('nav.expand') : t('nav.collapse')"
          :aria-expanded="!collapsed"
          @click="toggleCollapsed"
        >
          <AppIcon :name="collapsed ? 'chevronRight' : 'chevronLeft'" :size="18" class="shrink-0" />
          <span v-if="!isCollapsed" class="nav-label">{{ t('nav.collapse') }}</span>
        </button>

        <div class="footer-tools" :class="{ 'is-stacked': isCollapsed }">
          <!-- Sprache -->
          <div class="language-row">
            <button
              class="nav-row footer-row"
              :title="t('nav.language')"
              :aria-expanded="languageOpen"
              @click="languageOpen = !languageOpen"
            >
              <span class="language-flag">{{ localeInfo.flag }}</span>
              <span v-if="!isCollapsed" class="nav-label">{{ localeInfo.label }}</span>
            </button>
            <ul v-if="languageOpen" class="language-list">
              <li v-for="option in locales" :key="option.code">
                <button
                  class="nav-row language-option"
                  :class="{ active: option.code === locale }"
                  @click="chooseLocale(option.code)"
                >
                  <span class="language-flag">{{ option.flag }}</span>
                  <span class="nav-label">{{ option.label }}</span>
                </button>
              </li>
            </ul>
          </div>

          <!-- Design -->
          <button
            class="nav-row footer-row"
            :title="isDark ? t('nav.toLight') : t('nav.toDark')"
            @click="toggleTheme"
          >
            <AppIcon :name="isDark ? 'moon' : 'sun'" :size="18" class="shrink-0" />
            <span v-if="!isCollapsed" class="nav-label">
              {{ isDark ? t('nav.darkMode') : t('nav.lightMode') }}
            </span>
          </button>
        </div>

        <!-- Benutzer -->
        <div class="nav-row footer-row footer-row--static">
          <router-link
            to="/settings/password"
            class="avatar"
            :title="auth.user?.username"
            @click="onNavigate"
          >{{ userInitial }}</router-link>
          <div v-if="!isCollapsed" class="min-w-0 flex-1">
            <p class="user-name">{{ auth.user?.username }}</p>
            <p class="user-role">{{ roleLabel }}</p>
          </div>
          <button
            v-if="!isCollapsed"
            class="btn-icon logout-btn"
            :title="t('nav.logout')"
            :aria-label="t('nav.logout')"
            @click="handleLogout"
          >
            <AppIcon name="logout" :size="18" />
          </button>
        </div>
        <button
          v-if="isCollapsed"
          class="nav-row footer-row hidden md:flex"
          :title="t('nav.logout')"
          :aria-label="t('nav.logout')"
          @click="handleLogout"
        >
          <AppIcon name="logout" :size="18" class="shrink-0" />
        </button>
      </div>
    </aside>

    <!-- ================================================================
         INHALT
         ================================================================ -->
    <main class="flex-1 overflow-auto min-w-0 flex flex-col">
      <header class="topbar">
        <button class="btn-icon md:hidden" :aria-label="t('nav.openMenu')" @click="sidebarOpen = true">
          <AppIcon name="menu" :size="20" />
        </button>
        <div class="min-w-0">
          <p v-if="breadcrumb" class="topbar-crumb">{{ breadcrumb }}</p>
          <h1 class="topbar-title">{{ pageTitle }}</h1>
        </div>
        <div class="flex-1" />

        <!-- Externer HTTPS-Proxy gerade übernommen -->
        <span v-if="proxyNotice" class="proxy-pill" :title="proxyNotice">
          <AppIcon name="lock" :size="14" />
          <span class="hidden sm:inline">HTTPS über Proxy übernommen</span>
        </span>

        <!-- Meldet sich, sobald auf GitHub etwas Neues liegt -->
        <router-link v-if="updateNotice" to="/updates" class="update-pill" :title="updateNotice">
          <AppIcon name="download" :size="14" />
          <span class="hidden sm:inline">{{ t('updates.updateAvailable') }}</span>
        </router-link>

        <NotificationBell />
      </header>

      <div class="flex-1">
        <router-view />
      </div>

      <footer class="app-footer">
        <span v-if="footerText">{{ footerText }}</span>
        <router-link to="/about" class="footer-link">{{ t('nav.about') }}</router-link>
      </footer>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useThemeStore } from '../stores/themeStore'
import { useBrandingStore } from '../stores/brandingStore'
import { useI18n } from '../i18n'
import { sectionsFor, isEntryActive, findActive } from '../navigation'
import AppIcon from '../components/AppIcon.vue'
import NotificationBell from '../components/NotificationBell.vue'
import pkg from '../../package.json'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const themeStore = useThemeStore()
const brandingStore = useBrandingStore()
const { t, locale, locales, localeInfo, setLocale } = useI18n()

const appVersion = pkg.version

const sidebarOpen = ref(false)
const languageOpen = ref(false)
const query = ref('')
const searchInput = ref(null)

const COLLAPSE_KEY = 'rimperio.sidebarCollapsed'
const CLOSED_SECTIONS_KEY = 'rimperio.sidebarClosedSections'

function readStored(key, fallback = '') {
  try {
    return localStorage.getItem(key) ?? fallback
  } catch {
    return fallback
  }
}

function writeStored(key, value) {
  try {
    localStorage.setItem(key, value)
  } catch {
    // Kein localStorage (Privat-Modus) - der Zustand gilt nur für diese Sitzung.
  }
}

const collapsed = ref(readStored(COLLAPSE_KEY) === '1')

// Nur am Desktop gibt es die Icon-Leiste. Auf dem Handy ist die Sidebar ein
// Overlay - dort wäre "eingeklappt" sinnlos.
const isDesktopWidth = ref(true)
let mediaQuery = null

function syncWidth(event) {
  isDesktopWidth.value = event.matches
}

const isCollapsed = computed(() => collapsed.value && isDesktopWidth.value)

watch(collapsed, (value) => writeStored(COLLAPSE_KEY, value ? '1' : '0'))
watch(isCollapsed, (value) => {
  if (value) {
    languageOpen.value = false
    query.value = ''
  }
})

const isDark = computed(() => themeStore.currentTheme === 'dark')
const companyName = computed(() => brandingStore.config?.company_name || 'RIMPERIO')
const footerText = computed(() => brandingStore.config?.footer_text || '')
const logoUrl = computed(() => brandingStore.getLogoUrl())
const brandInitial = computed(() => (companyName.value || 'R').trim().charAt(0).toUpperCase())
const userInitial = computed(() => (auth.user?.username || '?').trim().charAt(0).toUpperCase())
const roleLabel = computed(() => (
  auth.user?.role === 'admin' ? t('nav.administrator') : t('nav.user')
))

// =============================================================================
// Menü
// =============================================================================
const sections = computed(() => sectionsFor(auth.isAdmin))
const active = computed(() => findActive(sections.value, route))

const searching = computed(() => query.value.trim().length > 0)

/** Bei Suche: nur passende Einträge, Bereiche ohne Treffer fallen weg. */
const visibleSections = computed(() => {
  if (!searching.value) return sections.value
  const needle = query.value.trim().toLowerCase()
  return sections.value
    .map(section => {
      const sectionHit = section.title && t('nav.' + section.title).toLowerCase().includes(needle)
      const items = section.items.filter(item => sectionHit
        || t('nav.' + item.label).toLowerCase().includes(needle)
        || item.to.toLowerCase().includes(needle))
      return { ...section, items }
    })
    .filter(section => section.items.length)
})

function readClosed() {
  try {
    const parsed = JSON.parse(readStored(CLOSED_SECTIONS_KEY, '[]'))
    return new Set(Array.isArray(parsed) ? parsed : [])
  } catch {
    return new Set()
  }
}

const closedSections = ref(readClosed())

function storeClosed(next) {
  closedSections.value = next
  writeStored(CLOSED_SECTIONS_KEY, JSON.stringify([...next]))
}

function isSectionOpen(section) {
  if (section.flat || isCollapsed.value || searching.value) return true
  return !closedSections.value.has(section.key)
}

function toggleSection(key) {
  if (searching.value) return
  const next = new Set(closedSections.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  storeClosed(next)
}

// Der Bereich der geöffneten Seite geht beim Seitenwechsel auf - sonst sucht
// man die Markierung in einem zugeklappten Bereich.
watch(() => active.value.section?.key, (key) => {
  if (key && closedSections.value.has(key)) {
    const next = new Set(closedSections.value)
    next.delete(key)
    storeClosed(next)
  }
}, { immediate: true })

function openFirstMatch() {
  const first = visibleSections.value[0]?.items[0]
  if (!first) return
  router.push(first.to)
  query.value = ''
  closeMobile()
}

function onNavigate() {
  query.value = ''
  closeMobile()
}

function toggleCollapsed() {
  collapsed.value = !collapsed.value
}

function closeMobile() {
  sidebarOpen.value = false
}

function chooseLocale(code) {
  setLocale(code)
  languageOpen.value = false
}

// Strg+K bzw. "/" springt ins Suchfeld - wie in den meisten Verwaltungsoberflächen.
function onKey(event) {
  const target = event.target
  const typing = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)
  if ((event.key === 'k' && (event.ctrlKey || event.metaKey)) || (event.key === '/' && !typing)) {
    event.preventDefault()
    if (collapsed.value) collapsed.value = false
    if (!isDesktopWidth.value) sidebarOpen.value = true
    setTimeout(() => searchInput.value?.focus(), 0)
  }
}

// =============================================================================
// Zähler am Menü: was wartet auf mich?
// =============================================================================
// Alarme, Freigaben und Rechner offline stehen direkt am Menüpunkt - man sieht
// es von jeder Seite aus, ohne erst die Übersicht zu öffnen.
const counters = ref({})
let counterTimer = null

async function loadCounters() {
  const [rmm, patch] = await Promise.all([
    auth.api('/api/rmm/summary').catch(() => null),
    auth.api('/api/patching/overview').catch(() => null),
  ])
  counters.value = {
    alarms: rmm?.alerts_active || 0,
    offline: rmm?.agents_offline || 0,
    approvals: patch?.totals?.needs_approval || 0,
  }
}

function badgeFor(key) {
  const c = counters.value
  if (key === 'alarms' && c.alarms) return { value: c.alarms, tone: 'is-danger', title: `${c.alarms} aktive Alarme` }
  if (key === 'patch-center' && c.approvals) return { value: c.approvals, tone: 'is-warning', title: `${c.approvals} Updates warten auf Freigabe` }
  if (key === 'computers' && c.offline) return { value: c.offline, tone: 'is-muted', title: `${c.offline} Rechner offline` }
  return null
}

// =============================================================================
// Kopfzeile
// =============================================================================
const pageTitle = computed(() => {
  if (route.name === 'DeviceLogs') return String(route.params.hostname || t('nav.logSources'))
  if (active.value.item) return t('nav.' + active.value.item.label)
  return companyName.value
})

/** "Geräte · Rechner" - zeigt, wo im Menü man gerade steht. */
const breadcrumb = computed(() => {
  const { section, item } = active.value
  if (!section || section.flat) return ''
  if (route.name === 'DeviceLogs' || route.name === 'RmmAgentDetail' || route.name === 'RmmSnmpDevice') {
    return `${t('nav.' + section.title)} · ${t('nav.' + item.label)}`
  }
  return t('nav.' + section.title)
})

// =============================================================================
// Externer HTTPS-Proxy: einmal je Sitzung übernehmen lassen
// =============================================================================
// Kommt ein Administrator über einen vorgeschalteten HTTPS-Proxy (Nginx Proxy
// Manager, Traefik, Firewall), stellt der Server Caddy darauf ein - sonst
// stünde in QR-Code, SSO-Rückadresse und Links "http://". Der Server prüft
// selbst, ob DIESE Anfrage über einen Proxy kam; hier wird nur angestoßen.
const proxyNotice = ref('')
const PROXY_CHECK_KEY = 'rimperio.proxyAutoChecked'

async function adoptExternalProxy() {
  if (!auth.isAdmin || window.location.protocol !== 'https:') return
  try {
    if (sessionStorage.getItem(PROXY_CHECK_KEY) === '1') return
    sessionStorage.setItem(PROXY_CHECK_KEY, '1')
  } catch {
    // ohne sessionStorage: dann eben bei jedem Laden - der Server ist idempotent
  }
  try {
    const result = await auth.api('/api/caddy/external-proxy/auto', { method: 'POST' })
    if (result?.adopted) {
      proxyNotice.value = result.message || 'HTTPS über externen Proxy übernommen.'
      setTimeout(() => { proxyNotice.value = '' }, 15000)
    }
  } catch {
    // Kein Grund, die Oberfläche zu stören.
  }
}

// =============================================================================
// Sofortmeldung bei neuem Stand
// =============================================================================
const updateNotice = ref('')
let updateStream = null

function listenForUpdates() {
  if (!auth.isAdmin || typeof EventSource === 'undefined') return
  updateStream = auth.eventStream('/api/updates/stream', 'updates', (source) => {
    source.addEventListener('update.available', (event) => {
      try {
        const data = JSON.parse(event.data)
        updateNotice.value = data.reason || t('updates.updateAvailable')
      } catch {
        updateNotice.value = t('updates.updateAvailable')
      }
    })
    source.addEventListener('update.cleared', () => { updateNotice.value = '' })
  })
}

onMounted(() => {
  if (typeof window !== 'undefined' && window.matchMedia) {
    mediaQuery = window.matchMedia('(min-width: 768px)')
    isDesktopWidth.value = mediaQuery.matches
    mediaQuery.addEventListener('change', syncWidth)
  }
  window.addEventListener('keydown', onKey)
  listenForUpdates()
  adoptExternalProxy()
  loadCounters()
  counterTimer = setInterval(loadCounters, 60000)
})

onBeforeUnmount(() => {
  mediaQuery?.removeEventListener('change', syncWidth)
  window.removeEventListener('keydown', onKey)
  updateStream?.close()
  if (counterTimer) clearInterval(counterTimer)
})

function toggleTheme() {
  themeStore.toggleTheme()
}

function handleLogout() {
  updateStream?.close()
  auth.logout()
  router.push('/login')
}
</script>

<style scoped>
/* ------------------------------------------------------------------ Sidebar */
.sidebar {
  position: fixed;
  inset-block: 0;
  left: 0;
  z-index: 30;
  width: var(--sidebar-width);
  display: flex;
  flex-direction: column;
  background-color: var(--color-surface);
  border-right: 1px solid var(--color-border);
  transform: translateX(-100%);
  transition: transform var(--duration) var(--ease), width var(--duration) var(--ease);
}

.sidebar.is-open {
  transform: translateX(0);
}

@media (min-width: 768px) {
  .sidebar {
    position: relative;
    transform: none;
  }

  .sidebar.is-collapsed {
    width: var(--sidebar-width-collapsed);
  }
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  padding: 0.75rem;
  border-bottom: 1px solid var(--color-border);
  min-height: var(--header-height);
}

.is-collapsed .sidebar-brand {
  justify-content: center;
  padding-inline: 0.5rem;
}

.brand-mark {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2.25rem;
  height: 2.25rem;
  border-radius: var(--radius);
  background-color: var(--primary-soft);
  color: var(--color-primary);
  font-weight: 700;
  flex-shrink: 0;
  overflow: hidden;
}

.brand-logo {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.brand-initial {
  font-size: 1rem;
  line-height: 1;
}

.brand-text {
  flex: 1;
  min-width: 0;
}

.brand-name {
  font-weight: 600;
  font-size: 0.9375rem;
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.brand-version {
  font-size: 0.6875rem;
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}

/* ------------------------------------------------------------------- Suche */
.sidebar-search {
  position: relative;
  padding: 0.625rem 0.625rem 0.25rem;
}

.search-icon {
  position: absolute;
  left: 1.25rem;
  top: 50%;
  transform: translateY(-35%);
  color: var(--color-text-muted);
  pointer-events: none;
}

.search-input {
  width: 100%;
  padding: 0.4375rem 0.625rem 0.4375rem 2rem;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background-color: var(--color-bg);
  color: var(--color-text-primary);
  font-size: 0.8125rem;
  outline: none;
}

.search-input:focus {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--primary-ring);
}

/* --------------------------------------------------------------- Navigation */
.sidebar-nav {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 0.5rem 0.625rem 0.75rem;
}

.is-collapsed .sidebar-nav {
  padding-inline: 0.5rem;
}

.nav-section + .nav-section {
  margin-top: 0.5rem;
}

.section-head {
  display: flex;
  align-items: center;
  width: 100%;
  gap: 0.375rem;
  padding: 0.375rem 0.75rem 0.25rem;
  border-radius: var(--radius-sm);
  cursor: pointer;
  color: var(--color-text-muted);
}

.section-head:hover {
  color: var(--color-text-primary);
}

.section-title {
  flex: 1;
  text-align: left;
  font-size: 0.6875rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.section-head.has-active .section-title {
  color: var(--color-primary);
}

.section-chevron {
  transition: transform var(--duration) var(--ease);
  transform: rotate(-90deg);
}

.section-chevron.is-open {
  transform: rotate(0deg);
}

.section-divider {
  height: 1px;
  margin: 0.5rem 0.25rem;
  background-color: var(--color-border);
}

.nav-list {
  display: flex;
  flex-direction: column;
  gap: 0.0625rem;
}

/* Eine Zeile im Menü. */
.nav-row {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  width: 100%;
  padding: 0.4375rem 0.75rem;
  border-radius: var(--radius);
  font-size: 0.8125rem;
  color: var(--color-text-secondary);
  cursor: pointer;
  text-align: left;
  position: relative;
  transition: background-color var(--duration) var(--ease), color var(--duration) var(--ease);
}

.nav-row:hover {
  background-color: var(--hover-surface);
  color: var(--color-text-primary);
}

.nav-link.active {
  background-color: var(--primary-soft);
  color: var(--color-primary);
  font-weight: 600;
}

.nav-link.active::before {
  content: '';
  position: absolute;
  left: -0.625rem;
  top: 0.375rem;
  bottom: 0.375rem;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background-color: var(--color-primary);
}

.is-collapsed .nav-link.active::before {
  left: -0.5rem;
}

/* Eingeklappt: nur das Icon, mittig. */
.is-collapsed .nav-row {
  justify-content: center;
  padding-inline: 0.5rem;
}

.nav-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nav-badge {
  min-width: 1.25rem;
  padding: 0 0.375rem;
  border-radius: var(--radius-full);
  font-size: 0.6875rem;
  font-weight: 700;
  line-height: 1.25rem;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.nav-badge.is-danger {
  background-color: var(--danger-soft);
  color: var(--color-danger);
}

.nav-badge.is-warning {
  background-color: var(--warning-soft);
  color: var(--color-warning);
}

.nav-badge.is-muted {
  background-color: var(--hover-surface);
  color: var(--color-text-muted);
}

/* Eingeklappt: Zähler als kleiner Punkt oben rechts am Icon. */
.is-collapsed .nav-badge {
  position: absolute;
  top: 0.125rem;
  right: 0.125rem;
  min-width: 1rem;
  padding: 0 0.25rem;
  font-size: 0.5625rem;
  line-height: 1rem;
}

.nav-empty {
  padding: 0.75rem;
  font-size: 0.8125rem;
  color: var(--color-text-muted);
}

/* --------------------------------------------------------------- Fußbereich */
.sidebar-footer {
  padding: 0.5rem 0.625rem 0.625rem;
  border-top: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

.is-collapsed .sidebar-footer {
  padding-inline: 0.5rem;
}

.footer-tools {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.125rem;
}

.footer-tools.is-stacked {
  grid-template-columns: 1fr;
}

.footer-row {
  padding-block: 0.4375rem;
}

.footer-row--static {
  cursor: default;
}

.footer-row--static:hover {
  background-color: transparent;
}

.collapse-btn {
  color: var(--color-text-muted);
}

/* ----------------------------------------------------------------- Sprache */
.language-row {
  position: relative;
}

.language-list {
  position: absolute;
  bottom: calc(100% + 0.25rem);
  left: 0;
  z-index: 5;
  min-width: 10rem;
  padding: 0.25rem;
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background-color: var(--color-surface);
  box-shadow: var(--shadow-md);
}

.language-option {
  font-size: 0.8125rem;
  padding-block: 0.375rem;
  justify-content: flex-start !important;
}

.language-option.active {
  color: var(--color-primary);
  font-weight: 600;
}

.language-flag {
  font-size: 1rem;
  line-height: 1;
  flex-shrink: 0;
}

/* ---------------------------------------------------------------- Benutzer */
.avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.875rem;
  height: 1.875rem;
  border-radius: var(--radius-full);
  background-color: var(--color-surface-elevated);
  border: 1px solid var(--color-border);
  color: var(--color-text-primary);
  font-size: 0.8125rem;
  font-weight: 600;
  flex-shrink: 0;
  text-decoration: none;
}

.avatar:hover {
  border-color: var(--color-primary);
}

.user-name {
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-role {
  font-size: 0.6875rem;
  color: var(--color-text-muted);
}

.logout-btn:hover {
  background-color: var(--danger-soft);
  color: var(--color-danger);
}

/* -------------------------------------------------------------- Kopfleiste */
.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  min-height: var(--header-height);
  padding: 0 1.5rem;
  background-color: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
}

.topbar-title {
  font-size: 1rem;
  font-weight: 600;
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.topbar-crumb {
  font-size: 0.6875rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.update-pill,
.proxy-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.3125rem 0.625rem;
  border-radius: var(--radius-full);
  font-size: 0.75rem;
  font-weight: 600;
  background-color: var(--primary-soft);
  color: var(--color-primary);
}

.proxy-pill {
  background-color: var(--success-soft);
  color: var(--color-success);
}

.update-pill:hover {
  filter: brightness(1.1);
}

/* ------------------------------------------------------------------ Footer */
.app-footer {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.75rem 1.5rem;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  border-top: 1px solid var(--color-border);
  background-color: var(--color-surface);
}

.footer-link {
  margin-left: auto;
  color: var(--color-primary);
  text-decoration: none;
}

.footer-link:hover {
  text-decoration: underline;
}

/* ---------------------------------------------------------------- Übergang */
.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--duration) var(--ease);
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
