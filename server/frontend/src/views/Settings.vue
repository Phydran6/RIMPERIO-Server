<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.08.14.12.00.00
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Einstellungen: eine Seite fuer alles am System.
                   Verzeichnis (LDAP), Archivierung, Erscheinungsbild und
                   Anmeldesicherheit sind hier Reiter statt eigener Menuepunkte.
     ============================================================================== -->

<template>
  <div class="p-6">
    <div class="mb-5">
      <h1 class="text-2xl font-bold" :style="{ color: 'var(--color-text-primary)' }">Einstellungen</h1>
      <p class="text-sm mt-1" :style="{ color: 'var(--color-text-muted)' }">
        Alles zum System an einem Ort. Die Adresse wechselt mit dem Reiter – so lässt sich
        ein Bereich verlinken oder als Lesezeichen ablegen.
      </p>
    </div>

    <!-- Reiter, nach Themen gruppiert -->
    <div class="space-y-2 mb-6">
      <div v-for="group in availableGroups" :key="group.title" class="flex flex-wrap items-center gap-2">
        <span class="tab-group-label">{{ group.title }}</span>
        <button
          v-for="tab in group.tabs"
          :key="tab.id"
          class="px-3 py-2 rounded text-sm"
          :style="tabButtonStyle(tab.id)"
          @click="setTab(tab.id)"
        >
          {{ tab.label }}
        </button>
      </div>
    </div>

    <!-- Allgemein -->
    <div v-if="activeTab === 'general'" class="rounded-lg shadow p-6" :style="cardStyle">
      <h2 class="text-lg font-semibold mb-4" :style="{ color: 'var(--color-text-primary)' }">Allgemeine Einstellungen</h2>
      <div class="space-y-4">
        <div>
          <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Anwendungsname</label>
          <input v-model="settings.app_name" type="text" class="w-full rounded px-3 py-2" :style="inputStyle">
        </div>
        <div>
          <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Zeitzone</label>
          <input v-model="settings.timezone" type="text" class="w-full rounded px-3 py-2" :style="inputStyle" placeholder="Europe/Berlin">
        </div>
        <div>
          <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Agent Offline Timeout (Sekunden)</label>
          <input v-model.number="settings.agent_offline_timeout" type="number" min="60" class="w-full rounded px-3 py-2" :style="inputStyle">
          <p class="text-xs mt-1" :style="{ color: 'var(--color-text-muted)' }">Nach dieser Zeit ohne Logs gilt ein Agent als offline</p>
        </div>
        <div>
          <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Max. Logs pro API-Anfrage</label>
          <input v-model.number="settings.max_logs_per_request" type="number" min="100" max="10000" class="w-full rounded px-3 py-2" :style="inputStyle">
        </div>
        <div class="flex items-center justify-between">
          <div>
            <label class="block text-sm font-medium" :style="{ color: 'var(--color-text-secondary)' }">Auto-Discovery</label>
            <p class="text-xs" :style="{ color: 'var(--color-text-muted)' }">Neue Agents automatisch erstellen</p>
          </div>
          <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" v-model="settings.enable_auto_discovery" class="sr-only peer">
            <div class="w-11 h-6 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all" :style="{ backgroundColor: settings.enable_auto_discovery ? 'var(--color-primary)' : 'var(--color-surface-elevated)' }"></div>
          </label>
        </div>
        <button @click="saveAllSettings" :disabled="saving" class="w-full text-white py-2 rounded disabled:opacity-50 mt-4 hover:opacity-90" :style="{ backgroundColor: 'var(--color-primary)' }">
          {{ saving ? 'Speichere...' : '💾 Einstellungen speichern' }}
        </button>
        <p v-if="saveMessage" class="text-sm text-center" :style="{ color: saveError ? 'var(--color-danger)' : 'var(--color-success)' }">
          {{ saveMessage }}
        </p>
      </div>
    </div>

    <!-- Retention -->
    <div v-if="activeTab === 'retention'" class="rounded-lg shadow p-6" :style="cardStyle">
      <h2 class="text-lg font-semibold mb-4" :style="{ color: 'var(--color-text-primary)' }">Log Retention</h2>
      <div class="space-y-4">
        <div>
          <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Aufbewahrung (Tage)</label>
          <input v-model.number="retentionDays" type="number" min="1" class="w-full rounded px-3 py-2" :style="inputStyle">
          <p class="text-xs mt-1" :style="{ color: 'var(--color-text-muted)' }">Logs älter als diese Anzahl Tage werden stündlich gelöscht – unabhängig davon, wie voll die Platte ist.</p>
        </div>
        <div>
          <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">INFO/DEBUG kürzer aufbewahren (Tage, 0 = wie oben)</label>
          <input v-model.number="retentionInfoDays" type="number" min="0" class="w-full rounded px-3 py-2" :style="inputStyle">
          <p class="text-xs mt-1" :style="{ color: 'var(--color-text-muted)' }">
            Gespeichert wird zunächst alles. INFO-, NOTICE- und DEBUG-Meldungen altern nur früher heraus –
            Warnungen und Fehler bleiben die volle Frist.
          </p>
        </div>
        <button @click="saveRetention" :disabled="retentionSaving" class="w-full py-2 rounded text-white hover:opacity-90 disabled:opacity-50" :style="{ backgroundColor: 'var(--color-primary)' }">
          {{ retentionSaving ? 'Speichere…' : 'Aufbewahrung speichern' }}
        </button>
        <p v-if="retentionMessage" class="text-sm text-center" :style="{ color: retentionMessage.startsWith('Fehler') ? 'var(--color-danger)' : 'var(--color-success)' }">
          {{ retentionMessage }}
        </p>
        <button @click="previewRetention" class="w-full py-2 rounded hover:opacity-80" :style="buttonSecondaryStyle">
          Vorschau anzeigen
        </button>
        <div v-if="retentionPreview" class="rounded p-4" :style="warningBoxStyle">
          <p class="font-medium" :style="{ color: 'var(--color-warning)' }">
            {{ retentionPreview.logs_to_delete.toLocaleString() }} Logs würden gelöscht
          </p>
          <p v-if="retentionPreview.oldest_log_date" class="text-sm mt-1" :style="{ color: 'var(--color-text-secondary)' }">
            Ältester Log: {{ formatDate(retentionPreview.oldest_log_date) }}
          </p>
        </div>
        <button
          @click="executeRetention"
          :disabled="!retentionPreview || retentionPreview.logs_to_delete === 0"
          class="w-full text-white py-2 rounded disabled:opacity-50 hover:opacity-90"
          :style="{ backgroundColor: 'var(--color-danger)' }"
        >
          Alte Logs löschen
        </button>
      </div>
      <div class="mt-6 pt-6 border-t" :style="{ borderColor: 'var(--color-border)' }">
        <h3 class="font-medium mb-4" :style="{ color: 'var(--color-danger)' }">Gefahrenzone</h3>
        <button
          @click="deleteAllLogs"
          class="w-full py-2 rounded hover:opacity-80 disabled:opacity-50"
          :disabled="deletingAllLogs"
          :style="dangerButtonStyle"
        >
          {{ deletingAllLogs ? 'Lösche alle Logs...' : 'ALARM: ALLE Logs löschen (TRUNCATE)' }}
        </button>
        <p v-if="deleteAllMessage" class="text-xs mt-2" :style="{ color: deleteAllError ? 'var(--color-danger)' : 'var(--color-success)' }">
          {{ deleteAllMessage }}
        </p>
        <button
          @click="compactLogs"
          class="w-full py-2 rounded hover:opacity-80 disabled:opacity-50 mt-3"
          :disabled="compactingLogs"
          :style="dangerButtonStyle"
        >
          {{ compactingLogs ? 'Komprimiere Logs...' : 'Speicher freigeben (VACUUM FULL)' }}
        </button>
        <p v-if="compactMessage" class="text-xs mt-2" :style="{ color: compactError ? 'var(--color-danger)' : 'var(--color-success)' }">
          {{ compactMessage }}
        </p>
        <p class="text-xs mt-2" :style="{ color: 'var(--color-danger)' }">Diese Aktion kann nicht rueckgaengig gemacht werden!</p>
      </div>
    </div>

    <!-- Agent Token -->
    <div v-if="activeTab === 'agents'" class="rounded-lg shadow p-6 mt-6" :style="cardStyle">
      <h2 class="text-lg font-semibold mb-4" :style="{ color: 'var(--color-text-primary)' }">Agent Token (global)</h2>
      <p class="text-sm mb-4" :style="{ color: 'var(--color-text-muted)' }">
        Ein Token für alle Agenten (Linux/Windows). Für höhere Sicherheit kannst du es bei Bedarf regenerieren.
      </p>

      <div v-if="tokenError" class="text-sm mb-2" :style="{ color: 'var(--color-danger)' }">{{ tokenError }}</div>
      <div v-if="tokenMessage" class="text-sm mb-2" :style="{ color: 'var(--color-success)' }">{{ tokenMessage }}</div>

      <div class="flex flex-col md:flex-row gap-3 items-start md:items-end">
        <div class="flex-1 w-full">
          <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Token</label>
          <input :value="agentToken?.token || ''" readonly class="w-full rounded px-3 py-2 font-mono" :style="inputStyle">
          <p class="text-xs mt-1" :style="{ color: 'var(--color-text-muted)' }">
            Name: {{ agentToken?.name || 'global-agent' }}
          </p>
        </div>
        <div class="flex gap-2">
          <button
            class="px-3 py-2 rounded text-white hover:opacity-90"
            :style="{ backgroundColor: 'var(--color-primary)' }"
            :disabled="tokenLoading || !agentToken"
            @click="copyAgentToken"
          >
            Kopieren
          </button>
          <button
            class="px-3 py-2 rounded text-white hover:opacity-90"
            :style="{ backgroundColor: 'var(--color-danger)' }"
            :disabled="tokenLoading || !agentToken"
            @click="regenerateAgentToken"
          >
            {{ tokenLoading ? 'Regeneriere…' : 'Neu generieren' }}
          </button>
        </div>
      </div>
    </div>
    <!-- Netzwerk (Reverse Proxy + DNS, nur Admin) -->
    <div v-if="authStore.isAdmin && activeTab === 'network'" class="mt-6 space-y-4">
      <!-- Unter-Navigation -->
      <div class="flex flex-wrap gap-2">
        <button
          v-for="sub in networkSubTabs"
          :key="sub.id"
          class="px-3 py-2 rounded text-sm"
          :style="subTabButtonStyle(sub.id)"
          @click="networkSubTab = sub.id"
        >
          {{ sub.label }}
        </button>
      </div>

    <!-- Reverse Proxy & TLS (Caddy) -->
    <div v-if="networkSubTab === 'caddy'" class="rounded-lg shadow p-6" :style="cardStyle">
      <h2 class="text-lg font-semibold mb-2" :style="{ color: 'var(--color-text-primary)' }">Reverse Proxy & TLS (Caddy)</h2>
      <p class="text-sm mb-4" :style="{ color: 'var(--color-text-muted)' }">
        Angewendet wird <strong>genau das, was unten im Editor steht</strong>. Die Konfiguration wird vorher
        von Caddy geprüft; schlägt das Laden fehl, wird automatisch die vorherige zurückgeholt.
        Port 80 bleibt in jeder Vorlage erreichbar – damit sperrt man sich nicht aus.
      </p>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">FQDN</label>
          <input
            v-model="caddyDomain"
            :disabled="caddyMode === 'http'"
            type="text"
            class="w-full rounded px-3 py-2"
            :style="{
              ...inputStyle,
              borderColor: caddyMode === 'http' ? inputStyle.borderColor : (isFqdnValid ? 'var(--color-success)' : 'var(--color-danger)')
            }"
            :placeholder="caddyMode === 'http' ? 'FQDN nur bei HTTPS' : 'rimperio.example.com'">
          <div class="mt-3 space-y-2 text-sm" :style="{ color: 'var(--color-text-secondary)' }">
            <label class="flex items-center gap-2 cursor-pointer">
              <input type="radio" value="http" v-model="caddyMode">
              <span>Nur HTTP (Port 80, ohne FQDN)</span>
            </label>
            <label class="flex items-center gap-2 cursor-pointer">
              <input type="radio" value="letsencrypt" v-model="caddyMode">
              <span>HTTPS via Let's Encrypt (Auto-Zertifikat)</span>
            </label>
            <label class="flex items-center gap-2 cursor-pointer">
              <input type="radio" value="custom" v-model="caddyMode">
              <span>HTTPS mit eigenem Zertifikat</span>
            </label>
            <label class="flex items-center gap-2 cursor-pointer">
              <input type="radio" value="internal" v-model="caddyMode">
              <span>HTTPS selbstsigniert (Caddy-eigene CA, ohne Internet)</span>
            </label>
          </div>
          <div v-if="caddyMode === 'letsencrypt'" class="mt-3">
            <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">E-Mail für Let's Encrypt</label>
            <input v-model="caddyEmail" type="email" class="w-full rounded px-3 py-2" :style="inputStyle" placeholder="admin@example.com">
          </div>

          <div v-if="caddyMode !== 'http'" class="mt-3">
            <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">
              Zusätzliche Adressen für HTTPS (IP oder Name, Leerzeichen-getrennt)
            </label>
            <input
              v-model="caddyExtraHostsInput"
              type="text"
              class="w-full rounded px-3 py-2"
              :style="inputStyle"
              placeholder="192.168.1.10 rimperio.intern"
            >
            <p class="text-xs mt-1" :style="{ color: 'var(--color-text-muted)' }">
              Diese Adressen bekommen ein selbstsigniertes Zertifikat (für IPs gibt es keine öffentlichen).
              Der Zugriff über Port 80 funktioniert unabhängig davon immer.
              <button class="underline ml-1" @click="useCurrentAddress">Aktuelle Adresse übernehmen</button>
            </p>
          </div>
        </div>
        <div>
          <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Zertifikat (.crt / fullchain)</label>
          <input type="file" accept=".crt,.pem" class="w-full text-sm" @change="e => certUpload.cert = e.target.files?.[0] || null">
          <label class="block text-sm font-medium mt-3 mb-1" :style="{ color: 'var(--color-text-secondary)' }">Private Key (.key)</label>
          <input type="file" accept=".key,.pem" class="w-full text-sm" @change="e => certUpload.key = e.target.files?.[0] || null">
          <p class="text-xs mt-2" :style="{ color: caddyCertPresent ? 'var(--color-success)' : 'var(--color-text-muted)' }">
            {{ caddyCertPresent ? 'Zertifikat vorhanden' : 'Noch kein Zertifikat gespeichert' }}
          </p>
          <p class="text-xs" :style="{ color: 'var(--color-text-muted)' }">
            Hinweis: Lade am besten die Fullchain (Leaf + Intermediate) als .crt hoch; Root wird nicht benötigt.
          </p>
          <button
            class="mt-3 px-3 py-2 rounded text-white hover:opacity-90 disabled:opacity-50"
            :style="{ backgroundColor: 'var(--color-primary)' }"
            @click="uploadCaddyCert"
            :disabled="caddyLoading"
          >
            Zertifikat speichern
          </button>
          <p v-if="caddyMode !== 'custom'" class="text-xs mt-2" :style="{ color: 'var(--color-text-muted)' }">
            Hinweis: Custom Cert wird nur genutzt, wenn der Modus „Eigenes Zertifikat“ aktiviert ist.
          </p>
        </div>
      </div>

      <div class="flex flex-col md:flex-row gap-3 mt-4">
        <button
          class="px-3 py-2 rounded hover:opacity-90"
          :style="buttonSecondaryStyle"
          @click="buildCaddyTemplate"
          :disabled="caddyLoading"
        >
          Template erzeugen
        </button>
        <button
          class="px-3 py-2 rounded text-white hover:opacity-90 disabled:opacity-50"
          :style="{ backgroundColor: 'var(--color-primary)' }"
          @click="applyCaddy"
          :disabled="caddyLoading"
        >
          {{ caddyLoading ? 'Wird angewendet...' : 'Apply & Reload' }}
        </button>
        <button
          class="px-3 py-2 rounded hover:opacity-90 disabled:opacity-50"
          :style="dangerButtonStyle"
          @click="resetCaddy"
          :disabled="caddyLoading"
          title="Setzt den Reverse Proxy auf reines HTTP zurück"
        >
          Zurück auf HTTP
        </button>
      </div>

      <textarea
        v-model="caddyfile"
        class="w-full mt-4 rounded px-3 py-2 font-mono text-sm"
        rows="12"
        :style="inputStyle"
        placeholder="Caddyfile"
      ></textarea>

      <p v-if="caddyMessage" class="text-sm mt-2" :style="{ color: 'var(--color-success)' }">{{ caddyMessage }}</p>
        <p v-if="caddyError" class="text-sm mt-2" :style="{ color: 'var(--color-danger)' }">{{ caddyError }}</p>
        <ul v-if="caddyWarnings.length" class="mt-2 space-y-1">
          <li
            v-for="(w, i) in caddyWarnings"
            :key="i"
            class="text-sm p-2 rounded"
            :style="warningBoxStyle"
          >{{ w }}</li>
        </ul>
        <div v-if="showHttpHint" class="mt-3 p-3 rounded" :style="warningBoxStyle">
          <p class="text-sm" :style="{ color: 'var(--color-text-primary)' }">
            HTTP-Modus aktiv. Öffne&nbsp;
            <a :href="httpLink" target="_blank" rel="noopener" class="underline">{{ httpLink }}</a>
            &nbsp;in einem neuen Tab.
          </p>
          <div class="flex items-center gap-2 mt-2">
            <button
              class="text-sm px-3 py-1 rounded"
              :style="buttonSecondaryStyle"
              @click="copyHttpLink"
            >
              Link kopieren
            </button>
            <span v-if="httpCopyMessage" class="text-xs" :style="{ color: 'var(--color-success)' }">
              {{ httpCopyMessage }}
            </span>
          </div>
          <p class="text-xs mt-2" :style="{ color: 'var(--color-text-muted)' }">
            Die Seite bleibt über HTTPS geöffnet, bis du wechselst – deine Sitzung geht dabei nicht verloren.
          </p>
        </div>
      </div>

      <!-- DNS -->
      <div v-if="networkSubTab === 'dns'" class="rounded-lg shadow p-6" :style="cardStyle">
        <h2 class="text-lg font-semibold mb-2" :style="{ color: 'var(--color-text-primary)' }">DNS</h2>
        <p class="text-sm mb-4" :style="{ color: 'var(--color-text-muted)' }">
          Standardmäßig wird der im Netzwerk per DHCP vergebene DNS verwendet. Alternativ kannst du eigene DNS-Server hinterlegen.
        </p>

        <div v-if="dnsError" class="text-sm mb-2" :style="{ color: 'var(--color-danger)' }">{{ dnsError }}</div>
        <div v-if="dnsMessage" class="text-sm mb-2" :style="{ color: 'var(--color-success)' }">{{ dnsMessage }}</div>

        <div class="space-y-2 text-sm" :style="{ color: 'var(--color-text-secondary)' }">
          <label class="flex items-center gap-2 cursor-pointer">
            <input type="radio" value="dhcp" v-model="dnsMode">
            <span>Automatisch (per DHCP vergebener DNS)</span>
          </label>
          <label class="flex items-center gap-2 cursor-pointer">
            <input type="radio" value="manual" v-model="dnsMode">
            <span>Manuell (eigene DNS-Server)</span>
          </label>
        </div>

        <div class="mt-4">
          <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Erkannte System-DNS (DHCP)</label>
          <p class="text-sm font-mono" :style="{ color: 'var(--color-text-primary)' }">
            {{ dnsDetected.length ? dnsDetected.join(', ') : 'Keine erkannt' }}
          </p>
        </div>

        <div v-if="dnsMode === 'manual'" class="mt-4">
          <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">DNS-Server</label>
          <div v-for="(srv, idx) in dnsServers" :key="idx" class="flex items-center gap-2 mb-2">
            <input v-model="dnsServers[idx]" type="text" class="flex-1 rounded px-3 py-2" :style="inputStyle" placeholder="z.B. 192.168.179.30">
            <button class="px-3 py-2 rounded hover:opacity-80" :style="buttonSecondaryStyle" @click="dnsServers.splice(idx, 1)">Entfernen</button>
          </div>
          <button class="px-3 py-2 rounded hover:opacity-80" :style="buttonSecondaryStyle" @click="dnsServers.push('')">+ Server hinzufügen</button>

          <label class="block text-sm font-medium mt-4 mb-1" :style="{ color: 'var(--color-text-secondary)' }">Such-Domains (optional)</label>
          <input v-model="dnsSearchInput" type="text" class="w-full rounded px-3 py-2" :style="inputStyle" placeholder="z.B. phytech.de intern.lan (Leerzeichen-getrennt)">
        </div>

        <div class="mt-4">
          <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Aktuell aktive Nameserver (Container)</label>
          <p class="text-sm font-mono" :style="{ color: 'var(--color-text-primary)' }">
            {{ dnsActive.length ? dnsActive.join(', ') : '-' }}
          </p>
        </div>

        <div class="flex flex-col md:flex-row gap-3 mt-4">
          <button
            class="px-3 py-2 rounded text-white hover:opacity-90 disabled:opacity-50"
            :style="{ backgroundColor: 'var(--color-primary)' }"
            @click="applyDns"
            :disabled="dnsLoading"
          >
            {{ dnsLoading ? 'Wird angewendet...' : 'Anwenden' }}
          </button>
        </div>

        <div class="mt-6 pt-4 border-t" :style="{ borderColor: 'var(--color-border)' }">
          <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Auflösung testen</label>
          <div class="flex items-center gap-2">
            <input v-model="dnsTestHost" type="text" class="flex-1 rounded px-3 py-2" :style="inputStyle" placeholder="rimperio.example.com">
            <button
              class="px-3 py-2 rounded hover:opacity-80 disabled:opacity-50"
              :style="buttonSecondaryStyle"
              @click="testDns"
              :disabled="dnsTesting"
            >
              {{ dnsTesting ? 'Teste…' : 'Testen' }}
            </button>
          </div>
          <p v-if="dnsTestResult" class="text-sm mt-2" :style="{ color: dnsTestResult.resolved ? 'var(--color-success)' : 'var(--color-danger)' }">
            {{ dnsTestResult.resolved ? ('OK: ' + (dnsTestResult.addresses || []).join(', ')) : ('Fehler: ' + (dnsTestResult.error || 'nicht auflösbar')) }}
          </p>
        </div>
      </div>
    </div>
    <!-- /Netzwerk -->

    <!-- Datenbank (nur Admin) -->
    <div v-if="authStore.isAdmin && activeTab === 'database'" class="rounded-lg shadow p-6 mt-6" :style="cardStyle">
      <h2 class="text-lg font-semibold mb-4" :style="{ color: 'var(--color-text-primary)' }">Datenbank</h2>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        <div>
          <p class="text-xs" :style="{ color: 'var(--color-text-muted)' }">Host</p>
          <p :style="{ color: 'var(--color-text-primary)' }">{{ dbSettings?.host || '-' }}</p>
        </div>
        <div>
          <p class="text-xs" :style="{ color: 'var(--color-text-muted)' }">Port</p>
          <p :style="{ color: 'var(--color-text-primary)' }">{{ dbSettings?.port || '-' }}</p>
        </div>
        <div>
          <p class="text-xs" :style="{ color: 'var(--color-text-muted)' }">Benutzer</p>
          <p :style="{ color: 'var(--color-text-primary)' }">{{ dbSettings?.user || '-' }}</p>
        </div>
        <div>
          <p class="text-xs" :style="{ color: 'var(--color-text-muted)' }">Datenbank</p>
          <p :style="{ color: 'var(--color-text-primary)' }">{{ dbSettings?.name || '-' }}</p>
        </div>
      </div>
      <div>
        <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Passwort</label>
        <div class="flex items-center gap-2">
          <input
            :type="showDbPassword ? 'text' : 'password'"
            :value="dbSettings?.password || ''"
            readonly
            class="w-full rounded px-3 py-2"
            :style="inputStyle"
          >
          <button
            type="button"
            class="px-3 py-2 rounded hover:opacity-80"
            :style="buttonSecondaryStyle"
            @click="showDbPassword = !showDbPassword"
            :disabled="!dbSettings"
          >
            {{ showDbPassword ? 'Verbergen' : 'Anzeigen' }}
          </button>
          <button
            type="button"
            class="px-3 py-2 rounded hover:opacity-80"
            :style="buttonSecondaryStyle"
            @click="copyDbPassword"
            :disabled="!dbSettings?.password"
          >
            Kopieren
          </button>
        </div>
        <p v-if="dbCopyMessage" class="text-xs mt-1" :style="{ color: 'var(--color-text-muted)' }">{{ dbCopyMessage }}</p>
        <p v-if="dbError" class="text-sm mt-2" :style="{ color: 'var(--color-danger)' }">{{ dbError }}</p>
      </div>
    </div>
    
    <!-- Wartung (nur Admin) -->
    <div v-if="authStore.isAdmin && activeTab === 'maintenance'" class="rounded-lg shadow p-6 mt-6" :style="cardStyle">
      <h2 class="text-lg font-semibold mb-4" :style="{ color: 'var(--color-text-primary)' }">Neustart</h2>
      <p class="text-sm mb-4" :style="{ color: 'var(--color-text-secondary)' }">
        Startet das Betriebssystem neu. Aktive Nutzer werden getrennt.
        Updates gibt es im Bereich <router-link to="/updates" class="link">System → Updates</router-link>.
      </p>
      <button
        @click="rebootSystem"
        :disabled="rebooting"
        class="w-full text-white py-2 rounded disabled:opacity-50 hover:opacity-90"
        :style="{ backgroundColor: 'var(--color-danger)' }"
      >
        {{ rebooting ? 'Neustart wird ausgelöst...' : 'System neu starten' }}
      </button>
      <p v-if="rebootMessage" class="text-xs mt-2" :style="{ color: rebootError ? 'var(--color-danger)' : 'var(--color-success)' }">
        {{ rebootMessage }}
      </p>
    </div>
    
    <!-- Passwort ändern -->
    <div v-if="activeTab === 'password'" class="rounded-lg shadow p-6 mt-6" :style="cardStyle">
      <h2 class="text-lg font-semibold mb-4" :style="{ color: 'var(--color-text-primary)' }">Passwort ändern</h2>
      <form @submit.prevent="changePassword" class="max-w-md space-y-4">
        <div>
          <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Aktuelles Passwort</label>
          <input v-model="currentPassword" type="password" required autocomplete="current-password" class="w-full rounded px-3 py-2" :style="inputStyle">
          <p class="text-xs mt-1" :style="{ color: 'var(--color-text-muted)' }">
            Wer sein eigenes Passwort ändert, muss das alte nennen — eine offene
            Sitzung allein reicht nicht.
          </p>
        </div>
        <div>
          <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Neues Passwort</label>
          <input v-model="newPassword" type="password" required minlength="8" autocomplete="new-password" class="w-full rounded px-3 py-2" :style="inputStyle">
          <p class="text-xs mt-1" :style="{ color: 'var(--color-text-muted)' }">
            Mindestens 8 Zeichen, mit Groß- und Kleinbuchstabe und einer Ziffer.
          </p>
        </div>
        <div>
          <label class="block text-sm font-medium mb-1" :style="{ color: 'var(--color-text-secondary)' }">Passwort bestätigen</label>
          <input v-model="confirmPassword" type="password" required minlength="8" autocomplete="new-password" class="w-full rounded px-3 py-2" :style="inputStyle">
        </div>
        <button type="submit" class="text-white px-4 py-2 rounded hover:opacity-90" :style="{ backgroundColor: 'var(--color-primary)' }">
          Passwort ändern
        </button>
      </form>
    </div>

    <!-- ================================================================
         Eingebettete Bereiche
         Das sind eigenstaendige Ansichten, die hier als Reiter haengen -
         frueher hatte jede davon einen eigenen Menuepunkt. Sie werden erst
         geladen, wenn der Reiter wirklich geoeffnet wird.
         ================================================================ -->
    <div v-if="activeTab === 'security'" class="embedded-panel">
      <SecuritySettings />
    </div>

    <div v-if="activeTab === 'branding'" class="embedded-panel">
      <BrandingSettings />
    </div>

    <div v-if="authStore.isAdmin && activeTab === 'ldap'" class="embedded-panel">
      <LdapSettings />
    </div>

    <div v-if="authStore.isAdmin && activeTab === 'archiving'" class="embedded-panel">
      <ArchivingSettings />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch, defineAsyncComponent } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

