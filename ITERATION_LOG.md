<!-- ITERATION_LOG — DERIVED PUBLIC EVIDENCE, machine-written, append-only. NOT authoritative loop state. -->
# ITERATION_LOG — ai-portfolio-sentinel bounded loop

**This file is derived public evidence. It is NOT authoritative loop
state.** The durable SQLite ledger (`loop_runs`, `loop_iterations`,
`runs`, `tasks`, `findings`) and the durable `CostRow`s are
authoritative. Every figure below is rendered from that state and is
checked back against it; where the two disagree, the ledger is right
and this file is wrong.

<!-- sentinel:phase4-loop loop-p4g-leg1::leg1-normal-n10 -->
## LEG1 / leg1-normal-n10 — loop loop-p4g-leg1

LEG1 case leg1-normal-n10 (SYNTHETIC) ran loop `loop-p4g-leg1` under N = 10, a loop ceiling of 750000 micro-EUR and a failure threshold of 3. It recorded 10 iterations and stopped on COMPLETED_ITERATION_CAP with exit code 0. These figures are derived from durable loop state, not authoritative over it.

<!-- sentinel:phase4-meta loop-p4g-leg1::leg1-normal-n10 -->
```json
{"alert_label":null,"classification":"SYNTHETIC","exit_code":0,"failure_threshold":3,"gate_case":"leg1-normal-n10","gate_leg":"LEG1","iterations_recorded":10,"loop_budget_eur_micros":750000,"loop_id":"loop-p4g-leg1","max_iterations":10,"source_sha":"338ad691f657ae123763a4810ed8170880bd8c7f","stop_reason":"COMPLETED_ITERATION_CAP"}
```
<!-- sentinel:phase4-meta-end loop-p4g-leg1::leg1-normal-n10 -->

<!-- sentinel:phase4-iterations loop-p4g-leg1::leg1-normal-n10 -->
```jsonl
{"bound_run_id":"r-p4g-l1-000","breaker":null,"consecutive_failures_after":0,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":40,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:04+00:00","iteration_cost_eur_micros":0,"iteration_index":0,"iteration_state":"FINALIZED","planned_run_id":"r-p4g-l1-000","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:02+00:00","tasks_created":80,"tasks_terminal":80}
{"bound_run_id":"r-p4g-l1-001","breaker":null,"consecutive_failures_after":0,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":40,"finished_at_utc":"2026-01-01T00:00:08+00:00","iteration_cost_eur_micros":0,"iteration_index":1,"iteration_state":"FINALIZED","planned_run_id":"r-p4g-l1-001","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:06+00:00","tasks_created":80,"tasks_terminal":80}
{"bound_run_id":"r-p4g-l1-002","breaker":null,"consecutive_failures_after":0,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":40,"finished_at_utc":"2026-01-01T00:00:12+00:00","iteration_cost_eur_micros":0,"iteration_index":2,"iteration_state":"FINALIZED","planned_run_id":"r-p4g-l1-002","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:10+00:00","tasks_created":80,"tasks_terminal":80}
{"bound_run_id":"r-p4g-l1-003","breaker":null,"consecutive_failures_after":0,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":40,"finished_at_utc":"2026-01-01T00:00:16+00:00","iteration_cost_eur_micros":0,"iteration_index":3,"iteration_state":"FINALIZED","planned_run_id":"r-p4g-l1-003","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:14+00:00","tasks_created":80,"tasks_terminal":80}
{"bound_run_id":"r-p4g-l1-004","breaker":null,"consecutive_failures_after":0,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":40,"finished_at_utc":"2026-01-01T00:00:20+00:00","iteration_cost_eur_micros":0,"iteration_index":4,"iteration_state":"FINALIZED","planned_run_id":"r-p4g-l1-004","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:18+00:00","tasks_created":80,"tasks_terminal":80}
{"bound_run_id":"r-p4g-l1-005","breaker":null,"consecutive_failures_after":0,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":40,"finished_at_utc":"2026-01-01T00:00:24+00:00","iteration_cost_eur_micros":0,"iteration_index":5,"iteration_state":"FINALIZED","planned_run_id":"r-p4g-l1-005","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:22+00:00","tasks_created":80,"tasks_terminal":80}
{"bound_run_id":"r-p4g-l1-006","breaker":null,"consecutive_failures_after":0,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":40,"finished_at_utc":"2026-01-01T00:00:28+00:00","iteration_cost_eur_micros":0,"iteration_index":6,"iteration_state":"FINALIZED","planned_run_id":"r-p4g-l1-006","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:26+00:00","tasks_created":80,"tasks_terminal":80}
{"bound_run_id":"r-p4g-l1-007","breaker":null,"consecutive_failures_after":0,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":40,"finished_at_utc":"2026-01-01T00:00:32+00:00","iteration_cost_eur_micros":0,"iteration_index":7,"iteration_state":"FINALIZED","planned_run_id":"r-p4g-l1-007","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:30+00:00","tasks_created":80,"tasks_terminal":80}
{"bound_run_id":"r-p4g-l1-008","breaker":null,"consecutive_failures_after":0,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":40,"finished_at_utc":"2026-01-01T00:00:36+00:00","iteration_cost_eur_micros":0,"iteration_index":8,"iteration_state":"FINALIZED","planned_run_id":"r-p4g-l1-008","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:34+00:00","tasks_created":80,"tasks_terminal":80}
{"bound_run_id":"r-p4g-l1-009","breaker":null,"consecutive_failures_after":0,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":40,"finished_at_utc":"2026-01-01T00:00:40+00:00","iteration_cost_eur_micros":0,"iteration_index":9,"iteration_state":"FINALIZED","planned_run_id":"r-p4g-l1-009","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:38+00:00","tasks_created":80,"tasks_terminal":80}
```
<!-- sentinel:phase4-iterations-end loop-p4g-leg1::leg1-normal-n10 -->

