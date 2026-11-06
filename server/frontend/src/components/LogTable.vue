<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.07.31.22.10.00
     Beschreibung: RIMPERIO - Wiederverwendbare Log-Liste (Filter, Tabelle, Detail,
                   Export). Wird von der allgemeinen Log-Ansicht und von der
                   Geraete-Ansicht (auf einen Host fixiert) genutzt.
     ============================================================================== -->

<template>
  <div>
    <!-- ================================================================
         FILTER-PANEL
         ================================================================ -->
    <div class="rounded-lg shadow mb-4" :style="cardStyle">
      <!-- Header -->
      <div
        class="flex items-center justify-between px-4 py-3 cursor-pointer select-none border-b"
        :style="{ borderColor: 'var(--color-border)' }"
        @click="filterOpen = !filterOpen"
      >
        <span class="font-semibold text-sm" :style="{ color: 'var(--color-text-primary)' }">
          Filter
          <span v-if="activeFilterCount > 0" class="ml-2 px-2 py-0.5 rounded-full text-xs text-white" :style="{ backgroundColor: 'var(--color-primary)' }">
            {{ activeFilterCount }}
          </span>
        </span>
        <div class="flex items-center gap-3">
          <button
            class="text-xs px-3 py-1 rounded"
            :style="buttonSecondaryStyle"
            :disabled="exporting"
            @click.stop="exportLogs('csv')"
          >CSV</button>
          <button
            class="text-xs px-3 py-1 rounded"
            :style="buttonSecondaryStyle"
            :disabled="exporting"
            @click.stop="exportLogs('json')"
          >JSON</button>
          <span :style="{ color: 'var(--color-text-muted)' }">{{ filterOpen ? '▲' : '▼' }}</span>
        </div>
      </div>

      <!-- Felder -->
      <div v-show="filterOpen" class="p-4 space-y-4">
        <!-- Zeile 0: Logtypen -->
        <div class="space-y-3 pb-3 border-b" :style="{ borderColor: 'var(--color-border)' }">
          <!-- Schweregrad als Schnellauswahl -->
          <div class="flex flex-wrap items-center gap-2">
            <span class="text-xs font-medium w-20 shrink-0" :style="{ color: 'var(--color-text-muted)' }">Schweregrad</span>
            <button
              class="text-xs px-3 py-1 rounded"
              :style="severityButtonStyle('')"
              @click="setSeverity('')"
            >Alle</button>
            <button
              v-for="s in filterOptions.severities"
              :key="s.key"
              class="text-xs px-3 py-1 rounded"
              :style="severityButtonStyle(s.key)"
              @click="setSeverity(s.key)"
            >{{ s.label }}</button>
          </div>

          <!-- Kategorie, Facility, Geraeteart -->
          <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            <div class="flex flex-col gap-1">
              <label class="text-xs font-medium" :style="{ color: 'var(--color-text-muted)' }">Kategorie</label>
              <select v-model="filters.category" class="rounded px-3 py-2 text-sm" :style="inputStyle" @change="applyFilters">
                <option value="">Alle Kategorien</option>
                <option v-for="c in filterOptions.categories" :key="c.key" :value="c.key">{{ c.label }}</option>
              </select>
            </div>

            <div class="flex flex-col gap-1">
              <label class="text-xs font-medium" :style="{ color: 'var(--color-text-muted)' }">Syslog-Facility</label>
              <select v-model="filters.facility" class="rounded px-3 py-2 text-sm" :style="inputStyle" @change="applyFilters">
                <option value="">Alle Facilities</option>
                <option v-for="f in filterOptions.facilities" :key="f.value" :value="String(f.value)">{{ f.label }}</option>
              </select>
            </div>

            <div v-if="!lockedHostname" class="flex flex-col gap-1">
              <label class="text-xs font-medium" :style="{ color: 'var(--color-text-muted)' }">Geräteart</label>
              <select v-model="filters.device_type" class="rounded px-3 py-2 text-sm" :style="inputStyle" @change="applyFilters">
                <option value="">Alle Gerätearten</option>
                <option v-for="d in filterOptions.device_types" :key="d" :value="d">{{ deviceTypeLabel(d) }}</option>
              </select>
            </div>
          </div>
        </div>

        <!-- Zeile 1: Host, Source, Level, Suche -->
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <!-- Hostname (entfaellt, wenn die Ansicht auf ein Geraet fixiert ist) -->
          <div v-if="!lockedHostname" class="flex flex-col gap-1">
            <label class="text-xs font-medium" :style="{ color: 'var(--color-text-muted)' }">Host</label>
            <select v-model="filters.hostname" class="rounded px-3 py-2 text-sm" :style="inputStyle" @change="applyFilters">
              <option value="">Alle Hosts</option>
              <option v-for="h in filterOptions.hostnames" :key="h" :value="h">{{ h }}</option>
            </select>
          </div>

          <!-- Source -->
          <div class="flex flex-col gap-1">
            <label class="text-xs font-medium" :style="{ color: 'var(--color-text-muted)' }">Source / Dienst</label>
            <input
              v-model="filters.source"
              type="text"
              list="source-options"
              placeholder="z.B. sshd, kernel…"
              class="rounded px-3 py-2 text-sm"
              :style="inputStyle"
              @keyup.enter="applyFilters"
            >
            <datalist id="source-options">
              <option v-for="s in filterOptions.sources" :key="s" :value="s" />
            </datalist>
          </div>

          <!-- Level -->
          <div class="flex flex-col gap-1">
            <label class="text-xs font-medium" :style="{ color: 'var(--color-text-muted)' }">Level</label>
            <select v-model="filters.level" class="rounded px-3 py-2 text-sm" :style="inputStyle" @change="applyFilters">
              <option value="">Alle Level</option>
              <option v-for="l in filterOptions.levels" :key="l" :value="l">{{ l }}</option>
            </select>
          </div>

          <!-- Volltextsuche -->
          <div class="flex flex-col gap-1">
            <label class="text-xs font-medium" :style="{ color: 'var(--color-text-muted)' }">Nachricht enthält</label>
            <input
              v-model="filters.search"
              type="text"
              placeholder="Suchbegriff…"
              class="rounded px-3 py-2 text-sm"
              :style="inputStyle"
              @keyup.enter="applyFilters"
            >
          </div>
        </div>

        <!-- Zeile 2: Zeitraum -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div class="flex flex-col gap-1">
            <label class="text-xs font-medium" :style="{ color: 'var(--color-text-muted)' }">Von (Datum & Uhrzeit)</label>
            <input
              v-model="filters.start_date"
              type="datetime-local"
              class="rounded px-3 py-2 text-sm"
              :style="inputStyle"
            >
          </div>
          <div class="flex flex-col gap-1">
            <label class="text-xs font-medium" :style="{ color: 'var(--color-text-muted)' }">Bis (Datum & Uhrzeit)</label>
            <input
              v-model="filters.end_date"
              type="datetime-local"
              class="rounded px-3 py-2 text-sm"
              :style="inputStyle"
            >
          </div>
        </div>

        <!-- Schnellauswahl Zeitraum -->
        <div class="flex flex-wrap gap-2">
          <span class="text-xs" :style="{ color: 'var(--color-text-muted)' }">Schnell:</span>
          <button
            v-for="q in quickRanges" :key="q.label"
            @click="applyQuickRange(q)"
            class="text-xs px-2 py-1 rounded"
            :style="buttonSecondaryStyle"
          >{{ q.label }}</button>
          <button
            v-if="filters.start_date || filters.end_date"
            @click="filters.start_date = ''; filters.end_date = ''"
            class="text-xs px-2 py-1 rounded"
            :style="{ ...buttonSecondaryStyle, color: 'var(--color-danger)' }"
          >Zeitraum löschen</button>
        </div>

        <!-- Aktions-Buttons -->
        <div class="flex gap-2 justify-end pt-1 border-t" :style="{ borderColor: 'var(--color-border)' }">
          <button @click="resetFilters" class="text-sm px-4 py-2 rounded" :style="buttonSecondaryStyle">
            Zurücksetzen
          </button>
          <button
            @click="applyFilters"
            class="text-sm px-4 py-2 rounded text-white"
            :style="{ backgroundColor: 'var(--color-primary)' }"
          >
            Filtern
          </button>
        </div>
      </div>
    </div>

    <!-- Aktive Filter als Chips -->
    <div v-if="activeFilterChips.length" class="flex flex-wrap gap-2 mb-4">
      <span
        v-for="chip in activeFilterChips"
        :key="chip.key"
        class="inline-flex items-center gap-1 px-3 py-1 rounded-full text-sm"
        :style="chipStyle"
      >
        <span class="font-medium">{{ chip.label }}:</span>
        <span>{{ chip.value }}</span>
        <button @click="clearFilter(chip.key)" class="ml-1 leading-none hover:opacity-70 font-bold">✕</button>
      </span>
    </div>

    <!-- Fehler -->
    <div v-if="error" class="rounded px-4 py-3 mb-4 text-sm" :style="errorStyle">{{ error }}</div>

    <!-- ================================================================
         TABELLE
         ================================================================ -->
    <div class="rounded-lg shadow" :style="cardStyle">
      <!-- Tabellen-Header -->
      <div class="px-4 py-3 border-b flex flex-wrap items-center justify-between gap-2" :style="{ borderColor: 'var(--color-border)' }">
        <span class="text-sm" :style="{ color: 'var(--color-text-secondary)' }">
          <template v-if="loading">Lade…</template>
          <template v-else>{{ total.toLocaleString('de-DE') }} Einträge</template>
        </span>
        <div class="flex items-center gap-4">
          <!-- Lesbar oder roh.

               Die Rohzeile ist das, was das Gerät geschickt hat - bei einer
               Firewall dreißig key=value-Paare, bei einem Switch eine
               Zahlenwurst. Für die Beweisführung unverzichtbar, zum
               Überfliegen unbrauchbar. Deshalb der Schalter: „Lesbar" zeigt
               den Satz, den der Parser daraus macht, und die wichtigsten
               Angaben als Abzeichen. Die Rohzeile bleibt eine Zeile
               darunter - wenn die Erkennung danebenliegt, sieht man es
               sofort. -->
          <label
            class="flex items-center gap-2 text-xs cursor-pointer select-none"
            :style="{ color: 'var(--color-text-secondary)' }"
            :title="readableHint"
          >
            <input type="checkbox" v-model="readable" :disabled="pageSize > 300">
            Lesbar
          </label>

          <div class="flex items-center gap-2">
            <label class="text-xs" :style="{ color: 'var(--color-text-muted)' }">Zeilen:</label>
            <select v-model.number="pageSize" class="rounded px-2 py-1 text-sm" :style="inputStyle" @change="page = 1; loadLogs()">
              <option :value="50">50</option>
              <option :value="100">100</option>
              <option :value="250">250</option>
              <option :value="500">500</option>
            </select>
          </div>
        </div>
      </div>
      <p v-if="readable && pageSize > 300" class="px-4 pb-2 text-xs" :style="{ color: 'var(--color-text-muted)' }">
        Die lesbare Darstellung gibt es bis 300 Zeilen pro Seite — darüber kostet das
        Zerlegen mehr, als es beim Überfliegen bringt.
      </p>

      <div class="overflow-x-auto">
        <table class="w-full">
          <thead :style="{ backgroundColor: 'var(--color-surface-elevated)' }">
            <tr>
              <th class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide" :style="{ color: 'var(--color-text-muted)' }">Zeit</th>
              <th v-if="!lockedHostname" class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide" :style="{ color: 'var(--color-text-muted)' }">Host</th>
              <th class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide" :style="{ color: 'var(--color-text-muted)' }">Level</th>
              <th class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide" :style="{ color: 'var(--color-text-muted)' }">Source</th>
              <th class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide" :style="{ color: 'var(--color-text-muted)' }">Nachricht</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="log in logs"
              :key="log.id"
              class="border-t hover-row cursor-pointer"
              :style="{ borderColor: 'var(--color-border)' }"
              @click="showDetail(log)"
            >
              <td class="px-4 py-2.5 text-xs whitespace-nowrap font-mono" :style="{ color: 'var(--color-text-muted)' }">
                {{ formatTime(log.timestamp) }}
              </td>
              <td v-if="!lockedHostname" class="px-4 py-2.5 text-sm font-medium" :style="{ color: 'var(--color-text-primary)' }">
                <router-link
                  v-if="log.hostname"
                  :to="{ name: 'DeviceLogs', params: { hostname: log.hostname } }"
                  class="hover:underline"
                  :style="{ color: 'var(--color-primary)' }"
                  title="Geräte-Ansicht öffnen"
                  @click.stop
                >{{ log.hostname }}</router-link>
                <span v-else>–</span>
              </td>
              <td class="px-4 py-2.5">
                <span :class="levelBadge(log.level)">{{ log.level || '–' }}</span>
              </td>
              <td class="px-4 py-2.5 text-xs" :style="{ color: 'var(--color-text-secondary)' }">
                {{ log.source || '–' }}
              </td>
              <td class="px-4 py-2.5 text-sm" :style="{ color: 'var(--color-text-secondary)', maxWidth: '40vw' }">
                <!-- Zusammengefasste Wiederholung: dieselbe Meldung N-mal
                     innerhalb einer Minute, gespeichert als eine Zeile. -->
                <span
                  v-if="log.repeat > 1"
                  class="chip float-right ml-2"
                  :style="chipStyleFor('warning')"
                  :title="`Diese Meldung kam ${log.repeat.toLocaleString('de-DE')}-mal kurz hintereinander und wurde zu einer Zeile zusammengefasst.`"
                >×{{ log.repeat.toLocaleString('de-DE') }}</span>
                <template v-if="readable && log.parsed">
                  <span class="block truncate" :style="{ color: 'var(--color-text-primary)' }">
                    {{ log.parsed.summary || log.message }}
                  </span>
                  <!-- Die paar Angaben, die fast immer interessieren: Quelle,
                       Ziel, Benutzer, Ergebnis. Als Abzeichen, damit man sie
                       nicht aus dem Satz herauslesen muss. -->
                  <span v-if="log.parsed.highlights?.length" class="flex flex-wrap gap-1 mt-1">
                    <span
                      v-for="badge in log.parsed.highlights"
                      :key="badge.key + badge.value"
                      class="chip"
                      :style="chipStyleFor(badge.kind)"
                    >
                      <span class="opacity-70">{{ badge.label }}</span>
                      <span class="font-medium">{{ badge.value }}</span>
                    </span>
                  </span>
                  <!-- Die Rohzeile bleibt sichtbar - gekuerzt, aber da. -->
                  <span
                    v-if="differsFromRaw(log)"
                    class="block truncate text-xs mt-0.5 font-mono"
                    :style="{ color: 'var(--color-text-muted)' }"
                  >{{ log.raw_message }}</span>
                </template>
                <span v-else class="block truncate">{{ log.message }}</span>
              </td>
            </tr>

            <tr v-if="loading">
              <td :colspan="columnCount" class="px-4 py-10 text-center text-sm" :style="{ color: 'var(--color-text-muted)' }">
                <span class="animate-pulse">Lade Logs…</span>
              </td>
            </tr>
            <tr v-else-if="!logs.length">
              <td :colspan="columnCount" class="px-4 py-10 text-center text-sm" :style="{ color: 'var(--color-text-muted)' }">
                Keine Logs gefunden – Filter anpassen?
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Pagination -->
      <div class="px-4 py-3 border-t flex flex-wrap items-center justify-between gap-2" :style="{ borderColor: 'var(--color-border)' }">
        <span class="text-sm" :style="{ color: 'var(--color-text-secondary)' }">
          Seite {{ page }} von {{ Math.max(1, Math.ceil(total / pageSize)) }}
        </span>
        <div class="flex gap-1">
          <button @click="goToPage(1)" :disabled="page <= 1" class="px-2 py-1 rounded text-sm disabled:opacity-40" :style="buttonSecondaryStyle">«</button>
          <button @click="goToPage(page - 1)" :disabled="page <= 1" class="px-3 py-1 rounded text-sm disabled:opacity-40" :style="buttonSecondaryStyle">‹ Zurück</button>
          <button @click="goToPage(page + 1)" :disabled="page * pageSize >= total" class="px-3 py-1 rounded text-sm disabled:opacity-40" :style="buttonSecondaryStyle">Weiter ›</button>
          <button @click="goToPage(Math.ceil(total / pageSize))" :disabled="page * pageSize >= total" class="px-2 py-1 rounded text-sm disabled:opacity-40" :style="buttonSecondaryStyle">»</button>
        </div>
      </div>
    </div>

    <!-- ================================================================
         DETAIL-MODAL
         ================================================================ -->
    <div
      v-if="selectedLog"
      class="fixed inset-0 flex items-center justify-center p-4 z-50"
      style="background: rgba(0,0,0,0.6)"
      @click.self="selectedLog = null"
    >
      <div class="rounded-lg shadow-xl w-full max-w-2xl max-h-[85vh] overflow-auto" :style="cardStyle">
        <div class="sticky top-0 px-5 py-4 border-b flex items-center justify-between" :style="{ backgroundColor: 'var(--color-surface)', borderColor: 'var(--color-border)' }">
          <div class="flex items-center gap-3">
            <span :class="levelBadge(selectedLog.level)">{{ selectedLog.level }}</span>
            <span class="font-semibold" :style="{ color: 'var(--color-text-primary)' }">{{ selectedLog.source }}</span>
            <span class="text-sm" :style="{ color: 'var(--color-text-muted)' }">@ {{ selectedLog.hostname }}</span>
          </div>
          <button @click="selectedLog = null" class="text-xl leading-none hover:opacity-60" :style="{ color: 'var(--color-text-muted)' }">✕</button>
        </div>

        <div class="p-5 space-y-4">
          <!-- Meta-Grid -->
          <div class="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
            <div>
              <p class="text-xs mb-0.5" :style="{ color: 'var(--color-text-muted)' }">Zeitstempel</p>
              <p class="font-mono" :style="{ color: 'var(--color-text-primary)' }">{{ formatTimeFull(selectedLog.timestamp) }}</p>
            </div>
            <div>
              <p class="text-xs mb-0.5" :style="{ color: 'var(--color-text-muted)' }">IP-Adresse</p>
              <p class="font-mono" :style="{ color: 'var(--color-text-primary)' }">{{ selectedLog.ip_address || '–' }}</p>
            </div>
            <div>
              <p class="text-xs mb-0.5" :style="{ color: 'var(--color-text-muted)' }">Facility</p>
              <p :style="{ color: 'var(--color-text-primary)' }">{{ facilityName(selectedLog.facility) }}</p>
            </div>
            <div>
              <p class="text-xs mb-0.5" :style="{ color: 'var(--color-text-muted)' }">Log-ID</p>
              <p class="font-mono" :style="{ color: 'var(--color-text-primary)' }">#{{ selectedLog.id }}</p>
            </div>
          </div>

          <!-- Lesbare Fassung: Zusammenfassung, Abzeichen, erkannte Felder.

               Erst das, was ein Mensch braucht. Die Rohzeile steht darunter,
               unveraendert - der Parser aendert nie Daten, er stellt nur
               anders dar. -->
          <div v-if="parsed?.readable">
            <p class="text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">
              Lesbar ({{ parsed.format }})
            </p>
            <p class="text-sm mb-2" :style="{ color: 'var(--color-text-primary)' }">{{ parsed.summary }}</p>

            <div v-if="parsed.highlights?.length" class="flex flex-wrap gap-1 mb-2">
              <span
                v-for="badge in parsed.highlights"
                :key="badge.key + badge.value"
                class="chip"
                :style="chipStyleFor(badge.kind)"
              >
                <span class="opacity-70">{{ badge.label }}</span>
                <span class="font-medium">{{ badge.value }}</span>
              </span>
            </div>

            <div v-if="Object.keys(parsed.fields || {}).length" class="rounded overflow-hidden" :style="codeBlockStyle">
              <div
                v-for="(value, key) in parsed.fields"
                :key="key"
                class="flex gap-3 px-3 py-1.5 text-xs border-b last:border-b-0"
                :style="{ borderColor: 'var(--color-border)' }"
              >
                <span class="w-40 shrink-0" :style="{ color: 'var(--color-text-muted)' }">{{ key }}</span>
                <span class="min-w-0 break-all font-mono" :style="{ color: 'var(--color-text-primary)' }">{{ value }}</span>
              </div>
            </div>
          </div>

          <!-- Nachricht -->
          <div>
            <p class="text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Nachricht</p>
            <pre class="p-3 rounded text-sm whitespace-pre-wrap break-all font-mono leading-relaxed" :style="codeBlockStyle">{{ selectedLog.message }}</pre>
          </div>

          <!-- Raw Message (nur wenn unterschiedlich) -->
          <div v-if="selectedLog.raw_message && selectedLog.raw_message !== selectedLog.message">
            <p class="text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Raw (original)</p>
            <pre class="p-3 rounded text-xs whitespace-pre-wrap break-all font-mono leading-relaxed overflow-x-auto" :style="codeBlockStyle">{{ selectedLog.raw_message }}</pre>
          </div>

          <!-- Extra-Daten -->
          <div v-if="selectedLog.extra_data && Object.keys(selectedLog.extra_data).length">
            <p class="text-xs mb-1" :style="{ color: 'var(--color-text-muted)' }">Zusatzdaten</p>
            <pre class="p-3 rounded text-xs font-mono whitespace-pre-wrap" :style="codeBlockStyle">{{ JSON.stringify(selectedLog.extra_data, null, 2) }}</pre>
          </div>

          <!-- Schnell-Filter Buttons -->
          <div class="flex flex-wrap gap-2 pt-2 border-t" :style="{ borderColor: 'var(--color-border)' }">
            <router-link
              v-if="selectedLog.hostname"
              :to="{ name: 'DeviceLogs', params: { hostname: selectedLog.hostname } }"
              class="text-xs px-3 py-1 rounded"
              :style="buttonSecondaryStyle"
            >
              Geräte-Ansicht: {{ selectedLog.hostname }}
            </router-link>
            <button v-if="!lockedHostname && selectedLog.hostname" @click="filterByHost(selectedLog.hostname)" class="text-xs px-3 py-1 rounded" :style="buttonSecondaryStyle">
              Nur Host: {{ selectedLog.hostname }}
            </button>
            <button v-if="selectedLog.source" @click="filterBySource(selectedLog.source)" class="text-xs px-3 py-1 rounded" :style="buttonSecondaryStyle">
              Nur Source: {{ selectedLog.source }}
            </button>
            <button v-if="selectedLog.level" @click="filterByLevel(selectedLog.level)" class="text-xs px-3 py-1 rounded" :style="buttonSecondaryStyle">
              Nur Level: {{ selectedLog.level }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const props = defineProps({
  // Fixiert die Liste auf genau ein Geraet (exakter Hostname-Vergleich).
  // Leer = alle Hosts, Host-Filter und Host-Spalte werden dann angezeigt.
  lockedHostname: { type: String, default: '' },
  // Filter zusaetzlich in der URL spiegeln (teilbare Links, Reload-fest).
  syncUrl: { type: Boolean, default: false },
})

const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()

const logs = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(100)
const loading = ref(false)
const exporting = ref(false)
const selectedLog = ref(null)
const parsed = ref(null)
const error = ref('')
const filterOpen = ref(true)

// Lesbare Darstellung. Die Wahl bleibt gemerkt - wer einmal entschieden hat,
// wie er Logs liest, will das nicht bei jedem Seitenaufruf neu einstellen.
const READABLE_KEY = 'rimperio.logs.readable'

function readStoredReadable() {
  try {
    // Vorgabe: an. Die Rohzeile ist weiter da, nur eine Zeile tiefer.
    return localStorage.getItem(READABLE_KEY) !== '0'
  } catch {
    return true
  }
}

const readable = ref(readStoredReadable())

const readableHint = computed(() => (
  readable.value
    ? 'Zeigt den Satz, den RIMPERIO aus der Rohzeile macht — die Rohzeile bleibt darunter stehen.'
    : 'Zeigt die Meldung so, wie sie in der Datenbank steht.'
))

const EMPTY_FILTERS = {
  hostname: '',
  source: '',
  level: '',
  search: '',
  start_date: '',
  end_date: '',
  // Logtypen
  min_severity: '',
  category: '',
  facility: '',
  device_type: '',
}

const filters = ref({ ...EMPTY_FILTERS })

const filterOptions = ref({
  hostnames: [],
  sources: [],
  levels: [],
  device_types: [],
  severities: [],
  categories: [],
  facilities: [],
})

// Schnell-Zeitraum-Auswahl
const quickRanges = [
  { label: 'Letzte 15 Min', minutes: 15 },
  { label: 'Letzte Stunde', minutes: 60 },
  { label: 'Letzte 6 h', minutes: 360 },
  { label: 'Letzte 24 h', minutes: 1440 },
  { label: 'Letzte 7 Tage', minutes: 10080 },
]

// ── Computed ─────────────────────────────────────────────────────────────────

const columnCount = computed(() => (props.lockedHostname ? 4 : 5))

const cardStyle = computed(() => ({
  backgroundColor: 'var(--color-surface)',
  borderColor: 'var(--color-border)',
}))
const inputStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  border: '1px solid var(--color-border)',
  color: 'var(--color-text-primary)',
}))
const buttonSecondaryStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  border: '1px solid var(--color-border)',
  color: 'var(--color-text-primary)',
}))
const codeBlockStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  color: 'var(--color-text-primary)',
}))
const chipStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  border: '1px solid var(--color-primary)',
  color: 'var(--color-text-primary)',
}))
const errorStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  border: '1px solid var(--color-danger)',
  color: 'var(--color-danger)',
}))