// Erst laden, wenn der Reiter geoeffnet wird - sonst zieht jede Einstellungsseite
// vier weitere Ansichten mit.
const SecuritySettings = defineAsyncComponent(() => import('./SecuritySettings.vue'))
const BrandingSettings = defineAsyncComponent(() => import('./BrandingSettings.vue'))
const LdapSettings = defineAsyncComponent(() => import('./LdapSettings.vue'))
const ArchivingSettings = defineAsyncComponent(() => import('./ArchivingSettings.vue'))

const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()

// Reiter, nach Themen gruppiert. Die id ist zugleich der Teil in der Adresse
// (/settings/ldap) - damit funktionieren die frueheren Links weiter.
// Die Reiter sind nach denselben Fragen gruppiert wie das linke Menue - wer
// dort "Zugang & Sicherheit" anklickt, findet die Reiter hier unter derselben
// Ueberschrift wieder. Vorher hiessen die Gruppen anders als die Menuepunkte,
// und man musste zweimal suchen.
const tabGroups = [
  {
    title: 'Daten & Aufbewahrung',
    tabs: [
      { id: 'retention', label: 'Aufbewahrung' },
      { id: 'archiving', label: 'Archivierung', adminOnly: true },
      { id: 'database', label: 'Datenbank', adminOnly: true },
    ],
  },
  {
    title: 'Zugang & Sicherheit',
    tabs: [
      { id: 'password', label: 'Passwort' },
      { id: 'security', label: 'Anmeldesicherheit' },
      { id: 'ldap', label: 'Verzeichnis (LDAP)', adminOnly: true },
      { id: 'agents', label: 'Zugangsschlüssel', adminOnly: true },
    ],
  },
  {
    title: 'System',
    tabs: [
      { id: 'general', label: 'Allgemein' },
      { id: 'network', label: 'Netzwerk', adminOnly: true },
      { id: 'maintenance', label: 'Neustart', adminOnly: true },
      { id: 'branding', label: 'Erscheinungsbild' },
    ],
  },
]