<!-- sentinel:phase4-loop-end loop-p4g-leg1::leg1-normal-n10 -->
<!-- sentinel:phase4-loop loop-p4g-leg2a::leg2a-749999-midloop -->
## LEG2 / leg2a-749999-midloop — loop loop-p4g-leg2a

LEG2 case leg2a-749999-midloop (SEEDED_FAULT) ran loop `loop-p4g-leg2a` under N = 3, a loop ceiling of 750000 micro-EUR and a failure threshold of 3. It recorded 3 iterations and stopped on COMPLETED_ITERATION_CAP with exit code 0. These figures are derived from durable loop state, not authoritative over it.

<!-- sentinel:phase4-meta loop-p4g-leg2a::leg2a-749999-midloop -->
```json
{"alert_label":null,"classification":"SEEDED_FAULT","exit_code":0,"failure_threshold":3,"gate_case":"leg2a-749999-midloop","gate_leg":"LEG2","iterations_recorded":3,"loop_budget_eur_micros":750000,"loop_id":"loop-p4g-leg2a","max_iterations":3,"source_sha":"338ad691f657ae123763a4810ed8170880bd8c7f","stop_reason":"COMPLETED_ITERATION_CAP"}
```
<!-- sentinel:phase4-meta-end loop-p4g-leg2a::leg2a-749999-midloop -->

<!-- sentinel:phase4-iterations loop-p4g-leg2a::leg2a-749999-midloop -->
```jsonl
{"bound_run_id":"r-p4g-l2a-seed","breaker":null,"consecutive_failures_after":0,"cumulative_cost_eur_micros":749999,"effective_allowance_eur_micros":null,"findings_new":0,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:01+00:00","iteration_cost_eur_micros":749999,"iteration_index":0,"iteration_state":"FINALIZED","planned_run_id":"r-p4g-l2a-seed","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:00+00:00","tasks_created":0,"tasks_terminal":0}
{"bound_run_id":"r-leg2a-000","breaker":null,"consecutive_failures_after":0,"cumulative_cost_eur_micros":749999,"effective_allowance_eur_micros":1,"findings_new":0,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:04+00:00","iteration_cost_eur_micros":0,"iteration_index":1,"iteration_state":"FINALIZED","planned_run_id":"r-leg2a-000","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:02+00:00","tasks_created":0,"tasks_terminal":0}
{"bound_run_id":"r-leg2a-001","breaker":null,"consecutive_failures_after":0,"cumulative_cost_eur_micros":749999,"effective_allowance_eur_micros":1,"findings_new":0,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:08+00:00","iteration_cost_eur_micros":0,"iteration_index":2,"iteration_state":"FINALIZED","planned_run_id":"r-leg2a-001","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:06+00:00","tasks_created":0,"tasks_terminal":0}
```
<!-- sentinel:phase4-iterations-end loop-p4g-leg2a::leg2a-749999-midloop -->

