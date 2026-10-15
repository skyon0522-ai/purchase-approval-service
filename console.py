"""Linux loopback console for the existing Temporal purchase workflow."""

import argparse
import asyncio
import fcntl
import json
import os
import re
import secrets
import signal
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from app import ROOT, SERVER_VERSION, TASK_QUEUE, emit
from approval import PurchaseApproval, fields, validate_request
from supplier import Supplier
from temporalio.client import WorkflowUpdateFailedError
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

ASSETS = ROOT / "web"


class Console:
    def __init__(self, client, supplier):
        self.client, self.supplier = client, supplier

    async def call(self, command, value):
        if command == "orders":
            fields(value, set())
            return {"orders": self.supplier.orders()}
        if command == "submit":
            validate_request(value)
            await self.client.start_workflow(PurchaseApproval.run, value,
                id=value["request_id"], task_queue=TASK_QUEUE,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE)
        else:
            expected = {"request_id"}
            if command == "revise":
                expected |= {"expected_revision", "item", "quantity", "unit_price_cents"}
            elif command == "decide":
                expected |= {"expected_revision", "decision", "actor"}
            elif command != "status":
                raise ValueError("Unknown console command")
            fields(value, expected)
            identity = value["request_id"]
            if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", identity):
                raise ValueError("Use a valid request ID")
            handle = self.client.get_workflow_handle(identity)
            payload = {key: item for key, item in value.items() if key != "request_id"}
            if command == "revise":
                await handle.execute_update(PurchaseApproval.revise, payload)
            elif command == "decide":
                await handle.execute_update(PurchaseApproval.decide, payload)
        handle = self.client.get_workflow_handle(value["request_id"])
        description = await handle.describe()
        return {"temporal_status": description.status.name,
                "request": await handle.query(PurchaseApproval.status)}


def make_server(console, loop, port):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Request bodies and identifiers do not belong in the console log.

        def reply(self, code, data, content_type="application/json"):
            if content_type == "application/json":
                data = json.dumps(data, ensure_ascii=True).encode()
            self.send_response(code)
            for name, value in {"Content-Type": content_type + "; charset=utf-8",
                    "Content-Length": str(len(data)), "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                    "Referrer-Policy": "no-referrer",
                    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"}.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def valid_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.valid_host():
                return self.reply(403, {"error": "Open the printed 127.0.0.1 address."})
            assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                      "/app.css": ("app.css", "text/css")}
            if self.path not in assets:
                return self.reply(404, {"error": "Not found"})
            filename, kind = assets[self.path]
            data = (ASSETS / filename).read_bytes()
            if filename == "index.html":
                data = data.replace(b"__CONSOLE_TOKEN__", token.encode())
            self.reply(200, data, kind)

        def do_POST(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if not self.valid_host() or self.headers.get("Origin") != origin or not secrets.compare_digest(self.headers.get("X-Console-Token", ""), token):
                return self.reply(403, {"error": "Use this console's local page and reload it after a restart."})
            if self.path not in {"/api/submit", "/api/status", "/api/revise", "/api/decide", "/api/orders"}:
                return self.reply(404, {"error": "Not found"})
            if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.reply(415, {"error": "Send a bounded JSON request."})
            future = None
            try:
                length = self.headers.get("Content-Length", "")
                if not length.isascii() or not length.isdigit() or not 0 < int(length) <= 16_384:
                    raise ValueError("Request body must contain 1..16384 bytes")
                self.connection.settimeout(5)
                data = self.rfile.read(int(length))
                if len(data) != int(length):
                    raise ValueError("Incomplete request body")
                value = json.loads(data)
                future = asyncio.run_coroutine_threadsafe(
                    asyncio.wait_for(console.call(self.path.removeprefix("/api/"), value), 15), loop)
                result = future.result(timeout=20)
                self.reply(200, result)
            except (ValueError, UnicodeError) as error:
                self.reply(400, {"error": str(error)})
            except WorkflowAlreadyStartedError:
                self.reply(409, {"error": "This request ID already exists. Load it or use a new ID."})
            except WorkflowUpdateFailedError as error:
                self.reply(409, {"error": f"Workflow rejected the change: {error.cause}"})
            except RPCError as error:
                if error.status == RPCStatusCode.NOT_FOUND:
                    self.reply(404, {"error": "Request was not found."})
                else:
                    self.reply(503, {"error": "The result is not confirmed. Reload the request before another action.", "outcome": "unknown"})
            except TimeoutError:
                if future is not None:
                    future.cancel()
                self.reply(504, {"error": "The result is not confirmed. Reload the request before another action.", "outcome": "unknown"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


async def run(args):
    data = Path(args.data_dir).expanduser().resolve()
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    # minimalism: one local console owns these development databases; no multiuser deployment.
    with (data / "console.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("This data directory already has a running console.") from error
        cache = ROOT / ".cache/server"
        cache.mkdir(parents=True, exist_ok=True)
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stopped.set)
        async with await WorkflowEnvironment.start_local(ip="127.0.0.1",
                download_dest_dir=str(cache), dev_server_download_version=SERVER_VERSION,
                dev_server_database_filename=str(data / "temporal.sqlite3")) as environment:
            supplier = Supplier(data / "supplier.sqlite3")
            with ThreadPoolExecutor(max_workers=4) as executor:
                async with Worker(environment.client, task_queue=TASK_QUEUE,
                        workflows=[PurchaseApproval], activities=[supplier.release], activity_executor=executor):
                    server = make_server(Console(environment.client, supplier), loop, args.port)
                    thread = Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    emit({"console": f"http://127.0.0.1:{server.server_port}",
                          "data_directory": str(data), "temporal": environment.client.service_client.config.target_host})
                    try:
                        await stopped.wait()
                    finally:
                        await asyncio.to_thread(server.shutdown)
                        server.server_close()
                        thread.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8088, choices=range(65536), metavar="PORT")
    parser.add_argument("--data-dir", default=str(ROOT / ".cache/console"))
    args = parser.parse_args()
    previous_umask = os.umask(0o077)
    try:
        asyncio.run(run(args))
    except (ValueError, OSError) as error:
        raise SystemExit(f"Console failed: {error}") from error
    finally:
        os.umask(previous_umask)
