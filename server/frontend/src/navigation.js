// ==============================================================================
// Name:        Phydran6
// Kontakt:     Phydran6
// Version:     2026.11.26.22.00.00
// Changelog:   docs/changelog/frontend.md
// Beschreibung: RIMPERIO - Das linke Menue. Eine Stelle fuer alle Menuepunkte.
//
// Vorher war RIMPERIO ein Log-Server mit angebauter Fernwartung, und das Menue
// sah auch so aus: "Geraete" gab es zweimal (Log-Geraete und RMM-Rechner),
// Einstellungen lagen teils links, teils als Reiterleiste in der Seite, teils
// als Unter-Reiter darin, und manche Seite (Zugangsschluessel) war gar nicht
// erreichbar.
//
// Jetzt: ein RMM. Das Menue folgt der Arbeit, die man damit macht:
//
//   Uebersicht         Wie steht es um alles?
//   Geraete            Was verwalte ich?            Rechner, SNMP-Geraete, Netzwerksuche,
//                                                   Proxmox VE (anbinden, Knoten patchen),
//                                                   NAS (Synology per HTTPS) & Raspberry Pi
//   Logs               Was protokollieren sie?      Ansicht, Quellen, Aufbewahrung
//   Ueberwachung       Was ist kaputt?              Alarme, Checks
//   Patchmanagement    Was muss aktualisiert werden?
//   Backup             Was wird wohin gesichert?    Auftraege, Orte, Verlauf, RIMPERIO selbst
//   Integrationen      Was ist angebunden?          Anmeldung (Microsoft 365, AD/LDAP),
//                                                   E-Mail (Postfix, SMTP, M365), KI-Anbieter,
//                                                   Zugangsdaten (Vaultwarden)
//   Automatisierung    Was laeuft von selbst?       Skripte, Webhooks
//   Benutzer           Wer darf rein?               Benutzer
//   Mein Konto         Meine eigene Anmeldung
//   RIMPERIO-Server    Die Maschine, auf der das laeuft
//   Einstellungen      Wie ist RIMPERIO eingestellt?
//
// Jeder Menuepunkt ist genau EINE Seite. Es gibt keine Reiterleisten mehr, die
// zu anderen Menuepunkten fuehren - Reiter innerhalb eines Werkzeugs (etwa
// "Freigaben" im Patch-Center) bleiben, die gehoeren zum Werkzeug.
//
// Felder eines Eintrags:
//   to      Ziel-Adresse
//   name    Name der Route (fuer "aktiv")
//   tab     bei name 'Settings': welcher Abschnitt der Einstellungsseite
//   match   weitere Routen, bei denen der Eintrag aktiv ist (Detailseiten)
//   admin   nur fuer Administratoren
//   group   Untermenue innerhalb des Bereichs (i18n nav.<group>); Eintraege
//           mit gleicher Gruppe stehen zusammen unter einer aufklappbaren Zeile
//   label   Schluessel in i18n (nav.<label>)
// ==============================================================================