<!-- sentinel:phase4-loop-end loop-p4g-leg2a::leg2a-749999-midloop -->
<!-- sentinel:phase4-loop loop-p4g-leg2b::leg2b-exact-cap-midloop -->
## LEG2 / leg2b-exact-cap-midloop — loop loop-p4g-leg2b

LEG2 case leg2b-exact-cap-midloop (SEEDED_FAULT) ran loop `loop-p4g-leg2b` under N = 3, a loop ceiling of 750000 micro-EUR and a failure threshold of 3. It recorded 1 iterations and stopped on COST_BREAKER_TRIPPED with exit code 1. These figures are derived from durable loop state, not authoritative over it.

<!-- sentinel:phase4-meta loop-p4g-leg2b::leg2b-exact-cap-midloop -->
```json
{"alert_label":null,"classification":"SEEDED_FAULT","exit_code":1,"failure_threshold":3,"gate_case":"leg2b-exact-cap-midloop","gate_leg":"LEG2","iterations_recorded":1,"loop_budget_eur_micros":750000,"loop_id":"loop-p4g-leg2b","max_iterations":3,"source_sha":"338ad691f657ae123763a4810ed8170880bd8c7f","stop_reason":"COST_BREAKER_TRIPPED"}
```
<!-- sentinel:phase4-meta-end loop-p4g-leg2b::leg2b-exact-cap-midloop -->

<!-- sentinel:phase4-iterations loop-p4g-leg2b::leg2b-exact-cap-midloop -->
```jsonl
{"bound_run_id":"r-p4g-l2b-seed","breaker":"COST_BREAKER_TRIPPED","consecutive_failures_after":0,"cumulative_cost_eur_micros":750000,"effective_allowance_eur_micros":null,"findings_new":0,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:01+00:00","iteration_cost_eur_micros":750000,"iteration_index":0,"iteration_state":"FINALIZED","planned_run_id":"r-p4g-l2b-seed","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:00+00:00","tasks_created":0,"tasks_terminal":0}
```
<!-- sentinel:phase4-iterations-end loop-p4g-leg2b::leg2b-exact-cap-midloop -->

<!-- sentinel:phase4-loop-end loop-p4g-leg2b::leg2b-exact-cap-midloop -->
<!-- sentinel:phase4-loop loop-p4g-leg2c::leg2c-overshoot-midloop -->
## LEG2 / leg2c-overshoot-midloop — loop loop-p4g-leg2c

LEG2 case leg2c-overshoot-midloop (SEEDED_FAULT) ran loop `loop-p4g-leg2c` under N = 3, a loop ceiling of 750000 micro-EUR and a failure threshold of 3. It recorded 1 iterations and stopped on COST_BREAKER_TRIPPED with exit code 1. These figures are derived from durable loop state, not authoritative over it.

<!-- sentinel:phase4-meta loop-p4g-leg2c::leg2c-overshoot-midloop -->
```json
{"alert_label":null,"classification":"SEEDED_FAULT","exit_code":1,"failure_threshold":3,"gate_case":"leg2c-overshoot-midloop","gate_leg":"LEG2","iterations_recorded":1,"loop_budget_eur_micros":750000,"loop_id":"loop-p4g-leg2c","max_iterations":3,"source_sha":"338ad691f657ae123763a4810ed8170880bd8c7f","stop_reason":"COST_BREAKER_TRIPPED"}
```
<!-- sentinel:phase4-meta-end loop-p4g-leg2c::leg2c-overshoot-midloop -->