const activeFilterChips = computed(() => {
  const chips = []
  const f = filters.value
  if (f.hostname && !props.lockedHostname) chips.push({ key: 'hostname', label: 'Host', value: f.hostname })
  if (f.min_severity) chips.push({ key: 'min_severity', label: 'Schweregrad', value: severityLabel(f.min_severity) })
  if (f.category) chips.push({ key: 'category', label: 'Kategorie', value: categoryLabel(f.category) })
  if (f.facility !== '') chips.push({ key: 'facility', label: 'Facility', value: facilityLabel(f.facility) })
  if (f.device_type) chips.push({ key: 'device_type', label: 'Geräteart', value: deviceTypeLabel(f.device_type) })
  if (f.source)   chips.push({ key: 'source',   label: 'Source', value: f.source })
  if (f.level)    chips.push({ key: 'level',     label: 'Level', value: f.level })
  if (f.search)   chips.push({ key: 'search',    label: 'Suche', value: f.search })
  if (f.start_date) chips.push({ key: 'start_date', label: 'Von', value: f.start_date.replace('T', ' ') })
  if (f.end_date)   chips.push({ key: 'end_date',   label: 'Bis', value: f.end_date.replace('T', ' ') })
  return chips
})

const activeFilterCount = computed(() => activeFilterChips.value.length)

