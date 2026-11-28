# ==============================================================================
# Name:        Phydran6
# Kontakt:     Phydran6
# Changelog:   docs/changelog/backend.md
# Beschreibung: RIMPERIO - Tests: HTTPS-Weiterleitung über einen Agent am
#               Standort (app/relay.py) samt NAS-Aufrufen (ohne Datenbank, ohne Netz)
# ==============================================================================
"""python -m unittest discover -s tests (in server/backend).

Statt des echten Agents holt hier ein kleiner Nachbau die Anfragen aus der
Warteschlange (wie /api/rmm/agent/wait), beantwortet sie mit einem
nachgebauten DSM und liefert die Antwort wie /api/rmm/agent/relay-results.
"""

import asyncio
import base64
import json
import unittest
from urllib.parse import parse_qs, urlparse

import httpx

from app import agentwake, nas, relay

AGENT = 7


def fake_dsm(method: str, url: str, headers: dict, body: bytes) -> dict:
    """Antwortet wie ein DSM - so, wie es der Agent zurückmelden würde."""
    u = urlparse(url)
    if u.path == "/webapi/query.cgi":
        data = {"success": True, "data": {"SYNO.API.Auth": {"path": "entry.cgi", "minVersion": 1, "maxVersion": 7},
                                          "SYNO.Core.System": {"path": "entry.cgi", "minVersion": 1, "maxVersion": 1}}}
    else:
        f = {k: v[0] for k, v in parse_qs(body.decode()).items()}
        if f.get("method") == "login":
            ok = f.get("passwd") == "geheim"
            data = {"success": True, "data": {"sid": "S", "synotoken": "T"}} if ok else \
                {"success": False, "error": {"code": 400}}
        elif f.get("_sid") == "S" and headers.get("x-syno-token") == "T":
            data = {"success": True, "data": {"model": "DS224+", "firmware_ver": "DSM 7.2.2"}}
        else:
            data = {"success": False, "error": {"code": 119}}
    raw = json.dumps(data).encode()
    return {"status": 200, "headers": {"Content-Type": ["application/json"], "Content-Length": [str(len(raw))],
                                       "Set-Cookie": ["a=1", "b=2"]},
            "body": base64.b64encode(raw).decode()}


