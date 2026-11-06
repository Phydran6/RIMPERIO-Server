<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Zugangsschluessel fuer Agents.

     Was sich gegenueber der Vorgaengerfassung geaendert hat - und warum:

     * Diese Liste zeigte die Schluessel im KLARTEXT, und zwar jedem
       angemeldeten Benutzer. Ein Konto mit reinen Leserechten kam damit an den
       Generalschluessel. Jetzt: nur Administratoren, und der Schluessel wird
       genau einmal angezeigt - direkt nach dem Erzeugen.
     * Es gab genau EINEN Schluessel fuer alle Geraete. Wer einen Rechner
       aufmachte, hatte den Schluessel fuer alle. Jetzt bekommt jedes Geraet
       seinen eigenen; beim Installieren holt der Agent ihn sich selbst.
     * "Einladungen" sind neu: kurzlebig, zaehlbar, duerfen nur einen
       Geraeteschluessel anfordern. Damit muss der Generalschluessel nicht mehr
       auf jeden Rechner kopiert werden.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">Zugangsschlüssel</h2>
        <p class="page-subtitle">
          Womit sich Agenten und Sammler am Server ausweisen.
        </p>
      </div>
      <button class="btn btn-secondary btn-sm" :disabled="loading" @click="load">
        <AppIcon name="refresh" :size="16" />
        Neu laden
      </button>
    </div>

    <!-- Der Schlüssel, direkt nach dem Erzeugen. Danach nie wieder. -->
    <div v-if="revealed" class="card mb-4" style="border-color: var(--color-warning)">
      <div class="card-header">
        <p class="card-title" style="color: var(--color-warning)">
          Jetzt notieren — danach ist dieser Schlüssel nicht mehr lesbar
        </p>
      </div>
      <div class="card-body">
        <p class="text-sm mb-2" style="color: var(--color-text-secondary)">
          {{ revealed.warning }}
        </p>
        <div class="flex gap-2">
          <input :value="revealed.token" class="input font-mono text-sm" readonly @focus="$event.target.select()">
          <button class="btn btn-primary btn-sm shrink-0" @click="copy(revealed.token)">
            <AppIcon :name="copied ? 'check' : 'key'" :size="14" />
            {{ copied ? 'Kopiert' : 'Kopieren' }}
          </button>
          <button class="btn btn-ghost btn-sm shrink-0" @click="revealed = null">Verstanden</button>
        </div>
      </div>
    </div>

    <div v-if="message" class="card mb-4" :style="{ borderColor: messageError ? 'var(--color-danger)' : 'var(--color-success)' }">
      <div class="card-body text-sm" :style="{ color: messageError ? 'var(--color-danger)' : 'var(--color-success)' }">
        {{ message }}
      </div>
    </div>

    <!-- Altbestand: Schlüssel, die noch im Klartext in der Datenbank liegen -->
    <div v-if="legacyCount" class="card mb-4" style="border-color: var(--color-warning)">
      <div class="card-body">
        <p class="font-semibold mb-1" style="color: var(--color-warning)">
          {{ legacyCount }} Schlüssel aus einer früheren Fassung
        </p>
        <p class="text-sm mb-3" style="color: var(--color-text-secondary)">
          Sie liegen noch im Klartext in der Datenbank — wer einen Datenbankabzug in die
          Hände bekommt, hat sie. Das Überführen trägt die Prüfsumme nach und löscht den
          Klartext. <strong>Die Schlüssel bleiben dabei gültig</strong>, auf den Geräten
          ändert sich nichts. Was verloren geht, ist nur die Möglichkeit, sie hier noch
          einmal abzulesen.
        </p>
        <button class="btn btn-primary btn-sm" :disabled="hardening" @click="harden">
          <AppIcon name="lock" :size="14" />
          {{ hardening ? 'Läuft…' : 'In den geschützten Speicher überführen' }}
        </button>
      </div>
    </div>

    <!-- Neu anlegen -->
    <div class="card mb-4">
      <div class="card-header"><p class="card-title">Neuen Schlüssel anlegen</p></div>
      <div class="card-body space-y-4">
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div class="md:col-span-2">
            <label class="label">Name</label>
            <input v-model="form.name" class="input" placeholder="z.B. n8n-Sammler oder Einladung Büro"
                   @keyup.enter="create">
          </div>
          <div>
            <label class="label">Art</label>
            <select v-model="form.kind" class="select">
              <option value="enroll">Einladung (empfohlen)</option>
              <option value="agent">Fester Geräteschlüssel</option>
              <option value="global">Generalschlüssel</option>
            </select>
          </div>
        </div>

        <p class="hint">{{ kindHint }}</p>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div v-if="form.kind === 'enroll'">
            <label class="label">Gilt für (Stunden)</label>
            <input v-model.number="form.expires_in_hours" type="number" min="1" class="input">
          </div>
          <div v-if="form.kind === 'enroll'">
            <label class="label">Wie oft einlösbar</label>
            <input v-model.number="form.max_uses" type="number" min="1" class="input">
          </div>
          <div :class="form.kind === 'enroll' ? '' : 'md:col-span-3'">
            <label class="label">Nur aus diesen Netzen (optional)</label>
            <input v-model="form.allowed_cidrs" class="input font-mono text-sm"
                   placeholder="10.0.0.0/8, 192.168.1.5/32">
            <p class="hint">Leer = von überall. Besonders sinnvoll beim Generalschlüssel.</p>
          </div>
        </div>

        <button class="btn btn-primary btn-sm" :disabled="creating || !form.name.trim()" @click="create">
          {{ creating ? 'Wird angelegt…' : 'Anlegen' }}
        </button>
      </div>
    </div>

    <!-- Liste -->
    <div class="card">
      <div class="card-body p-0">
        <div v-if="!items.length && !loading" class="empty-state">
          <p class="empty-state-title">Es gibt noch keinen Schlüssel.</p>
        </div>

        <table v-else class="table">
          <thead>
            <tr>
              <th>Name</th>
              <th style="width: 9rem">Art</th>
              <th style="width: 10rem">Kennung</th>
              <th style="width: 12rem">Zuletzt benutzt</th>
              <th style="width: 7rem">Zustand</th>
              <th style="width: 13rem">Aktionen</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in items" :key="item.id">
              <td>
                <p style="color: var(--color-text-primary)">{{ item.name }}</p>
                <p v-if="item.agent_hostname" class="text-xs" style="color: var(--color-text-muted)">
                  Gerät: {{ item.agent_hostname }}
                </p>
                <p v-if="item.warning" class="text-xs mt-0.5" style="color: var(--color-warning)">
                  {{ item.warning }}
                </p>
                <p v-if="item.allowed_cidrs" class="text-xs mt-0.5 font-mono" style="color: var(--color-text-muted)">
                  nur aus {{ item.allowed_cidrs }}
                </p>
              </td>
              <td>
                <span class="badge" :class="kindBadge(item.kind)">{{ kindLabel(item.kind) }}</span>
                <p v-if="item.kind === 'enroll'" class="text-xs mt-1" style="color: var(--color-text-muted)">
                  {{ item.use_count }} von {{ item.max_uses ?? '∞' }} eingelöst
                </p>
              </td>
              <td class="font-mono text-xs" style="color: var(--color-text-muted)">
                {{ item.prefix || '—' }}…
              </td>
              <td class="text-xs" style="color: var(--color-text-secondary)">
                <template v-if="item.last_used_at">
                  {{ formatTime(item.last_used_at) }}
                  <span v-if="item.last_used_ip" class="block" style="color: var(--color-text-muted)">
                    von {{ item.last_used_ip }}
                  </span>
                </template>
                <span v-else style="color: var(--color-text-muted)">noch nie</span>
              </td>
              <td>
                <span class="badge" :class="item.usable ? 'badge-success' : 'badge-neutral'">
                  {{ item.usable ? 'gültig' : 'gesperrt' }}
                </span>
                <p v-if="!item.usable && item.state_reason" class="text-xs mt-1" style="color: var(--color-text-muted)">
                  {{ item.state_reason }}
                </p>
              </td>
              <td>
                <div class="flex flex-wrap gap-1">
                  <button class="btn btn-secondary btn-sm" @click="regenerate(item)">Neu würfeln</button>
                  <button
                    v-if="item.kind !== 'global'"
                    class="btn btn-danger btn-sm"
                    @click="remove(item)"
                  >Löschen</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <p class="hint mt-3">{{ note }}</p>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'