export const NAV_SECTIONS = [
  {
    key: 'overview',
    flat: true,            // ohne Ueberschrift - steht ganz oben
    items: [
      { key: 'dashboard', to: '/', name: 'Dashboard', icon: 'dashboard', label: 'overview' },
    ],
  },
  {
    // Alles, was RIMPERIO verwaltet - auch die Proxmox-Hosts selbst: anbinden,
    // Gaeste zuordnen, Knoten patchen, Konsole.
    key: 'devices',
    title: 'secDevices',
    icon: 'devices',
    items: [
      { key: 'computers', to: '/rmm', name: 'RmmAgents', match: ['RmmAgentDetail'], icon: 'devices', label: 'computers' },
      { key: 'snmp', to: '/rmm/snmp', name: 'RmmSnmp', match: ['RmmSnmpDevice'], icon: 'router', label: 'snmp' },
      { key: 'discovery', to: '/discovery', name: 'Discovery', icon: 'network', label: 'discovery', admin: true },
      { key: 'agent-tokens', to: '/agent-tokens', name: 'AgentTokens', icon: 'key', label: 'agentTokens', admin: true },
      { key: 'proxmox', group: 'grpProxmox', to: '/devices/proxmox', name: 'RmmProxmox', icon: 'backup', label: 'proxmox' },
      { key: 'pve-patching', group: 'grpProxmox', to: '/devices/proxmox/patching', name: 'PveUpdates', icon: 'download', label: 'pveUpdates' },
      { key: 'nas', group: 'grpNas', to: '/devices/nas', name: 'NasDevices', match: ['NasDetail'], icon: 'archive', label: 'nas' },
      { key: 'special', group: 'grpNas', to: '/devices/special', name: 'SpecialSystems', icon: 'devices', label: 'special' },
    ],
  },
  {
    // Logs sind ein eigenes, großes Thema: Quellen, Ansicht, Aufbewahrung.
    key: 'logs',
    title: 'secLogs',
    icon: 'logs',
    items: [
      { key: 'logs', to: '/logs', name: 'Logs', icon: 'logs', label: 'logs' },
      { key: 'log-handling', to: '/logs/handling', name: 'LogHandling', icon: 'filter', label: 'logHandling' },
      { key: 'log-sources', to: '/agents', name: 'Agents', match: ['DeviceLogs'], icon: 'agents', label: 'logSources' },
      { key: 'retention', to: '/settings/retention', name: 'Settings', tab: 'retention', icon: 'clock', label: 'retention' },
      { key: 'archiving', to: '/settings/archiving', name: 'Settings', tab: 'archiving', icon: 'archive', label: 'archiving', admin: true },
    ],
  },
  {
    key: 'monitoring',
    title: 'secMonitoring',
    icon: 'bell',
    items: [
      { key: 'alarms', to: '/rmm/alerts', name: 'RmmAlerts', icon: 'warning', label: 'alarms' },
      { key: 'checks', to: '/rmm/checks', name: 'RmmChecks', icon: 'check', label: 'checks' },
    ],
  },
  {
    key: 'patching',
    title: 'secPatching',
    icon: 'download',
    items: [
      { key: 'patch-center', to: '/rmm/patches', name: 'RmmPatches', icon: 'download', label: 'patchCenter' },
      { key: 'db-updates', to: '/rmm/db-updates', name: 'RmmDbUpdates', icon: 'database', label: 'dbUpdates' },
      { key: 'policies', to: '/rmm/policies', name: 'RmmPolicies', icon: 'clock', label: 'policies' },
      { key: 'sources', to: '/rmm/sources', name: 'RmmSources', icon: 'globe', label: 'sources' },
    ],
  },
  {
    // Backup ist ein eigener Bereich: WAS gesichert wird (Auftraege: RIMPERIO,
    // Ordner auf Rechnern/Raspberry Pis, NAS-Konfiguration, Hyper Backup,
    // Proxmox), WOHIN (Orte: Ordner am Server, SMB-Freigabe am NAS, SFTP, FTP)
    // und was dabei herauskam (Verlauf, Wiederherstellen).
    key: 'backups',
    title: 'secBackups',
    icon: 'backup',
    items: [
      { key: 'backup-jobs', to: '/backups/jobs', name: 'BackupJobs', icon: 'backup', label: 'backupJobs' },
      { key: 'backup-targets', to: '/backups/targets', name: 'BackupTargets', icon: 'archive', label: 'backupTargets' },
      { key: 'backup-runs', to: '/backups/runs', name: 'BackupRuns', icon: 'restore', label: 'backupRuns' },
      { key: 'backup', to: '/backup', name: 'Backup', icon: 'database', label: 'rimperioBackup', admin: true },
    ],
  },
  {
    // Integrationen = alles, was man an RIMPERIO anbindet: Anmeldung ueber
    // Microsoft 365 und Verzeichnisdienste, Mail-Versand (Postfix, SMTP,
    // Microsoft 365), KI-Anbieter. Darunter geht es in Untermenues weiter.
    // Proxmox ist ein Geraet und steht unter "Geraete".
    key: 'integrations',
    title: 'secIntegrations',
    icon: 'stacks',
    admin: true,
    items: [
      { key: 'sso', group: 'grpSignOn', to: '/sso', name: 'SsoSettings', icon: 'shield', label: 'sso', admin: true },
      { key: 'ldap', group: 'grpSignOn', to: '/settings/ldap', name: 'Settings', tab: 'ldap', icon: 'database', label: 'ldap', admin: true },
      { key: 'mail', group: 'grpMail', to: '/mail', name: 'Mail', icon: 'mail', label: 'mail', admin: true },
      { key: 'ai', group: 'grpServices', to: '/ai', name: 'AiSettings', icon: 'ai', label: 'ai', admin: true },
      { key: 'vault', group: 'grpSecrets', to: '/integrations/vault', name: 'VaultSettings', icon: 'key', label: 'vault', admin: true },
    ],
  },
  {
    key: 'automation',
    title: 'secAutomation',
    icon: 'terminal',
    items: [
      { key: 'scripts', to: '/rmm/scripts', name: 'RmmScripts', icon: 'terminal', label: 'scripts' },
      { key: 'webhooks', to: '/webhooks', name: 'Webhooks', icon: 'webhooks', label: 'webhooks' },
    ],
  },
  {
    key: 'access',
    title: 'secAccess',
    icon: 'users',
    admin: true,
    items: [
      { key: 'users', to: '/users', name: 'Users', icon: 'users', label: 'users', admin: true },
      // Nur Betreiber und nur, wenn die Mandantenfaehigkeit da ist (oder um
      // sie einzuschalten): Benutzer → Mandanten.
      { key: 'tenants', to: '/tenants', name: 'Tenants', icon: 'stacks', label: 'tenants', admin: true, provider: true },
    ],
  },
  {
    key: 'account',
    title: 'secAccount',
    icon: 'lock',
    items: [
      { key: 'password', to: '/settings/password', name: 'Settings', tab: 'password', icon: 'key', label: 'password' },
      { key: 'security', to: '/settings/security', name: 'Settings', tab: 'security', icon: 'shield', label: 'security' },
      { key: 'app', to: '/app', name: 'AppQR', icon: 'mobile', label: 'app' },
    ],
  },
  {
    key: 'server',
    title: 'secServer',
    icon: 'health',
    items: [
      { key: 'health', to: '/health', name: 'Health', icon: 'health', label: 'health' },
      { key: 'updates', to: '/updates', name: 'Updates', icon: 'download', label: 'updates', admin: true },
      { key: 'containers', to: '/containers', name: 'Containers', icon: 'container', label: 'containers', admin: true },
      { key: 'stacks', to: '/stacks', name: 'Stacks', icon: 'stacks', label: 'stacks', admin: true },
      { key: 'storage', to: '/storage', name: 'Storage', icon: 'archive', label: 'storage', admin: true },
      { key: 'journal', to: '/journal', name: 'Journal', icon: 'logs', label: 'journal', admin: true },
      { key: 'terminal', to: '/terminal', name: 'Terminal', icon: 'terminal', label: 'console', admin: true },
      { key: 'maintenance', to: '/settings/maintenance', name: 'Settings', tab: 'maintenance', icon: 'power', label: 'maintenance', admin: true },
    ],
  },
  {
    key: 'settings',
    title: 'secSettings',
    icon: 'settings',
    items: [
      { key: 'general', to: '/settings/general', name: 'Settings', tab: 'general', icon: 'settings', label: 'general' },
      { key: 'https', to: '/settings/network', name: 'Settings', tab: 'network', icon: 'lock', label: 'https', admin: true },
      { key: 'dns', to: '/settings/dns', name: 'Settings', tab: 'dns', icon: 'globe', label: 'dns', admin: true },
      { key: 'alerts', to: '/alerts', name: 'Alerts', icon: 'bell', label: 'alerts', admin: true },
      { key: 'branding', to: '/settings/branding', name: 'Settings', tab: 'branding', icon: 'palette', label: 'branding' },
      { key: 'database', to: '/settings/database', name: 'Settings', tab: 'database', icon: 'database', label: 'database', admin: true },
    ],
  },
  {
    key: 'help',
    title: 'help',
    icon: 'help',
    items: [
      { key: 'about', to: '/about', name: 'About', icon: 'help', label: 'about' },
    ],
  },
]