// ── Lifecycle ────────────────────────────────────────────────────────────────

onMounted(() => {
  applyQueryToFilters()
  loadFilterOptions()
  loadLogs()
})

// Geraetewechsel (z.B. /devices/a -> /devices/b) ohne Neuaufbau der Komponente
watch(() => props.lockedHostname, () => {
  page.value = 1
  loadLogs()
})

// ── Methoden ─────────────────────────────────────────────────────────────────

function applyQueryToFilters() {
  const q = route.query
  for (const key of Object.keys(filters.value)) {
    if (typeof q[key] === 'string') filters.value[key] = q[key]
  }
  // Bei fixiertem Geraet steuert die Route den Host, nicht der Filter.
  if (props.lockedHostname) filters.value.hostname = ''
}

function syncFiltersToUrl() {
  if (!props.syncUrl) return
  const query = {}
  for (const [key, value] of Object.entries(filters.value)) {
    if (value) query[key] = value
  }
  router.replace({ query })
}

async function loadFilterOptions() {
  try {
    const data = await authStore.api('/api/logs/filter-options')
    // Merge mit den Defaults: ein aelteres Backend liefert die Logtyp-Listen noch nicht.
    filterOptions.value = { ...filterOptions.value, ...data }
  } catch {}
}

function applyFilters() {
  page.value = 1
  syncFiltersToUrl()
  loadLogs()
}

