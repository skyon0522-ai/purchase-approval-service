"""Run one complete local approval using an official Temporal development server."""

import asyncio
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from temporalio.client import WorkflowUpdateFailedError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app import ROOT, TASK_QUEUE, SERVER_VERSION, emit
from approval import PurchaseApproval
from supplier import Supplier


async def main() -> None:
    cache = ROOT / ".cache/server"
    cache.mkdir(parents=True, exist_ok=True)
    async with await WorkflowEnvironment.start_local(download_dest_dir=str(cache), dev_server_download_version=SERVER_VERSION) as env:
        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
            sink = Supplier(Path(directory) / "supplier.sqlite3")
            with ThreadPoolExecutor(max_workers=2) as executor:
                async with Worker(env.client, task_queue=TASK_QUEUE, workflows=[PurchaseApproval],
                                  activities=[sink.release], activity_executor=executor):
                    request = {"request_id": "purchase-demo-001", "item": "USB keyboard", "quantity": 2, "unit_price_cents": 3200}
                    handle = await env.client.start_workflow(PurchaseApproval.run, request, id=request["request_id"], task_queue=TASK_QUEUE)
                    emit({"step": "submitted", "request": await handle.query(PurchaseApproval.status)})
                    revision = {"expected_revision": 1, "item": "USB keyboard", "quantity": 1, "unit_price_cents": 3200}
                    emit({"step": "revised", "request": await handle.execute_update(PurchaseApproval.revise, revision)})
                    try:
                        await handle.execute_update(PurchaseApproval.decide, {"decision": "approve", "expected_revision": 1, "actor": "reviewer"})
                    except WorkflowUpdateFailedError:
                        emit({"step": "stale_decision", "expected_revision": 1, "current_revision": 2, "result": "rejected"})
                    else:
                        raise AssertionError("The stale approval was incorrectly accepted")
                    await handle.execute_update(PurchaseApproval.decide, {"decision": "approve", "expected_revision": 2, "actor": "reviewer"})
                    emit({"step": "released", "request": await handle.result()})
                    emit({"step": "supplier_orders", "count": len(sink.orders()), "orders": sink.orders()})


if __name__ == "__main__":
    asyncio.run(main())
