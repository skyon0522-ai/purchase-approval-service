# Implementation brief and scope boundary

Build a local purchase-request approval application, using actual Temporal execution rather than a simulation. The reader is a developer evaluating a small business-workflow integration.

## Reference inspected before implementation

- temporalio/samples-python commit `ae5df2f5cd1f58a2c55d1db0fd46a7f2c6df67a1`: `message_passing/introduction/workflows.py`, its tests, `safe_message_handlers/workflow.py`, cancellation/compensation tests, `replay/replayer.py`, and the MIT license. The reference establishes approval messages, update validators, waiting for decisions, handler completion, idempotency and history replay.
- Official SDK testing and replay examples: https://github.com/temporalio/sdk-python#testing and https://github.com/temporalio/sdk-python#workflow-replay. Use a real local development server through `WorkflowEnvironment.start_local`; do not replace a failed server with a fake.
- PyPI metadata checked: https://pypi.org/pypi/temporalio/json reports Temporal Python SDK 1.34.0 and Python >=3.10. Pin the installed package and record resolved dependencies.

## Small coherent product

One Temporal workflow holds one request. The request has a stable ID, a bounded item description, quantity, integer USD unit-price cents and a revision. Only a pending request can be edited. Each decision includes the current revision and a bounded actor label. Approval schedules a local SQLite mock-supplier activity; rejection and cancellation create no supplier order. The SQLite sink uses the request/workflow ID as its unique idempotency key and rejects conflicting payloads.

The command-line interface provides local server, worker, submit, status, revise, approve, reject and cancel operations. A scripted demo supplies reviewable output. There is no frontend, cloud adapter or LLM integration.

## Acceptance evidence

Actual-server integration tests must demonstrate: pending state survives worker shutdown/restart; a revision update invalidates stale decisions; malformed decisions and edits are rejected; edits after approval are rejected; the correct approval produces one order; duplicate supplier release is idempotent; conflicting reuse of a supplier key fails; rejection and cancellation produce no order; completed history replays deterministically. Unit tests exercise strict integer/size/range boundaries and SQLite behavior. Save commands, versions, server metadata, test counts and observed demo output locally.

## Limits and authority

This is a single-node local example. Actor labels are not authenticated identities; no roles, payment, real supplier sending, deployment, production availability or customer validation is claimed. Waiting-state restart proof concerns the worker while the Temporal server remains running. Initial packages and an official server binary may be downloaded; no paid cloud account or provider call is authorized. Publication requires Human approval. Keep all generated files in this repository or its assigned adjacent temporal receipt files. After two failed corrections to a real environment dependency, stop and report it.