// Was ein Mandanten-Konto (Kunde) im Menue sieht. Alles andere betrifft den
// Server oder die ganze Instanz und bleibt beim Betreiber - der Server sperrt
// es ohnehin (app/tenancy.py, TENANT_PATHS).
export const TENANT_KEYS = new Set([
  'dashboard', 'computers', 'snmp', 'agent-tokens', 'proxmox', 'pve-patching',
  'logs', 'log-sources', 'alarms', 'checks', 'patch-center', 'db-updates', 'policies',
  'sources', 'scripts', 'users', 'password', 'security', 'app', 'about',
  'nas', 'special', 'backup-jobs', 'backup-targets', 'backup-runs',
])

// Routen, die ein Mandanten-Konto aufrufen darf (fuer den Router-Waechter):
// die Menuepunkte oben samt ihren Detailseiten.
export const TENANT_ROUTES = new Set(['Login', 'ChangePassword', 'NotFound'])
export const TENANT_SETTINGS_TABS = new Set(['password', 'security'])
for (const section of NAV_SECTIONS) {
  for (const item of section.items) {
    if (!TENANT_KEYS.has(item.key) || item.name === 'Settings') continue
    TENANT_ROUTES.add(item.name)
    for (const extra of item.match || []) TENANT_ROUTES.add(extra)
  }
}

