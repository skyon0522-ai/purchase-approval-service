import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import temporalio
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError, WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError, CancelledError
from temporalio.service import RPCError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from app import ROOT, TASK_QUEUE, SERVER_VERSION
from approval import PurchaseApproval, validate_request
from supplier import Supplier
from tests.probe_workflow import SupplierProbe


def request(request_id="unit-request"):
    return {"request_id": request_id, "item": "USB keyboard", "quantity": 2, "unit_price_cents": 3200}


def release_payload(request_id="unit-request"):
    return dict(request(request_id), total_cents=6400, revision=1, actor="reviewer", currency="USD")


class BoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / ".cache").mkdir(exist_ok=True)

    def test_money_and_quantity_are_strict_bounded_integers(self):
        for field, values in {
            "quantity": [True, 0, -1, 1001, 1.5, "2"],
            "unit_price_cents": [False, 0, -1, 1_000_001, 32.00, "3200"],
        }.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_request(dict(request(), **{field: value}))
        with self.assertRaises(ValueError):
            validate_request(dict(request(), quantity=1000, unit_price_cents=100_001))
        validate_request(dict(request(), quantity=1000, unit_price_cents=10_000))

    def test_identity_description_and_fields_have_limits(self):
        for change in ({"request_id": "../bad"}, {"request_id": "x" * 65}, {"item": ""},
                       {"item": "x" * 201}, {"item": "\nitem"}, {"extra": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_request(dict(request(), **change))
        with self.assertRaises(ValueError):
            validate_request([])

    def test_sqlite_duplicate_is_idempotent_and_conflict_is_atomic(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
            sink = Supplier(Path(directory) / "supplier.sqlite3")
            payload = release_payload()
            first = sink.release(payload)
            self.assertEqual(first, sink.release(payload))
            with self.assertRaises(ApplicationError) as conflict:
                sink.release(dict(payload, item="Different item"))
            self.assertEqual(conflict.exception.type, "SupplierPayloadConflict")
            self.assertEqual(sink.orders(), [dict(payload, order_id=first["order_id"])])

    def test_supplier_rejects_inconsistent_total_and_currency(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
            sink = Supplier(Path(directory) / "supplier.sqlite3")
            for change in ({"total_cents": 6399}, {"total_cents": 6400.0}, {"currency": "EUR"}):
                with self.subTest(change=change), self.assertRaises(ApplicationError):
                    sink.release(dict(release_payload(), **change))
            self.assertEqual(sink.orders(), [])


class ActualServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_decisions_supplier_retry_and_replay(self):
        cache = ROOT / ".cache/server"
        cache.mkdir(parents=True, exist_ok=True)
        evidence = ROOT / "evidence"
        evidence.mkdir(exist_ok=True)
        checks = []
        processes = []
        logs = []
        async with await WorkflowEnvironment.start_local(download_dest_dir=str(cache), dev_server_download_version=SERVER_VERSION) as env:
            client = env.client
            endpoint = client.service_client.config.target_host
            port = int(endpoint.rsplit(":", 1)[1])
            with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
                database = Path(directory) / "supplier.sqlite3"
                sink = Supplier(database)

                async def start_worker():
                    log = (evidence / f"worker-{len(processes) + 1}.log").open("wb")
                    logs.append(log)
                    process = await asyncio.create_subprocess_exec(
                        sys.executable, str(ROOT / "app.py"), "--port", str(port), "worker", "--db", str(database),
                        cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=log,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                    processes.append(process)
                    line = await asyncio.wait_for(process.stdout.readline(), 30)
                    self.assertEqual(json.loads(line)["worker"], "ready")
                    return process

                async def stop_worker(process):
                    if process.returncode is None:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), 10)

                try:
                    first_worker = await start_worker()
                    identifier = f"restart-{uuid.uuid4().hex[:12]}"
                    handle = await client.start_workflow(PurchaseApproval.run, request(identifier), id=identifier, task_queue=TASK_QUEUE)
                    self.assertEqual((await asyncio.wait_for(handle.query(PurchaseApproval.status), 30))["status"], "pending")
                    bad_decisions = [
                        {"expected_revision": True, "decision": "approve", "actor": "reviewer"},
                        {"expected_revision": "1", "decision": "approve", "actor": "reviewer"},
                        {"expected_revision": 1, "decision": "approve", "actor": ""},
                        {"expected_revision": 1, "decision": "APPROVE", "actor": "reviewer"},
                        {"expected_revision": 1, "decision": [], "actor": "reviewer"},
                        {"expected_revision": 1, "decision": "approve", "actor": "reviewer", "extra": 1},
                    ]
                    for bad in bad_decisions:
                        with self.subTest(bad=bad), self.assertRaises(WorkflowUpdateFailedError):
                            await handle.execute_update(PurchaseApproval.decide, bad)
                    with self.assertRaises(WorkflowUpdateFailedError):
                        await handle.execute_update(PurchaseApproval.revise, {"expected_revision": 1, "item": "USB keyboard", "quantity": True, "unit_price_cents": 3200})
                    revised = await handle.execute_update(PurchaseApproval.revise, {"expected_revision": 1, "item": "USB keyboard", "quantity": 1, "unit_price_cents": 3200})
                    self.assertEqual(revised["revision"], 2)
                    with self.assertRaises(WorkflowUpdateFailedError):
                        await handle.execute_update(PurchaseApproval.decide, {"expected_revision": 1, "decision": "approve", "actor": "reviewer"})
                    checks.append("malformed edits/decisions and stale approval rejected; request remains pending at revision 2")

                    await stop_worker(first_worker)
                    self.assertEqual((await handle.describe()).status, WorkflowExecutionStatus.RUNNING)
                    self.assertEqual(sink.orders(), [])
                    second_worker = await start_worker()
                    retained = await asyncio.wait_for(handle.query(PurchaseApproval.status), 30)
                    self.assertEqual((retained["revision"], retained["quantity"], retained["status"]), (2, 1, "pending"))
                    self.assertNotEqual(first_worker.pid, second_worker.pid)
                    checks.append("pending revision survived actual worker process termination and restart")

                    await handle.execute_update(PurchaseApproval.decide, {"expected_revision": 2, "decision": "approve", "actor": "reviewer"})
                    with self.assertRaises((WorkflowUpdateFailedError, RPCError)):
                        await handle.execute_update(PurchaseApproval.revise, {"expected_revision": 2, "item": "Changed after approval", "quantity": 1, "unit_price_cents": 3200})
                    released = await asyncio.wait_for(handle.result(), 30)
                    self.assertEqual(released["status"], "released")
                    self.assertEqual(len(sink.orders()), 1)
                    self.assertEqual((sink.orders()[0]["revision"], sink.orders()[0]["total_cents"]), (2, 3200))
                    checks.append("approved revision released exactly one matching supplier order; subsequent edit rejected")

                    completed = [handle]
                    for action, expected in (("reject", "rejected"), ("cancel", "cancelled")):
                        identity = f"{action}-{uuid.uuid4().hex[:12]}"
                        other = await client.start_workflow(PurchaseApproval.run, request(identity), id=identity, task_queue=TASK_QUEUE)
                        await other.query(PurchaseApproval.status)
                        await other.execute_update(PurchaseApproval.decide, {"expected_revision": 1, "decision": action, "actor": "reviewer"})
                        self.assertEqual((await asyncio.wait_for(other.result(), 30))["status"], expected)
                        completed.append(other)
                    identity = f"temporal-cancel-{uuid.uuid4().hex[:12]}"
                    cancelled = await client.start_workflow(PurchaseApproval.run, request(identity), id=identity, task_queue=TASK_QUEUE)
                    await cancelled.query(PurchaseApproval.status)
                    await cancelled.cancel()
                    with self.assertRaises(WorkflowFailureError) as failure:
                        await asyncio.wait_for(cancelled.result(), 30)
                    self.assertIsInstance(failure.exception.cause, CancelledError)
                    self.assertEqual(len(sink.orders()), 1)
                    completed.append(cancelled)
                    checks.append("reject, application cancel and Temporal cancellation while pending created no supplier order")

                    invalid_id = f"invalid-{uuid.uuid4().hex[:12]}"
                    invalid = await client.start_workflow(PurchaseApproval.run, dict(request(invalid_id), unit_price_cents=32.0), id=invalid_id, task_queue=TASK_QUEUE)
                    with self.assertRaises(WorkflowFailureError) as failure:
                        await asyncio.wait_for(invalid.result(), 30)
                    self.assertEqual(failure.exception.cause.type, "InvalidRequest")
                    checks.append("malformed submitted money fails the real workflow with InvalidRequest")

                    payload = release_payload(f"retry-{uuid.uuid4().hex[:12]}")
                    attempts = []

                    @activity.defn(name="release_order")
                    def lost_acknowledgement(value: dict) -> dict:
                        answer = sink.release(value)
                        attempts.append(activity.info().attempt)
                        if activity.info().attempt == 1:
                            raise RuntimeError("Simulated acknowledgement loss after SQLite commit")
                        return answer

                    with ThreadPoolExecutor(max_workers=2) as executor:
                        async with Worker(client, task_queue="supplier-probe", workflows=[SupplierProbe], activities=[lost_acknowledgement], activity_executor=executor):
                            result = await asyncio.wait_for(client.execute_workflow(SupplierProbe.run, {"payload": payload, "conflict": False}, id=f"probe-{uuid.uuid4().hex}", task_queue="supplier-probe"), 30)
                            self.assertEqual(result["first"], result["second"])
                            self.assertEqual(attempts, [1, 2, 1, 2])
                            self.assertEqual(len(sink.orders()), 2)
                            with self.assertRaises(WorkflowFailureError) as conflict:
                                await asyncio.wait_for(client.execute_workflow(SupplierProbe.run, {"payload": payload, "conflict": True}, id=f"conflict-{uuid.uuid4().hex}", task_queue="supplier-probe"), 30)
                            self.assertEqual(conflict.exception.cause.cause.type, "SupplierPayloadConflict")
                            self.assertEqual(len(sink.orders()), 2)
                    checks.append("real Temporal retries after supplier commit and repeated release produce one order; conflicting reuse fails")

                    for index, item in enumerate(completed):
                        history = await item.fetch_history()
                        await Replayer(workflows=[PurchaseApproval]).replay_workflow(history)
                        (evidence / f"history-{index + 1}.json").write_text(history.to_json(), encoding="utf-8")
                    checks.append("released, rejected, application-cancelled and Temporal-cancelled histories replay deterministically")
                    receipt = {
                        "sdk_version": temporalio.__version__, "server_endpoint": endpoint,
                        "worker_process_ids": [first_worker.pid, second_worker.pid],
                        "checks": checks, "supplier_orders": len(sink.orders()),
                        "released_request": released, "server_binaries": [p.name for p in cache.iterdir()],
                    }
                    (evidence / "integration.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
                    print("INTEGRATION_RESULT " + json.dumps(receipt, sort_keys=True))
                finally:
                    for process in processes:
                        await stop_worker(process)
                    for log in logs:
                        log.close()


if __name__ == "__main__":
    (ROOT / ".cache").mkdir(exist_ok=True)
    unittest.main()
