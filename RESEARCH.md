# Research and the tested boundary

Peter Alvaro, Joshua Rosen and Joseph M. Hellerstein. **Lineage-driven Fault
Injection.** ACM SIGMOD 2015, pp. 331–346. DOI:
[10.1145/2723372.2723711](https://doi.org/10.1145/2723372.2723711).
The [official accepted research list](https://sigmod2015.sigmodconf.hosting.acm.org/sigmod_research_list.shtml)
records the conference paper; the [author-hosted paper](https://people.ucsc.edu/~palvaro/molly.pdf),
sections 1–1.2, describes using outcome lineage to select fault-injection executions.

This is a design reference for examining end-to-end outcomes under failure.
The checks here are manually selected:

| Failure | Observable outcome | Evidence |
| --- | --- | --- |
| Supplier commit followed by lost acknowledgement | Activity retry leaves one matching order. | `tests/test_approval.py` |
| Console stopped with revision 2 pending | Restart loads the same pending revision and quantity. | `tests/test_console.py` |
| Approval response discarded after the real server handled it | Browser avoids automatic write retry; reload finds one released order. | `evidence/linux-console-20261015.json` |

This repository does not implement LDFI or MOLLY, infer failure schedules from
lineage, reproduce the paper's benchmarks or establish bounded completeness.
Its passing tests cover only the stated local executions. The publication
does not establish production reliability, real supplier idempotency or customer use.