<!-- sentinel:phase4-iterations loop-p4g-leg2c::leg2c-overshoot-midloop -->
```jsonl
{"bound_run_id":"r-p4g-l2c-seed","breaker":"COST_BREAKER_TRIPPED","consecutive_failures_after":0,"cumulative_cost_eur_micros":750001,"effective_allowance_eur_micros":null,"findings_new":0,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:01+00:00","iteration_cost_eur_micros":750001,"iteration_index":0,"iteration_state":"FINALIZED","planned_run_id":"r-p4g-l2c-seed","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:00+00:00","tasks_created":0,"tasks_terminal":0}
```
<!-- sentinel:phase4-iterations-end loop-p4g-leg2c::leg2c-overshoot-midloop -->

<!-- sentinel:phase4-loop-end loop-p4g-leg2c::leg2c-overshoot-midloop -->
<!-- sentinel:phase4-loop loop-p4g-leg2d::leg2d-terminal-exact-cap -->
## LEG2 / leg2d-terminal-exact-cap — loop loop-p4g-leg2d

LEG2 case leg2d-terminal-exact-cap (SEEDED_FAULT) ran loop `loop-p4g-leg2d` under N = 1, a loop ceiling of 750000 micro-EUR and a failure threshold of 3. It recorded 1 iterations and stopped on COMPLETED_ITERATION_CAP with exit code 0. These figures are derived from durable loop state, not authoritative over it.

<!-- sentinel:phase4-meta loop-p4g-leg2d::leg2d-terminal-exact-cap -->
```json
{"alert_label":null,"classification":"SEEDED_FAULT","exit_code":0,"failure_threshold":3,"gate_case":"leg2d-terminal-exact-cap","gate_leg":"LEG2","iterations_recorded":1,"loop_budget_eur_micros":750000,"loop_id":"loop-p4g-leg2d","max_iterations":1,"source_sha":"338ad691f657ae123763a4810ed8170880bd8c7f","stop_reason":"COMPLETED_ITERATION_CAP"}
```
<!-- sentinel:phase4-meta-end loop-p4g-leg2d::leg2d-terminal-exact-cap -->

<!-- sentinel:phase4-iterations loop-p4g-leg2d::leg2d-terminal-exact-cap -->
```jsonl
{"bound_run_id":"r-leg2d-000","breaker":null,"consecutive_failures_after":0,"cumulative_cost_eur_micros":750000,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:04+00:00","iteration_cost_eur_micros":750000,"iteration_index":0,"iteration_state":"FINALIZED","planned_run_id":"r-leg2d-000","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:02+00:00","tasks_created":0,"tasks_terminal":0}
```
<!-- sentinel:phase4-iterations-end loop-p4g-leg2d::leg2d-terminal-exact-cap -->

<!-- sentinel:phase4-loop-end loop-p4g-leg2d::leg2d-terminal-exact-cap -->
<!-- sentinel:phase4-loop loop-p4g-leg2e::leg2e-terminal-overshoot -->
## LEG2 / leg2e-terminal-overshoot — loop loop-p4g-leg2e

LEG2 case leg2e-terminal-overshoot (SEEDED_FAULT) ran loop `loop-p4g-leg2e` under N = 1, a loop ceiling of 750000 micro-EUR and a failure threshold of 3. It recorded 1 iterations and stopped on COST_BREAKER_TRIPPED with exit code 1. These figures are derived from durable loop state, not authoritative over it.

<!-- sentinel:phase4-meta loop-p4g-leg2e::leg2e-terminal-overshoot -->
```json
{"alert_label":null,"classification":"SEEDED_FAULT","exit_code":1,"failure_threshold":3,"gate_case":"leg2e-terminal-overshoot","gate_leg":"LEG2","iterations_recorded":1,"loop_budget_eur_micros":750000,"loop_id":"loop-p4g-leg2e","max_iterations":1,"source_sha":"338ad691f657ae123763a4810ed8170880bd8c7f","stop_reason":"COST_BREAKER_TRIPPED"}
```
<!-- sentinel:phase4-meta-end loop-p4g-leg2e::leg2e-terminal-overshoot -->

