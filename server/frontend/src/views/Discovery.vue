<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.10.08.22.00.00
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Systeme im Netz. Zeigt, was die Netzwerksuche
                   gefunden hat, ob es schon Logs liefert und wie man es
                   einbindet.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">Systeme im Netz</h2>
        <p class="page-subtitle">
          {{ visibleCount }} Systeme · {{ counts.delivering }} liefern Logs ·
          {{ counts.open }} noch nicht eingebunden
        </p>
      </div>
      <div class="flex items-center gap-2">
        <button class="btn btn-secondary btn-sm" @click="openSettings">
          <AppIcon name="settings" :size="16" />
          Einstellungen
        </button>
        <button class="btn btn-primary btn-sm" :disabled="scan.running" @click="startScan">
          <AppIcon name="search" :size="16" />
          {{ scan.running ? 'Suche läuft…' : 'Jetzt suchen' }}
        </button>
      </div>
    </div>

    <!-- ================================================================
         LAUFENDE SUCHE / LETZTER LAUF
         ================================================================ -->
    <div v-if="scan.running" class="card mb-4">
      <div class="card-body space-y-2">
        <div class="flex items-center justify-between text-sm">
          <span class="font-medium">{{ scan.phase || 'Startet…' }}</span>
          <span class="tabular" style="color: var(--color-text-muted)">
            <template v-if="scan.total">{{ scan.done }} / {{ scan.total }} · </template>{{ scan.found }} gefunden
          </span>
        </div>
        <div class="progress"><div class="progress-bar" :style="{ width: progress + '%' }" /></div>
      </div>
    </div>

    <div v-else-if="lastRun.finished_at || lastRun.error" class="card mb-4">
      <div class="card-body text-sm space-y-2">
        <p v-if="lastRun.error" style="color: var(--color-danger)">Letzte Suche fehlgeschlagen: {{ lastRun.error }}</p>
        <p v-else style="color: var(--color-text-secondary)">
          Letzte Suche {{ formatTime(lastRun.finished_at) }}
          {{ lastRun.trigger === 'auto' ? '(automatisch)' : '(von Hand)' }} ·
          {{ lastRun.addresses?.toLocaleString('de-DE') }} Adressen in {{ lastRun.duration_s }} s ·
          {{ lastRun.alive }} Systeme, davon {{ lastRun.new }} neu
        </p>
        <p v-if="routerSummary" style="color: var(--color-text-muted)">{{ routerSummary }}</p>
        <ul v-if="lastRun.notes?.length" class="notes">
          <li v-for="(note, i) in lastRun.notes" :key="i">
            <AppIcon name="warning" :size="14" /> <span>{{ note }}</span>
          </li>
        </ul>
      </div>
    </div>

    <!-- ================================================================
         FILTER
         ================================================================ -->
    <div class="card mb-4">
      <div class="card-body flex flex-col lg:flex-row gap-3">
        <div class="flex flex-wrap gap-2">
          <button
            v-for="f in FILTERS"
            :key="f.key"
            class="btn btn-sm"
            :class="filter === f.key ? 'btn-primary' : 'btn-secondary'"
            @click="filter = f.key"
          >{{ f.label }} <span class="tabular opacity-70">{{ counts[f.key] }}</span></button>
        </div>
        <div class="search-wrap flex-1">
          <AppIcon name="search" :size="16" class="search-icon" />
          <input v-model="search" type="text" placeholder="Name, IP, MAC, Art…" class="input pl-9">
        </div>
      </div>
    </div>

    <!-- ================================================================
         LISTE
         ================================================================ -->
    <div class="card">
      <div class="card-body p-0">
        <div v-if="loading && !devices.length" class="p-5 space-y-3">
          <div v-for="n in 5" :key="n" class="skeleton h-6 w-full" />
        </div>

        <div v-else-if="!filtered.length" class="empty-state">
          <AppIcon name="network" :size="30" />
          <p class="empty-state-title">
            {{ devices.length ? 'Für diese Auswahl gibt es keine Systeme.' : 'Noch nichts gefunden.' }}
          </p>
          <p v-if="!devices.length" class="text-sm">
            {{ scan.running ? 'Die Suche läuft gerade.' : 'Mit „Jetzt suchen“ wird das Netz abgesucht.' }}
          </p>
        </div>

        <div v-else class="overflow-x-auto">
          <table class="table table-clickable">
            <thead>
              <tr>
                <th style="width: 10rem">Status</th>
                <th>System</th>
                <th style="width: 11rem">Art</th>
                <th style="width: 9rem">Netz</th>
                <th>Dienste</th>
                <th style="width: 10rem">Zuletzt gesehen</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="d in filtered" :key="d.id" :class="{ 'row-absent': !d.present }" @click="openDevice(d)">
                <td>
                  <span class="badge" :class="STATUS[d.status].badge">{{ STATUS[d.status].label }}</span>
                </td>
                <td class="min-w-0">
                  <div class="font-medium truncate">{{ d.hostname || d.details?.snmp?.sys_name || d.ip_address }}</div>
                  <div class="text-xs font-mono" style="color: var(--color-text-muted)">
                    {{ d.ip_address }}<template v-if="d.mac_address"> · {{ d.mac_address }}</template>
                  </div>
                </td>
                <td>
                  <div>{{ d.kind_label }}</div>
                  <div v-if="d.os_guess" class="text-xs truncate" style="color: var(--color-text-muted)">{{ d.os_guess }}</div>
                </td>
                <td class="text-xs" style="color: var(--color-text-secondary)">
                  {{ d.same_subnet === false ? 'anderes Subnetz' : d.same_subnet ? 'eigenes Subnetz' : '–' }}
                </td>
                <td>
                  <div class="flex flex-wrap gap-1">
                    <span v-for="p in d.open_ports.slice(0, 6)" :key="p.port" class="port">{{ p.name }}</span>
                    <span v-if="d.open_ports.length > 6" class="port">+{{ d.open_ports.length - 6 }}</span>
                  </div>
                </td>
                <td class="text-xs whitespace-nowrap" style="color: var(--color-text-muted)">
                  {{ formatTime(d.last_seen) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- ================================================================
         DETAILS + ANLEITUNG (MODAL)
         ================================================================ -->
    <div v-if="selected" class="modal-backdrop" @click.self="selected = null">
      <div class="modal max-w-2xl">
        <div class="card-header">
          <span class="card-title truncate">{{ selected.hostname || selected.ip_address }}</span>
          <button class="btn-icon" aria-label="Schließen" @click="selected = null">
            <AppIcon name="close" :size="18" />
          </button>
        </div>

        <div class="card-body space-y-5 overflow-y-auto">
          <dl class="facts">
            <div><dt>Status</dt><dd><span class="badge" :class="STATUS[selected.status].badge">{{ STATUS[selected.status].label }}</span></dd></div>
            <div><dt>Art</dt><dd>{{ selected.kind_label }}<template v-if="selected.os_guess"> · {{ selected.os_guess }}</template></dd></div>
            <div><dt>IP</dt><dd class="font-mono">{{ selected.ip_address }}</dd></div>
            <div><dt>MAC</dt><dd class="font-mono">{{ selected.mac_address || '–' }}<template v-if="selected.details?.random_mac"> (zufällig)</template></dd></div>
            <div><dt>Netz</dt><dd>{{ selected.network || '–' }}</dd></div>
            <div><dt>Gefunden über</dt><dd>{{ foundBy(selected) }}</dd></div>
            <div><dt>Erstmals gesehen</dt><dd>{{ formatTime(selected.first_seen) }}</dd></div>
            <div><dt>Zuletzt gesehen</dt><dd>{{ formatTime(selected.last_seen) }}</dd></div>
          </dl>

          <div v-if="selected.open_ports.length">
            <p class="label">Offene Dienste</p>
            <div class="flex flex-wrap gap-1">
              <span v-for="p in selected.open_ports" :key="p.port" class="port">{{ p.name }} <span class="opacity-60">{{ p.port }}</span></span>
            </div>
          </div>

          <div v-if="selected.details?.ssh_banner || selected.details?.snmp" class="space-y-1 text-xs">
            <p v-if="selected.details.ssh_banner"><span class="label inline">SSH:</span> <span class="font-mono">{{ selected.details.ssh_banner }}</span></p>
            <p v-if="selected.details.snmp?.sys_descr"><span class="label inline">SNMP:</span> {{ selected.details.snmp.sys_descr }}</p>
          </div>

          <!-- Abgleich mit den Geräten -->
          <div v-if="selected.agent" class="callout">
            <AppIcon name="agents" :size="16" />
            <div class="text-sm">
              Gehört zum Gerät <router-link class="link" :to="{ name: 'DeviceLogs', params: { hostname: selected.agent.hostname } }">{{ selected.agent.hostname }}</router-link>
              ({{ AGENT_TYPES[selected.agent.device_type] || selected.agent.device_type }}, erkannt an {{ MATCHED_BY[selected.agent.matched_by] }}).
              Letzte Logzeile: {{ formatTime(selected.agent.last_seen) }}.
              <template v-if="selected.status === 'stale'"> Seitdem kommt nichts mehr.</template>
            </div>
          </div>

          <!-- Empfehlung -->
          <section v-if="selected.method !== 'none'" class="space-y-3">
            <p class="section-title">So wird es eingebunden</p>

            <template v-if="selected.method === 'agent_linux'">
              <p class="text-sm">
                Empfohlen: der <strong>HTTPS-Agent</strong> — verschlüsselt und mit eigenem Schlüssel,
                funktioniert auch aus anderen Netzen.
              </p>

              <!-- Automatisch über SSH -->
              <div v-if="!onboard.open && !onboard.state" class="flex flex-wrap gap-2">
                <button class="btn btn-primary btn-sm" @click="beginOnboard">
                  <AppIcon name="terminal" :size="16" /> Automatisch einbinden (SSH)
                </button>
                <button class="btn btn-secondary btn-sm" @click="onboard.manual = !onboard.manual">
                  Einzeiler anzeigen
                </button>
              </div>

              <!-- Schritt 1: Fingerabdruck bestätigen + Zugangsdaten -->
              <div v-if="onboard.open" class="onboard-panel space-y-3">
                <div v-if="onboard.fpLoading" class="text-sm" style="color: var(--color-text-muted)">
                  SSH-Hostschlüssel wird geholt…
                </div>
                <template v-else-if="onboard.fingerprint">
                  <div>
                    <p class="label">SSH-Hostschlüssel von {{ selected.ip_address }}</p>
                    <p class="font-mono text-xs" style="overflow-wrap: anywhere">{{ onboard.fingerprint }}</p>
                    <p class="hint">
                      Nur fortfahren, wenn dieser Fingerabdruck zum Zielsystem passt — er schützt davor,
                      dass sich jemand dazwischenschaltet.
                    </p>
                  </div>
                  <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <label class="label">Benutzer (mit sudo, nicht root)</label>
                      <input v-model="onboard.username" type="text" class="input" autocomplete="off" placeholder="z. B. admin">
                    </div>
                    <div>
                      <label class="label">Passwort</label>
                      <input v-model="onboard.password" type="password" class="input" autocomplete="off">
                    </div>
                  </div>
                  <div>
                    <label class="label">sudo-Passwort (nur falls abweichend)</label>
                    <input v-model="onboard.sudoPassword" type="password" class="input" autocomplete="off" placeholder="leer = wie oben">
                  </div>
                  <p class="hint">
                    Die Zugangsdaten gelten nur für diesen Vorgang und werden danach verworfen —
                    nichts davon landet in der Datenbank oder im Tagebuch.
                  </p>
                </template>
                <p v-else-if="onboard.error" class="text-sm" style="color: var(--color-danger)">{{ onboard.error }}</p>
                <div class="flex gap-2">
                  <button class="btn btn-ghost btn-sm" @click="cancelOnboard">Abbrechen</button>
                  <button class="btn btn-primary btn-sm"
                          :disabled="!onboard.fingerprint || !onboard.username || !onboard.password"
                          @click="runOnboard">Einbinden starten</button>
                </div>
              </div>

              <!-- Schritt 2: Fortschritt -->
              <div v-if="onboard.state" class="onboard-panel space-y-2">
                <ol class="onboard-steps">
                  <li v-for="(s, i) in onboard.state.steps" :key="i" :class="{ bad: !s.ok }">
                    <AppIcon :name="s.ok ? 'check' : 'warning'" :size="14" /> <span>{{ s.text }}</span>
                  </li>
                  <li v-if="onboard.state.running" class="pending">
                    <span class="spinner" /> <span>{{ onboard.state.phase || 'Läuft…' }}</span>
                  </li>
                </ol>
                <p v-if="onboard.state.error" class="text-sm" style="color: var(--color-danger)">{{ onboard.state.error }}</p>
                <div v-if="!onboard.state.running" class="flex gap-2">
                  <button class="btn btn-secondary btn-sm" @click="onboard.state = null">Schließen</button>
                  <button v-if="!onboard.state.ok" class="btn btn-primary btn-sm" @click="beginOnboard">Erneut versuchen</button>
                </div>
              </div>

              <template v-if="onboard.manual && !onboard.open && !onboard.state">
                <p class="text-sm">Oder von Hand auf dem System ausführen:</p>
                <CopyBlock :text="linuxCommand" @copy="copy" />
                <p class="hint">
                  <code>EINLADUNG</code> durch eine Einladung ersetzen: Verwaltung → Zugang &amp; Sicherheit →
                  <router-link class="link" to="/settings/agents">Agent-Token</router-link>, Art „Einladung“.
                </p>
              </template>
            </template>

            <template v-else-if="selected.method === 'agent_windows'">
              <p class="text-sm">
                Empfohlen: der <strong>HTTPS-Agent für Windows</strong>. In einer PowerShell
                <strong>als Administrator</strong> auf dem Rechner ausführen:
              </p>
              <CopyBlock :text="windowsCommand" @copy="copy" />
              <p class="hint">
                <code>EINLADUNG</code> durch eine Einladung ersetzen (Agent-Token, Art „Einladung“).
                Automatisches Einbinden aus der Ferne ist für Windows vorerst nicht vorgesehen.
              </p>
            </template>

            <template v-else-if="selected.method === 'syslog'">
              <p class="text-sm">
                Auf {{ articleFor(selected.kind) }} läuft kein Agent — hier ist <strong>Syslog</strong> der Weg.
                In der Verwaltungsoberfläche des Geräts als Syslog-Server eintragen:
              </p>
              <dl class="facts">
                <div><dt>Server</dt><dd class="font-mono">{{ selected.syslog_target || serverHost }}</dd></div>
                <div><dt>Port / Protokoll</dt><dd class="font-mono">514 / UDP (oder TCP)</dd></div>
              </dl>
              <p class="hint">{{ SYSLOG_HINTS[selected.kind] }}</p>
              <p v-if="selected.same_subnet === false" class="hint" style="color: var(--color-warning)">
                Das Gerät steht in einem anderen Subnetz. Syslog ist unverschlüsselt — der Weg dorthin
                sollte im eigenen Netz bleiben, und eine Firewall dazwischen muss Port 514 durchlassen.
              </p>
            </template>

            <template v-else-if="selected.method === 'collector'">
              <p class="text-sm">
                Die FRITZ!Box spricht kein Syslog. Ihr Ereignisprotokoll holt der <strong>n8n-Sammler</strong> ab
                und liefert es im Namen der Box ein — Vorlage: <code>server/n8n/rimperio.json</code>.
              </p>
            </template>

            <template v-else-if="selected.method === 'unsupported'">
              <p class="text-sm">
                Smartphones und Tablets lassen sich nicht einbinden: es gibt dort weder Syslog noch einen Agent,
                und Fernzugriff ist gesperrt. Ausblenden hält die Liste übersichtlich.
              </p>
            </template>

            <template v-else>
              <p class="text-sm">
                Was das ist, ließ sich nicht sicher erkennen. Hat das Gerät eine Weboberfläche, lohnt ein
                Blick nach einer Syslog-Einstellung — Ziel wäre
                <span class="font-mono">{{ selected.syslog_target || serverHost }}</span>, Port 514.
                Ist es ein Server, hilft ein offener SSH-Zugang (Port 22) bei der Erkennung.
              </p>
            </template>
          </section>

          <!-- Notiz & Ausblenden -->
          <div class="space-y-2">
            <label class="label">Notiz</label>
            <textarea v-model="noteDraft" class="textarea" rows="2" placeholder="z. B. „Drucker Büro 2. OG“" />
            <label class="flex items-center gap-2 text-sm cursor-pointer">
              <input v-model="ignoredDraft" type="checkbox">
              <span>Ausblenden — taucht dann nur noch unter „Ausgeblendet“ auf</span>
            </label>
          </div>

          <p v-if="copied" class="hint" style="color: var(--color-success)">In die Zwischenablage kopiert.</p>
          <p v-if="error" class="text-sm" style="color: var(--color-danger)">{{ error }}</p>
        </div>

        <div class="modal-footer">
          <button class="btn btn-ghost mr-auto device-delete" @click="removeDevice(selected)">
            <AppIcon name="trash" :size="16" /> Aus der Liste entfernen
          </button>
          <button class="btn btn-ghost" @click="selected = null">Schließen</button>
          <button class="btn btn-primary" @click="saveDevice">Speichern</button>
        </div>
      </div>
    </div>

    <!-- ================================================================
         EINSTELLUNGEN (MODAL)
         ================================================================ -->
    <div v-if="settingsOpen" class="modal-backdrop" @click.self="settingsOpen = false">
      <div class="modal max-w-2xl">
        <div class="card-header">
          <span class="card-title">Netzwerksuche einstellen</span>
          <button class="btn-icon" aria-label="Schließen" @click="settingsOpen = false">
            <AppIcon name="close" :size="18" />
          </button>
        </div>

        <div class="card-body space-y-5 overflow-y-auto">
          <!-- Was der Server sieht -->
          <div class="callout">
            <AppIcon name="network" :size="16" />
            <div class="text-sm space-y-1">
              <template v-if="host.host_access">
                <p>
                  Eigene Netze:
                  <span v-if="!host.interfaces?.length">keine erkannt</span>
                  <span v-for="(i, n) in host.interfaces" :key="i.network" class="font-mono">
                    {{ n ? ', ' : '' }}{{ i.network }} ({{ i.iface }})
                  </span>
                </p>
                <p>Gateway: <span class="font-mono">{{ host.gateways?.join(', ') || '–' }}</span></p>
              </template>
              <p v-else style="color: var(--color-warning)">
                Kein Zugriff auf das Netz des Hosts (gehärteter Betrieb). Es werden nur eingetragene Bereiche
                und Router abgesucht, und MAC-Adressen fehlen.
              </p>
            </div>
          </div>

          <div class="space-y-2">
            <label class="flex items-start gap-2 text-sm cursor-pointer">
              <input v-model="form.auto_scan" type="checkbox" class="mt-0.5">
              <span>
                Automatisch suchen
                <span class="hint block">
                  Einmal kurz nach dem ersten Start, danach im Takt unten. Die Suche baut nur Verbindungen
                  auf — eine Firewall mit Angriffserkennung kann trotzdem anschlagen.
                </span>
              </span>
            </label>
            <div class="flex items-center gap-2 text-sm pl-6">
              <span>Alle</span>
              <input v-model.number="form.interval_hours" type="number" min="0" max="720" class="input w-24">
              <span>Stunden (0 = nur von Hand)</span>
            </div>
          </div>

          <div class="space-y-2">
            <p class="label">Wo gesucht wird</p>
            <label class="flex items-center gap-2 text-sm cursor-pointer">
              <input v-model="form.scan_local" type="checkbox"> Eigene Subnetze des Servers
            </label>
            <label class="flex items-start gap-2 text-sm cursor-pointer">
              <input v-model="form.query_gateway" type="checkbox" class="mt-0.5">
              <span>
                Standard-Gateway per SNMP fragen
                <span class="hint block">Ein Router kennt, was hinter ihm hängt: ARP-Tabelle (IP + MAC) und angeschlossene Subnetze.</span>
              </span>
            </label>
            <label class="flex items-center gap-2 text-sm cursor-pointer">
              <input v-model="form.scan_router_subnets" type="checkbox"> Vom Router gemeldete Subnetze mit absuchen
            </label>
          </div>

          <div>
            <label class="label">Weitere Router (einer pro Zeile: <code>IP [Community]</code>)</label>
            <textarea v-model="routersText" class="textarea font-mono" rows="3" placeholder="10.0.0.1&#10;10.20.0.1 meinecommunity" />
          </div>

          <div>
            <label class="label">Weitere Bereiche (einer pro Zeile)</label>
            <textarea v-model="rangesText" class="textarea font-mono" rows="3" placeholder="10.20.0.0/24&#10;10.30.0.10-10.30.0.80&#10;172.16.5.12" />
            <p class="hint">Für Netze, die weder lokal sind noch von einem Router gemeldet werden — etwa hinter einem VPN.</p>
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label class="label">SNMP-Community (nur lesen)</label>
              <input v-model="form.snmp_community" type="text" class="input font-mono" autocomplete="off">
            </div>
            <div>
              <label class="label">Höchstzahl Adressen je Suche</label>
              <input v-model.number="form.max_hosts" type="number" min="1" max="65536" class="input">
            </div>
          </div>
          <label class="flex items-center gap-2 text-sm cursor-pointer">
            <input v-model="form.snmp_probe_hosts" type="checkbox"> Gefundene Geräte per SNMP nach Name und Beschreibung fragen
          </label>

          <p v-if="settingsError" class="text-sm" style="color: var(--color-danger)">{{ settingsError }}</p>
        </div>

        <div class="modal-footer">
          <button class="btn btn-ghost mr-auto" @click="forgetMissing">Seit 30 Tagen nicht Gesehenes entfernen</button>
          <button class="btn btn-ghost" @click="settingsOpen = false">Abbrechen</button>
          <button class="btn btn-primary" @click="saveSettings">Speichern</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, h } from 'vue'
import { useAuthStore } from '../stores/auth'
import AppIcon from '../components/AppIcon.vue'

const auth = useAuthStore()

const STATUS = {
  delivering: { label: 'Liefert Logs', badge: 'badge-success' },
  stale: { label: 'Liefert nicht mehr', badge: 'badge-warning' },
  open: { label: 'Nicht eingebunden', badge: 'badge-primary' },
  unsupported: { label: 'Nicht einbindbar', badge: 'badge-neutral' },
  self: { label: 'RIMPERIO', badge: 'badge-neutral' },
}

const FILTERS = [
  { key: 'todo', label: 'Zu erledigen' },
  { key: 'delivering', label: 'Liefert' },
  { key: 'unsupported', label: 'Nicht einbindbar' },
  { key: 'all', label: 'Alle' },
  { key: 'ignored', label: 'Ausgeblendet' },
]

const AGENT_TYPES = {
  syslog: 'Syslog', linux_agent: 'Linux-Agent', windows_agent: 'Windows-Agent', fritzbox: 'FRITZ!Box',
}
const MATCHED_BY = { mac: 'der MAC-Adresse', ip: 'der IP-Adresse', hostname: 'dem Hostnamen' }
const FOUND_BY = { icmp: 'Ping', tcp: 'TCP', arp: 'ARP-Tabelle', router: 'Router (SNMP)' }

const SYSLOG_HINTS = {
  network: 'Bei Switches und Firewalls heißt das meist „Syslog“, „Remote Logging“ oder „Log-Server“ unter System → Protokollierung.',
  printer: 'Bei Druckern steht es oft unter Netzwerk → Protokolle oder Sicherheit → Syslog. Nicht jedes Modell kann es.',
  nas: 'Synology: Protokoll-Center → Protokolle senden. QNAP: QuLog Center → Remote-Protokoll. TrueNAS: System → Allgemein → Syslog-Server.',
  appliance: 'Meist unter System, Protokollierung oder Wartung zu finden.',
  iot: 'Shelly: Einstellungen → Debug → UDP-Debug. Tasmota: Konfiguration → Logging → SysLog-Host.',
}

// Kleiner Baustein: Befehl in einem Kasten mit Kopier-Knopf
const CopyBlock = (props, { emit }) => h('div', { class: 'copy-block' }, [
  h('pre', { class: 'font-mono' }, props.text),
  h('button', { class: 'btn btn-secondary btn-sm', onClick: () => emit('copy', props.text) }, 'Kopieren'),
])
CopyBlock.props = ['text']
CopyBlock.emits = ['copy']

const devices = ref([])
const loading = ref(false)
const filter = ref('todo')
const search = ref('')
const scan = ref({ running: false })
const lastRun = ref({})
const host = ref({})
const siteUrl = ref(null)
const config = ref({})
const selected = ref(null)
const noteDraft = ref('')
const ignoredDraft = ref(false)
const copied = ref(false)
const error = ref('')

const onboard = ref({ open: false, manual: false, fpLoading: false, fingerprint: '',
  username: '', password: '', sudoPassword: '', error: '', state: null })
let onboardTimer = null

const settingsOpen = ref(false)
const form = ref({})
const routersText = ref('')
const rangesText = ref('')
const settingsError = ref('')

let pollTimer = null

const progress = computed(() => {
  const s = scan.value
  return s.total ? Math.round((s.done / s.total) * 100) : 5
})

function matchesFilter(d, key) {
  if (key === 'ignored') return d.ignored
  if (d.ignored) return false
  if (key === 'all') return true
  if (key === 'todo') return d.status === 'open' || d.status === 'stale'
  return d.status === key
}

const counts = computed(() => {
  const result = {}
  for (const f of FILTERS) result[f.key] = devices.value.filter(d => matchesFilter(d, f.key)).length
  result.open = result.todo
  return result
})

const visibleCount = computed(() => devices.value.filter(d => !d.ignored).length)

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  return devices.value
    .filter(d => matchesFilter(d, filter.value))
    .filter(d => !q || [d.hostname, d.ip_address, d.mac_address, d.kind_label, d.os_guess, d.note,
      d.details?.snmp?.sys_name].some(v => (v || '').toLowerCase().includes(q)))
})

