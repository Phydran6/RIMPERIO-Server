# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Tests: Patch-Einstufung nach Stufe und Dringlichkeit
# ==============================================================================
"""Einstufung ohne Datenbank: python -m unittest discover -s tests (in server/backend)."""

import unittest
from datetime import datetime, timedelta

from app import patchmgmt as pm
from app.models import PatchItem, PatchPolicy

NOW = datetime(2026, 11, 25, 12, 0, 0)


def item(platform="linux", kind="os", key="pkg", **kw) -> PatchItem:
    data = kw.pop("data", {})
    it = PatchItem(agent_id=kw.pop("agent_id", 1), platform=platform, kind=kind, key=key,
                   title=kw.pop("title", key), category=kw.pop("category", ""),
                   severity="", kb=kw.pop("kb", ""), version_from=kw.pop("version_from", "1.0"),
                   version_to=kw.pop("version_to", "1.1"), security=kw.pop("security", False),
                   reboot=False, urgent=kw.pop("urgent", False), data=data, first_seen=NOW,
                   decided_by="", ai=kw.pop("ai", None))
    assert not kw, kw
    return it


def settings(**over) -> dict:
    s = pm.effective_settings(None)
    s["ai"]["mode"] = over.pop("ai_mode", "off")
    for k, v in over.items():
        s[k] = v
    return s


class TierTests(unittest.TestCase):
    def test_plain_package_is_normal_low_and_automatic(self):
        it = item(key="htop")
        pm.decide_status(it, settings(), NOW)
        self.assertEqual((it.tier, it.urgency, it.status), ("normal", "low", "auto"))
        self.assertEqual(it.not_before, NOW + timedelta(days=3))

    def test_security_update_is_important_soon_and_automatic(self):
        it = item(key="libxml2", security=True)
        pm.decide_status(it, settings(), NOW)
        self.assertEqual((it.tier, it.urgency, it.status), ("important", "soon", "auto"))
        self.assertIsNone(it.not_before)

    def test_database_container_is_critical(self):
        it = item(platform="docker", kind="docker", key="db", data={"image": "postgres:16"},
                  version_from="16.3", version_to="16.4")
        pm.decide_status(it, settings(), NOW)
        self.assertEqual((it.tier, it.status), ("critical", "needs_approval"))

    def test_webserver_and_login_are_critical(self):
        for key in ("nginx", "apache2-bin", "openssh-server", "libpam-modules", "sssd-common"):
            it = item(key=key, security=True)
            pm.decide_status(it, settings(), NOW)
            self.assertEqual(it.tier, "critical", key)
        for image in ("jc21/nginx-proxy-manager:latest", "traefik:v3.1", "quay.io/keycloak/keycloak:26",
                      "ghcr.io/goauthentik/server:2024.8", "vaultwarden/server:latest"):
            it = item(platform="docker", kind="docker", key="c", data={"image": image})
            pm.decide_status(it, settings(), NOW)
            self.assertEqual(it.tier, "critical", image)

    def test_ordinary_container_is_normal(self):
        it = item(platform="docker", kind="docker", key="app", data={"image": "ghcr.io/foo/bar:latest"},
                  version_from="3fa9c01b2e4d", version_to="77ab01cd99ef")
        pm.decide_status(it, settings(), NOW)
        self.assertEqual((it.tier, it.status), ("normal", "auto"))

    def test_kev_is_immediate_even_when_normal(self):
        it = item(key="htop", urgent=True)
        pm.decide_status(it, settings(), NOW)
        self.assertEqual((it.urgency, it.status), ("immediate", "auto"))
        self.assertIsNone(it.not_before)

    def test_important_needs_approval_when_ai_sees_issues(self):
        it = item(key="libxml2", security=True,
                  ai={"risk": "low", "recommendation": "install", "known_issues": ["bricht xyz"]})
        pm.decide_status(it, settings(ai_mode="gate"), NOW)
        self.assertEqual(it.status, "needs_approval")
        it = item(key="libxml2", security=True, ai={"risk": "low", "recommendation": "install"})
        pm.decide_status(it, settings(ai_mode="gate"), NOW)
        self.assertEqual(it.status, "auto")

    def test_advise_mode_does_not_block(self):
        it = item(key="libxml2", security=True, ai={"risk": "high", "recommendation": "hold"})
        pm.decide_status(it, settings(ai_mode="advise"), NOW)
        self.assertEqual(it.status, "auto")

    def test_normal_escalates_on_high_risk(self):
        it = item(key="htop", ai={"risk": "high", "recommendation": "hold", "summary": "kaputt"})
        pm.decide_status(it, settings(ai_mode="gate"), NOW)
        self.assertEqual((it.tier, it.status), ("important", "needs_approval"))
        self.assertTrue(it.data.get("escalated"))
        s = settings(ai_mode="gate")
        s["tiers"]["normal"]["escalate"] = False
        it = item(key="htop", ai={"risk": "high", "recommendation": "hold"})
        pm.decide_status(it, s, NOW)
        self.assertEqual((it.tier, it.status), ("normal", "auto"))

    def test_failed_elsewhere_blocks_important(self):
        it = item(key="libxml2", security=True, agent_id=1)
        failed = {pm._fingerprint(it): {2}}
        pm.decide_status(it, settings(), NOW, failed)
        self.assertEqual(it.status, "needs_approval")
        failed = {pm._fingerprint(it): {1}}           # nur auf diesem Rechner selbst
        pm.decide_status(it, settings(), NOW, failed)
        self.assertEqual(it.status, "auto")

    def test_ai_may_raise_but_not_lower_urgency(self):
        it = item(key="htop", ai={"risk": "low", "recommendation": "install", "urgency": "immediate"})
        pm.decide_status(it, settings(ai_mode="gate"), NOW)
        self.assertEqual(it.urgency, "immediate")
        it = item(key="libxml2", security=True, ai={"risk": "low", "recommendation": "install", "urgency": "low"})
        pm.decide_status(it, settings(ai_mode="gate"), NOW)
        self.assertEqual(it.urgency, "soon")

    def test_reassess_keeps_rule_tier(self):
        it = item(key="htop", ai={"risk": "high", "recommendation": "hold"})
        s = settings(ai_mode="gate")
        pm.decide_status(it, s, NOW)
        self.assertEqual(it.tier, "important")
        it.ai = {"risk": "low", "recommendation": "install"}
        pm.apply_assessment(it, s)
        self.assertEqual((it.tier, it.status), ("normal", "auto"))

    def test_major_version_is_critical_but_dates_are_not(self):
        self.assertTrue(pm.major_change("1.9.3", "2.0.0"))
        self.assertTrue(pm.major_change("1:2.3-1", "1:3.0-1"))
        self.assertFalse(pm.major_change("2024a-0ubuntu1", "2025a-0ubuntu1"))
        self.assertFalse(pm.major_change("3fa9c01b2e4d", "4bb9c01b2e4d"))
        it = item(platform="docker", kind="docker", key="nc", data={"image": "nextcloud:29"},
                  version_from="29.0.8", version_to="30.0.1")
        pm.decide_status(it, settings(), NOW)
        self.assertEqual(it.tier, "critical")