<!-- sentinel:phase4-iterations loop-p4g-leg2e::leg2e-terminal-overshoot -->
```jsonl
{"bound_run_id":"r-leg2e-000","breaker":"COST_BREAKER_TRIPPED","consecutive_failures_after":0,"cumulative_cost_eur_micros":750001,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:04+00:00","iteration_cost_eur_micros":750001,"iteration_index":0,"iteration_state":"FINALIZED","planned_run_id":"r-leg2e-000","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:02+00:00","tasks_created":0,"tasks_terminal":0}
```
<!-- sentinel:phase4-iterations-end loop-p4g-leg2e::leg2e-terminal-overshoot -->

<!-- sentinel:phase4-loop-end loop-p4g-leg2e::leg2e-terminal-overshoot -->
<!-- sentinel:phase4-loop loop-p4g-leg3trip::leg3-trip-at-three -->
## LEG3 / leg3-trip-at-three — loop loop-p4g-leg3trip

PHASE4_FAILURE_ALERT — LEG3 case leg3-trip-at-three (SEEDED_FAULT) ran loop `loop-p4g-leg3trip` under N = 4, a loop ceiling of 750000 micro-EUR and a failure threshold of 3. It recorded 3 iterations and stopped on CONSECUTIVE_FAILURE_BREAKER_TRIPPED with exit code 1. These figures are derived from durable loop state, not authoritative over it.

<!-- sentinel:phase4-meta loop-p4g-leg3trip::leg3-trip-at-three -->
```json
{"alert_label":"PHASE4_FAILURE_ALERT","classification":"SEEDED_FAULT","exit_code":1,"failure_threshold":3,"gate_case":"leg3-trip-at-three","gate_leg":"LEG3","iterations_recorded":3,"loop_budget_eur_micros":750000,"loop_id":"loop-p4g-leg3trip","max_iterations":4,"source_sha":"338ad691f657ae123763a4810ed8170880bd8c7f","stop_reason":"CONSECUTIVE_FAILURE_BREAKER_TRIPPED"}
```
<!-- sentinel:phase4-meta-end loop-p4g-leg3trip::leg3-trip-at-three -->

<!-- sentinel:phase4-iterations loop-p4g-leg3trip::leg3-trip-at-three -->
```jsonl
{"bound_run_id":"r-leg3trip-000","breaker":null,"consecutive_failures_after":1,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:04+00:00","iteration_cost_eur_micros":0,"iteration_index":0,"iteration_state":"FINALIZED","planned_run_id":"r-leg3trip-000","run_status":"FAILED","started_at_utc":"2026-01-01T00:00:02+00:00","tasks_created":0,"tasks_terminal":0}
{"bound_run_id":"r-leg3trip-001","breaker":null,"consecutive_failures_after":2,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:08+00:00","iteration_cost_eur_micros":0,"iteration_index":1,"iteration_state":"FINALIZED","planned_run_id":"r-leg3trip-001","run_status":"FAILED","started_at_utc":"2026-01-01T00:00:06+00:00","tasks_created":0,"tasks_terminal":0}
{"bound_run_id":"r-leg3trip-002","breaker":"CONSECUTIVE_FAILURE_BREAKER_TRIPPED","consecutive_failures_after":3,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:12+00:00","iteration_cost_eur_micros":0,"iteration_index":2,"iteration_state":"FINALIZED","planned_run_id":"r-leg3trip-002","run_status":"FAILED","started_at_utc":"2026-01-01T00:00:10+00:00","tasks_created":0,"tasks_terminal":0}
```
<!-- sentinel:phase4-iterations-end loop-p4g-leg3trip::leg3-trip-at-three -->

<!-- sentinel:phase4-loop-end loop-p4g-leg3trip::leg3-trip-at-three -->
<!-- sentinel:phase4-loop loop-p4g-leg3reset::leg3-reset-sequence -->
## LEG3 / leg3-reset-sequence — loop loop-p4g-leg3reset

LEG3 case leg3-reset-sequence (SEEDED_FAULT) ran loop `loop-p4g-leg3reset` under N = 5, a loop ceiling of 750000 micro-EUR and a failure threshold of 3. It recorded 5 iterations and stopped on COMPLETED_ITERATION_CAP with exit code 0. These figures are derived from durable loop state, not authoritative over it.

