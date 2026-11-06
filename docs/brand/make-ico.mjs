// ---------------------------------------------------------------------------
// Datei:        scripts/make-ico.mjs
// Projekt:      RIMPERIO
// Autor:        Phydran6
// Version:      2026.11.05.12.00.00
//
// Beschreibung: Packt ein PNG in eine .ico-Datei (für das exe-/MSI-Symbol).
//               Reines Node ohne Abhängigkeiten — das ICO bettet das PNG direkt
//               ein (Vista+). Am besten ein quadratisches PNG, 256x256 oder 512.
//
// Aufruf:  node scripts/make-ico.mjs brand/rimperio-icon.png installer/windows/rimperio.ico
// ---------------------------------------------------------------------------
import { readFileSync, writeFileSync } from 'node:fs'

const [, , input, output = 'icon.ico'] = process.argv
if (!input) {
  console.error('Nutzung: node scripts/make-ico.mjs <input.png> [output.ico]')
  process.exit(1)
}

const png = readFileSync(input)
if (png.readUInt32BE(0) !== 0x89504e47) {
  console.error('Das ist keine PNG-Datei:', input)
  process.exit(1)
}

const w = png.readUInt32BE(16)
const h = png.readUInt32BE(20)
const bw = w >= 256 ? 0 : w // 0 bedeutet im ICO-Format 256
const bh = h >= 256 ? 0 : h

const dir = Buffer.alloc(6)
dir.writeUInt16LE(0, 0) // reserviert
dir.writeUInt16LE(1, 2) // Typ 1 = Icon
dir.writeUInt16LE(1, 4) // ein Bild

const entry = Buffer.alloc(16)
entry.writeUInt8(bw, 0)
entry.writeUInt8(bh, 1)
entry.writeUInt8(0, 2) // Farbpalette
entry.writeUInt8(0, 3) // reserviert
entry.writeUInt16LE(1, 4) // Farbebenen
entry.writeUInt16LE(32, 6) // Bits pro Pixel
entry.writeUInt32LE(png.length, 8) // Größe der Bilddaten
entry.writeUInt32LE(22, 12) // Offset (6 + 16)

writeFileSync(output, Buffer.concat([dir, entry, png]))
console.log(`ICO geschrieben: ${output}  (${w}x${h})`)
