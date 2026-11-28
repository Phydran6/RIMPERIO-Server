"""DSM-Verbindung: eigener Sitzungsname, erneute Anmeldung bei verlorener Sitzung,
Paket-Update mit DSM-Kodierung als zweitem Versuch (ohne echtes NAS)."""

import asyncio
import json
import unittest
from urllib.parse import parse_qs

import httpx

from app import nas


def _form(req: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(req.content.decode()).items()}


class DsmTest(unittest.TestCase):
    def _dsm(self, handler):
        dsm = nas.Synology("https://nas.test:5001")
        dsm._c = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        dsm.apis = {"SYNO.API.Auth": {"path": "entry.cgi", "maxVersion": 7, "minVersion": 1},
                    "SYNO.Core.Package.Installation": {"path": "entry.cgi", "maxVersion": 1, "minVersion": 1},
                    "SYNO.Core.Package.Installation.Download": {"path": "entry.cgi", "maxVersion": 1, "minVersion": 1}}
        return dsm

    def test_relogin_and_unique_session(self):
        seen = {"logins": [], "status": 0}

        def handler(req):
            f = _form(req)
            if f["api"] == "SYNO.API.Auth" and f["method"] == "login":
                seen["logins"].append(f["session"])
                return httpx.Response(200, json={"success": True, "data": {"sid": f"s{len(seen['logins'])}", "did": "dev"}})
            if f["method"] == "status":
                seen["status"] += 1
                if seen["status"] == 1:
                    return httpx.Response(200, json={"success": False, "error": {"code": 119}})
                return httpx.Response(200, json={"success": True, "data": {"finished": True}})
            return httpx.Response(200, json={"success": True, "data": {}})

        async def run():
            dsm = self._dsm(handler)
            await dsm.login("admin", "pw")
            data = await dsm.call("SYNO.Core.Package.Installation", "status", version=1, task_id="t")
            return dsm, data
        dsm, data = asyncio.run(run())
        self.assertTrue(data["finished"])
        self.assertEqual(len(seen["logins"]), 2)
        self.assertTrue(seen["logins"][0].startswith("RIMPERIO-"))
        self.assertNotEqual(nas.Synology("https://x").session_name, dsm.session_name)

    def test_package_install_falls_back_to_json_strings(self):
        calls = []

        def handler(req):
            f = _form(req)
            calls.append(f)
            if f["method"] == "install":
                if not f["name"].startswith('"'):
                    return httpx.Response(200, json={"success": False, "error": {"code": 120}})
                return httpx.Response(200, json={"success": True, "data": {"taskid": "t1"}})
            if f["method"] == "status":
                return httpx.Response(200, json={"success": True, "data": {"finished": True}})
            return httpx.Response(200, json={"success": True, "data": {"message": "ok"}})

        logs = []
        pkg = {"id": "ContainerManager", "link": "https://dl/x.spk", "md5": "abc", "size": 10,
               "installed": "1", "available": "2"}
        asyncio.run(nas.synology_update_package(self._dsm(handler), pkg, logs.append))
        installs = [c for c in calls if c["method"] == "install"]
        self.assertEqual(len(installs), 2)
        self.assertEqual(json.loads(installs[1]["url"]), "https://dl/x.spk")
        self.assertTrue(any(c["method"] == "upgrade" for c in calls))

    def test_package_error_text(self):
        def handler(req):
            return httpx.Response(200, json={"success": False, "error": {"code": 4503}})

        async def run():
            await self._dsm(handler).call("SYNO.Core.Package.Installation", "status", version=1)
        with self.assertRaises(nas.NasError) as ctx:
            asyncio.run(run())
        self.assertIn("Speicher", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