function resetFilters() {
  filters.value = { ...EMPTY_FILTERS }
  applyFilters()
}

function setSeverity(key) {
  filters.value.min_severity = key
  applyFilters()
}

function clearFilter(key) {
  filters.value[key] = ''
  applyFilters()
}

function goToPage(n) {
  page.value = n
  loadLogs()
}

function applyQuickRange(q) {
  const now = new Date()
  const from = new Date(now.getTime() - q.minutes * 60_000)
  filters.value.start_date = toLocalDatetimeInput(from)
  filters.value.end_date   = toLocalDatetimeInput(now)
  applyFilters()
}

function toLocalDatetimeInput(d) {
  // Gibt "YYYY-MM-DDTHH:MM" in Lokalzeit zurück (für datetime-local input)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** Baut die Filter-Parameter für Liste und Export. */
function buildFilterParams() {
  const p = new URLSearchParams()
  const f = filters.value
  if (props.lockedHostname) {
    p.append('hostname', props.lockedHostname)
    p.append('hostname_exact', 'true')
  } else if (f.hostname) {
    p.append('hostname', f.hostname)
  }
  if (f.source)     p.append('source', f.source)
  if (f.level)      p.append('level', f.level)
  if (f.search)     p.append('search', f.search)
  if (f.start_date) p.append('start_date', new Date(f.start_date).toISOString())
  if (f.end_date)   p.append('end_date', new Date(f.end_date).toISOString())
  // Logtypen
  if (f.min_severity) p.append('min_severity', f.min_severity)
  if (f.category)     p.append('category', f.category)
  if (f.facility !== '') p.append('facility', f.facility)
  if (f.device_type)  p.append('device_type', f.device_type)
  return p
}

async function loadLogs() {
  loading.value = true
  error.value = ''
  try {
    const p = buildFilterParams()
    p.append('page', page.value)
    p.append('page_size', pageSize.value)
    // Nur anfordern, wenn es auch etwas bringt: ueber 300 Zeilen liefert der
    // Server die lesbare Fassung ohnehin nicht mehr mit.
    if (readable.value && pageSize.value <= 300) p.append('readable', 'true')

    const data = await authStore.api(`/api/logs?${p}`)
    logs.value  = data.items
    total.value = data.total
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

// Umschalten wirkt sofort und bleibt gemerkt.
watch(readable, (value) => {
  try {
    localStorage.setItem(READABLE_KEY, value ? '1' : '0')
  } catch {
    // Privater Modus: die Wahl gilt dann nur fuer diese Sitzung.
  }
  loadLogs()
})

/** Lohnt es sich, die Rohzeile zusaetzlich zu zeigen? */
function differsFromRaw(log) {
  if (!log.raw_message) return false
  const summary = (log.parsed?.summary || '').trim()
  const raw = log.raw_message.trim()
  return raw.length > 0 && raw !== summary && raw !== (log.message || '').trim()
}

/** Faerbt ein Abzeichen nach Art des Wertes - IP anders als Aktion. */
function chipStyleFor(kind) {
  const map = {
    ip: 'var(--color-primary)',
    mac: 'var(--color-secondary)',
    state: 'var(--color-accent)',
    number: 'var(--color-text-secondary)',
    url: 'var(--color-primary)',
    warning: 'var(--color-warning)',
  }
  const color = map[kind] || 'var(--color-text-secondary)'
  return {
    color,
    borderColor: color,
    backgroundColor: 'var(--color-surface-elevated)',
  }
}

async function showDetail(log) {
  parsed.value = null
  try {
    // Die zerlegte Fassung bringt die Rohzeile unveraendert mit - ein Aufruf
    // statt zwei.
    const data = await authStore.api(`/api/logs/${log.id}/parsed`)
    selectedLog.value = data
    parsed.value = data.parsed
  } catch {
    // Faellt der Parser aus, bleibt die Zeile trotzdem ansehbar.
    try {
      selectedLog.value = await authStore.api(`/api/logs/${log.id}`)
    } catch {}
  }
}

function filterByHost(hostname) {
  selectedLog.value = null
  filters.value.hostname = hostname
  applyFilters()
}
function filterBySource(source) {
  selectedLog.value = null
  filters.value.source = source
  applyFilters()
}
function filterByLevel(level) {
  selectedLog.value = null
  filters.value.level = level
  applyFilters()
}

/**
 * Export laeuft ueber fetch + Blob, weil der Endpoint den Bearer-Token
 * braucht (ein window.open kann keinen Auth-Header setzen).
 */
async function exportLogs(format) {
  exporting.value = true
  error.value = ''
  try {
    const p = buildFilterParams()
    p.append('format', format)

    const res = await fetch(`/api/logs/export?${p}`, {
      headers: authStore.token ? { Authorization: `Bearer ${authStore.token}` } : {},
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)

    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')
    const namePart = props.lockedHostname ? `-${props.lockedHostname}` : ''

    const link = document.createElement('a')
    link.href = url
    link.download = `rimperio-logs${namePart}-${stamp}.${format}`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch (e) {
    error.value = `Export fehlgeschlagen: ${e.message}`
  } finally {
    exporting.value = false
  }
}

// ── Formatierung ─────────────────────────────────────────────────────────────

function formatTime(ts) {
  if (!ts) return '–'
  return new Date(ts).toLocaleString('de-DE', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function formatTimeFull(ts) {
  if (!ts) return '–'
  return new Date(ts).toLocaleString('de-DE', {
    weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

const FACILITY_NAMES = {
  0: 'kern', 1: 'user', 2: 'mail', 3: 'daemon', 4: 'auth', 5: 'syslog',
  6: 'lpr', 7: 'news', 8: 'uucp', 9: 'cron', 10: 'authpriv', 11: 'ftp',
  16: 'local0', 17: 'local1', 18: 'local2', 19: 'local3',
  20: 'local4', 21: 'local5', 22: 'local6', 23: 'local7',
}
function facilityName(f) {
  if (f == null) return '–'
  return FACILITY_NAMES[f] ? `${FACILITY_NAMES[f]} (${f})` : String(f)
}

// ── Logtypen: Beschriftungen ─────────────────────────────────────────────────

function severityLabel(key) {
  return filterOptions.value.severities.find(s => s.key === key)?.label || key
}

function categoryLabel(key) {
  return filterOptions.value.categories.find(c => c.key === key)?.label || key
}

function facilityLabel(value) {
  return filterOptions.value.facilities.find(f => String(f.value) === String(value))?.label || value
}

const DEVICE_TYPE_LABELS = {
  syslog: 'Syslog',
  windows_agent: 'Windows-Agent',
  linux_agent: 'Linux-Agent',
  unifi_ap: 'UniFi AP',
  fritzbox: 'FRITZ!Box',
  linux: 'Linux',
  windows: 'Windows',
  unknown: 'Unbekannt',
}
function deviceTypeLabel(t) {
  return DEVICE_TYPE_LABELS[t] || t || 'Unbekannt'
}

/** Aktiver Schweregrad-Knopf wird farbig hervorgehoben. */
function severityButtonStyle(key) {
  if (filters.value.min_severity === key) {
    return {
      backgroundColor: 'var(--color-primary)',
      border: '1px solid var(--color-primary)',
      color: '#fff',
    }
  }
  return buttonSecondaryStyle.value
}

// Farben kommen aus dem Design-System (main.css), damit sie sich mit dem
// Branding aendern und in beiden Themes lesbar bleiben.
function levelBadge(level) {
  const key = (level || '').toLowerCase()
  if (['emergency', 'emerg', 'panic', 'alert', 'critical', 'crit', 'error', 'err'].includes(key)) {
    return 'badge badge-danger'
  }
  if (['warning', 'warn'].includes(key)) return 'badge badge-warning'
  if (key === 'notice') return 'badge badge-success'
  if (['info', 'information', 'informational'].includes(key)) return 'badge badge-primary'
  return 'badge badge-neutral'
}
</script>

<style scoped>
.hover-row:hover {
  background-color: var(--color-surface-elevated);
}

/* Abzeichen fuer die wichtigsten erkannten Angaben (Quell-IP, Aktion, …).
   Bewusst klein und randbetont statt flaechig: in einer Liste mit hundert
   Zeilen soll das Auge die Meldung lesen und die Abzeichen nur streifen. */
.chip {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
  border: 1px solid;
  font-size: 0.65rem;
  line-height: 1.4;
  white-space: nowrap;
}
</style>
