<!--
  Datei:        App.vue
  Projekt:      RIMPERIO — Frontend
  Autor:        Phydran6
  Version:      2026.10.31.15.40.00
  Beschreibung: Wurzelkomponente. Lädt bei vorhandener Sitzung den Benutzer.
-->
<template>
  <router-view />
</template>

<script setup>
import { onMounted } from 'vue'
import { useAuth } from './stores/auth'

const auth = useAuth()
onMounted(() => auth.fetchMe())
</script>