const tabs = tabGroups.flatMap(group => group.tabs)
const activeTab = ref('general')
const tabLoaded = ref({ network: false, database: false })
const networkSubTabs = [
  { id: 'caddy', label: 'Reverse Proxy' },
  { id: 'dns', label: 'DNS' }
]
const networkSubTab = ref('caddy')

const settings = ref({
  app_name: 'RIMPERIO',
  timezone: 'Europe/Berlin',
  agent_offline_timeout: 300,
  max_logs_per_request: 1000,
  enable_auto_discovery: true
})

const retentionDays = ref(90)
const retentionInfoDays = ref(0)
const retentionSaving = ref(false)
const retentionMessage = ref('')
const retentionPreview = ref(null)

/** Aufbewahrung speichern - gilt ab jetzt stündlich, nicht erst bei voller Platte. */
async function saveRetention() {
  retentionSaving.value = true
  retentionMessage.value = ''
  try {
    if (retentionInfoDays.value && retentionInfoDays.value >= retentionDays.value) {
      throw new Error('Die Frist für INFO/DEBUG muss kürzer sein als die allgemeine (oder 0).')
    }
    await authStore.api('/api/settings/log_retention_days', {
      method: 'PUT', body: { value: retentionDays.value },
    })
    await authStore.api('/api/settings/log_retention_days_info', {
      method: 'PUT', body: { value: retentionInfoDays.value || 0 },
    })
    retentionMessage.value = '✓ Gespeichert – wird ab jetzt stündlich durchgesetzt.'
  } catch (e) {
    retentionMessage.value = 'Fehler: ' + e.message
  } finally {
    retentionSaving.value = false
  }
}
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const dbSettings = ref(null)
const dbError = ref('')
const dbCopyMessage = ref('')
const showDbPassword = ref(false)
// Agent Token (Single)
const agentToken = ref(null)
const tokenMessage = ref('')
const tokenError = ref('')
const tokenLoading = ref(false)
const saving = ref(false)
const saveMessage = ref('')
const saveError = ref(false)
const deletingAllLogs = ref(false)
const deleteAllMessage = ref('')
const deleteAllError = ref(false)
const compactingLogs = ref(false)
const compactMessage = ref('')
const compactError = ref(false)
const rebooting = ref(false)
const rebootMessage = ref('')
const rebootError = ref(false)
// Caddy / Reverse Proxy
const caddyLoading = ref(false)
const caddyMessage = ref('')
const caddyError = ref('')
const caddyfile = ref('')
const caddyDomain = ref('rimperio.example.com')
const caddyMode = ref('custom') // 'http' | 'letsencrypt' | 'custom'
const caddyEmail = ref('')
const caddyCertPresent = ref(false)
const showHttpHint = ref(false)
const httpLink = ref('')
const httpCopyMessage = ref('')
// DNS
const dnsMode = ref('dhcp')
const dnsServers = ref([''])
const dnsSearchInput = ref('')
const dnsDetected = ref([])
const dnsActive = ref([])
const dnsLoading = ref(false)
const dnsMessage = ref('')
const dnsError = ref('')
const dnsTestHost = ref('')
const dnsTesting = ref(false)
const dnsTestResult = ref(null)
let certUpload = { cert: null, key: null }
// Zusaetzliche HTTPS-Adressen (IPs/Namen) als Eingabefeld, Leerzeichen-getrennt
const caddyExtraHostsInput = ref('')
const caddyWarnings = ref([])

