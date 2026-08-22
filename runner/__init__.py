"""Phase-4 bounded-loop runner (BLUEPRINT section 4 tree; ADR-0010).

A supervisory layer over MULTIPLE complete Sentinel runs, not a second
Sentinel pipeline. The package is deliberately split so the safety logic
can be reasoned about without the domain:

* ``runner.breakers`` — pure ADR-0010 predicates, stdlib only.
* ``runner.loop``     — the domain-free bounded supervisor, stdlib only.
* ``runner.state``    — durable loop state on the shared SQLite ledger.
* ``runner.sentinel_adapter`` — the sole Sentinel integration boundary.
* ``runner.__main__`` — ``python -m runner``, stub mode only.

Nothing here is provider-capable. Agent/provider mode refuses
fail-closed, and no model call is reachable from any code path in this
package.
"""