const routerSummary = computed(() => {
  const routers = lastRun.value.routers || []
  if (!routers.length) return ''
  return routers.map(r => r.ok
    ? `Router ${r.host}${r.name ? ` (${r.name})` : ''}: ${r.neighbors} Nachbarn, ${(r.subnets || []).length + (r.routes || []).length} Subnetze`
    : `Router ${r.host}: keine SNMP-Antwort`).join(' · ')
})

// Unter diesem Namen erreichen die Agents den Server. SITE_URL hat Vorrang,
// sonst die Adresse, unter der diese Seite gerade offen ist.
const serverHost = computed(() => {
  try {
    if (siteUrl.value) return new URL(siteUrl.value).hostname
  } catch { /* SITE_URL ohne Schema */ }
  return window.location.hostname
})

const RAW = 'https://raw.githubusercontent.com/Phydran6/RIMPERIO-Server/main/agents'
// Erst GitHub, sonst der RIMPERIO-Server selbst — so klappt es auch ohne Internet.
const linuxCommand = computed(() =>
  `sh -c 'curl -fsSL ${RAW}/install-linux.sh -o /tmp/lb.sh || ` +
  `curl -fsSLk https://${serverHost.value}/api/agents/installer/linux -o /tmp/lb.sh' && ` +
  `sudo bash /tmp/lb.sh --fqdn ${serverHost.value} --token EINLADUNG --yes`)