const caddyExtraHosts = computed(() =>
  caddyExtraHostsInput.value.split(/[\s,]+/).map(s => s.trim()).filter(Boolean)
)

const availableTabs = computed(() => tabs.filter(t => !t.adminOnly || authStore.isAdmin))

const availableGroups = computed(() =>
  tabGroups
    .map(group => ({
      title: group.title,
      tabs: group.tabs.filter(t => !t.adminOnly || authStore.isAdmin),
    }))
    .filter(group => group.tabs.length)
)

const tlsHint = computed(() => {
  if (caddyMode.value === 'http') return 'Nur HTTP (Port 80). Kein Zertifikat nötig.'
  if (caddyMode.value === 'letsencrypt') return 'Let’s Encrypt besorgt das Zertifikat automatisch (FQDN + 80/443 öffentlich erreichbar, E-Mail erforderlich).'
  if (caddyMode.value === 'internal') return 'Caddy stellt ein eigenes Zertifikat aus. Funktioniert ohne Internet, der Browser warnt einmalig.'
  return caddyCertPresent.value
    ? 'Eigenes Zertifikat gefunden. Wird für HTTPS genutzt.'
    : 'Eigenes Zertifikat wählen und speichern, dann Apply.'
})
const fqdnRegex = /^[A-Za-z0-9]([-A-Za-z0-9]*\.)+[A-Za-z]{2,}$/
const isFqdnValid = computed(() => {
  if (caddyMode.value === 'http') return true
  const d = (caddyDomain.value || '').trim()
  return fqdnRegex.test(d)
})