const auth = useAuthStore()

const items = ref([])
const note = ref('')
const loading = ref(false)
const creating = ref(false)
const hardening = ref(false)
const revealed = ref(null)
const copied = ref(false)
const message = ref('')
const messageError = ref(false)

const form = reactive({
  name: '',
  kind: 'enroll',
  allowed_cidrs: '',
  expires_in_hours: 24,
  max_uses: 1,
})

const legacyCount = computed(() => items.value.filter(item => item.legacy_plaintext).length)

const KIND_HINTS = {
  enroll: 'Eine Einladung. Der Agent tauscht sie beim Installieren gegen einen eigenen '
    + 'Schlüssel und wirft sie weg. Nach Ablauf oder Verbrauch ist sie wertlos — '
    + 'genau deshalb ist sie der sichere Weg, um einen Rechner anzuschließen.',
  agent: 'Ein fester Schlüssel für genau ein Gerät. Er darf nur für dieses Gerät '
    + 'liefern und nur sich selbst abmelden. Sinnvoll, wenn der Agent nicht selbst '
    + 'anfragen kann.',
  global: 'Der Generalschlüssel des Administrators: darf anmelden, für jedes Gerät '
    + 'liefern und jedes abmelden. Den braucht man für Sammler wie n8n, die Logs '
    + 'für fremde Geräte einliefern — und sonst möglichst nirgends. Es gibt nur einen.',
}