const windowsCommand = computed(() =>
  `& ([scriptblock]::Create((irm ${RAW}/install-windows.ps1))) -Action install -Fqdn ${serverHost.value} -Token EINLADUNG -Yes`)

onMounted(async () => {
  await loadStatus()
  await loadDevices()
  if (scan.value.running) startPolling()
})

onBeforeUnmount(() => { stopPolling(); stopOnboardPoll() })

async function loadStatus() {
  try {
    const data = await auth.api('/api/discovery/status')
    const wasRunning = scan.value.running
    scan.value = data.scan
    lastRun.value = data.last_run || {}
    host.value = data.host || {}
    siteUrl.value = data.site_url
    config.value = data.config || {}
    if (wasRunning && !data.scan.running) {
      stopPolling()
      await loadDevices()
    }
  } catch (e) {
    error.value = e.message
  }
}

async function loadDevices() {
  loading.value = true
  try {
    const data = await auth.api('/api/discovery/devices')
    devices.value = data.items
  } catch (e) {
    console.error('Systeme laden fehlgeschlagen:', e)
  } finally {
    loading.value = false
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(loadStatus, 1500)
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

async function startScan() {
  try {
    const data = await auth.api('/api/discovery/scan', { method: 'POST' })
    scan.value = data.scan
    startPolling()
  } catch (e) {
    alert(e.message)
  }
}

function openDevice(d) {
  selected.value = d
  noteDraft.value = d.note || ''
  ignoredDraft.value = d.ignored
  copied.value = false
  error.value = ''
  resetOnboard()
}

function resetOnboard() {
  stopOnboardPoll()
  onboard.value = { open: false, manual: false, fpLoading: false, fingerprint: '',
    username: '', password: '', sudoPassword: '', error: '', state: null }
}

async function beginOnboard() {
  onboard.value.state = null
  onboard.value.open = true
  onboard.value.fpLoading = true
  onboard.value.error = ''
  onboard.value.fingerprint = ''
  try {
    const data = await auth.api(`/api/discovery/devices/${selected.value.id}/fingerprint`)
    onboard.value.fingerprint = `${data.fingerprint} (${data.key_type})`
    onboard.value.fingerprintRaw = data.fingerprint
  } catch (e) {
    onboard.value.error = e.message
  } finally {
    onboard.value.fpLoading = false
  }
}

function cancelOnboard() {
  // Zugangsdaten sofort aus dem Speicher der Seite nehmen.
  resetOnboard()
}

async function runOnboard() {
  const o = onboard.value
  try {
    await auth.api(`/api/discovery/devices/${selected.value.id}/onboard`, {
      method: 'POST',
      body: {
        username: o.username, password: o.password,
        sudo_password: o.sudoPassword, fingerprint: o.fingerprintRaw,
      },
    })
  } catch (e) {
    o.error = e.message
    return
  }
  // Passwörter nicht länger als nötig in der Seite halten.
  o.password = ''
  o.sudoPassword = ''
  o.open = false
  o.state = { running: true, steps: [], phase: 'Startet…' }
  startOnboardPoll()
}

function startOnboardPoll() {
  stopOnboardPoll()
  const id = selected.value.id
  onboardTimer = setInterval(async () => {
    try {
      const state = await auth.api(`/api/discovery/devices/${id}/onboard`)
      onboard.value.state = state
      if (!state.running) {
        stopOnboardPoll()
        await loadDevices()
        if (selected.value) selected.value = devices.value.find(d => d.id === id) || selected.value
      }
    } catch {
      stopOnboardPoll()
    }
  }, 1500)
}

function stopOnboardPoll() {
  if (onboardTimer) clearInterval(onboardTimer)
  onboardTimer = null
}

async function saveDevice() {
  error.value = ''
  try {
    await auth.api(`/api/discovery/devices/${selected.value.id}`, {
      method: 'PUT', body: { note: noteDraft.value, ignored: ignoredDraft.value },
    })
    selected.value = null
    await loadDevices()
  } catch (e) {
    error.value = e.message
  }
}

async function removeDevice(d) {
  if (!confirm(`„${d.hostname || d.ip_address}“ aus der Liste entfernen? Findet die nächste Suche es wieder, taucht es wieder auf.`)) return
  try {
    await auth.api(`/api/discovery/devices/${d.id}`, { method: 'DELETE' })
    selected.value = null
    await loadDevices()
  } catch (e) {
    error.value = e.message
  }
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = true
  } catch {
    error.value = 'Kopieren nicht möglich (Zwischenablage braucht HTTPS) — bitte von Hand markieren.'
  }
}

function openSettings() {
  form.value = { ...config.value }
  routersText.value = (config.value.routers || [])
    .map(r => (r.community ? `${r.host} ${r.community}` : r.host)).join('\n')
  rangesText.value = (config.value.extra_ranges || []).join('\n')
  settingsError.value = ''
  settingsOpen.value = true
}

async function saveSettings() {
  settingsError.value = ''
  const routers = routersText.value.split('\n').map(l => l.trim()).filter(Boolean).map(l => {
    const [hostPart, community] = l.split(/\s+/, 2)
    return { host: hostPart, community: community || '' }
  })
  try {
    config.value = await auth.api('/api/discovery/config', {
      method: 'PUT',
      body: {
        ...form.value,
        routers,
        extra_ranges: rangesText.value.split('\n').map(l => l.trim()).filter(Boolean),
      },
    })
    settingsOpen.value = false
  } catch (e) {
    settingsError.value = e.message
  }
}

async function forgetMissing() {
  if (!confirm('Alle Systeme entfernen, die seit 30 Tagen keine Suche mehr gefunden hat? Ausgeblendete bleiben.')) return
  try {
    const data = await auth.api('/api/discovery/forget?days=30', { method: 'POST' })
    settingsError.value = ''
    alert(`${data.removed} Einträge entfernt.`)
    await loadDevices()
  } catch (e) {
    settingsError.value = e.message
  }
}

function foundBy(d) {
  return (d.details?.alive_by || []).map(k => FOUND_BY[k] || k).join(', ') || '–'
}

function articleFor(kind) {
  return {
    network: 'Netzwerkgeräten', printer: 'Druckern', nas: 'einem NAS', appliance: 'Appliances',
    iot: 'IoT-Geräten',
  }[kind] || 'diesem Gerät'
}

function formatTime(ts) {
  if (!ts) return '–'
  let s = String(ts).trim()
  if (s.includes(' ')) s = s.replace(' ', 'T')
  if (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(s)) s = `${s}Z`    // Server liefert UTC ohne Zone
  const t = Date.parse(s)
  return Number.isFinite(t) ? new Date(t).toLocaleString('de-DE') : '–'
}
</script>

<style scoped>
.search-wrap {
  position: relative;
}

.search-icon {
  position: absolute;
  top: 50%;
  left: 0.75rem;
  transform: translateY(-50%);
  color: var(--color-text-muted);
  pointer-events: none;
}

.progress {
  height: 0.375rem;
  border-radius: 999px;
  background-color: var(--color-border);
  overflow: hidden;
}

.progress-bar {
  height: 100%;
  background-color: var(--color-primary);
  transition: width 0.4s ease;
}

.notes li {
  display: flex;
  gap: 0.375rem;
  align-items: flex-start;
  color: var(--color-warning);
  font-size: 0.8125rem;
}

.notes li :deep(svg) {
  flex-shrink: 0;
  margin-top: 0.15rem;
}

.port {
  display: inline-flex;
  gap: 0.25rem;
  padding: 0.0625rem 0.375rem;
  border-radius: var(--radius);
  background-color: var(--primary-soft);
  color: var(--color-text-secondary);
  font-size: 0.6875rem;
  white-space: nowrap;
}

.row-absent td {
  opacity: 0.55;
}

.facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem 1rem;
  font-size: 0.8125rem;
}

