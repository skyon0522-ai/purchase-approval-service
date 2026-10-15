# Local console boundary

The console is a Linux, single-operator development example. Actor names are
caller-supplied labels. It has no authenticated users, approval roles or real
supplier integration. Keep it on the printed `127.0.0.1` address; do not expose
or proxy it as a shared service. Python's development HTTP server is not a
production deployment stack.

The HTTP layer checks the exact loopback Host, same-origin POSTs, a per-run
browser token, JSON content type and a 16 KiB body limit. These controls reduce
cross-site browser requests; they do not authenticate another process or user
on this machine. The page renders request text as text and loads only local
assets. The existing workflow still validates every revision and decision.

Use fictional data. The data directory contains persistent Temporal history and
the separate SQLite mock-supplier database. Newly created directories/files use
private Linux permissions; existing directory permissions are preserved. These
files are not encrypted or signed. Never commit databases, caches, private logs
or real purchase details. A second console cannot share the same locked data
directory. Graceful restart was tested; power-loss recovery and backup restore
were not.

A lost response does not establish whether a write happened. The console does
not automatically retry a write; reload the request to inspect its actual state
before deciding again. Mock-supplier idempotency is not a guarantee for a real
supplier API. Cancellation after release does not reverse an order.

For a bug, use a synthetic reproducer in the repository's
[issues](https://github.com/skyon0522-ai/purchase-approval-service/issues).
For sensitive vulnerabilities, use **Report a vulnerability** on the
[security page](https://github.com/skyon0522-ai/purchase-approval-service/security)
if available; otherwise request a private channel without posting exploit
details or sensitive data. This does not assert that private reporting is enabled.