class FakeAgent:
    """Holt ab, was der Server einreiht, und antwortet - wie runner.go/relay.go."""

    def __init__(self, handler=fake_dsm, agent_id: int = AGENT):
        self.handler, self.agent_id = handler, agent_id
        self.seen = []
        self.task = None

    async def _loop(self):
        ev = agentwake.event(self.agent_id)
        while True:
            agentwake.mark_waiting(self.agent_id)
            ev.clear()
            for item in relay.take(self.agent_id):
                self.seen.append(item)
                body = base64.b64decode(item["body"]) if item["body"] else b""
                hdr = {k.lower(): v for k, v in item["headers"].items()}
                res = self.handler(item["method"], item["url"], hdr, body)
                if res is not None:
                    relay.deliver(self.agent_id, [{"id": item["id"], **res}])
            try:
                await asyncio.wait_for(ev.wait(), 0.5)
            except asyncio.TimeoutError:
                pass

    def start(self):
        relay.set_caps(self.agent_id, ["relay"])
        agentwake.mark_waiting(self.agent_id)
        self.task = asyncio.create_task(self._loop())
        return self

    async def stop(self):
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
        relay.forget(self.agent_id)
        agentwake.forget(self.agent_id)


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        relay.forget(AGENT)
        agentwake.forget(AGENT)

    async def test_not_connected(self):
        with self.assertRaises(relay.RelayUnavailable):
            await relay.forward(AGENT, method="GET", url="https://10.0.0.5:5001/")
        # verbunden, aber alter Agent ohne Fähigkeit
        agentwake.mark_waiting(AGENT)
        with self.assertRaises(relay.RelayUnavailable) as cm:
            await relay.forward(AGENT, method="GET", url="https://10.0.0.5:5001/")
        self.assertIn("0.5.0", str(cm.exception))
        self.assertFalse(relay.ready(AGENT))

    async def test_transport_roundtrip(self):
        agent = FakeAgent().start()
        try:
            async with httpx.AsyncClient(transport=relay.transport(AGENT)) as c:
                r = await c.post("https://192.168.1.20:5001/webapi/entry.cgi", data={"method": "login", "passwd": "geheim"},
                                 headers={"X-Test": "1"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["data"]["sid"], "S")
            self.assertEqual(r.headers.get_list("set-cookie"), ["a=1", "b=2"])
            item = agent.seen[-1]
            self.assertEqual(item["method"], "POST")
            self.assertTrue(item["insecure"])                    # verify=False -> Selbstzertifikat erlaubt
            self.assertNotIn("host", {k.lower() for k in item["headers"]})
            self.assertEqual(item["headers"].get("x-test"), "1")
            self.assertTrue(relay.status(AGENT)["ready"])
        finally:
            await agent.stop()

    async def test_synology_over_agent(self):
        agent = FakeAgent().start()
        try:
            async with nas.Synology("https://127.0.0.1:5001", via_agent=AGENT) as dsm:
                await dsm.login("rimperio", "geheim")
                info = await dsm.call("SYNO.Core.System", "info", version=1)
            self.assertEqual(info["model"], "DS224+")
            with self.assertRaises(nas.NasError) as cm:
                async with nas.Synology("https://127.0.0.1:5001", via_agent=AGENT) as dsm:
                    await dsm.login("rimperio", "falsch")
            self.assertIn("Benutzer oder Passwort falsch", str(cm.exception))
            p = await nas.probe("127.0.0.1", 5001, "https", via_agent=AGENT)
            self.assertEqual(p["vendor"], "synology")
            self.assertEqual(p["via_agent_id"], AGENT)
        finally:
            await agent.stop()

    async def test_agent_errors_map_to_nas_errors(self):
        def broken(method, url, headers, body):
            if "timeout" in url:
                return {"error": "i/o timeout", "error_kind": "timeout"}
            if "tls" in url:
                return {"error": "x509: certificate signed by unknown authority", "error_kind": "tls"}
            return {"error": "dial tcp 10.0.0.9:5001: connect: connection refused", "error_kind": "connect"}
        agent = FakeAgent(broken).start()
        try:
            for host, expect in (("timeout", "über Agent"), ("tls", "Zertifikat nicht vertrauenswürdig"),
                                 ("refused", "connection refused")):
                with self.assertRaises(nas.NasError) as cm:
                    async with nas.Synology(f"https://{host}.lan:5001", via_agent=AGENT) as dsm:
                        await dsm.info()
                self.assertIn(expect, str(cm.exception))
        finally:
            await agent.stop()

    async def test_unanswered_request_times_out_and_expires(self):
        agent = FakeAgent(lambda *a: None).start()               # holt ab, antwortet nie
        slack, relay.REPLY_SLACK = relay.REPLY_SLACK, 0.5
        try:
            with self.assertRaises(relay.RelayUnavailable):
                await asyncio.wait_for(relay.forward(AGENT, method="GET", url="https://10.0.0.5/", timeout=1),
                                       timeout=30)
            self.assertEqual(relay._pending, {})
        finally:
            relay.REPLY_SLACK = slack
            await agent.stop()

    async def test_foreign_and_unknown_results_ignored(self):
        agentwake.mark_waiting(AGENT)
        relay.set_caps(AGENT, ["relay"])
        fut = asyncio.ensure_future(relay.forward(AGENT, method="GET", url="https://10.0.0.5/", timeout=2))
        await asyncio.sleep(0)
        item = relay.take(AGENT)[0]
        self.assertEqual(relay.deliver(AGENT + 1, [{"id": item["id"], "status": 200}]), 0)   # anderer Agent
        self.assertEqual(relay.deliver(AGENT, [{"id": "gibt-es-nicht", "status": 200}, "Müll"]), 0)
        self.assertEqual(relay.deliver(AGENT, [{"id": item["id"], "status": 204}]), 1)
        res = await fut
        self.assertEqual(res["status"], 204)
        self.assertEqual(res["body"], b"")

    async def test_queue_drops_abandoned(self):
        agentwake.mark_waiting(AGENT)
        relay.set_caps(AGENT, ["relay"])
        fut = asyncio.ensure_future(relay.forward(AGENT, method="GET", url="https://10.0.0.5/", timeout=5))
        await asyncio.sleep(0)
        fut.cancel()
        await asyncio.sleep(0)
        self.assertEqual(relay.take(AGENT), [])                  # aufgegebene Anfrage nicht mehr zustellen

    async def test_certificate_over_agent(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        import datetime
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "nas")])
        now = datetime.datetime.now(datetime.timezone.utc)
        der = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
               .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=30))
               .sign(key, hashes.SHA256())).public_bytes(serialization.Encoding.DER)

        def with_cert(method, url, headers, body):
            return {"status": 200, "headers": {}, "cert": base64.b64encode(der).decode()}
        agent = FakeAgent(with_cert).start()
        try:
            cert = await nas.certificate("127.0.0.1", 5001, via_agent=AGENT)
            self.assertTrue(cert["self_signed"])
            self.assertIn(cert["days_left"], (29, 30))
            self.assertTrue(agent.seen[-1]["cert"])
        finally:
            await agent.stop()


if __name__ == "__main__":
    unittest.main()
