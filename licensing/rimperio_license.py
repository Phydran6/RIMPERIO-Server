#!/usr/bin/env python3
# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Lizenzen ausstellen: Schlüssel erzeugen, signieren, prüfen
# ==============================================================================
"""
Lizenzwerkzeug für den Herausgeber.

    keygen    Schlüsselpaar erzeugen (privat -> GitHub-Secret, öffentlich -> app/license_keys.py)
    new       Vorlage für eine neue Lizenz ausgeben
    sign      eine Lizenz (YAML/JSON) signieren -> Lizenzdatei
    sign-dir  alle Lizenzen eines Ordners signieren (für die GitHub-Action)
    verify    eine Lizenzdatei prüfen

Braucht `cryptography`; für YAML-Quellen zusätzlich `pyyaml`.
Der private Schlüssel kommt aus --key (Datei) oder der Umgebungsvariablen
RIMPERIO_LICENSE_SIGNING_KEY (Base64, 32 Byte).
"""

import argparse
import base64
import json
import os
import pathlib
import re
import sys
import uuid
from datetime import datetime, timezone

FORMAT = "rimperio-license/1"
MODELS = ("per_device", "per_instance", "per_tenant")


def canonical(obj) -> bytes:
    """Muss mit server/backend/app/licensing.py:canonical übereinstimmen."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _crypto():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        sys.exit("Bitte zuerst installieren: pip install cryptography pyyaml")
    return Ed25519PrivateKey, Ed25519PublicKey, serialization


def _raw_public(private) -> str:
    _, _, ser = _crypto()
    return base64.b64encode(private.public_key().public_bytes(
        ser.Encoding.Raw, ser.PublicFormat.Raw)).decode()


def load_private(path: str = ""):
    Priv, _, _ = _crypto()
    value = pathlib.Path(path).read_text().strip() if path else os.getenv("RIMPERIO_LICENSE_SIGNING_KEY", "").strip()
    if not value:
        sys.exit("Kein privater Schlüssel: --key DATEI oder RIMPERIO_LICENSE_SIGNING_KEY setzen.")
    try:
        return Priv.from_private_bytes(base64.b64decode(value))
    except ValueError as exc:
        sys.exit(f"Privater Schlüssel unlesbar: {exc}")


def load_source(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yml", ".yaml"):
        try:
            import yaml
        except ImportError:
            sys.exit("Für YAML bitte installieren: pip install pyyaml")
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: erwartet ein Objekt mit Feldern")
    return data


def check(lic: dict) -> dict:
    """Die wichtigsten Felder prüfen und Datumswerte als Text ablegen."""
    out = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in lic.items()}
    if not re.fullmatch(r"[0-9a-f-]{36}", str(out.get("instance_id") or "")):
        raise ValueError("instance_id fehlt oder ist keine RIMPERIO-Instanz-ID")
    if not str(out.get("license_id") or "").strip():
        raise ValueError("license_id fehlt")
    if out.get("model", "per_device") not in MODELS:
        raise ValueError(f"model muss eines von {', '.join(MODELS)} sein")
    if out.get("status", "active") not in ("active", "suspended", "revoked"):
        raise ValueError("status muss active, suspended oder revoked sein")
    for field in ("valid_from", "valid_until"):
        if out.get(field):
            datetime.strptime(str(out[field])[:10], "%Y-%m-%d")
            out[field] = str(out[field])[:10]
    return out


def sign(lic: dict, private, key_id: str, issued_at: str = "") -> dict:
    lic = check(lic)
    lic["issued_at"] = issued_at or lic.get("issued_at") or \
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    signature = base64.b64encode(private.sign(canonical(lic))).decode()
    return {"format": FORMAT, "key_id": key_id, "license": lic, "signature": signature}


def verify(envelope: dict, public_b64: str) -> None:
    _, Pub, _ = _crypto()
    Pub.from_public_bytes(base64.b64decode(public_b64)).verify(
        base64.b64decode(envelope["signature"]), canonical(envelope["license"]))


# ------------------------------------------------------------------------------
def cmd_keygen(args) -> None:
    Priv, _, ser = _crypto()
    key = Priv.generate()
    private_b64 = base64.b64encode(key.private_bytes(
        ser.Encoding.Raw, ser.PrivateFormat.Raw, ser.NoEncryption())).decode()
    if args.out:
        p = pathlib.Path(args.out)
        p.write_text(private_b64 + "\n")
        os.chmod(p, 0o600)
        print(f"Privater Schlüssel gespeichert: {p}  (NIE ins Repository!)")
    else:
        print("PRIVATER SCHLÜSSEL (als GitHub-Secret RIMPERIO_LICENSE_SIGNING_KEY hinterlegen, nirgends sonst):")
        print(f"  {private_b64}")
    print("\nZeile für server/backend/app/license_keys.py (TRUSTED_KEYS):")
    print(f'    "{args.key_id}": "{_raw_public(key)}",')
    print(f"\nIm Lizenz-Repository als Variable RIMPERIO_LICENSE_KEY_ID = {args.key_id} anlegen.")


def cmd_new(args) -> None:
    iid = args.instance_id or "00000000-0000-0000-0000-000000000000"
    print(f"""# Lizenz für eine RIMPERIO-Instanz - Datei: sources/{iid}.yml
