"""A local mock supplier; SQLite uniqueness makes acknowledgement retries safe."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationError

from approval import bounded_int, bounded_text, fields, validate_request, MAX_REVISION


def validate_release(payload: dict) -> None:
    fields(payload, {"request_id", "item", "quantity", "unit_price_cents", "total_cents", "revision", "actor", "currency"})
    validate_request({key: payload[key] for key in ("request_id", "item", "quantity", "unit_price_cents")})
    bounded_int(payload["revision"], "revision", MAX_REVISION)
    bounded_text(payload["actor"], "actor", 80)
    if payload["currency"] != "USD":
        raise ValueError("Only USD is supported")
    if type(payload["total_cents"]) is not int or payload["total_cents"] != payload["quantity"] * payload["unit_price_cents"]:
        raise ValueError("total_cents must exactly match quantity * unit_price_cents")


class Supplier:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS orders (request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, order_id TEXT NOT NULL UNIQUE)")

    @activity.defn(name="release_order")
    def release(self, payload: dict) -> dict:
        try:
            validate_release(payload)
        except ValueError as error:
            raise ApplicationError(str(error), type="InvalidSupplierPayload", non_retryable=True) from error
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        order_id = f"mock-{payload['request_id']}"
        with closing(sqlite3.connect(self.db_path, timeout=5)) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute("SELECT payload, order_id FROM orders WHERE request_id = ?", (payload["request_id"],)).fetchone()
                if existing:
                    if existing[0] != canonical:
                        raise ApplicationError("Idempotency key was already used with a different payload", type="SupplierPayloadConflict", non_retryable=True)
                    order_id = existing[1]
                else:
                    connection.execute("INSERT INTO orders VALUES (?, ?, ?)", (payload["request_id"], canonical, order_id))
        return {"order_id": order_id, "request_id": payload["request_id"]}

    def orders(self) -> list[dict]:
        with closing(sqlite3.connect(self.db_path)) as connection:
            return [dict(json.loads(payload), order_id=order_id) for payload, order_id in connection.execute("SELECT payload, order_id FROM orders ORDER BY request_id")]