.facts dt {
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-text-muted);
  margin-bottom: 0.125rem;
}

.facts dd {
  color: var(--color-text-secondary);
  overflow-wrap: anywhere;
}

.callout {
  display: flex;
  gap: 0.625rem;
  align-items: flex-start;
  padding: 0.75rem;
  border-radius: var(--radius);
  background-color: var(--primary-soft);
}

.callout :deep(svg) {
  flex-shrink: 0;
  margin-top: 0.15rem;
}

.copy-block {
  display: flex;
  gap: 0.5rem;
  align-items: flex-start;
}

.copy-block pre {
  flex: 1;
  min-width: 0;
  padding: 0.625rem 0.75rem;
  border-radius: var(--radius);
  background-color: var(--color-surface-elevated);
  border: 1px solid var(--color-border);
  font-size: 0.75rem;
  white-space: pre-wrap;
  word-break: break-all;
}

.label.inline {
  display: inline;
}

.device-delete:hover {
  background-color: var(--danger-soft);
  color: var(--color-danger);
}

.modal-footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 0.5rem;
  padding: 0.875rem 1.25rem;
  border-top: 1px solid var(--color-border);
}

.onboard-panel {
  padding: 0.875rem;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background-color: var(--color-surface-elevated);
}

.onboard-steps {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  font-size: 0.8125rem;
}

.onboard-steps li {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: var(--color-success);
}

.onboard-steps li.bad {
  color: var(--color-danger);
}

.onboard-steps li.pending {
  color: var(--color-text-muted);
}

.spinner {
  width: 0.875rem;
  height: 0.875rem;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
  flex-shrink: 0;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