<!-- sentinel:phase4-meta loop-p4g-leg3reset::leg3-reset-sequence -->
```json
{"alert_label":null,"classification":"SEEDED_FAULT","exit_code":0,"failure_threshold":3,"gate_case":"leg3-reset-sequence","gate_leg":"LEG3","iterations_recorded":5,"loop_budget_eur_micros":750000,"loop_id":"loop-p4g-leg3reset","max_iterations":5,"source_sha":"338ad691f657ae123763a4810ed8170880bd8c7f","stop_reason":"COMPLETED_ITERATION_CAP"}
```
<!-- sentinel:phase4-meta-end loop-p4g-leg3reset::leg3-reset-sequence -->

<!-- sentinel:phase4-iterations loop-p4g-leg3reset::leg3-reset-sequence -->
```jsonl
{"bound_run_id":"r-leg3reset-000","breaker":null,"consecutive_failures_after":1,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:04+00:00","iteration_cost_eur_micros":0,"iteration_index":0,"iteration_state":"FINALIZED","planned_run_id":"r-leg3reset-000","run_status":"FAILED","started_at_utc":"2026-01-01T00:00:02+00:00","tasks_created":0,"tasks_terminal":0}
{"bound_run_id":"r-leg3reset-001","breaker":null,"consecutive_failures_after":2,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:08+00:00","iteration_cost_eur_micros":0,"iteration_index":1,"iteration_state":"FINALIZED","planned_run_id":"r-leg3reset-001","run_status":"FAILED","started_at_utc":"2026-01-01T00:00:06+00:00","tasks_created":0,"tasks_terminal":0}
{"bound_run_id":"r-leg3reset-002","breaker":null,"consecutive_failures_after":0,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:12+00:00","iteration_cost_eur_micros":0,"iteration_index":2,"iteration_state":"FINALIZED","planned_run_id":"r-leg3reset-002","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:10+00:00","tasks_created":0,"tasks_terminal":0}
{"bound_run_id":"r-leg3reset-003","breaker":null,"consecutive_failures_after":1,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:16+00:00","iteration_cost_eur_micros":0,"iteration_index":3,"iteration_state":"FINALIZED","planned_run_id":"r-leg3reset-003","run_status":"FAILED","started_at_utc":"2026-01-01T00:00:14+00:00","tasks_created":0,"tasks_terminal":0}
{"bound_run_id":"r-leg3reset-004","breaker":null,"consecutive_failures_after":2,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:20+00:00","iteration_cost_eur_micros":0,"iteration_index":4,"iteration_state":"FINALIZED","planned_run_id":"r-leg3reset-004","run_status":"FAILED","started_at_utc":"2026-01-01T00:00:18+00:00","tasks_created":0,"tasks_terminal":0}
```
<!-- sentinel:phase4-iterations-end loop-p4g-leg3reset::leg3-reset-sequence -->

<!-- sentinel:phase4-loop-end loop-p4g-leg3reset::leg3-reset-sequence -->
<!-- sentinel:phase4-loop loop-p4g-leg3prec::leg3-terminal-precedence -->
## LEG3 / leg3-terminal-precedence — loop loop-p4g-leg3prec

LEG3 case leg3-terminal-precedence (SEEDED_FAULT) ran loop `loop-p4g-leg3prec` under N = 3, a loop ceiling of 750000 micro-EUR and a failure threshold of 3. It recorded 3 iterations and stopped on CONSECUTIVE_FAILURE_BREAKER_TRIPPED with exit code 1. These figures are derived from durable loop state, not authoritative over it.

<!-- sentinel:phase4-meta loop-p4g-leg3prec::leg3-terminal-precedence -->
```json
{"alert_label":null,"classification":"SEEDED_FAULT","exit_code":1,"failure_threshold":3,"gate_case":"leg3-terminal-precedence","gate_leg":"LEG3","iterations_recorded":3,"loop_budget_eur_micros":750000,"loop_id":"loop-p4g-leg3prec","max_iterations":3,"source_sha":"338ad691f657ae123763a4810ed8170880bd8c7f","stop_reason":"CONSECUTIVE_FAILURE_BREAKER_TRIPPED"}
```
<!-- sentinel:phase4-meta-end loop-p4g-leg3prec::leg3-terminal-precedence -->

