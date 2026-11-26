#!/usr/bin/env python3
# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Sicherung (.rimpbak, Format RIMPBAK1) ohne RIMPERIO
#               entschluesseln. Braucht nur Python 3 und das Paket "cryptography".
# ==============================================================================
"""
Notfall-Werkzeug: Eine verschluesselte RIMPERIO-Sicherung zurueckholen, auch wenn
RIMPERIO selbst nicht mehr laeuft.

    pip install cryptography
    python3 rimperio-decrypt.py DATEI.rimpbak [ZIEL]
    # Passwort: in Vaultwarden der Eintrag "RIMPERIO · Backup-Schlüssel"

Ohne ZIEL wird neben die Datei geschrieben (ohne .rimpbak). Mit ``--info`` nur
den Kopf anzeigen (Auftrag, Zeitpunkt, Inhalt) - dafuer braucht es kein Passwort.

Format::

    "RIMPBAK1" | u32 Laenge | Kopf (JSON)
    Bloecke:   u32 Laenge | AES-256-GCM(Block)
               Nonce = Kopf.nonce (8 Byte) | Blocknummer (u32)
               AAD   = SHA256(Kopf)[:16] | Blocknummer (u32) | 1 Byte (1 = letzter Block)
    Schluessel = PBKDF2-HMAC-SHA256(Passwort, Kopf.salt, Kopf.iter, 32 Byte)
"""

import argparse
import base64
import getpass
import hashlib
import json
import os
import struct
import sys

MAGIC = b"RIMPBAK1"


def read_header(fh):
    if fh.read(8) != MAGIC:
        sys.exit("Keine RIMPERIO-Sicherungsdatei (RIMPBAK1).")
    (hlen,) = struct.unpack(">I", fh.read(4))
    raw = fh.read(hlen)
    return raw, json.loads(raw)


def main() -> None:
    ap = argparse.ArgumentParser(description="RIMPERIO-Sicherung (.rimpbak) entschlüsseln")
    ap.add_argument("datei")
    ap.add_argument("ziel", nargs="?")
    ap.add_argument("--info", action="store_true", help="nur den Kopf anzeigen")
    ap.add_argument("--passwort-datei", help="Passwort aus einer Datei lesen statt abfragen")
    args = ap.parse_args()

    with open(args.datei, "rb") as src:
        raw, header = read_header(src)
        if args.info:
            print(json.dumps({k: v for k, v in header.items() if k not in ("salt", "nonce")}, indent=2, ensure_ascii=False))
            return
        try:
            from cryptography.exceptions import InvalidTag
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            sys.exit("Bitte zuerst installieren: pip install cryptography")
        if args.passwort_datei:
            with open(args.passwort_datei, encoding="utf-8") as fh:
                pw = fh.read().strip()
        else:
            pw = getpass.getpass("Backup-Passwort: ")
        key = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), base64.b64decode(header["salt"]),
                                  int(header["iter"]), dklen=32)
        aes = AESGCM(key)
        base_nonce = base64.b64decode(header["nonce"])
        hh = hashlib.sha256(raw).digest()[:16]
        ziel = args.ziel or (args.datei[:-8] if args.datei.endswith(".rimpbak") else args.datei + ".plain")
        if os.path.exists(ziel):
            sys.exit(f"{ziel} gibt es schon – bitte ein anderes Ziel angeben.")
        n, total = 0, 0
        with open(ziel + ".part", "wb") as dst:
            while True:
                lb = src.read(4)
                if len(lb) < 4:
                    sys.exit("Datei abgeschnitten – der letzte Block fehlt.")
                (clen,) = struct.unpack(">I", lb)
                ct = src.read(clen)
                nonce = base_nonce + struct.pack(">I", n)
                plain, final = None, False
                for flag in (b"\x00", b"\x01"):
                    try:
                        plain = aes.decrypt(nonce, ct, hh + struct.pack(">I", n) + flag)
                        final = flag == b"\x01"
                        break
                    except InvalidTag:
                        continue
                if plain is None:
                    os.unlink(ziel + ".part")
                    sys.exit("Falsches Passwort oder beschädigte Datei." if n == 0 else f"Block {n} beschädigt.")
                dst.write(plain)
                total += len(plain)
                n += 1
                if final:
                    break
        os.replace(ziel + ".part", ziel)
        print(f"Entschlüsselt: {ziel} ({total} Byte, Inhalt: {header.get('content', '?')})")


if __name__ == "__main__":
    main()
