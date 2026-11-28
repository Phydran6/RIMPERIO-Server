"""Autopilot: Einordnung von Releases und Wartungsfenster (ohne Datenbank)."""

import unittest
from datetime import datetime

from app import selfupdate as s


class SelfUpdateTest(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(s.classify("0.3.0", {"tag": "v0.3.1"})["level"], "patch")
        self.assertFalse(s.classify("0.3.0", {"tag": "v0.4.0", "notes": "Neu: X"})["needs_approval"])
        self.assertTrue(s.classify("0.3.0", {"tag": "v1.0.0"})["needs_approval"])
        self.assertTrue(s.classify("0.3.0", {"tag": "v0.3.1", "notes": "[Freigabe] Umbau"})["needs_approval"])
        self.assertTrue(s.classify("0.3.0", {"tag": "v0.3.1", "notes": "behebt CVE-2026-0001"})["security"])

    def test_window(self):
        w = {"days": list(range(7)), "start": "03:00", "end": "05:00", "tz": "Europe/Berlin"}
        self.assertTrue(s.in_window(w, datetime(2026, 11, 28, 2, 30)))     # 03:30 Berlin
        self.assertFalse(s.in_window(w, datetime(2026, 11, 28, 12, 0)))
        night = {**w, "start": "22:00", "end": "02:00"}
        self.assertTrue(s.in_window(night, datetime(2026, 11, 28, 23, 30)))  # 00:30 Berlin


if __name__ == "__main__":
    unittest.main()