const kindHint = computed(() => KIND_HINTS[form.kind] || '')

function kindLabel(kind) {
  return { global: 'Generalschlüssel', agent: 'Gerät', enroll: 'Einladung' }[kind] || kind
}

function kindBadge(kind) {
  if (kind === 'global') return 'badge-danger'
  if (kind === 'enroll') return 'badge-primary'
  return 'badge-neutral'
}

function formatTime(value) {
  if (!value) return '–'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('de-DE')
}

function say(text, isError = false) {
  message.value = text
  messageError.value = isError
}

async function load() {
  loading.value = true
  try {
    const data = await auth.api('/api/agent-tokens')
    items.value = data.items || []
    note.value = data.note || ''
  } catch (e) {
    say(e.message, true)
  } finally {
    loading.value = false
  }
}

async function create() {
  if (!form.name.trim()) return
  creating.value = true
  say('')
  try {
    const body = {
      name: form.name.trim(),
      kind: form.kind,
      allowed_cidrs: form.allowed_cidrs.trim(),
    }
    if (form.kind === 'enroll') {
      body.expires_in_hours = form.expires_in_hours
      body.max_uses = form.max_uses
    }
    revealed.value = await auth.api('/api/agent-tokens', { method: 'POST', body })
    form.name = ''
    await load()
  } catch (e) {
    say(e.message, true)
  } finally {
    creating.value = false
  }
}

async function regenerate(item) {
  const warning = item.kind === 'global'
    ? 'Der Generalschlüssel wird neu gewürfelt. Alles, was ihn benutzt (z.B. n8n), '
      + 'kommt danach nicht mehr durch, bis der neue eingetragen ist. Fortfahren?'
    : `Schlüssel "${item.name}" neu würfeln? Geräte mit dem alten liefern danach nichts mehr.`
  if (!confirm(warning)) return

  try {
    revealed.value = await auth.api(`/api/agent-tokens/${item.id}/regenerate`, { method: 'POST' })
    await load()
  } catch (e) {
    say(e.message, true)
  }
}

async function remove(item) {
  if (!confirm(`Schlüssel "${item.name}" löschen? Das lässt sich nicht rückgängig machen.`)) return
  try {
    await auth.api(`/api/agent-tokens/${item.id}`, { method: 'DELETE' })
    await load()
    say(`Schlüssel "${item.name}" gelöscht.`)
  } catch (e) {
    say(e.message, true)
  }
}

async function harden() {
  hardening.value = true
  try {
    const result = await auth.api('/api/agent-tokens/harden', { method: 'POST' })
    say(result.message)
    await load()
  } catch (e) {
    say(e.message, true)
  } finally {
    hardening.value = false
  }
}

async function copy(value) {
  try {
    await navigator.clipboard.writeText(value)
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  } catch {
    // Ohne Zwischenablage-Recht bleibt das Feld zum Markieren stehen.
  }
}

onMounted(load)
</script>