// Automatisch auf sicheres Template umschalten, wenn der Modus gewechselt wird
watch(caddyMode, async (mode) => {
  // FQDN ist für HTTPS erforderlich, bei HTTP bewusst leer halten
  if (mode === 'http') {
    caddyDomain.value = ''
    caddyEmail.value = ''
    showHttpHint.value = false
    httpLink.value = ''
    httpCopyMessage.value = ''
  }
  await buildCaddyTemplate()
})

// Aendert sich FQDN, E-Mail oder eine Zusatzadresse, wird die Vorlage neu gebaut -
// verzoegert, damit nicht bei jedem Tastendruck eine Anfrage rausgeht.
let templateTimer = null
function scheduleTemplateRebuild() {
  clearTimeout(templateTimer)
  templateTimer = setTimeout(() => { buildCaddyTemplate() }, 400)
}

watch(caddyDomain, (val) => {
  if (caddyMode.value !== 'http' && val) scheduleTemplateRebuild()
})
watch(caddyEmail, () => {
  if (caddyMode.value === 'letsencrypt') scheduleTemplateRebuild()
})
watch(caddyExtraHostsInput, () => {
  if (caddyMode.value !== 'http') scheduleTemplateRebuild()
})

// Computed Styles
const cardStyle = computed(() => ({
  backgroundColor: 'var(--color-surface)',
  borderColor: 'var(--color-border)'
}))

const inputStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  borderColor: 'var(--color-border)',
  color: 'var(--color-text-primary)',
  border: '1px solid var(--color-border)'
}))

const buttonSecondaryStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  color: 'var(--color-text-primary)',
  border: '1px solid var(--color-border)'
}))

const warningBoxStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  border: '1px solid var(--color-warning)'
}))

const dangerButtonStyle = computed(() => ({
  backgroundColor: 'var(--color-surface-elevated)',
  color: 'var(--color-danger)',
  border: '1px solid var(--color-danger)'
}))

onMounted(async () => {
  try {
    const data = await authStore.api('/api/settings')
    settings.value = { ...settings.value, ...data.settings }
    if (data.settings.log_retention_days) {
      retentionDays.value = data.settings.log_retention_days
    }
    if (data.settings.log_retention_days_info !== undefined) {
      retentionInfoDays.value = Number(data.settings.log_retention_days_info) || 0
    }
    if (authStore.isAdmin) {
      // lazy: nur laden, wenn Tab geöffnet wird
    }
    await loadAgentToken()
  } catch (e) {
    console.error('Fehler:', e)
  }
})

function tabButtonStyle(id) {
  const isActive = activeTab.value === id
  return {
    backgroundColor: isActive ? 'var(--color-primary)' : 'var(--color-surface-elevated)',
    color: isActive ? '#fff' : 'var(--color-text-primary)',
    border: '1px solid var(--color-border)'
  }
}

function subTabButtonStyle(id) {
  const isActive = networkSubTab.value === id
  return {
    backgroundColor: isActive ? 'var(--color-primary)' : 'var(--color-surface-elevated)',
    color: isActive ? '#fff' : 'var(--color-text-primary)',
    border: '1px solid var(--color-border)'
  }
}

/** Reiter wechseln und dabei die Adresse mitziehen (/settings/<id>). */
async function setTab(id) {
  await openTab(id)
  const target = id === 'general' ? '/settings' : `/settings/${id}`
  if (route.path !== target) router.push(target)
}

/** Reiter oeffnen ohne die Adresse anzufassen (fuer den Weg von aussen). */
async function openTab(id) {
  if (!availableTabs.value.some(tab => tab.id === id)) id = 'general'
  activeTab.value = id
  if (id === 'database' && authStore.isAdmin && !tabLoaded.value.database) {
    await loadDatabaseSettings()
    tabLoaded.value.database = true
  }
  if (id === 'network' && authStore.isAdmin && !tabLoaded.value.network) {
    await loadCaddyConfig()
    await loadDnsConfig()
    tabLoaded.value.network = true
  }
}

// Adresse -> Reiter. Greift beim ersten Aufruf und bei Vor/Zurueck im Browser.
watch(
  () => route.params.tab,
  (tab) => {
    const wanted = String(tab || 'general')
    if (wanted !== activeTab.value) openTab(wanted)
  },
  { immediate: true }
)

async function loadDatabaseSettings() {
  dbError.value = ''
  try {
    dbSettings.value = await authStore.api('/api/settings/database')
  } catch (e) {
    dbError.value = e.message
  }
}

async function loadAgentToken() {
  tokenLoading.value = true
  tokenError.value = ''
  tokenMessage.value = ''
  try {
    let list = await authStore.api('/api/agent-tokens')
    if (!Array.isArray(list)) list = []
    const preferred = list.find(t => t.name === 'global-agent')
    if (!preferred) {
      const created = await authStore.api('/api/agent-tokens', {
        method: 'POST',
        body: { name: 'global-agent' }
      })
      agentToken.value = created
      tokenMessage.value = 'Standard-Token erstellt'
    } else {
      agentToken.value = preferred
    }
  } catch (e) {
    tokenError.value = e.message || 'Token laden fehlgeschlagen'
  } finally {
    tokenLoading.value = false
  }
}

async function loadCaddyConfig() {
  caddyLoading.value = true
  caddyError.value = ''
  caddyMessage.value = ''
  try {
    const res = await authStore.api('/api/caddy/config')
    caddyfile.value = res.saved_caddyfile || ''
    caddyCertPresent.value = !!res.cert_present
  } catch (e) {
    caddyError.value = e.message || 'Caddy-Konfiguration konnte nicht geladen werden'
  } finally {
    caddyLoading.value = false
  }
}

