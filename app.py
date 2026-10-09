"""Local-only CLI; Temporal executes the workflow and SQLite mocks the supplier."""

import argparse
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from temporalio.client import Client, WorkflowUpdateFailedError
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from approval import PurchaseApproval, validate_request
from supplier import Supplier

ROOT = Path(__file__).resolve().parent
TASK_QUEUE = "purchase-approval-local"
SERVER_VERSION = "v1.9.1"


def emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=True, sort_keys=True), flush=True)


def read_json(path: str) -> dict:
    raw = Path(path).read_bytes()
    if len(raw) > 16_384:
        raise ValueError("Command files must not exceed 16 KiB")
    return json.loads(raw)


async def main(args: argparse.Namespace) -> None:
    if args.command == "server":
        cache = ROOT / ".cache/server"
        cache.mkdir(parents=True, exist_ok=True)
        database = ROOT / ".cache/temporal.sqlite3"
        async with await WorkflowEnvironment.start_local(
            port=args.port, download_dest_dir=str(cache),
            dev_server_database_filename=str(database),
            dev_server_download_version=SERVER_VERSION,
        ) as env:
            emit({"server": env.client.service_client.config.target_host, "database": str(database)})
            await asyncio.Event().wait()
        return
    if args.command == "orders":
        emit(Supplier(args.db).orders())
        return
    client = await Client.connect(f"127.0.0.1:{args.port}")
    if args.command == "worker":
        supplier = Supplier(args.db)
        with ThreadPoolExecutor(max_workers=4) as executor:
            worker = Worker(client, task_queue=TASK_QUEUE, workflows=[PurchaseApproval],
                            activities=[supplier.release], activity_executor=executor)
            emit({"worker": "ready", "task_queue": TASK_QUEUE, "supplier_database": str(Path(args.db).resolve())})
            await worker.run()
        return
    if args.command == "submit":
        request = read_json(args.file)
        validate_request(request)
        handle = await client.start_workflow(PurchaseApproval.run, request,
                                             id=request["request_id"], task_queue=TASK_QUEUE,
                                             id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE)
        emit({"request_id": handle.id, "run_id": handle.first_execution_run_id})
        return
    handle = client.get_workflow_handle(args.request_id)
    if args.command == "status":
        description = await handle.describe()
        emit({"temporal_status": description.status.name, "request": await handle.query(PurchaseApproval.status)})
    elif args.command == "revise":
        emit(await handle.execute_update(PurchaseApproval.revise, read_json(args.file)))
    else:
        emit(await handle.execute_update(PurchaseApproval.decide, {
            "decision": args.command, "expected_revision": args.revision, "actor": args.actor,
        }))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--port", type=int, default=7233, choices=range(1, 65536), metavar="PORT")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("server", help="Start the official Temporal local server")
    for name in ("worker", "orders"):
        command = commands.add_parser(name)
        command.add_argument("--db", default=str(ROOT / ".cache/supplier.sqlite3"))
    submit = commands.add_parser("submit")
    submit.add_argument("file")
    status = commands.add_parser("status")
    status.add_argument("request_id")
    revise = commands.add_parser("revise")
    revise.add_argument("request_id")
    revise.add_argument("file")
    for name in ("approve", "reject", "cancel"):
        decision = commands.add_parser(name)
        decision.add_argument("request_id")
        decision.add_argument("--revision", type=int, required=True)
        decision.add_argument("--actor", required=True)
    return result


if __name__ == "__main__":
    try:
        asyncio.run(main(parser().parse_args()))
    except KeyboardInterrupt:
        pass
    except (ValueError, WorkflowUpdateFailedError, WorkflowAlreadyStartedError, RPCError) as error:
        raise SystemExit(f"Request failed: {error}") from error
