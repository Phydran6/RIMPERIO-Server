# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Tests: Backup-Format, Vaultwarden-Kryptografie,
#               NAS-Bewertung (ohne Datenbank, ohne Netz)
# ==============================================================================
"""python -m unittest discover -s tests (in server/backend)."""

import io
import os
import secrets
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app import backupjobs as bj
from app import nas
from app import vault

ROOT = Path(__file__).resolve().parents[3]


class BackupFormat(unittest.TestCase):
    def _enc(self, data: bytes, pw: str = "geheim") -> bytes:
        out = io.BytesIO()
        e = bj.Encryptor(out, pw, {"content": "test"})
        for i in range(0, len(data), 333_333):
            e.write(data[i:i + 333_333])
        e.close()
        return out.getvalue()

    def test_roundtrip_multi_chunk(self):
        data = secrets.token_bytes(bj.CHUNK * 2 + 12345)
        blob = self._enc(data)
        out = io.BytesIO()
        header = bj.decrypt_stream(io.BytesIO(blob), out, "geheim")
        self.assertEqual(out.getvalue(), data)
        self.assertEqual(header["content"], "test")

    def test_empty(self):
        out = io.BytesIO()
        bj.decrypt_stream(io.BytesIO(self._enc(b"")), out, "geheim")
        self.assertEqual(out.getvalue(), b"")

    def test_wrong_password(self):
        with self.assertRaises(bj.BackupError):
            bj.decrypt_stream(io.BytesIO(self._enc(b"abc")), io.BytesIO(), "falsch")

    def test_truncation_detected(self):
        data = secrets.token_bytes(bj.CHUNK + 10)
        blob = self._enc(data)
        # letzten Block abschneiden: der vorletzte ist nicht als "letzter" markiert
        header_len = 12 + int.from_bytes(blob[8:12], "big")
        first_len = int.from_bytes(blob[header_len:header_len + 4], "big")
        cut = blob[:header_len + 4 + first_len]
        with self.assertRaises(bj.BackupError):
            bj.decrypt_stream(io.BytesIO(cut), io.BytesIO(), "geheim")

    def test_standalone_script(self):
        data = b"RIMPERIO" * 50000
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "x.tar.gz.rimpbak"
            src.write_bytes(self._enc(data, "pw123"))
            (Path(d) / "pw").write_text("pw123")
            script = ROOT / "docs" / "backup" / "rimperio-decrypt.py"
            r = subprocess.run([sys.executable, str(script), str(src), "--passwort-datei", str(Path(d) / "pw")],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((Path(d) / "x.tar.gz").read_bytes(), data)

    def test_schedule(self):
        after = datetime(2026, 11, 26, 12, 0)                 # Donnerstag, 13:00 Berlin
        nxt = bj.next_run({"type": "daily", "time": "02:30", "tz": "Europe/Berlin"}, after)
        self.assertEqual(nxt, datetime(2026, 11, 27, 1, 30))
        nxt = bj.next_run({"type": "weekly", "time": "03:00", "days": [6], "tz": "Europe/Berlin"}, after)
        self.assertEqual(nxt, datetime(2026, 11, 29, 2, 0))
        self.assertIsNone(bj.next_run({"type": "manual"}, after))
        self.assertEqual(bj.next_run({"type": "hourly", "every_hours": 4}, after), datetime(2026, 11, 26, 16, 0))

    def test_agent_script_quotes(self):
        shell, script = bj._agent_script("linux", 7, "TOK", ["/srv/it's here"], ["*.log"], "https://rmm")
        self.assertEqual(shell, "bash")
        self.assertIn("'/srv/it'\\''s here'", script)
        r = subprocess.run(["bash", "-n"], input=script, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        shell, script = bj._agent_script("windows", 7, "TOK", ["C:\\Daten"], [], "https://rmm")
        self.assertEqual(shell, "powershell")
        self.assertIn("'C:\\Daten'", script)


class VaultCrypto(unittest.TestCase):
    def test_enc_string_roundtrip(self):
        key = secrets.token_bytes(64)
        s = vault.enc_text("Passwort äöü", key)
        self.assertTrue(s.startswith("2."))
        self.assertEqual(vault.dec_text(s, key), "Passwort äöü")
        with self.assertRaises(vault.VaultError):
            vault.dec_text(s, secrets.token_bytes(64))

    def test_user_key_protection(self):
        mk = vault.make_master_key("pw", "User@Example.com ", 0, 1000)
        self.assertEqual(mk, vault.make_master_key("pw", "user@example.com", 0, 1000))
        uk = secrets.token_bytes(64)
        prot = vault.enc_string(uk, vault.stretch(mk))
        self.assertEqual(vault.dec_string(prot, vault.stretch(mk)), uk)
        self.assertEqual(len(vault._unb64(vault.master_password_hash(mk, "pw"))), 32)

    def test_argon2(self):
        try:
            import argon2  # noqa: F401
        except ImportError:
            self.skipTest("argon2-cffi fehlt")
        mk = vault.make_master_key("pw", "a@b.de", 1, 3, 16, 2)
        self.assertEqual(len(mk), 32)

    def test_case_insensitive_fields(self):
        self.assertEqual(vault._g({"Name": 1}, "name"), 1)
        self.assertEqual(vault._g({"organizationId": "x"}, "organizationId"), "x")
        self.assertEqual(vault._g({"ORGANIZATIONID": "y"}, "organizationId"), "y")

    def test_totp(self):
        code = vault.totp_now("JBSWY3DPEHPK3PXP")
        self.assertTrue(code.isdigit() and len(code) == 6)
        self.assertEqual(len(vault.totp_now("otpauth://totp/x?secret=JBSWY3DPEHPK3PXP")), 6)


class NasEvaluate(unittest.TestCase):
    def test_healthy(self):
        st = {"disks": [{"id": "sata1", "name": "Laufwerk 1", "status": "normal", "smart": "normal", "temp": 35}],
              "pools": [{"id": "p1", "name": "Pool 1", "status": "normal"}],
              "volumes": [{"id": "v1", "name": "Volume 1", "status": "normal", "percent": 40.0}],
              "system": {}, "errors": {}}
        self.assertEqual(nas.evaluate(st), ("ok", []))

    def test_problems(self):
        st = {"disks": [{"id": "sata2", "name": "Laufwerk 2", "status": "crashed", "smart": "failing", "temp": 61}],
              "pools": [{"id": "p1", "name": "Pool 1", "status": "degraded"}],
              "volumes": [{"id": "v1", "name": "Volume 1", "status": "normal", "percent": 96.0}],
              "backup_tasks": [{"id": 1, "name": "Cloud", "last_result": "failed"}], "system": {}, "errors": {}}
        level, problems = nas.evaluate(st)
        self.assertEqual(level, "critical")
        whats = {p["what"] for p in problems}
        self.assertTrue({"disk:sata2", "smart:sata2", "disktemp:sata2", "pool:p1", "volfull:v1", "hb:1"} <= whats)

    def test_snmp_params(self):
        cur = {"enable_snmp": False, "enable_snmpv1v2": False, "rocommunity": "public", "enable_snmpv3": False,
               "snmpv3_password": "x", "device_location": "Keller"}
        p = nas.snmp_set_params(cur, "rimpabc")
        self.assertTrue(p["enable_snmp"] and p["enable_snmpv1v2"])
        self.assertFalse(p["enable_snmpv3"])
        self.assertEqual(p["rocommunity"], "rimpabc")
        self.assertNotIn("snmpv3_password", p)
        self.assertEqual(p["device_location"], "Keller")
        self.assertTrue(nas.snmp_set_params({}, "c")["enable_snmp"])
        self.assertFalse(nas._snmp_enabled(cur))

    def test_versions_and_window(self):
        self.assertGreater(nas.version_key("2.0.3-0130"), nas.version_key("2.0.2-0129"))
        self.assertGreater(nas.version_key("7.2.2-72806"), nas.version_key("7.2.1-69057"))
        auto = {"days": [6], "start": "03:00", "duration_min": 120, "tz": "Europe/Berlin"}
        self.assertTrue(nas.in_window(auto, datetime(2026, 11, 29, 2, 30)))   # So 03:30 Berlin
        self.assertFalse(nas.in_window(auto, datetime(2026, 11, 29, 5, 0)))   # So 06:00 Berlin
        self.assertFalse(nas.in_window(auto, datetime(2026, 11, 28, 2, 30)))  # Sa

    def test_raspberry_mac(self):
        self.assertTrue(nas.is_raspberry_mac("DC:A6:32:12:34:56"))
        self.assertFalse(nas.is_raspberry_mac("00:11:32:12:34:56"))


if __name__ == "__main__":
    unittest.main()
