// ==============================================================================
// Name:        Phydran6
// Kontakt:     Phydran6
// Version:     2026.11.07.23.00.00
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
//   Geraete            Was verwalte ich?            Rechner, Netzwerkgeraete, Log-Quellen
//   Ueberwachung       Was ist kaputt?              Alarme, Checks, Logs
//   Patchmanagement    Was muss aktualisiert werden?
//   Automatisierung    Was laeuft von selbst?       Skripte, Webhooks, KI
//   Benutzer & Zugang  Wer darf rein?
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
    key: 'devices',
    title: 'secDevices',
    icon: 'devices',
    items: [
      { key: 'computers', to: '/rmm', name: 'RmmAgents', match: ['RmmAgentDetail'], icon: 'devices', label: 'computers' },
      { key: 'snmp', to: '/rmm/snmp', name: 'RmmSnmp', match: ['RmmSnmpDevice'], icon: 'router', label: 'snmp' },
      { key: 'log-sources', to: '/agents', name: 'Agents', match: ['DeviceLogs'], icon: 'agents', label: 'logSources' },
      { key: 'discovery', to: '/discovery', name: 'Discovery', icon: 'network', label: 'discovery', admin: true },
      { key: 'agent-tokens', to: '/agent-tokens', name: 'AgentTokens', icon: 'key', label: 'agentTokens', admin: true },
    ],
  },
  {
    key: 'monitoring',
    title: 'secMonitoring',
    icon: 'bell',
    items: [
      { key: 'alarms', to: '/rmm/alerts', name: 'RmmAlerts', icon: 'warning', label: 'alarms' },
      { key: 'checks', to: '/rmm/checks', name: 'RmmChecks', icon: 'check', label: 'checks' },
      { key: 'logs', to: '/logs', name: 'Logs', icon: 'logs', label: 'logs' },
    ],
  },
  {
    key: 'patching',
    title: 'secPatching',
    icon: 'download',
    items: [
      { key: 'patch-center', to: '/rmm/patches', name: 'RmmPatches', icon: 'download', label: 'patchCenter' },
      { key: 'policies', to: '/rmm/policies', name: 'RmmPolicies', icon: 'clock', label: 'policies' },
      { key: 'sources', to: '/rmm/sources', name: 'RmmSources', icon: 'globe', label: 'sources' },
      { key: 'proxmox', to: '/rmm/proxmox', name: 'RmmProxmox', icon: 'backup', label: 'proxmox' },
    ],
  },
  {
    key: 'automation',
    title: 'secAutomation',
    icon: 'terminal',
    items: [
      { key: 'scripts', to: '/rmm/scripts', name: 'RmmScripts', icon: 'terminal', label: 'scripts' },
      { key: 'webhooks', to: '/webhooks', name: 'Webhooks', icon: 'webhooks', label: 'webhooks' },
      { key: 'ai', to: '/ai', name: 'AiSettings', icon: 'ai', label: 'ai', admin: true },
    ],
  },
  {
    key: 'access',
    title: 'secAccess',
    icon: 'users',
    admin: true,
    items: [
      { key: 'users', to: '/users', name: 'Users', icon: 'users', label: 'users', admin: true },
      { key: 'sso', to: '/sso', name: 'SsoSettings', icon: 'shield', label: 'sso', admin: true },
      { key: 'ldap', to: '/settings/ldap', name: 'Settings', tab: 'ldap', icon: 'database', label: 'ldap', admin: true },
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
      { key: 'backup', to: '/backup', name: 'Backup', icon: 'backup', label: 'backup', admin: true },
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
      { key: 'mail', to: '/mail', name: 'Mail', icon: 'mail', label: 'mail', admin: true },
      { key: 'alerts', to: '/alerts', name: 'Alerts', icon: 'bell', label: 'alerts', admin: true },
      { key: 'branding', to: '/settings/branding', name: 'Settings', tab: 'branding', icon: 'palette', label: 'branding' },
      { key: 'retention', to: '/settings/retention', name: 'Settings', tab: 'retention', icon: 'clock', label: 'retention' },
      { key: 'archiving', to: '/settings/archiving', name: 'Settings', tab: 'archiving', icon: 'archive', label: 'archiving', admin: true },
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

/** Menue fuer diesen Benutzer: ohne Admin-Punkte, leere Bereiche fallen weg. */
export function sectionsFor(isAdmin) {
  return NAV_SECTIONS
    .filter(section => isAdmin || !section.admin)
    .map(section => ({ ...section, items: section.items.filter(item => isAdmin || !item.admin) }))
    .filter(section => section.items.length)
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