async function loadDnsConfig() {
  dnsError.value = ''
  try {
    const res = await authStore.api('/api/network/dns')
    dnsMode.value = res.mode || 'dhcp'
    dnsServers.value = (res.configured && res.configured.length) ? [...res.configured] : ['']
    dnsSearchInput.value = (res.search_domains || []).join(' ')
    dnsDetected.value = res.detected || []
    dnsActive.value = res.active || []
    if (!dnsTestHost.value) dnsTestHost.value = caddyDomain.value || ''
  } catch (e) {
    dnsError.value = e.message || 'DNS-Konfiguration konnte nicht geladen werden'
  }
}

async function applyDns() {
  dnsLoading.value = true
  dnsError.value = ''
  dnsMessage.value = ''
  try {
    const servers = dnsMode.value === 'manual'
      ? dnsServers.value.map(s => s.trim()).filter(Boolean)
      : []
    const search = dnsSearchInput.value.split(/\s+/).map(s => s.trim()).filter(Boolean)
    const res = await authStore.api('/api/network/dns', {
      method: 'PUT',
      body: { mode: dnsMode.value, servers, search_domains: search }
    })
    dnsDetected.value = res.detected || []
    dnsActive.value = res.active || []
    dnsMessage.value = 'DNS angewendet.'
  } catch (e) {
    dnsError.value = e.message || 'DNS konnte nicht angewendet werden'
  } finally {
    dnsLoading.value = false
  }
}

async function testDns() {
  if (!dnsTestHost.value) return
  dnsTesting.value = true
  dnsTestResult.value = null
  try {
    dnsTestResult.value = await authStore.api('/api/network/dns/test', {
      method: 'POST',
      body: { host: dnsTestHost.value.trim() }
    })
  } catch (e) {
    dnsTestResult.value = { resolved: false, error: e.message }
  } finally {
    dnsTesting.value = false
  }
}

async function buildCaddyTemplate() {
  caddyError.value = ''
  caddyWarnings.value = []
  if (caddyMode.value !== 'http' && !isFqdnValid.value) {
    // Beim Tippen im FQDN-Feld ist das der Normalzustand - nicht als Fehler anzeigen.
    return
  }
  try {
    // Die Vorlage kommt immer vom Backend, damit UI und Server nicht auseinanderlaufen.
    const res = await authStore.api('/api/caddy/template', {
      method: 'POST',
      body: {
        domain: caddyMode.value === 'http' ? null : caddyDomain.value,
        mode: caddyMode.value,
        letsencrypt_email: caddyMode.value === 'letsencrypt' ? caddyEmail.value : null,
        extra_hosts: caddyMode.value === 'http' ? [] : caddyExtraHosts.value,
      }
    })
    caddyfile.value = res.caddyfile
    caddyWarnings.value = res.warnings || []
    caddyMessage.value = 'Vorlage eingefügt. Angewendet wird genau dieser Text.'
  } catch (e) {
    caddyError.value = e.message
  }
}

/** Uebernimmt die Adresse, ueber die gerade zugegriffen wird (praktisch bei IP-Zugriff). */
function useCurrentAddress() {
  const current = window.location.hostname
  if (!current) return
  if (!caddyExtraHosts.value.includes(current)) {
    caddyExtraHostsInput.value = [...caddyExtraHosts.value, current].join(' ')
  }
}

async function resetCaddy() {
  if (!confirm('Reverse Proxy auf reines HTTP (Port 80) zurücksetzen?')) return
  caddyLoading.value = true
  caddyError.value = ''
  caddyMessage.value = ''
  caddyWarnings.value = []
  try {
    const res = await authStore.api('/api/caddy/reset', { method: 'POST' })
    caddyfile.value = res.caddyfile || ''
    caddyMode.value = 'http'
    caddyMessage.value = 'Zurückgesetzt: Oberfläche ist über http://<Adresse> erreichbar.'
  } catch (e) {
    caddyError.value = e.message
  } finally {
    caddyLoading.value = false
  }
}

async function uploadCaddyCert() {
  if (!certUpload.cert || !certUpload.key) {
    caddyError.value = 'Bitte Zertifikat UND Key auswählen.'
    return
  }
  caddyError.value = ''
  caddyMessage.value = ''
  const form = new FormData()
  form.append('cert_file', certUpload.cert)
  form.append('key_file', certUpload.key)
  try {
    const res = await authStore.api('/api/caddy/certificates', {
      method: 'POST',
      body: form
    })
    caddyCertPresent.value = true
    caddyMessage.value = res.message + (res.not_after ? ` Gültig bis ${res.not_after}.` : '')
  } catch (e) {
    caddyError.value = e.message
  }
}

async function applyCaddy() {
  if (caddyMode.value !== 'http' && !caddyDomain.value) {
    caddyError.value = 'FQDN ist für HTTPS erforderlich.'
    return
  }
  if (caddyMode.value !== 'http' && !isFqdnValid.value) {
    caddyError.value = 'FQDN ist ungültig (z.B. rimperio.example.com).'
    return
  }
  if (caddyMode.value === 'letsencrypt' && !caddyEmail.value) {
    caddyError.value = 'E-Mail für Let\'s Encrypt fehlt.'
    return
  }
  if (caddyMode.value === 'custom' && !caddyCertPresent.value) {
    caddyError.value = 'Custom-Zertifikat hochladen, bevor du es aktivierst.'
    return
  }
  // Ohne Editor-Inhalt zuerst eine Vorlage bauen, sonst gäbe es nichts anzuwenden.
  if (!caddyfile.value.trim()) {
    await buildCaddyTemplate()
  }
  if (!caddyfile.value.trim()) {
    caddyError.value = 'Es gibt keine Konfiguration zum Anwenden.'
    return
  }
  if (!confirm('Apply & Reload Caddy jetzt ausführen?')) return

  caddyLoading.value = true
  caddyError.value = ''
  caddyMessage.value = ''
  caddyWarnings.value = []
  try {
    // Bewusst OHNE mode/domain: angewendet wird exakt der Editor-Inhalt.
    // Sonst hätte der Server aus dem Modus eine neue Datei gebaut und
    // manuelle Änderungen im Editor stillschweigend verworfen.
    const res = await authStore.api('/api/caddy/apply', {
      method: 'POST',
      body: {
        caddyfile: caddyfile.value,
        save: true,
      }
    })
    caddyWarnings.value = res.warnings || []
    caddyMessage.value = 'Caddy neu geladen. Port 80 bleibt als Zugang erhalten.'
    if (caddyMode.value === 'http' && window.location.protocol === 'https:') {
      // Wer gerade per HTTPS verbunden ist, verliert diesen Weg - Link anbieten,
      // aber nicht mehr automatisch umleiten (das riss frueher die Sitzung mitten
      // im Speichern ab).
      httpLink.value = `http://${window.location.hostname}/settings`
      showHttpHint.value = true
      httpCopyMessage.value = ''
      caddyMessage.value = 'HTTP-Modus aktiv. Diese Seite läuft noch über HTTPS – bitte über den Link unten wechseln.'
    }
  } catch (e) {
    caddyError.value = e.message
  } finally {
    caddyLoading.value = false
  }
}

