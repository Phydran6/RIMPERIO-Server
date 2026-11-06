<!-- ==============================================================================
     Name:        Phydran6
     Kontakt:     Phydran6
     Changelog:   docs/changelog/frontend.md
     Beschreibung: RIMPERIO - Pflicht-Rueckfrage "vorher sichern?" vor Systemeingriffen.

     Jeder Eingriff, der das System veraendert (Update, Rueckfall, Zurueckspielen,
     Zusatzdienst ein/aus), laeuft durch diesen Dialog. Er ist bewusst nicht
     wegklickbar: es gibt genau zwei Wege hinaus, und "nein" ist einer davon -
     aber man muss ihn waehlen.

     Das Ergebnis geht als `backup`-Objekt an den Endpunkt. Fehlt es, antwortet
     der Server mit 400 (siehe backend app/guard.py) - die Rueckfrage laesst sich
     also nicht dadurch umgehen, dass man diese Komponente weglaesst.
     ============================================================================== -->

<template>
  <teleport to="body">
    <div v-if="open" class="modal-backdrop" @click.self="cancel">
      <div class="modal" role="dialog" aria-modal="true" :aria-label="t('backupDialog.title')">
        <div class="card-header">
          <div>
            <h2 class="card-title">{{ t('backupDialog.title') }}</h2>
            <p class="hint mt-1">{{ t('backupDialog.intro') }}</p>
          </div>
        </div>

        <div class="card-body space-y-4">
          <!-- Worum es geht -->
          <div class="operation-box">
            <span class="label">{{ t('backupDialog.operation') }}</span>
            <p class="operation-name">{{ operation }}</p>
            <p v-if="warning" class="operation-warning">{{ warning }}</p>
          </div>

          <!-- Die beiden Wege -->
          <div class="space-y-2">
            <label class="choice" :class="{ 'is-picked': create }">
              <input v-model="create" type="radio" :value="true">
              <span>
                <strong>{{ t('backupDialog.withBackup') }}</strong>
              </span>
            </label>

            <label class="choice" :class="{ 'is-picked': !create }">
              <input v-model="create" type="radio" :value="false">
              <span>
                <strong>{{ t('backupDialog.withoutBackup') }}</strong>
                <em class="choice-warning">{{ t('backupDialog.withoutWarning') }}</em>
              </span>
            </label>
          </div>

          <!-- Nur wenn gesichert wird: Umfang und Verschlüsselung -->
          <div v-if="create" class="space-y-4 pt-1">
            <div>
              <span class="label">{{ t('backup.scope') }}</span>
              <p class="hint mb-2">{{ t('backup.scopeHint') }}</p>
              <div class="scope-grid">
                <label v-for="scope in scopes" :key="scope.id" class="scope-item">
                  <input v-model="selectedScopes" type="checkbox" :value="scope.id">
                  <span>
                    <strong>{{ scopeLabel(scope) }}</strong>
                    <em>{{ scope.hint }}</em>
                  </span>
                </label>
              </div>
              <p v-if="!selectedScopes.length" class="field-error">
                {{ t('backup.scopeHint') }}
              </p>
            </div>

            <div>
              <label class="choice choice--inline">
                <input v-model="encrypt" type="checkbox">
                <span><strong>{{ t('backup.encrypt') }}</strong></span>
              </label>
              <p class="hint">{{ t('backup.encryptHint') }}</p>
            </div>

            <div v-if="encrypt" class="grid gap-2 sm:grid-cols-2">
              <div>
                <label class="label">{{ t('backup.passphrase') }}</label>
                <input v-model="passphrase" type="password" class="input" autocomplete="new-password">
              </div>
              <div>
                <label class="label">{{ t('backup.passphraseRepeat') }}</label>
                <input v-model="passphraseRepeat" type="password" class="input" autocomplete="new-password">
              </div>
              <p v-if="passphraseProblem" class="field-error sm:col-span-2">{{ passphraseProblem }}</p>
            </div>

            <div>
              <label class="label">{{ t('backup.note') }}</label>
              <input v-model="note" type="text" class="input" :placeholder="t('backup.noteHint')">
            </div>
          </div>
        </div>

        <div class="modal-actions">
          <button class="btn btn-ghost" @click="cancel">{{ t('common.cancel') }}</button>
          <button class="btn btn-primary" :disabled="!canProceed" @click="proceed">
            {{ t('backupDialog.proceed') }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from '../i18n'

const props = defineProps({
  open: { type: Boolean, default: false },
  /** Was gleich passiert - steht im Dialog, damit klar ist, wofür gesichert wird. */
  operation: { type: String, required: true },
  /** Zusätzliche Warnung für besonders einschneidende Schritte. */
  warning: { type: String, default: '' },
  /** Bereiche aus GET /api/backup/overview. */
  scopes: { type: Array, default: () => [] },
})

const emit = defineEmits(['confirm', 'cancel'])
const { t, locale } = useI18n()

const create = ref(true)
const encrypt = ref(false)
const passphrase = ref('')
const passphraseRepeat = ref('')
const note = ref('')
const selectedScopes = ref([])

// Beim Öffnen zurück auf die Vorgabe - sonst steht beim nächsten Mal noch das
// Passwort vom letzten Vorgang im Feld.
watch(() => props.open, (isOpen) => {
  if (!isOpen) return
  create.value = true
  encrypt.value = false
  passphrase.value = ''
  passphraseRepeat.value = ''
  note.value = ''
  selectedScopes.value = props.scopes.filter(s => s.default).map(s => s.id)
})

function scopeLabel(scope) {
  return locale.value === 'en' ? (scope.label_en || scope.label) : scope.label
}

const passphraseProblem = computed(() => {
  if (!encrypt.value) return ''
  if (passphrase.value.length && passphrase.value.length < 8) {
    return t('backup.encryptHint')
  }
  if (passphraseRepeat.value && passphrase.value !== passphraseRepeat.value) {
    return t('backup.passphraseMismatch')
  }
  return ''
})

const canProceed = computed(() => {
  if (!create.value) return true
  if (!selectedScopes.value.length) return false
  if (!encrypt.value) return true
  return passphrase.value.length >= 8 && passphrase.value === passphraseRepeat.value
})

function proceed() {
  if (!canProceed.value) return
  emit('confirm', create.value
    ? {
        create: true,
        scopes: [...selectedScopes.value],
        passphrase: encrypt.value ? passphrase.value : '',
        note: note.value,
      }
    // Auch das "nein" wird ausdrücklich mitgeschickt - der Server verlangt eine
    // Entscheidung, nicht ihr Fehlen.
    : { create: false })
}

function cancel() {
  emit('cancel')
}
</script>

<style scoped>
.operation-box {
  padding: 0.75rem 0.875rem;
  border-radius: var(--radius);
  background-color: var(--color-surface-elevated);
  border: 1px solid var(--color-border);
}

.operation-name {
  font-weight: 600;
  color: var(--color-text-primary);
  margin-top: 0.125rem;
}

.operation-warning {
  font-size: 0.75rem;
  color: var(--color-warning);
  margin-top: 0.375rem;
}

.choice {
  display: flex;
  align-items: flex-start;
  gap: 0.625rem;
  padding: 0.625rem 0.75rem;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  cursor: pointer;
  transition: border-color var(--duration) var(--ease), background-color var(--duration) var(--ease);
}

.choice:hover {
  background-color: var(--hover-surface);
}

.choice.is-picked {
  border-color: var(--color-primary);
  background-color: var(--primary-soft);
}

.choice--inline {
  border: none;
  padding: 0;
  background: transparent;
}

.choice--inline:hover {
  background: transparent;
}

.choice input {
  margin-top: 0.1875rem;
  flex-shrink: 0;
}

.choice span {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
  color: var(--color-text-primary);
  font-size: 0.875rem;
}

.choice-warning {
  font-style: normal;
  font-size: 0.75rem;
  color: var(--color-warning);
}

.scope-grid {
  display: grid;
  gap: 0.375rem;
}

@media (min-width: 640px) {
  .scope-grid {
    grid-template-columns: 1fr 1fr;
  }
}

.scope-item {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.5rem 0.625rem;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  cursor: pointer;
  font-size: 0.8125rem;
}

.scope-item:hover {
  background-color: var(--hover-surface);
}

.scope-item input {
  margin-top: 0.1875rem;
  flex-shrink: 0;
}

.scope-item span {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.scope-item strong {
  color: var(--color-text-primary);
  font-weight: 600;
}

.scope-item em {
  font-style: normal;
  font-size: 0.6875rem;
  color: var(--color-text-muted);
}

.field-error {
  font-size: 0.75rem;
  color: var(--color-danger);
  margin-top: 0.375rem;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  padding: 0.875rem 1.25rem;
  border-top: 1px solid var(--color-border);
}
</style>
