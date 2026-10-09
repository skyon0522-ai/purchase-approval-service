"""Test-only workflow to repeat a real supplier Activity through Temporal."""

from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn
class SupplierProbe:
    @workflow.run
    async def run(self, command: dict) -> dict:
        options = {
            "start_to_close_timeout": timedelta(seconds=10),
            "retry_policy": RetryPolicy(initial_interval=timedelta(milliseconds=100), maximum_attempts=3),
            "result_type": dict,
        }
        first = await workflow.execute_activity("release_order", command["payload"], **options)
        repeated = dict(command["payload"])
        if command["conflict"]:
            repeated["item"] = "Changed payload under the same key"
        second = await workflow.execute_activity("release_order", repeated, **options)
        return {"first": first, "second": second}