license_id: {args.license_id or 'LIC-' + uuid.uuid4().hex[:8].upper()}
instance_id: {iid}
licensee: "{args.licensee or 'Firma GmbH'}"
customer_no: ""
# per_device (je Gerät) | per_instance (je Instanz) | per_tenant (je Mandant)
model: {args.model}
max_devices: {args.max_devices if args.max_devices is not None else 'null'}   # nur per_device; null = unbegrenzt (Abrechnung nach Nutzung)
max_tenants: null   # nur per_tenant
features: [billing, billing_api, tenants]
valid_from: {datetime.now(timezone.utc):%Y-%m-%d}
valid_until: {args.valid_until or 'null'}   # null = unbefristet
grace_days: 14      # so lange gilt sie weiter, wenn GitHub nicht erreichbar ist
status: active      # active | suspended | revoked
note: ""
""")


def cmd_sign(args) -> None:
    private = load_private(args.key)
    env = sign(load_source(pathlib.Path(args.source)), private, args.key_id)
    text = json.dumps(env, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        pathlib.Path(args.out).write_text(text, encoding="utf-8")
        print(f"Signiert: {args.out}")
    else:
        sys.stdout.write(text)


def cmd_sign_dir(args) -> None:
    """sources/*.yml|json -> licenses/<instance_id>.json. Unverändertes bleibt unangetastet."""
    private = load_private(args.key)
    src, out = pathlib.Path(args.sources), pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    seen, changed, failed = set(), 0, 0
    for path in sorted(p for p in src.iterdir() if p.suffix.lower() in (".yml", ".yaml", ".json")):
        if path.name.startswith(("_", "EXAMPLE")):
            continue
        try:
            lic = check(load_source(path))
        except (ValueError, KeyError) as exc:
            print(f"::error file={path}::{exc}")
            failed += 1
            continue
        target = out / f"{lic['instance_id']}.json"
        if lic["instance_id"] in seen:
            print(f"::error file={path}::instance_id doppelt vergeben")
            failed += 1
            continue
        seen.add(lic["instance_id"])
        if target.exists():
            old = json.loads(target.read_text(encoding="utf-8"))
            old_lic = {k: v for k, v in old.get("license", {}).items() if k != "issued_at"}
            if old_lic == {k: v for k, v in lic.items() if k != "issued_at"} and old.get("key_id") == args.key_id:
                continue
        target.write_text(json.dumps(sign(lic, private, args.key_id), indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        print(f"signiert: {path.name} -> {target}")
        changed += 1
    if args.prune:
        for f in out.glob("*.json"):
            if f.stem not in seen:
                f.unlink()
                print(f"entfernt (keine Quelle mehr): {f}")
    print(f"{changed} Lizenz(en) neu signiert, {failed} Fehler.")
    if failed:
        sys.exit(1)


def cmd_verify(args) -> None:
    env = json.loads(pathlib.Path(args.file).read_text(encoding="utf-8"))
    if env.get("format") != FORMAT:
        sys.exit("Keine RIMPERIO-Lizenzdatei.")
    try:
        verify(env, args.public_key)
    except Exception:
        sys.exit("Signatur UNGÜLTIG.")
    lic = env["license"]
    print(f"Signatur gültig. {lic.get('license_id')} für {lic.get('instance_id')} "
          f"({lic.get('licensee', '')}), {lic.get('model')}, bis {lic.get('valid_until') or 'unbefristet'}, "
          f"Status {lic.get('status', 'active')}.")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="RIMPERIO-Lizenzen ausstellen")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("keygen")
    p.add_argument("--key-id", default=f"rimperio-{datetime.now():%Y}")
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_keygen)
    p = sub.add_parser("new")
    p.add_argument("--instance-id", default="")
    p.add_argument("--license-id", default="")
    p.add_argument("--licensee", default="")
    p.add_argument("--model", default="per_device", choices=MODELS)
    p.add_argument("--max-devices", type=int, default=None)
    p.add_argument("--valid-until", default="")
    p.set_defaults(func=cmd_new)
    p = sub.add_parser("sign")
    p.add_argument("source")
    p.add_argument("--key", default="")
    p.add_argument("--key-id", default=os.getenv("RIMPERIO_LICENSE_KEY_ID", ""), required=False)
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_sign)
    p = sub.add_parser("sign-dir")
    p.add_argument("sources")
    p.add_argument("out")
    p.add_argument("--key", default="")
    p.add_argument("--key-id", default=os.getenv("RIMPERIO_LICENSE_KEY_ID", ""))
    p.add_argument("--prune", action="store_true", help="signierte Dateien ohne Quelle löschen")
    p.set_defaults(func=cmd_sign_dir)
    p = sub.add_parser("verify")
    p.add_argument("file")
    p.add_argument("--public-key", required=True)
    p.set_defaults(func=cmd_verify)
    args = ap.parse_args(argv)
    if args.cmd in ("sign", "sign-dir") and not args.key_id:
        sys.exit("--key-id fehlt (oder RIMPERIO_LICENSE_KEY_ID setzen).")
    args.func(args)


if __name__ == "__main__":
    main()