async function copyDbPassword() {
  if (!dbSettings.value?.password) return
  await navigator.clipboard.writeText(dbSettings.value.password)
  dbCopyMessage.value = 'Passwort kopiert'
  setTimeout(() => { dbCopyMessage.value = '' }, 2000)
}

async function regenerateAgentToken() {
  if (!agentToken.value) return
  if (!confirm(`Agent-Token "${agentToken.value.name}" regenerieren?`)) return
  tokenLoading.value = true
  tokenMessage.value = ''
  tokenError.value = ''
  try {
    const updated = await authStore.api(`/api/agent-tokens/${agentToken.value.id}/regenerate`, { method: 'POST' })
    agentToken.value = updated
    tokenMessage.value = 'Token regeneriert'
  } catch (e) {
    tokenError.value = e.message || 'Fehler beim Regenerieren'
  } finally {
    tokenLoading.value = false
  }
}

async function copyAgentToken() {
  if (!agentToken.value?.token) return
  try {
    await navigator.clipboard.writeText(agentToken.value.token)
    tokenMessage.value = 'Token kopiert'
    tokenError.value = ''
  } catch (e) {
    tokenError.value = 'Kopieren fehlgeschlagen'
  }
}

async function saveAllSettings() {
  saving.value = true
  saveMessage.value = ''
  saveError.value = false
  
  try {
    const settingsToSave = [
      ['app_name', settings.value.app_name],
      ['timezone', settings.value.timezone],
      ['agent_offline_timeout', settings.value.agent_offline_timeout],
      ['max_logs_per_request', settings.value.max_logs_per_request],
      ['enable_auto_discovery', settings.value.enable_auto_discovery],
      ['log_retention_days', retentionDays.value]
    ]
    
    for (const [key, value] of settingsToSave) {
      await authStore.api(`/api/settings/${key}`, {
        method: 'PUT',
        body: { value }
      })
    }
    
    saveMessage.value = '✓ Einstellungen gespeichert!'
    setTimeout(() => { saveMessage.value = '' }, 3000)
  } catch (e) {
    saveError.value = true
    saveMessage.value = 'Fehler: ' + e.message
  } finally {
    saving.value = false
  }
}

async function previewRetention() {
  try {
    retentionPreview.value = await authStore.api(`/api/settings/retention/preview?days=${retentionDays.value}`, {
      method: 'POST'
    })
  } catch (e) {
    alert('Fehler: ' + e.message)
  }
}

async function executeRetention() {
  if (!confirm(`Wirklich ${retentionPreview.value.logs_to_delete.toLocaleString()} Logs löschen?`)) return
  
  try {
    const result = await authStore.api(`/api/settings/retention/execute?days=${retentionDays.value}`, {
      method: 'POST'
    })
    alert(result.message)
    retentionPreview.value = null
  } catch (e) {
    alert('Fehler: ' + e.message)
  }
}

async function deleteAllLogs() {
  const confirm1 = prompt('Diese Aktion löscht ALLE Logs! Geben Sie "DELETE_ALL_LOGS" ein, um zu bestätigen:')
  if (confirm1 !== 'DELETE_ALL_LOGS') return
  
  deletingAllLogs.value = true
  deleteAllMessage.value = ''
  deleteAllError.value = false
  try {
    const result = await authStore.api('/api/settings/logs/all?confirm=DELETE_ALL_LOGS', {
      method: 'DELETE'
    })
    deleteAllMessage.value = result?.message || 'Alle Logs wurden gelöscht.'
  } catch (e) {
    deleteAllError.value = true
    deleteAllMessage.value = 'Fehler: ' + e.message
  }
  deletingAllLogs.value = false
}

async function compactLogs() {
  const confirm1 = prompt('VACUUM FULL sperrt die Tabelle kurz. Geben Sie "COMPACT_LOGS" ein, um zu bestätigen:')
  if (confirm1 !== 'COMPACT_LOGS') return

  compactingLogs.value = true
  compactMessage.value = ''
  compactError.value = false
  try {
    const result = await authStore.api('/api/settings/logs/compact?confirm=COMPACT_LOGS', {
      method: 'POST'
    })
    compactMessage.value = result?.message || 'VACUUM FULL abgeschlossen.'
  } catch (e) {
    compactError.value = true
    compactMessage.value = 'Fehler: ' + e.message
  }
  compactingLogs.value = false
}

async function rebootSystem() {
  if (!confirm('System jetzt neu starten? Alle Benutzer werden getrennt.')) return
  
  rebooting.value = true
  rebootMessage.value = ''
  rebootError.value = false
  
  try {
    const result = await authStore.api('/api/settings/reboot', { method: 'POST' })
    rebootMessage.value = result?.message || 'Neustart ausgelöst.'
  } catch (e) {
    rebootError.value = true
    rebootMessage.value = 'Fehler: ' + e.message
  } finally {
    rebooting.value = false
  }
}

async function changePassword() {
  if (newPassword.value !== confirmPassword.value) {
    alert('Passwörter stimmen nicht überein!')
    return
  }
  
  if (!currentPassword.value) {
    alert('Bitte das aktuelle Passwort eingeben.')
    return
  }

  try {
    // Das aktuelle Passwort muss mit: eine gültige Sitzung allein reicht nicht
    // mehr aus, um das Passwort zu setzen. Sonst könnte jemand, der sich einen
    // Token beschafft hat, den rechtmäßigen Besitzer aussperren.
    await authStore.api(`/api/users/${authStore.user.id}`, {
      method: 'PUT',
      body: {
        current_password: currentPassword.value,
        password: newPassword.value,
      }
    })
    alert('Passwort geändert!')
    currentPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
  } catch (e) {
    alert('Fehler: ' + e.message)
  }
}

function formatDate(timestamp) {
  if (!timestamp) return '-'
  return new Date(timestamp).toLocaleString('de-DE')
}

async function copyHttpLink() {
  if (!httpLink.value) return
  try {
    await navigator.clipboard.writeText(httpLink.value)
    httpCopyMessage.value = 'Link kopiert'
    setTimeout(() => { httpCopyMessage.value = '' }, 2000)
  } catch (e) {
    httpCopyMessage.value = 'Kopieren fehlgeschlagen'
    setTimeout(() => { httpCopyMessage.value = '' }, 2000)
  }
}
</script>

<style scoped>
.tab-group-label {
  min-width: 9rem;
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--color-text-muted);
}

@media (max-width: 640px) {
  .tab-group-label {
    min-width: 100%;
  }
}

.embedded-panel {
  margin-top: 1.5rem;
}

/* Die eingebetteten Ansichten bringen ihren eigenen Seitenrand mit. Innerhalb
   dieser Seite ist der doppelt - deshalb nur beim aeussersten Element weg. */
.embedded-panel > :deep(.page),
.embedded-panel > :deep(.p-6) {
  padding: 0;
  max-width: none;
}
</style>
