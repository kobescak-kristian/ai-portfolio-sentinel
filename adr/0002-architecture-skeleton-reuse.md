# 0002 — Reuse ai-compliance-orchestrator's control-plane skeleton

Status: ACCEPTED (2026-07-12)

## Context

This system's learning objective (BLUEPRINT.md §0, §1) is the long-horizon
operations layer — scheduling, run-over-run state, dedup, circuit breakers,
cost telemetry, bounded iteration loops. That layer is new ground. The
control-plane primitives underneath it (typed handoff contracts, an audit
ledger, a deterministic state machine, a caged-agent pattern, frozen-gate
eval discipline, failure-injection tests, stubs-before-models phasing) are
not new ground — they were already built and proven in
ai-compliance-orchestrator.

## Decision

Reuse the ai-compliance-orchestrator control-plane skeleton (pinned,
read-only reference) as-is. Build only the new flesh listed in BLUEPRINT.md
§3: scheduler, run-over-run state and finding lifecycle, circuit breakers,
cost telemetry from run zero, the bounded-loop runner, and live-inventory
derivation.

## Consequences

Learning time and build time both go to the actually-new layer instead of
re-deriving already-solved primitives. Cost: this repo carries a dependency
on understanding a second repo's design (read-only reference, not a runtime
dependency) to make sense of the skeleton it inherited.
