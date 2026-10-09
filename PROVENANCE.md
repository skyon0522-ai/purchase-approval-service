# Source and adaptation record

The upstream examples were inspected before this application was built. This repository adapts their effective workflow and verification patterns rather than republishing their sample collection.

| Reference | Pattern retained | Application change |
| --- | --- | --- |
| [Approval and updates](https://github.com/temporalio/samples-python/blob/ae5df2f5cd1f58a2c55d1db0fd46a7f2c6df67a1/message_passing/introduction/workflows.py) | Workflow waits for a decision; queries inspect state; validators reject invalid updates | Purchase revisions, strict money limits and decisions bound to a particular revision |
| [Atomic message handlers](https://github.com/temporalio/samples-python/blob/ae5df2f5cd1f58a2c55d1db0fd46a7f2c6df67a1/message_passing/safe_message_handlers/workflow.py) | Idempotency and waiting for handlers before completing | Synchronous state updates need no asynchronous lock; the supplier uses a durable SQLite uniqueness constraint |
| [Original workflow test](https://github.com/temporalio/samples-python/blob/ae5df2f5cd1f58a2c55d1db0fd46a7f2c6df67a1/tests/hello/hello_signal_test.py) | Real client/worker execution and workflow status assertions | Terminate and restart worker processes while approval is pending |
| [Replay example](https://github.com/temporalio/samples-python/blob/ae5df2f5cd1f58a2c55d1db0fd46a7f2c6df67a1/replay/replayer.py) | Replay actual workflow histories | Save and replay released, rejected and both cancellation histories |
| [SDK 1.34.0 testing examples](https://github.com/temporalio/sdk-python/blob/1.34.0/README.md#testing) | Official `WorkflowEnvironment` and worker testing | Use `start_local`, with a downloaded official CLI development server, instead of time skipping |
| [Upstream CI](https://github.com/temporalio/samples-python/blob/ae5df2f5cd1f58a2c55d1db0fd46a7f2c6df67a1/.github/workflows/ci.yml) | Install dependencies and execute actual workflow tests | A single Python 3.12 job runs this product's tests and demo; action versions were resolved from their official release/tag APIs |

Upstream sample commit: `ae5df2f5cd1f58a2c55d1db0fd46a7f2c6df67a1`, inspected on 2026-10-09. Its [MIT license](https://github.com/temporalio/samples-python/blob/ae5df2f5cd1f58a2c55d1db0fd46a7f2c6df67a1/LICENSE) notice is retained in this repository's LICENSE. The new supplier sink, purchase schema, CLI, fixtures and application-specific tests were written for this product.

[PyPI metadata](https://pypi.org/pypi/temporalio/1.34.0/json) established the released SDK version and Python requirement; `requirements.lock` records the resolved Python package versions. The observed official development binary reports CLI 1.9.1, Server 1.32.0 and UI 2.54.1, consistent with the [CLI release](https://github.com/temporalio/cli/releases/tag/v1.9.1). The binary hash and actual commands belong in the verification receipt. Binary caches, the virtual environment and SQLite files are excluded from source publication.

All fixture requests and saved histories are synthetic. No real customer data, authenticated identity, supplier submission, production use or revenue is implied. Dependency licenses are separate from this source license; no dependency sources or server binary are vendored for publication.