<!-- sentinel:phase4-iterations loop-p4g-leg3prec::leg3-terminal-precedence -->
```jsonl
{"bound_run_id":"r-leg3prec-000","breaker":null,"consecutive_failures_after":1,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:04+00:00","iteration_cost_eur_micros":0,"iteration_index":0,"iteration_state":"FINALIZED","planned_run_id":"r-leg3prec-000","run_status":"FAILED","started_at_utc":"2026-01-01T00:00:02+00:00","tasks_created":0,"tasks_terminal":0}
{"bound_run_id":"r-leg3prec-001","breaker":null,"consecutive_failures_after":2,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:08+00:00","iteration_cost_eur_micros":0,"iteration_index":1,"iteration_state":"FINALIZED","planned_run_id":"r-leg3prec-001","run_status":"FAILED","started_at_utc":"2026-01-01T00:00:06+00:00","tasks_created":0,"tasks_terminal":0}
{"bound_run_id":"r-leg3prec-002","breaker":"CONSECUTIVE_FAILURE_BREAKER_TRIPPED","consecutive_failures_after":3,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:12+00:00","iteration_cost_eur_micros":0,"iteration_index":2,"iteration_state":"FINALIZED","planned_run_id":"r-leg3prec-002","run_status":"FAILED","started_at_utc":"2026-01-01T00:00:10+00:00","tasks_created":0,"tasks_terminal":0}
```
<!-- sentinel:phase4-iterations-end loop-p4g-leg3prec::leg3-terminal-precedence -->

<!-- sentinel:phase4-loop-end loop-p4g-leg3prec::leg3-terminal-precedence -->
<!-- sentinel:phase4-loop loop-p4g-leg4primary::leg4-primary-before-finalize -->
## LEG4 / leg4-primary-before-finalize — loop loop-p4g-leg4primary

LEG4 case leg4-primary-before-finalize (SEEDED_FAULT) ran loop `loop-p4g-leg4primary` under N = 2, a loop ceiling of 750000 micro-EUR and a failure threshold of 3. It recorded 2 iterations and stopped on COMPLETED_ITERATION_CAP with exit code 0. These figures are derived from durable loop state, not authoritative over it.

<!-- sentinel:phase4-meta loop-p4g-leg4primary::leg4-primary-before-finalize -->
```json
{"alert_label":null,"classification":"SEEDED_FAULT","exit_code":0,"failure_threshold":3,"gate_case":"leg4-primary-before-finalize","gate_leg":"LEG4","iterations_recorded":2,"loop_budget_eur_micros":750000,"loop_id":"loop-p4g-leg4primary","max_iterations":2,"source_sha":"338ad691f657ae123763a4810ed8170880bd8c7f","stop_reason":"COMPLETED_ITERATION_CAP"}
```
<!-- sentinel:phase4-meta-end loop-p4g-leg4primary::leg4-primary-before-finalize -->

<!-- sentinel:phase4-iterations loop-p4g-leg4primary::leg4-primary-before-finalize -->
```jsonl
{"bound_run_id":"r-p4g-l4p-000","breaker":null,"consecutive_failures_after":0,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":null,"findings_new":40,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:03+00:00","iteration_cost_eur_micros":0,"iteration_index":0,"iteration_state":"FINALIZED","planned_run_id":"r-p4g-l4p-000","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:02+00:00","tasks_created":80,"tasks_terminal":80}
{"bound_run_id":"r-p4g-l4p-001","breaker":null,"consecutive_failures_after":0,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":0,"findings_resolved":0,"findings_still_open":40,"finished_at_utc":"2026-01-01T00:00:07+00:00","iteration_cost_eur_micros":0,"iteration_index":1,"iteration_state":"FINALIZED","planned_run_id":"r-p4g-l4p-001","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:05+00:00","tasks_created":80,"tasks_terminal":80}
```
<!-- sentinel:phase4-iterations-end loop-p4g-leg4primary::leg4-primary-before-finalize -->

