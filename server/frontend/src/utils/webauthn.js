/**
 * ==============================================================================
 * Name:        Phydran6
 * Kontakt:     Phydran6
 * Version:     2026.08.02.18.00.00
 * Changelog:   docs/changelog/frontend.md
 * ==============================================================================
 *
 * WebAuthn-Helfer (Passkeys)
 * ==========================
 * Der Browser erwartet und liefert Binärdaten (ArrayBuffer), das Backend
 * spricht base64url. Diese Datei übersetzt in beide Richtungen und kapselt die
 * beiden Aufrufe `navigator.credentials.create/get`.
 * ==============================================================================
 */

/** base64url -> ArrayBuffer */
export function base64urlToBuffer(value) {
  const padded = value.replace(/-/g, '+').replace(/_/g, '/')
  const binary = atob(padded + '='.repeat((4 - (padded.length % 4)) % 4))
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return bytes.buffer
}

/** ArrayBuffer -> base64url */
export function bufferToBase64url(buffer) {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i])
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** Wird der Browser Passkeys überhaupt anbieten? */
export function isSupported() {
  return typeof window !== 'undefined'
    && !!window.PublicKeyCredential
    && !!navigator.credentials
    // Ohne sicheren Kontext (HTTPS oder localhost) sperrt der Browser WebAuthn.
    && window.isSecureContext
}

/** Registrierung: Server-Optionen -> Browser -> Antwort für den Server */
export async function createCredential(options) {
  const publicKey = {
    ...options,
    challenge: base64urlToBuffer(options.challenge),
    user: { ...options.user, id: base64urlToBuffer(options.user.id) },
    excludeCredentials: (options.excludeCredentials || []).map(c => ({
      ...c,
      id: base64urlToBuffer(c.id),
    })),
  }

  const credential = await navigator.credentials.create({ publicKey })
  if (!credential) throw new Error('Der Browser hat keinen Passkey erzeugt')

  return {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
      attestationObject: bufferToBase64url(credential.response.attestationObject),
      transports: credential.response.getTransports ? credential.response.getTransports() : [],
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  }
}

/** Anmeldung: Server-Optionen -> Browser -> Antwort für den Server */
export async function getCredential(options) {
  const publicKey = {
    ...options,
    challenge: base64urlToBuffer(options.challenge),
    allowCredentials: (options.allowCredentials || []).map(c => ({
      ...c,
      id: base64urlToBuffer(c.id),
    })),
  }

  const credential = await navigator.credentials.get({ publicKey })
  if (!credential) throw new Error('Es wurde kein Passkey ausgewählt')

  return {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
      authenticatorData: bufferToBase64url(credential.response.authenticatorData),
      signature: bufferToBase64url(credential.response.signature),
      userHandle: credential.response.userHandle
        ? bufferToBase64url(credential.response.userHandle)
        : null,
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  }
}

/** Fehlermeldungen des Browsers in verständliche Sätze übersetzen. */
export function describeError(error) {
  if (!error) return 'Unbekannter Fehler'
  switch (error.name) {
    case 'NotAllowedError':
      return 'Abgebrochen oder zu lange gewartet.'
    case 'InvalidStateError':
      return 'Dieser Passkey ist für das Konto bereits hinterlegt.'
    case 'SecurityError':
      return 'Der Browser verweigert Passkeys auf dieser Adresse — sie funktionieren nur über HTTPS mit gültigem Zertifikat.'
    case 'NotSupportedError':
      return 'Dieses Gerät unterstützt keine Passkeys.'
    default:
      return error.message || String(error)
  }
}
