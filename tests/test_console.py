"""HTTP trust boundaries and actual persisted Temporal console operations on Linux."""
import asyncio
import http.client
import json
import os
import re
import selectors
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

from app import ROOT
from console import make_server


def request(port, method, path, value=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=25)
    body = json.dumps(value).encode() if value is not None else None
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        return response.status, data, dict(response.getheaders())
    finally:
        connection.close()


def access(port):
    status, page, _ = request(port, "GET", "/")
    assert status == 200
    token = re.search(rb'name="console-token" content="([^"]+)"', page).group(1).decode()
    return {"Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json", "X-Console-Token": token}


class FakeConsole:
    def __init__(self):
        self.calls = []

    async def call(self, command, value):
        self.calls.append((command, value))
        if value.get("fail") == "value":
            raise ValueError("A deliberate boundary rejection")
        if value.get("fail") == "timeout":
            raise TimeoutError()
        return {"seen": command}


class HTTPBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.console = FakeConsole()
        self.server = make_server(self.console, asyncio.get_running_loop(), 0)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port
        self.headers = await asyncio.to_thread(access, self.port)

    async def asyncTearDown(self):
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        self.thread.join(5)

    async def post(self, value, headers=None, path="/api/submit"):
        return await asyncio.to_thread(request, self.port, "POST", path, value,
                                       self.headers if headers is None else headers)

    async def test_host_origin_and_run_token_reject_before_dispatch(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        for change in ({"Host": "attacker.invalid"}, {"Origin": "https://attacker.invalid"},
                       {"Origin": "null"}, {"X-Console-Token": "old-token"}):
            self.assertEqual((await self.post({}, {**self.headers, **change}))[0], 403)
        self.assertEqual((await self.post({}, {}))[0], 403)
        self.assertEqual(self.console.calls, [])

    async def test_json_limit_and_exact_route_allowlist(self):
        self.assertEqual((await self.post({}, {**self.headers, "Content-Type": "text/plain"}))[0], 415)
        self.assertEqual((await self.post({"large": "x" * 17000}))[0], 400)
        self.assertEqual((await self.post({}, path="/api/../../supplier.py"))[0], 404)
        for path in ("/../supplier.py", "/app.py", "/app.js?other=1"):
            self.assertEqual((await asyncio.to_thread(request, self.port, "GET", path))[0], 404)
        self.assertEqual(self.console.calls, [])

    async def test_static_assets_security_headers_and_forwarding(self):
        for path in ("/", "/app.js", "/app.css"):
            status, _, headers = await asyncio.to_thread(request, self.port, "GET", path)
            self.assertEqual(status, 200)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        status, body, _ = await self.post({})
        self.assertEqual((status, json.loads(body)), (200, {"seen": "submit"}))
        self.assertEqual(self.console.calls, [("submit", {})])

    async def test_known_rejection_and_unknown_timeout_are_distinct_without_retry(self):
        self.assertEqual((await self.post({"fail": "value"}))[0], 400)
        status, body, _ = await self.post({"fail": "timeout"})
        self.assertEqual(status, 504)
        self.assertEqual(json.loads(body)["outcome"], "unknown")
        self.assertEqual(len(self.console.calls), 2)


class ActualConsoleTests(unittest.TestCase):
    def test_operations_persisted_restart_and_owned_shutdown(self):
        (ROOT / ".cache").mkdir(exist_ok=True)
        with ExitStack() as stack, tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
            data = Path(directory) / "data"
            active = []
            checks = []

            def start():
                log = stack.enter_context(tempfile.TemporaryFile())
                process = subprocess.Popen([sys.executable, "-B", ROOT / "console.py",
                    "--port", "0", "--data-dir", data], cwd=ROOT,
                    stdout=subprocess.PIPE, stderr=log, start_new_session=True)
                active.append(process)
                selector = selectors.DefaultSelector()
                selector.register(process.stdout, selectors.EVENT_READ)
                try:
                    if not selector.select(60):
                        self.fail("Console readiness timed out")
                    line = process.stdout.readline()
                    if not line:
                        log.seek(0)
                        self.fail(log.read().decode(errors="replace"))
                    ready = json.loads(line)
                finally:
                    selector.close()
                port = urlsplit(ready["console"]).port
                return process, ready, port, access(port)

            def post(port, headers, command, value, code=200):
                status, body, _ = request(port, "POST", "/api/" + command, value, headers)
                self.assertEqual(status, code, body)
                return json.loads(body)

            def stop(process, ready):
                process.terminate()
                process.wait(20)
                self.assertEqual(process.returncode, 0)
                ports = (urlsplit(ready["console"]).port, int(ready["temporal"].rsplit(":", 1)[1]))
                for port in ports:
                    with self.assertRaises(OSError):
                        socket.create_connection(("127.0.0.1", port), timeout=1)

            def state(port, headers, identity):
                return post(port, headers, "status", {"request_id": identity})["request"]

            def final(port, headers, identity, expected):
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    value = state(port, headers, identity)
                    if value["status"] == expected:
                        return value
                    time.sleep(.1)
                self.fail(f"No final {expected} state: {value}")

            try:
                process, ready, port, headers = start()
                purchase = {"request_id": "console-main", "item": "USB keyboard", "quantity": 2, "unit_price_cents": 3200}
                initial = post(port, headers, "submit", purchase)["request"]
                self.assertEqual((initial["revision"], initial["status"]), (1, "pending"))
                post(port, headers, "submit", purchase, 409)
                post(port, headers, "submit", {**purchase, "request_id": "bad-money", "quantity": True}, 400)
                revised = post(port, headers, "revise", {**purchase, "quantity": 1, "expected_revision": 1})["request"]
                self.assertEqual(revised["revision"], 2)
                decision = {"request_id": "console-main", "expected_revision": 1, "decision": "approve", "actor": "reviewer"}
                post(port, headers, "decide", decision, 409)
                self.assertEqual(post(port, headers, "orders", {})["orders"], [])
                checks.append("Real HTTP create/revise; duplicate, malformed money and stale decision rejected without an order")

                stop(process, ready)
                old_headers = headers
                process, ready, port, headers = start()
                retained = state(port, headers, "console-main")
                self.assertEqual((retained["revision"], retained["quantity"], retained["status"]), (2, 1, "pending"))
                post(port, {**old_headers, "Origin": headers["Origin"]}, "status", {"request_id": "console-main"}, 403)
                collision = subprocess.run([sys.executable, "-B", ROOT / "console.py", "--port", "0", "--data-dir", data],
                    cwd=ROOT, capture_output=True, timeout=15, check=False)
                self.assertNotEqual(collision.returncode, 0)
                self.assertIn(b"already has a running console", collision.stderr)
                checks.append("Pending revision retained across full console/worker/server restart; old token and second console rejected")

                post(port, headers, "decide", {**decision, "expected_revision": 2})
                released = final(port, headers, "console-main", "released")
                self.assertEqual(released["order_id"], "mock-console-main")
                orders = post(port, headers, "orders", {})["orders"]
                self.assertEqual((len(orders), orders[0]["total_cents"], orders[0]["revision"]), (1, 3200, 2))
                self.assertNotEqual(request(port, "POST", "/api/decide", {**decision, "expected_revision": 2}, headers)[0], 200)
                self.assertEqual(post(port, headers, "orders", {})["orders"], orders)
                checks.append("Approval releases one matching mock order; repeated decision cannot create another")
                for action, expected in (("reject", "rejected"), ("cancel", "cancelled")):
                    identity = "console-" + action
                    post(port, headers, "submit", {**purchase, "request_id": identity})
                    post(port, headers, "decide", {**decision, "request_id": identity, "decision": action})
                    final(port, headers, identity, expected)
                self.assertEqual(len(post(port, headers, "orders", {})["orders"]), 1)
                checks.append("Reject and application cancel create no mock order")
                stop(process, ready)
                self.assertEqual(data.stat().st_mode & 0o777, 0o700)
                self.assertTrue(all(path.stat().st_mode & 0o077 == 0 for path in data.iterdir()))
                checks.append("SIGTERM closes both owned listeners; private local database modes retained")
                (ROOT / "evidence/console-integration.json").write_text(json.dumps({"status": "PASS",
                    "checks": checks, "actual_temporal_execution": True, "core_approval_logic_changed": False,
                    "supplier": "Local SQLite mock only", "authentication": "Not implemented"}, indent=2) + "\n")
            finally:
                for process in active:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(20)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait(5)
                    process.stdout.close()


if __name__ == "__main__":
    unittest.main()