<!-- sentinel:phase4-loop-end loop-p4g-leg4primary::leg4-primary-before-finalize -->
<!-- sentinel:phase4-loop loop-p4g-leg4casea::leg4-intent-before-run -->
## LEG4 / leg4-intent-before-run — loop loop-p4g-leg4casea

LEG4 case leg4-intent-before-run (SEEDED_FAULT) ran loop `loop-p4g-leg4casea` under N = 1, a loop ceiling of 750000 micro-EUR and a failure threshold of 3. It recorded 1 iterations and stopped on COMPLETED_ITERATION_CAP with exit code 0. These figures are derived from durable loop state, not authoritative over it.

<!-- sentinel:phase4-meta loop-p4g-leg4casea::leg4-intent-before-run -->
```json
{"alert_label":null,"classification":"SEEDED_FAULT","exit_code":0,"failure_threshold":3,"gate_case":"leg4-intent-before-run","gate_leg":"LEG4","iterations_recorded":1,"loop_budget_eur_micros":750000,"loop_id":"loop-p4g-leg4casea","max_iterations":1,"source_sha":"338ad691f657ae123763a4810ed8170880bd8c7f","stop_reason":"COMPLETED_ITERATION_CAP"}
```
<!-- sentinel:phase4-meta-end loop-p4g-leg4casea::leg4-intent-before-run -->

<!-- sentinel:phase4-iterations loop-p4g-leg4casea::leg4-intent-before-run -->
```jsonl
{"bound_run_id":"r-p4g-l4a-000","breaker":null,"consecutive_failures_after":0,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":750000,"findings_new":40,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:03+00:00","iteration_cost_eur_micros":0,"iteration_index":0,"iteration_state":"FINALIZED","planned_run_id":"r-p4g-l4a-000","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:02+00:00","tasks_created":80,"tasks_terminal":80}
```
<!-- sentinel:phase4-iterations-end loop-p4g-leg4casea::leg4-intent-before-run -->

<!-- sentinel:phase4-loop-end loop-p4g-leg4casea::leg4-intent-before-run -->
<!-- sentinel:phase4-loop loop-p4g-leg4cased::leg4-terminal-outputs -->
## LEG4 / leg4-terminal-outputs — loop loop-p4g-leg4cased

LEG4 case leg4-terminal-outputs (SEEDED_FAULT) ran loop `loop-p4g-leg4cased` under N = 1, a loop ceiling of 750000 micro-EUR and a failure threshold of 3. It recorded 1 iterations and stopped on COMPLETED_ITERATION_CAP with exit code 0. These figures are derived from durable loop state, not authoritative over it.

<!-- sentinel:phase4-meta loop-p4g-leg4cased::leg4-terminal-outputs -->
```json
{"alert_label":null,"classification":"SEEDED_FAULT","exit_code":0,"failure_threshold":3,"gate_case":"leg4-terminal-outputs","gate_leg":"LEG4","iterations_recorded":1,"loop_budget_eur_micros":750000,"loop_id":"loop-p4g-leg4cased","max_iterations":1,"source_sha":"338ad691f657ae123763a4810ed8170880bd8c7f","stop_reason":"COMPLETED_ITERATION_CAP"}
```
<!-- sentinel:phase4-meta-end loop-p4g-leg4cased::leg4-terminal-outputs -->

<!-- sentinel:phase4-iterations loop-p4g-leg4cased::leg4-terminal-outputs -->
```jsonl
{"bound_run_id":"r-p4g-l4d-000","breaker":null,"consecutive_failures_after":0,"cumulative_cost_eur_micros":0,"effective_allowance_eur_micros":null,"findings_new":40,"findings_resolved":0,"findings_still_open":0,"finished_at_utc":"2026-01-01T00:00:03+00:00","iteration_cost_eur_micros":0,"iteration_index":0,"iteration_state":"FINALIZED","planned_run_id":"r-p4g-l4d-000","run_status":"COMPLETED","started_at_utc":"2026-01-01T00:00:02+00:00","tasks_created":80,"tasks_terminal":80}
```
<!-- sentinel:phase4-iterations-end loop-p4g-leg4cased::leg4-terminal-outputs -->

<!-- sentinel:phase4-loop-end loop-p4g-leg4cased::leg4-terminal-outputs -->
