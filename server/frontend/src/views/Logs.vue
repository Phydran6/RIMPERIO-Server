<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Version:     2026.08.02.14.00.00
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Log-Ansicht ueber alle Hosts.
                   Filter/Tabelle/Export stecken in components/LogTable.vue und
                   werden mit der Geraete-Ansicht (DeviceLogs.vue) geteilt.
     ============================================================================== -->

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-title">Logs</h2>
        <p class="page-subtitle">Alle Geräte · Filter stehen in der Adresszeile und lassen sich teilen</p>
      </div>
      <router-link to="/agents" class="btn btn-secondary btn-sm">Geräte-Übersicht →</router-link>
    </div>

    <LogTable sync-url />
  </div>
</template>

<script setup>
import LogTable from '../components/LogTable.vue'
</script>
