# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Tests: Lizenzprüfung, Kontingent, Abrechnung je Gerät
# ==============================================================================
"""Ohne Datenbank: python -m unittest discover -s tests (in server/backend)."""

import base64
import importlib.util
import pathlib
import unittest
from datetime import date, datetime, timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import billing, licensing as lic
from app.models import LicenseDevice

_TOOL = pathlib.Path(__file__).resolve().parents[3] / "licensing" / "rimperio_license.py"
_spec = importlib.util.spec_from_file_location("rimperio_license", _TOOL)
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

IID = "1b4e28ba-2fa1-41d2-883f-0016d3cca427"
NOW = datetime(2026, 11, 27, 12, 0, 0)
PRIV = Ed25519PrivateKey.generate()
PUB = base64.b64encode(PRIV.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
KEYS = {"test": PUB}


def envelope(**over):
    data = {"license_id": "LIC-1", "instance_id": IID, "licensee": "Muster GmbH",
            "model": "per_device", "max_devices": 2, "valid_until": "2027-11-30"}
    data.update(over)
    return tool.sign(data, PRIV, "test", issued_at="2026-11-27T10:00:00Z")


def state(env, origin="upload", last_ok=NOW):
    return {"envelope": env, "origin": origin, "last_ok_at": lic._iso(last_ok)}


class SignatureTests(unittest.TestCase):
    def test_tool_signature_verifies_on_server(self):
        got, err = lic.verify_envelope(envelope(), KEYS)
        self.assertEqual(err, "")
        self.assertEqual(got["max_devices"], 2)
        self.assertEqual(got["features"], sorted(lic.DEFAULT_FEATURES))

    def test_tampered_license_is_rejected(self):
        env = envelope()
        env["license"]["max_devices"] = 9999
        got, err = lic.verify_envelope(env, KEYS)
        self.assertIsNone(got)
        self.assertIn("Signatur ungültig", err)

    def test_foreign_key_is_rejected(self):
        other = Ed25519PrivateKey.generate()
        env = tool.sign({"license_id": "X", "instance_id": IID}, other, "test")
        self.assertIsNone(lic.verify_envelope(env, KEYS)[0])

    def test_without_trusted_keys_nothing_validates(self):
        got, err = lic.verify_envelope(envelope(), {})
        self.assertIsNone(got)
        self.assertIn("kein Lizenz-Schlüssel", err)


class EvaluateTests(unittest.TestCase):
    def test_no_license_is_community(self):
        out = lic.evaluate({}, IID, NOW, KEYS)
        self.assertEqual(out["edition"], "community")
        self.assertEqual(out["features"], sorted(lic.COMMUNITY_FEATURES))

    def test_valid_license_is_enterprise(self):
        out = lic.evaluate(state(envelope()), IID, NOW, KEYS)
        self.assertEqual(out["edition"], "enterprise")
        self.assertEqual(out["max_devices"], 2)
        self.assertIn("billing", out["features"])

    def test_wrong_instance(self):
        out = lic.evaluate(state(envelope(instance_id="00000000-0000-0000-0000-000000000000")), IID, NOW, KEYS)
        self.assertEqual(out["edition"], "community")
        self.assertIn("anderen Instanz", out["errors"][0])

    def test_revoked_and_suspended(self):
        for status in ("revoked", "suspended"):
            self.assertEqual(lic.evaluate(state(envelope(status=status)), IID, NOW, KEYS)["edition"], "community")

    def test_expiry_grace(self):
        env = envelope(valid_until="2026-11-20", grace_days=14)
        out = lic.evaluate(state(env), IID, NOW, KEYS)
        self.assertEqual(out["edition"], "enterprise")
        self.assertIn("Kulanz", out["warnings"][0])
        out = lic.evaluate(state(env), IID, NOW + timedelta(days=10), KEYS)
        self.assertEqual(out["edition"], "community")

    def test_online_license_needs_confirmation(self):
        env = envelope(grace_days=14)
        self.assertEqual(lic.evaluate(state(env, "github", NOW - timedelta(days=5)), IID, NOW, KEYS)["edition"],
                         "enterprise")
        self.assertEqual(lic.evaluate(state(env, "github", NOW - timedelta(days=15)), IID, NOW, KEYS)["edition"],
                         "community")
        # Hochgeladen: kein Online-Zwang
        self.assertEqual(lic.evaluate(state(env, "upload", NOW - timedelta(days=200)), IID, NOW, KEYS)["edition"],
                         "enterprise")

    def test_not_yet_valid(self):
        out = lic.evaluate(state(envelope(valid_from="2027-01-01")), IID, NOW, KEYS)
        self.assertEqual(out["edition"], "community")


def dev(i, tenant="A", first=0, active=0, billable=True, licensed_at=None):
    return LicenseDevice(id=i, device_key=f"agent:{i}", kind="agent", ref_id=i, name=f"pc{i}",
                         device_class="windows_client", tenant=tenant, billable=billable,
                         first_seen_at=NOW - timedelta(days=100 - first),
                         last_active_at=NOW - timedelta(days=active), licensed_at=licensed_at,
                         removed_at=None, tenant_override=None)


class AllocateTests(unittest.TestCase):
    def test_community_everything_free(self):
        devices = [dev(1), dev(2)]
        lic.allocate(devices, {"edition": "community"}, NOW)
        self.assertEqual({d.state for d in devices}, {"free"})

    def test_first_come_keeps_seat(self):
        devices = [dev(1, first=5), dev(2, first=1), dev(3, first=9, licensed_at=NOW - timedelta(days=3))]
        counts = lic.allocate(devices, {"edition": "enterprise", "model": "per_device", "max_devices": 2}, NOW)
        self.assertEqual(counts, {"licensed": 2, "over_limit": 1})
        self.assertEqual(devices[2].state, "licensed")          # hatte schon einen Platz
        self.assertEqual(devices[1].state, "licensed")          # zuerst gesehen
        self.assertEqual(devices[0].state, "over_limit")

    def test_excluded_and_inactive_take_no_seat(self):
        devices = [dev(1, billable=False), dev(2, active=40), dev(3), dev(4)]
        lic.allocate(devices, {"edition": "enterprise", "model": "per_device", "max_devices": 2}, NOW)
        self.assertEqual([d.state for d in devices], ["excluded", "inactive", "licensed", "licensed"])

    def test_class_not_billable_takes_no_seat(self):
        devices = [dev(1), dev(2)]
        devices[0].device_class = "log_source"
        lic.allocate(devices, {"edition": "enterprise", "model": "per_device", "max_devices": 1}, NOW,
                     class_billable={"log_source": False})
        self.assertEqual([d.state for d in devices], ["excluded", "licensed"])

    def test_per_tenant_limit(self):
        devices = [dev(1, "A", first=1), dev(2, "B", first=2), dev(3, "A", first=3)]
        lic.allocate(devices, {"edition": "enterprise", "model": "per_tenant", "max_tenants": 1}, NOW)
        self.assertEqual([d.state for d in devices], ["licensed", "over_limit", "licensed"])

    def test_per_instance_unlimited(self):
        devices = [dev(i) for i in range(1, 6)]
        lic.allocate(devices, {"edition": "enterprise", "model": "per_instance"}, NOW)
        self.assertEqual({d.state for d in devices}, {"licensed"})


def days(key, tenant, cls, n, start=1):
    return [(date(2026, 11, d), key, tenant, cls) for d in range(start, start + n)]


class BillingTests(unittest.TestCase):
    def cfg(self, **over):
        cfg = billing.clean_config({"default_price": 5, "class_prices": {"windows_server": 20},
                                    "tenants": {"KundeA": {"class_prices": {"windows_client": 4},
                                                           "flat_fee": 10, "discount_percent": 10}}})
        cfg.update(over)
        return cfg

    def test_price_precedence(self):
        cfg = self.cfg()
        self.assertEqual(billing.unit_price(cfg, "KundeA", "windows_client"), 4)      # Mandant/Klasse
        self.assertEqual(billing.unit_price(cfg, "KundeA", "windows_server"), 20)     # Klasse global
        self.assertEqual(billing.unit_price(cfg, "KundeB", "linux"), 5)               # Standard
        self.assertEqual(billing.unit_price(cfg, "KundeA", "linux", 7.5), 7.5)        # am Gerät

    def test_month_method_with_fees(self):
        rows = (days("agent:1", "KundeA", "windows_client", 3) + days("agent:2", "KundeA", "windows_server", 30)
                + days("agent:3", "KundeB", "linux", 1))
        rep = billing.build_report("2026-11", rows, {}, self.cfg(), today=date(2026, 12, 2))
        a = next(t for t in rep["tenants"] if t["tenant"] == "KundeA")
        self.assertEqual(a["subtotal"], 24.0)            # 4 + 20
        self.assertEqual(a["discount"], 2.4)
        self.assertEqual(a["net"], 31.6)                 # 24 - 2,40 + 10
        self.assertFalse(rep["partial"])
        self.assertEqual(rep["totals"]["net"], 36.6)     # + KundeB 5

    def test_min_days_prorata_peak(self):
        rows = days("agent:1", "A", "linux", 3) + days("agent:2", "A", "linux", 15, start=10)
        cfg = self.cfg(min_days=5)
        self.assertEqual(billing.build_report("2026-11", rows, {}, cfg)["tenants"][0]["lines"][0]["quantity"], 1)
        cfg = self.cfg(count_method="prorata")
        self.assertEqual(billing.build_report("2026-11", rows, {}, cfg)["tenants"][0]["lines"][0]["quantity"], 0.6)
        cfg = self.cfg(count_method="peak")
        self.assertEqual(billing.build_report("2026-11", rows, {}, cfg)["tenants"][0]["lines"][0]["quantity"], 1)

    def test_free_devices_minimum_and_excluded_tenant(self):
        cfg = billing.clean_config({"default_price": 10, "class_prices": {"windows_server": 30}, "tax_percent": 19,
                                    "tenants": {"A": {"free_devices": 1, "minimum_fee": 100},
                                                "Intern": {"billable": False}}})
        rows = (days("agent:1", "A", "linux", 2) + days("agent:2", "A", "windows_server", 2)
                + days("agent:3", "Intern", "linux", 2))
        rep = billing.build_report("2026-11", rows, {}, cfg)
        a = rep["tenants"][0]
        self.assertEqual(a["subtotal"], 30.0)            # günstigstes Gerät (Linux) ist inklusive
        self.assertTrue(a["minimum_applied"])
        self.assertEqual(a["net"], 100.0)
        self.assertEqual(a["gross"], 119.0)
        self.assertEqual(rep["excluded_tenants"], ["Intern"])
        csv_text = billing.to_csv(rep)
        self.assertIn("Mindestbetrag angewendet", csv_text)
        self.assertIn("119,00", csv_text)

    def test_config_validation(self):
        with self.assertRaises(ValueError):
            billing.clean_config({"default_price": -1})
        with self.assertRaises(ValueError):
            billing.clean_config({"class_prices": {"toaster": 1}})
        with self.assertRaises(ValueError):
            billing.clean_config({"count_method": "random"})


class ClassifyTests(unittest.TestCase):
    def test_classes(self):
        class A:
            def __init__(self, **kw):
                self.agent_uid, self.platform, self.os, self.site = None, "", "", None
                self.__dict__.update(kw)
        self.assertEqual(lic.classify_agent(A(agent_uid="x", platform="windows",
                                              os="Windows Server 2022")), "windows_server")
        self.assertEqual(lic.classify_agent(A(agent_uid="x", platform="windows", os="Windows 11")), "windows_client")
        self.assertEqual(lic.classify_agent(A(agent_uid="x", platform="linux")), "linux")
        self.assertEqual(lic.classify_agent(A()), "log_source")
        self.assertEqual(lic.tenant_of(A(site="Kunde X")), "Kunde X")
        self.assertEqual(lic.tenant_of(A()), lic.DEFAULT_TENANT)


if __name__ == "__main__":
    unittest.main()