/** Darf ein Mandanten-Konto diese Route sehen? */
export function tenantMayOpen(route) {
  if (route.name === 'Settings') return TENANT_SETTINGS_TABS.has(String(route.params?.tab || 'general'))
  return TENANT_ROUTES.has(route.name)
}

/**
 * Menue fuer diesen Benutzer: ohne Admin-Punkte, leere Bereiche fallen weg.
 * opts.provider  = Konto ohne Mandant (Betreiber); false = Mandanten-Konto
 */
export function sectionsFor(isAdmin, opts = {}) {
  const provider = opts.provider !== false
  return NAV_SECTIONS
    .filter(section => isAdmin || !section.admin)
    .map(section => ({
      ...section,
      items: section.items.filter(item => (isAdmin || !item.admin)
        && (provider || TENANT_KEYS.has(item.key))
        && (!item.provider || provider)),
    }))
    .filter(section => section.items.length)
}

/**
 * Zeilen eines Bereichs fuer das Menue: Eintraege ohne Gruppe direkt, Eintraege
 * mit Gruppe unter einer Untermenue-Zeile ({type:'group'}). Die Reihenfolge der
 * Gruppen folgt ihrem ersten Eintrag.
 */
export function rowsFor(section) {
  const rows = []
  const groups = new Map()
  for (const item of section.items) {
    if (!item.group) {
      rows.push({ type: 'item', item, key: item.key })
      continue
    }
    if (!groups.has(item.group)) {
      const group = { type: 'group', group: item.group, key: `${section.key}:${item.group}`, items: [] }
      groups.set(item.group, group)
      rows.push(group)
    }
    groups.get(item.group).items.push(item)
  }
  return rows
}

/** Ist dieser Eintrag fuer die aktuelle Route der aktive? */
export function isEntryActive(entry, route) {
  if (entry.tab) {
    // Ohne Abschnitt in der Adresse gilt "general" - /settings ist nie ohne Markierung.
    const current = String(route.params?.tab || 'general')
    return route.name === 'Settings' && current === entry.tab
  }
  if (route.name === entry.name) return true
  return Array.isArray(entry.match) && entry.match.includes(route.name)
}

/** {section, item} fuer die aktuelle Route - fuer Titel, Pfad und Aufklappen. */
export function findActive(sections, route) {
  for (const section of sections) {
    const item = section.items.find(entry => isEntryActive(entry, route))
    if (item) return { section, item }
  }
  return { section: null, item: null }
}