class LegacyTests(unittest.TestCase):
    def test_legacy_actions_map_to_tiers(self):
        p = PatchPolicy(settings={"rules": [
            {"name": "x", "platform": "linux", "field": "package", "op": "equals", "value": "htop",
             "action": "approve"}],
            "fallback": {"linux": "auto"}, "defer_days": 5})
        s = pm.effective_settings(p)
        self.assertEqual(s["rules"][0]["tier"], "critical")
        self.assertEqual(s["fallback"]["linux"], "normal")
        self.assertEqual(s["urgency"]["soon"]["defer_days"], 5)
        self.assertEqual(s["urgency"]["low"]["defer_days"], 5)
        self.assertNotIn("defer_days", s)
        s["ai"]["mode"] = "off"
        it = item(key="htop")
        pm.decide_status(it, s, NOW)
        self.assertEqual(it.status, "needs_approval")

    def test_legacy_default_rules_detected(self):
        old = [{"name": n, "action": a, "platform": "any", "field": "title", "op": "contains", "value": ""}
               for n, a in pm.LEGACY_DEFAULT_RULES]
        self.assertTrue(pm.is_legacy_default_rules(old))
        old[0]["action"] = "approve"
        self.assertFalse(pm.is_legacy_default_rules(old))

    def test_validate_roundtrip(self):
        s = pm.validate_settings(pm.effective_settings(None))
        self.assertEqual(s["rules"][0]["tier"], "normal")
        with self.assertRaises(ValueError):
            pm.validate_settings({"tiers": {"important": {"approve_on": ["nope"]}}})
        with self.assertRaises(ValueError):
            pm.validate_settings({"rules": [{"name": "x", "tier": "urgent"}]})


if __name__ == "__main__":
    unittest.main()
