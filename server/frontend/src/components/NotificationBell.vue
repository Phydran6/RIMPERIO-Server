<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Glocke in der Kopfzeile: Eingang (Freigaben, Fehlschläge, Hinweise)
     ============================================================================== -->
<template>
  <div class="bell" ref="root">
    <button class="btn-icon bell-btn" :title="unread ? `${unread} ungelesen` : 'Eingang'" @click="toggle">
      <AppIcon name="bell" :size="18" />
      <span v-if="unread" class="bell-count">{{ unread > 99 ? '99+' : unread }}</span>
    </button>
    <div v-if="open" class="bell-panel card">
      <div class="bell-head">
        <strong>Eingang</strong>
        <button v-if="unread" class="btn btn-ghost btn-sm" @click="readAll">alle gelesen</button>
      </div>
      <p v-if="!items.length" class="rmm-muted bell-empty">Keine Meldungen.</p>
      <ul class="bell-list">
        <li v-for="n in items" :key="n.id" :class="{ 'is-unread': !n.read }" @click="go(n)">
          <span class="badge" :class="levelClass(n.level)">{{ n.category }}</span>
          <div class="bell-text">
            <div class="bell-title">{{ n.title }}</div>
            <div v-if="n.body" class="rmm-muted bell-body">{{ n.body }}</div>
            <div class="rmm-muted">{{ fmtAgo(n.created_at) }}
              <span v-if="n.action && !n.resolved" class="badge badge-warning">wartet</span>
              <span v-else-if="n.action" class="badge badge-neutral">erledigt</span>
            </div>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import AppIcon from './AppIcon.vue'
import { fmtAgo } from '../utils/rmm'

const auth = useAuthStore()
const router = useRouter()
const root = ref(null)
const open = ref(false)
const items = ref([])
const unread = ref(0)
let timer = null

function levelClass(level) {
  return level === 'critical' ? 'badge-danger' : level === 'warning' ? 'badge-warning' : 'badge-primary'
}

async function load() {
  try {
    const data = await auth.api('/api/notifications?limit=30')
    items.value = data.items
    unread.value = data.unread
  } catch { /* still - die Glocke darf die Seite nicht stören */ }
}

async function toggle() {
  open.value = !open.value
  if (open.value) await load()
}

async function readAll() {
  await auth.api('/api/notifications/read', { method: 'POST', body: { ids: [] } })
  await load()
}

async function go(n) {
  if (!n.read) await auth.api('/api/notifications/read', { method: 'POST', body: { ids: [n.id] } })
  open.value = false
  await load()
  if (n.link) router.push(n.link)
}

function outside(e) {
  if (open.value && root.value && !root.value.contains(e.target)) open.value = false
}

onMounted(() => {
  load()
  timer = setInterval(load, 30000)
  document.addEventListener('click', outside)
})
onBeforeUnmount(() => {
  clearInterval(timer)
  document.removeEventListener('click', outside)
})
</script>

<style scoped>
.bell { position: relative; }
.bell-btn { position: relative; }
.bell-count {
  position: absolute; top: -2px; right: -4px; min-width: 1.1rem; height: 1.1rem; padding: 0 .25rem;
  border-radius: 9999px; background: var(--color-danger); color: #fff; font-size: .65rem;
  line-height: 1.1rem; text-align: center; font-weight: 700;
}
.bell-panel {
  position: absolute; right: 0; top: calc(100% + .4rem); width: min(24rem, calc(100vw - 2rem));
  max-height: 70vh; overflow: auto; z-index: 50; padding: .5rem;
}
.bell-head { display: flex; justify-content: space-between; align-items: center; padding: .25rem .5rem .5rem; }
.bell-empty { padding: .5rem; }
.bell-list { list-style: none; margin: 0; padding: 0; }
.bell-list li { display: flex; gap: .6rem; padding: .5rem; border-radius: var(--radius-sm); cursor: pointer; align-items: flex-start; }
.bell-list li:hover { background: var(--color-surface); }
.bell-list li.is-unread .bell-title { font-weight: 600; }
.bell-text { min-width: 0; flex: 1; }
.bell-title { font-size: .875rem; overflow-wrap: anywhere; }
.bell-body { overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
</style>
