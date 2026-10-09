"""Revision-bound purchase approvals; all I/O belongs in activities or clients."""

import asyncio
import re
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

MAX_QUANTITY = 1000
MAX_UNIT_CENTS = 1_000_000
MAX_TOTAL_CENTS = 10_000_000
MAX_REVISION = 100


def fields(value: dict, required: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError(f"Expected exactly these fields: {', '.join(sorted(required))}")


def bounded_text(value: object, name: str, maximum: int) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError(f"{name} must contain 1..{maximum} characters")
    if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{name} must have no surrounding whitespace or control characters")


def bounded_int(value: object, name: str, maximum: int) -> None:
    # minimalism: integer cents avoid floating-point rounding and currency conversion.
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer in 1..{maximum}")


def validate_purchase(value: dict) -> None:
    bounded_text(value.get("item"), "item", 200)
    bounded_int(value.get("quantity"), "quantity", MAX_QUANTITY)
    bounded_int(value.get("unit_price_cents"), "unit_price_cents", MAX_UNIT_CENTS)
    if value["quantity"] * value["unit_price_cents"] > MAX_TOTAL_CENTS:
        raise ValueError(f"total_cents must not exceed {MAX_TOTAL_CENTS}")


def validate_request(value: dict) -> None:
    fields(value, {"request_id", "item", "quantity", "unit_price_cents"})
    request_id = value["request_id"]
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", request_id):
        raise ValueError("request_id must be 1..64 ASCII letters, digits, underscores or hyphens")
    validate_purchase(value)


@workflow.defn
class PurchaseApproval:
    def __init__(self) -> None:
        self.state: dict = {"status": "initializing"}

    @workflow.run
    async def run(self, request: dict) -> dict:
        try:
            validate_request(request)
            if request["request_id"] != workflow.info().workflow_id:
                raise ValueError("request_id must equal the Temporal workflow ID")
        except ValueError as error:
            raise ApplicationError(str(error), type="InvalidRequest", non_retryable=True) from error
        self.state = dict(request, revision=1, status="pending", currency="USD")
        try:
            await workflow.wait_condition(
                lambda: self.state["status"] != "pending" and workflow.all_handlers_finished()
            )
            if self.state["status"] == "approved":
                payload = {key: self.state[key] for key in (
                    "request_id", "item", "quantity", "unit_price_cents", "revision", "currency", "actor"
                )}
                payload["total_cents"] = payload["quantity"] * payload["unit_price_cents"]
                result = await workflow.execute_activity(
                    "release_order", payload, result_type=dict,
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(milliseconds=100),
                        maximum_interval=timedelta(seconds=1), maximum_attempts=3,
                    ),
                )
                self.state.update(status="released", order_id=result["order_id"])
            return dict(self.state)
        except asyncio.CancelledError:
            self.state["status"] = "cancelled"
            raise

    def pending_revision(self, expected_revision: object) -> None:
        bounded_int(expected_revision, "expected_revision", MAX_REVISION)
        if self.state["status"] != "pending":
            raise ValueError("Only a pending request can be changed or decided")
        if expected_revision != self.state["revision"]:
            raise ValueError(f"Stale revision: current revision is {self.state['revision']}")

    @workflow.update
    def revise(self, command: dict) -> dict:
        self.validate_revision(command)
        self.state.update({key: command[key] for key in ("item", "quantity", "unit_price_cents")})
        self.state["revision"] += 1
        return dict(self.state)

    @revise.validator
    def validate_revision(self, command: dict) -> None:
        fields(command, {"expected_revision", "item", "quantity", "unit_price_cents"})
        self.pending_revision(command["expected_revision"])
        if self.state["revision"] >= MAX_REVISION:
            raise ValueError("The request has reached its revision limit")
        validate_purchase(command)

    @workflow.update
    def decide(self, command: dict) -> dict:
        self.validate_decision(command)
        self.state.update(status={"approve": "approved", "reject": "rejected", "cancel": "cancelled"}[command["decision"]], actor=command["actor"])
        return dict(self.state)

    @decide.validator
    def validate_decision(self, command: dict) -> None:
        fields(command, {"expected_revision", "decision", "actor"})
        self.pending_revision(command["expected_revision"])
        bounded_text(command["actor"], "actor", 80)
        if not isinstance(command["decision"], str) or command["decision"] not in {"approve", "reject", "cancel"}:
            raise ValueError("decision must be approve, reject or cancel")

    @workflow.query
    def status(self) -> dict:
        return dict(self.state)
