<!--
  Datei:        AppShell.vue
  Projekt:      RIMPERIO — Frontend
  Autor:        Phydran6
  Version:      2026.10.31.15.40.00
  Beschreibung: Rahmen mit Seitenleiste, Navigation und abgemeldetem Benutzer.
-->
<template>
  <div class="min-h-screen flex">
    <!-- Seitenleiste -->
    <aside class="hidden md:flex md:flex-col w-60 shrink-0 bg-ink-900 border-r border-white/5">
      <div class="h-16 flex items-center gap-2 px-5 border-b border-white/5">
        <img src="/favicon.svg" alt="RIMPERIO" class="w-7 h-7 rounded-lg" />
        <span class="font-semibold tracking-tight">RIMPERIO</span>
      </div>
      <nav class="flex-1 p-3 space-y-1">
        <router-link v-for="item in nav" :key="item.to" :to="item.to"
          class="flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-slate-300 hover:bg-white/5"
          active-class="bg-brand-500/15 text-brand-200">
          <span v-html="item.icon" class="w-4 h-4 opacity-80"></span>
          {{ item.label }}
        </router-link>
      </nav>
      <div class="p-3 border-t border-white/5 text-xs text-slate-500">
        v{{ version }}
      </div>
    </aside>

    <!-- Hauptbereich -->
    <div class="flex-1 flex flex-col min-w-0">
      <header class="h-16 flex items-center justify-between px-4 md:px-6 border-b border-white/5 bg-ink-900/60 backdrop-blur">
        <!-- Mobile-Navigation -->
        <div class="md:hidden flex items-center gap-2 overflow-x-auto">
          <router-link v-for="item in nav" :key="item.to" :to="item.to"
            class="px-2.5 py-1.5 rounded-lg text-xs text-slate-300 hover:bg-white/5 whitespace-nowrap"
            active-class="bg-brand-500/15 text-brand-200">{{ item.label }}</router-link>
        </div>
        <div class="hidden md:block text-sm text-slate-400">{{ title }}</div>
        <div class="flex items-center gap-3">
          <span class="text-sm text-slate-400">{{ auth.user?.username }}
            <span class="text-slate-600">· {{ auth.user?.role }}</span>
          </span>
          <button class="btn-ghost" @click="logout">Abmelden</button>
        </div>
      </header>

      <main class="flex-1 p-4 md:p-6 max-w-7xl w-full mx-auto">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from '../stores/auth'

const auth = useAuth()
const router = useRouter()
const route = useRoute()
const version = '2026.11.05.12.00.00'

const icon = {
  dash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/></svg>',
  agents: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="4" width="20" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>',
  alerts: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3a6 6 0 0 0-6 6v4l-2 3h16l-2-3V9a6 6 0 0 0-6-6zM10 20a2 2 0 0 0 4 0"/></svg>',
  settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1l2-1.6-2-3.4-2.3 1a7 7 0 0 0-1.7-1L14.5 2h-5l-.4 2.4a7 7 0 0 0-1.7 1l-2.3-1-2 3.4 2 1.6a7 7 0 0 0 0 2l-2 1.6 2 3.4 2.3-1a7 7 0 0 0 1.7 1l.4 2.4h5l.4-2.4a7 7 0 0 0 1.7-1l2.3 1 2-3.4-2-1.6a7 7 0 0 0 .1-1z"/></svg>',
  users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="8" r="3"/><path d="M3 20a6 6 0 0 1 12 0M16 11a3 3 0 0 0 0-6M21 20a6 6 0 0 0-5-5.9"/></svg>',
  checks: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>',
  scripts: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 17l6-6-6-6M12 19h8"/></svg>',
  patches: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-3-6.7M21 4v5h-5"/></svg>',
  integ: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></svg>'
}

const nav = computed(() => {
  const items = [
    { to: '/dashboard', label: 'Dashboard', icon: icon.dash },
    { to: '/agents', label: 'Agents', icon: icon.agents },
    { to: '/checks', label: 'Checks', icon: icon.checks },
    { to: '/scripts', label: 'Skripte', icon: icon.scripts },
    { to: '/patches', label: 'Patches', icon: icon.patches },
    { to: '/alerts', label: 'Alarme', icon: icon.alerts }
  ]
  if (auth.isAdmin) items.push({ to: '/integrations', label: 'Integrationen', icon: icon.integ })
  items.push({ to: '/settings', label: 'Einstellungen', icon: icon.settings })
  if (auth.isAdmin) items.push({ to: '/users', label: 'Benutzer', icon: icon.users })
  return items
})

const title = computed(() => {
  const map = {
    dashboard: 'Dashboard', agents: 'Agents', agent: 'Agent', checks: 'Checks',
    scripts: 'Skripte', patches: 'Patches', alerts: 'Alarme',
    integrations: 'Integrationen', settings: 'Einstellungen', users: 'Benutzer'
  }
  return map[route.name] || ''
})

function logout() {
  auth.logout()
  router.push({ name: 'login' })
}
</script>
