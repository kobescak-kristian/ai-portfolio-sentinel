<!-- Mapped Phase-4 closure artifact (BLUEPRINT §6 P4, §11(d);
adr/0003-production-readiness-program.md P4; adr/0010 §8). Authored
AFTER the Phase-4 capability and its designated gate evidence exist —
never as a placeholder. Status: in development toward production-ready.
No production claim is made in this document. -->

# TEST_MATRIX — ai-portfolio-sentinel Phase-4 bounded loop

## 1. Scope and status

This matrix records what was actually tested for the Phase-4
long-horizon layer — the bounded-loop supervisor, both circuit
breakers, the durable iteration-intent invariant, crash recovery, the
derived `ITERATION_LOG` evidence surface and the read-only boundary —
at which layer each control is exercised, and which evidence supports
it.

Status: **in development toward production-ready.** The
production-readiness program (`adr/0003`) remains open, so no
production or production-ready claim appears here or follows from
anything below.

Scope boundary: Phase 4 only. The Phase-2 deterministic control plane
and the Phase-3 caged checker agent have their own evidence
(`EVAL_RESULTS.md`, `THREAT_MODEL.md`, `MODEL_CARD.md`) and are not
re-gated here.

**The Phase-4 technical gate is MODEL-FREE** (`adr/0010` §7). It proves
loop-safety behaviour mechanically with seeded, model-free faults. It
makes no provider contact and therefore establishes nothing about
provider availability, model quality or agent-mode loop behaviour.
Section 13 states that limitation again as a residual.

## 2. Evidence types used in this matrix

| Type | Meaning |
|---|---|
| UNIT | Pure predicate/function test, no I/O |
| INTEGRATION | Real supervisor over real durable SQLite loop state |
| FAULT INJECTION | Seeded fault driven through the real supervisor |
| DESIGNATED GATE | A frozen `adr/0010` §7 predicate recorded by the one designated gate execution |
| STATIC BOUNDARY | AST/static scan asserting an import or write boundary |
| SCHEMA | A database CHECK/UNIQUE constraint or trigger |
| CI | Whole suite on every push (ubuntu-latest, Python 3.12) |

Designated-gate predicate ids are quoted exactly as recorded in
`artifacts/phase4_loop_gate.json`.

## 3. A — Bounded loop

| Control | Evidence type | Reference |
|---|---|---|
| `1 <= N <= 10` enforced | UNIT | `tests/test_breakers.py::test_valid_iteration_limits_accepted`, `::test_iteration_limit_outside_1_to_10_refused`, `::test_iteration_limit_must_be_a_plain_int` |
| N validated before any durable intent | INTEGRATION | `tests/test_loop_runner.py::test_invalid_n_refused_before_any_loop_or_iteration_intent` |
| An illegal N cannot be represented in the ledger | SCHEMA | `contracts/ledger_schema.sql` — `loop_runs.max_iterations CHECK (BETWEEN 1 AND 10)` |
| The entry point rejects an out-of-range N | INTEGRATION | `tests/test_loop_runner.py::test_runner_cli_rejects_an_out_of_range_iteration_count` |
| Normal N = 10 execution | DESIGNATED GATE | `LEG1_NORMAL_N10_ITERATION_COUNT` (`iterations: 10`) |
| Normal N = 10 execution | INTEGRATION | `tests/test_loop_runner.py::test_normal_loop_completes_exactly_n_iterations` |
| Contiguous iteration indexes | DESIGNATED GATE | `LEG1_NORMAL_N10_INDEX_CONTIGUITY` |
| No duplicate and no skipped iteration | INTEGRATION | `tests/test_loop_runner.py::test_no_duplicate_and_no_skipped_iteration` |
| Unique planned/bound run identities | DESIGNATED GATE | `LEG1_NORMAL_N10_IDENTITY_UNIQUE` (`unique_planned: 10`, `unique_bound: 10`) |
| A second intent for one iteration is refused by the database | INTEGRATION + SCHEMA | `tests/test_loop_runner.py::test_a_second_intent_for_the_same_iteration_is_refused_by_the_database`; `loop_iterations` `PRIMARY KEY (loop_id, iteration_index)` and `UNIQUE (planned_run_id)` |
| Every iteration finalized | DESIGNATED GATE | `LEG1_NORMAL_N10_ALL_FINALIZED` |
| An iteration finalizes exactly once | INTEGRATION | `tests/test_loop_runner.py::test_an_iteration_finalizes_exactly_once` |
| Underlying runs terminal | DESIGNATED GATE | `LEG1_NORMAL_N10_RUNS_TERMINAL` (`runs: 10`) |
| Tasks terminal | DESIGNATED GATE | `LEG1_NORMAL_N10_TASKS_TERMINAL` |
| Exactly one `CostRow` per run | DESIGNATED GATE | `LEG1_NORMAL_N10_ONE_COSTROW_PER_RUN` |
| Cross-iteration continuity demonstrated | DESIGNATED GATE | `LEG1_NORMAL_N10_CONTINUITY` (`later_iterations: 9`) |
| Accounted cost within the ceiling | DESIGNATED GATE | `LEG1_NORMAL_N10_COST_WITHIN_CEILING` (`accounted_cost_eur_micros: 0`) |
| Normal stop reason and exit 0 | DESIGNATED GATE | `LEG1_NORMAL_N10_STOP_REASON` (`COMPLETED_ITERATION_CAP`), `LEG1_NORMAL_N10_EXIT_ZERO` |
| Exactly one terminal stop reason per loop | INTEGRATION | `tests/test_loop_runner.py::test_exactly_one_terminal_stop_reason_per_loop`, `::test_a_finished_loop_cannot_be_finished_twice` |
| A loop's bounds are fixed at creation | INTEGRATION | `tests/test_loop_runner.py::test_a_loop_cannot_be_resumed_under_different_bounds` |

Continuity note: `LEG1_NORMAL_N10_CONTINUITY` shows that iterations
2–10 introduce no new findings over the same fixture bed. It is a
continuity demonstration, not a new cross-run dedup acceptance gate —
cross-run identity evidence belongs to the Phase-3 record
(`EVAL_RESULTS.md`) and is not re-gated by Phase 4.

## 4. B — Durable intent and recovery

| Control | Evidence type | Reference |
|---|---|---|
| `planned_run_id` persisted and committed before any work | INTEGRATION | `tests/test_loop_runner.py::test_iteration_callable_is_unreachable_before_the_intent_is_committed` |
| That exact id becomes the run's `run_id` | INTEGRATION | `tests/test_loop_runner.py::test_planned_run_id_is_the_run_id_handed_to_the_iteration`, `::test_adapter_binds_the_planned_run_id_as_the_actual_run_id` |
| Iteration identity is immutable once committed | SCHEMA | `contracts/ledger_schema.sql` — `loop_iterations_finalize_guard` permits only `INTENT -> FINALIZED` |
| Case A — intent committed, no run yet: the same id is reused and the run starts once | FAULT INJECTION | `tests/test_loop_runner.py::test_crash_after_intent_before_run_start_reuses_the_same_planned_run_id`, `::test_recovery_a_no_run_row_starts_once_with_the_same_planned_run_id` |
| Case A | DESIGNATED GATE | `LEG4_INTENT_BEFORE_RUN_REUSED` (`planned_run_id: r-p4g-l4a-000`, `runs: 1`) |
| Case A still respects the hard cost ceiling | INTEGRATION | `tests/test_loop_runner.py::test_recovery_a_still_respects_the_hard_cost_ceiling` |
| Case B — a terminal run is adopted, never re-executed | INTEGRATION | `tests/test_loop_runner.py::test_recovery_b_terminal_run_is_adopted_and_never_re_invoked` |
| Case C — an interrupted RUNNING run is driven terminal, with no replacement id | INTEGRATION | `tests/test_loop_runner.py::test_recovery_c_running_run_uses_interrupted_run_recovery`, `::test_adapter_recovery_c_drives_an_interrupted_run_to_terminal`, `::test_adapter_recovery_c_refuses_if_the_run_never_became_terminal` |
| Case D — a terminal run with incomplete outputs is reconciled without a rerun | INTEGRATION | `tests/test_loop_runner.py::test_recovery_d_incomplete_outputs_are_reconciled_without_a_rerun`, `::test_adapter_recovery_d_reconciles_missing_outputs_without_a_rerun` |
| Case D | DESIGNATED GATE | `LEG4_TERMINAL_OUTPUTS_RECONCILED` (`planned_run_id: r-p4g-l4d-000`, `cost_rows: 1`) |
| Primary seam — run terminal and durable, loop finalization not yet committed | FAULT INJECTION | `tests/test_loop_runner.py::test_crash_after_terminal_run_before_finalize_adopts_and_never_repeats`; seam `runner.loop.LoopHooks.after_run_terminal_before_finalize` |
| Primary seam — adoption without re-execution | DESIGNATED GATE | `LEG4_TERMINAL_BEFORE_FINALIZE_ADOPTED` (`adopted_without_reexecution: true`, `stop_reason: COMPLETED_ITERATION_CAP`) |
| The same `planned_run_id` after restart | DESIGNATED GATE | `LEG4_SAME_PLANNED_RUN_ID` (`planned_run_id: r-p4g-l4p-000`) |
| No duplicate run | DESIGNATED GATE | `LEG4_NO_DUPLICATE_RUN` (`runs: 2`) |
| No skipped iteration | DESIGNATED GATE | `LEG4_NO_SKIPPED_ITERATION` (`iterations: 2`) |
| Earlier unfinished indexes are reconciled before any later new one | INTEGRATION | `tests/test_loop_runner.py::test_earlier_unfinished_iterations_are_reconciled_before_any_later_new_one` |

## 5. C — Cost breaker

The Phase-4 loop ceiling is **750,000 micro-EUR**, fixed. It does not
replace or raise the unchanged **EUR 0.75** per-run cap.

| Control | Evidence type | Reference |
|---|---|---|
| Ceiling frozen at 750,000 micro-EUR | UNIT | `tests/test_breakers.py::test_frozen_adr0010_constants` |
| No entry-point flag raises the ceiling | INTEGRATION | `tests/test_loop_runner.py::test_runner_cli_offers_no_flag_that_raises_the_loop_ceiling` |
| The gate exposes no flag that changes a frozen bound | STATIC BOUNDARY | `tests/test_phase4_gate.py::test_the_gate_offers_no_flag_that_changes_a_frozen_bound` |
| 749,999 mid-loop does not trip on cost alone | DESIGNATED GATE | `LEG2_749999_MIDLOOP_CONTINUES` (`accounted_cost_eur_micros: 749999`, `further_iterations: 2`) |
| 749,999 mid-loop | INTEGRATION | `tests/test_loop_runner.py::test_mid_loop_749999_does_not_trip_cost_alone` |
| The next allowance is reduced to exactly 1 and propagated downward | DESIGNATED GATE | `LEG2_REDUCED_ALLOWANCE_PROPAGATED` (`allowance_seen: 1`, `coordinator_remaining: 1`) |
| The reduced allowance reaches the real budget coordinator | UNIT | `tests/test_loop_runner.py::test_reduced_allowance_is_propagated_into_the_run_budget_coordinator` |
| The reduced allowance is never restored to the full per-run cap | INTEGRATION | `tests/test_loop_runner.py::test_reduced_allowance_reaches_each_iteration_and_is_never_restored` |
| An unenforceable allowance refuses fail-closed | UNIT | `tests/test_loop_runner.py::test_non_positive_allowance_refuses_fail_closed`, `::test_an_allowance_above_the_per_run_cap_refuses_fail_closed` |
| A tiny remaining allowance rounds the derived figure down, not up | UNIT | `tests/test_loop_runner.py::test_tiny_remaining_allowance_rounds_the_sdk_figure_down_not_up` |
| Exactly 750,000 mid-loop refuses the next iteration | DESIGNATED GATE | `LEG2_EXACT_CAP_MIDLOOP_REFUSED` (`stop_reason: COST_BREAKER_TRIPPED`) |
| That refusal starts no next underlying run | DESIGNATED GATE | `LEG2_NO_NEXT_RUN_AT_EXACT_CAP` (`intents: 1`, `runs: 1`) |
| Exactly at the ceiling mid-loop | INTEGRATION | `tests/test_loop_runner.py::test_mid_loop_exactly_at_the_ceiling_refuses_the_next_iteration` |
| 750,001 trips the breaker and starts no next run | DESIGNATED GATE | `LEG2_OVERSHOOT_TRIPS`, `LEG2_NO_NEXT_RUN_AFTER_OVERSHOOT` (`intents: 1`, `runs: 1`) |
| Overshoot accounted in full, never clamped | DESIGNATED GATE | `LEG2_OVERSHOOT_FULL_NOT_CLAMPED` (`accounted_cost_eur_micros: 750001`) |
| Overshoot never clamped | UNIT | `tests/test_breakers.py::test_remaining_budget_is_never_clamped`; `tests/test_loop_runner.py::test_overshoot_is_accounted_in_full_and_never_clamped`, `::test_accounted_cost_never_clamps_a_row_above_the_per_run_cap` |
| N reached at exactly 750,000 completes normally, exit 0 | DESIGNATED GATE | `LEG2_TERMINAL_EXACT_CAP_NORMAL` (`accounted_cost_eur_micros: 750000`, `stop_reason: COMPLETED_ITERATION_CAP`) |
| N reached at exactly the ceiling | UNIT + INTEGRATION | `tests/test_breakers.py::test_n_reached_cost_exactly_750000_completes`; `tests/test_loop_runner.py::test_n_reached_at_exactly_the_ceiling_completes_normally` |
| N reached above the ceiling trips, nonzero exit | DESIGNATED GATE | `LEG2_TERMINAL_OVERSHOOT_TRIPS` (`accounted_cost_eur_micros: 750001`, `stop_reason: COST_BREAKER_TRIPPED`) |
| N reached above the ceiling | UNIT + INTEGRATION | `tests/test_breakers.py::test_n_reached_cost_above_ceiling_trips_the_cost_breaker`; `tests/test_loop_runner.py::test_n_reached_above_the_ceiling_trips_the_cost_breaker` |
| Post-iteration strict `>` versus pre-start remaining `<= 0` | UNIT | `tests/test_breakers.py::test_overshoot_uses_strict_greater_than`, `::test_pre_start_refusal_uses_remaining_less_than_or_equal_zero`, `::test_the_two_comparisons_are_deliberately_asymmetric_at_the_ceiling` |
| The EUR 0.75 per-run cap is not raised | UNIT | `tests/test_loop_runner.py::test_full_allowance_still_equals_the_unchanged_per_run_cap`; `tests/test_breakers.py::test_continue_at_full_budget_is_capped_by_the_per_run_cap_not_raised_by_it` |
| Seeded overspend trips the real supervisor | FAULT INJECTION | `tests/test_failures.py::test_cost_breaker_trips_on_seeded_overspend` |

## 6. D — Consecutive-failure breaker

| Control | Evidence type | Reference |
|---|---|---|
| The failure threshold is exactly 3 | UNIT | `tests/test_breakers.py::test_threshold_is_exactly_three`, `::test_frozen_adr0010_constants` |
| The failure unit is the run's final status, never the exit code alone | UNIT | `tests/test_breakers.py::test_any_status_other_than_completed_is_a_failed_iteration`, `::test_only_completed_is_not_a_failure` |
| Exactly three consecutive failures trip | DESIGNATED GATE | `LEG3_TRIP_AT_THREE` (`iterations: 3`, `streak: 3`) |
| Exactly three consecutive failures trip | INTEGRATION | `tests/test_loop_runner.py::test_failed_iterations_increment_the_streak_and_trip_at_exactly_three` |
| The fourth iteration never starts | DESIGNATED GATE | `LEG3_NO_FOURTH_ITERATION` (`intents: 3`, `runs: 3`) |
| Only a `COMPLETED` iteration resets the streak | UNIT | `tests/test_breakers.py::test_streak_increments_on_failure_and_resets_only_on_completed` |
| A completed iteration resets the streak | INTEGRATION | `tests/test_loop_runner.py::test_a_completed_iteration_resets_the_streak` |
| fail / fail / success / fail / fail ends at streak 2 | DESIGNATED GATE | `LEG3_RESET_SEQUENCE` (`iterations: 5`, `streak: 2`) |
| The same sequence does not trip from stale state | UNIT | `tests/test_breakers.py::test_fail_fail_success_fail_fail_does_not_trip_from_stale_state` |
| The streak is reconstructed from durable rows, not memory | INTEGRATION | `tests/test_loop_runner.py::test_streak_is_reconstructed_from_durable_rows_not_memory` |
| The streak at each step is persisted | INTEGRATION | `tests/test_loop_runner.py::test_iteration_rows_record_the_streak_at_each_step` |
| The threshold outranks normal N completion at the terminal boundary | DESIGNATED GATE | `LEG3_TERMINAL_STREAK_PRECEDENCE` (`stop_reason: CONSECUTIVE_FAILURE_BREAKER_TRIPPED`) |
| Terminal-boundary precedence | UNIT + INTEGRATION | `tests/test_breakers.py::test_n_reached_within_ceiling_with_streak_three_prefers_the_failure_breaker`; `tests/test_loop_runner.py::test_terminal_boundary_n_reached_with_streak_three_is_the_failure_breaker` |
| Accounted overshoot outranks the failure breaker | UNIT | `tests/test_breakers.py::test_overshoot_outranks_the_failure_breaker` |
| Seeded consecutive failures trip the real supervisor | FAULT INJECTION | `tests/test_failures.py::test_consecutive_failure_breaker_trips_on_seeded_failures` |

## 7. E — Failure alert (all four parts)

`adr/0010` §5 requires all four of: a structured ERROR-severity event
from the closed logging vocabulary; a durable `stop_reason`; a nonzero
process exit; and a labelled `ITERATION_LOG.md` evidence section.

| Part | Evidence type | Reference |
|---|---|---|
| 1 — structured ERROR event | INTEGRATION | `tests/test_loop_runner.py::test_cost_breaker_logs_an_error_severity_event`, `::test_consecutive_failure_breaker_logs_an_error_severity_event`; closed vocabulary in `sentinel/logs.py` (`breaker.cost_tripped`, `breaker.consecutive_failure_tripped`, `loop.failed`) |
| 2 — durable `stop_reason` | INTEGRATION + SCHEMA | `tests/test_loop_runner.py::test_exactly_one_terminal_stop_reason_per_loop`; `loop_runs.stop_reason` closed CHECK |
| 3 — nonzero exit | UNIT | `tests/test_breakers.py::test_stop_reason_vocabulary_is_closed_and_exit_codes_are_frozen` |
| 4 — labelled `PHASE4_FAILURE_ALERT` section | UNIT | `tests/test_phase4_gate.py::test_alert_label_appears_only_where_intended` |
| All four together, through the real supervisor and a written log file | FAULT INJECTION | `tests/test_failures.py::test_seeded_breaker_trip_produces_failure_alert` |
| All four together, under the designated gate | DESIGNATED GATE | `LEG3_FOUR_PART_ALERT` (`structured_error_event`, `durable_stop_reason`, `nonzero_exit`, `labeled_iteration_log_section` — all true) |

**No email, Slack, webhook, push-notification or dashboard integration
exists.** The labelled evidence section, the structured event, the
durable stop reason and the nonzero exit are the entire alert channel.
Loop operational failures are never appended into monitored-surface
findings in order to manufacture an alert.

## 8. F — Durable cost and state truth

| Control | Evidence type | Reference |
|---|---|---|
| Durable `CostRow`s are authoritative for accounted cost | UNIT | `tests/test_loop_runner.py::test_accounted_cost_is_read_from_durable_cost_rows`, `::test_adapter_reads_cost_from_the_committed_cost_ledger` |
| A volatile in-memory counter is never the source of truth | INTEGRATION | `runner/loop.py` re-reads durable cost through `IterationExecutor.accounted_cost` at every decision point; exercised by every cost row in §5 |
| `ITERATION_LOG` is derived evidence, not control state | UNIT | `tests/test_phase4_gate.py::test_the_header_states_that_the_file_is_not_authoritative`, `::test_no_numerical_fact_appears_only_in_prose` |
| The self-check rereads the written bytes, not the render object | UNIT | `tests/test_phase4_gate.py::test_the_self_check_reads_the_file_rather_than_the_render_object`, `::test_the_self_check_passes_on_a_cleanly_written_section` |
| The self-check compares the fields the contract requires | UNIT | `tests/test_phase4_gate.py::test_the_self_check_compares_the_fields_adr0010_requires` |
| One corrupted machine figure is caught | UNIT | `tests/test_phase4_gate.py::test_the_self_check_detects_one_corrupted_machine_figure` |
| Derived evidence checked back against durable state, every case | DESIGNATED GATE | `ITERATION_LOG_MATCHES_DURABLE_STATE` (`sections_checked: 12`) |
| A corrupt trailing cost line reports nothing rather than raising | UNIT | `tests/test_loop_runner.py::test_a_corrupt_trailing_cost_line_reports_nothing_rather_than_raising` |
| A failing cost read never hides the stop reason | INTEGRATION | `tests/test_loop_runner.py::test_a_failing_cost_read_never_hides_the_stop_reason` |
| The persisted loop summary matches the iteration rows | INTEGRATION | `tests/test_loop_runner.py::test_loop_summary_is_persisted_and_matches_the_iteration_rows` |
| Append is idempotent and crash-consistent | UNIT | `tests/test_phase4_gate.py::test_append_then_reappend_is_an_idempotent_no_op`, `::test_a_crash_truncated_trailing_section_is_repaired`, `::test_an_earlier_complete_section_is_byte_preserved_by_a_later_repair` |
| The recorded hash is taken over the exact written bytes | UNIT | `tests/test_phase4_gate.py::test_iteration_log_sha256_is_taken_over_the_exact_bytes`, `::test_the_recorded_hash_is_of_the_written_iteration_log` |

## 9. G — Read-only and dependency boundary

| Control | Evidence type | Reference |
|---|---|---|
| The generic loop and breaker modules stay domain-free | STATIC BOUNDARY | `tests/test_read_only_boundary.py::test_generic_loop_and_breakers_stay_domain_free` |
| The integration adapter is the sole boundary, and is narrow | STATIC BOUNDARY | `tests/test_read_only_boundary.py::test_sentinel_adapter_is_the_sole_integration_boundary` |
| No runner module reaches a provider execution surface | STATIC BOUNDARY | `tests/test_read_only_boundary.py::test_no_runner_module_imports_a_provider_execution_surface`, `::test_integration_adapter_has_no_direct_model_sdk_import` |
| The model SDK is imported nowhere outside the one Phase-3 package | STATIC BOUNDARY | `tests/test_read_only_boundary.py::test_static_scan_no_model_sdk_import_anywhere` |
| The gate script imports and reaches no provider or network surface | STATIC BOUNDARY | `tests/test_phase4_gate.py::test_the_gate_script_imports_no_provider_or_network_surface`, `::test_the_gate_script_reaches_no_provider_execution_surface` |
| That static boundary test is not vacuous | STATIC BOUNDARY | `tests/test_phase4_gate.py::test_the_static_boundary_test_is_not_vacuous` |
| The bounded-loop entry point refuses agent mode fail-closed | INTEGRATION | `tests/test_loop_runner.py::test_runner_cli_refuses_agent_mode_fail_closed_before_any_construction` |
| Monitored repositories stay read-only; no credentialed write path exists | INTEGRATION | `tests/test_read_only_boundary.py::test_dynamic_full_run_makes_zero_model_calls_and_zero_cost`, `::test_containment_full_run_touches_nothing_outside_explicit_paths`, `::test_no_credential_env_var_ever_reaches_a_request_or_output` |
| No `DELETE` statement in the control plane | STATIC BOUNDARY | `tests/test_read_only_boundary.py::test_static_scan_no_delete_statement_in_control_plane` |
| Ledger rows are never deleted, loop tables included | SCHEMA | `contracts/ledger_schema.sql` — `loop_runs_never_deleted`, `loop_iterations_never_deleted` |
| The third-party import surface stays pinned and minimal | STATIC BOUNDARY | `tests/test_dependency_surface.py::test_runtime_modules_import_only_their_roots_allowed_third_party`, `::test_test_modules_import_only_the_pinned_dev_set` |

## 10. H — Public-evidence hygiene

| Control | Evidence type | Reference |
|---|---|---|
| A closed input schema validates every rendered value | UNIT | `tests/test_phase4_gate.py::test_the_closed_schema_refuses_bad_enums_counters_timestamps_and_shas`, `::test_the_closed_schema_refuses_a_bad_exit_code` |
| The module accepts no caller-supplied free prose | UNIT | `tests/test_phase4_gate.py::test_the_module_takes_no_caller_supplied_free_prose` |
| Written bytes reparse and revalidate | UNIT | `tests/test_phase4_gate.py::test_machine_rows_parse_back_out_of_written_bytes`, `::test_parse_refuses_a_machine_row_with_an_unknown_field`, `::test_parse_refuses_a_machine_row_with_a_missing_field`, `::test_parse_refuses_a_row_whose_field_fails_its_validator`, `::test_parse_refuses_a_section_declaring_the_wrong_row_count` |
| Machine-local paths and secret-shaped tokens are refused at the boundary | UNIT | `tests/test_phase4_gate.py::test_unsafe_values_are_refused_by_the_identifier_validator`, `::test_unsafe_run_ids_are_refused`, `::test_a_temporary_gate_root_value_cannot_become_an_identifier` |
| Raw-byte backstops on both public outputs | UNIT | `tests/test_phase4_gate.py::test_public_output_hygiene_flags_the_temporary_gate_root`, `::test_public_output_hygiene_flags_an_unsanitized_diagnostic`, `::test_public_output_hygiene_flags_a_traceback_block`, `::test_public_output_hygiene_flags_an_unparseable_iteration_log` |
| The gate never dumps an environment | UNIT | `tests/test_phase4_gate.py::test_the_gate_never_dumps_an_environment` |
| The artifact schema is closed and tamper-evident | UNIT | `tests/test_phase4_gate.py::test_the_artifact_schema_is_closed`, `::test_the_artifact_schema_refuses_a_tampered_frozen_field` |
| Public output clean under the designated gate | DESIGNATED GATE | `PUBLIC_OUTPUT_CLEAN` — "structured revalidation, artifact schema and raw-byte backstops all clean" |

## 11. I — The designated gate

The judge was frozen before the judged run, and the judged run happened
exactly once.

| Property | Value |
|---|---|
| Gate | `phase4_bounded_loop` |
| Gate contract | `ADR-0010-section-7` |
| Source under gate | `338ad691f657ae123763a4810ed8170880bd8c7f` |
| Evidence-recording commit | `fa510bd4bb275b12d1530148f0582901fb45ba6e` |
| Designated executions | 1 |
| Overall | **PASS** |
| Frozen predicates | **33 / 33 PASS** |
| Legs | LEG1 PASS · LEG2 PASS · LEG3 PASS · LEG4 PASS |
| Derived-evidence self-check | PASS (12 sections) |
| Public output | `PUBLIC_OUTPUT_CLEAN` PASS |
| Model calls | 0 |
| Provider spend | 0 micro-EUR |
| Loop ceiling | 750,000 micro-EUR |
| Failure threshold | 3 |
| `ITERATION_LOG.md` SHA-256 | `d47294873206bd96abb64a4a0377f5bb4e4685c5a7980a8890ab7e92668e3e40` |

Artifacts: `artifacts/phase4_loop_gate.json` and `ITERATION_LOG.md`,
both machine-written, neither hand-edited.

Gate-mechanism controls — the judge held to its own standard:

| Control | Evidence type | Reference |
|---|---|---|
| The predicate set is closed and covers every leg | UNIT | `tests/test_phase4_gate.py::test_the_predicate_set_is_closed_and_covers_every_leg` |
| An unknown or duplicated predicate is refused | UNIT | `tests/test_phase4_gate.py::test_an_unknown_predicate_is_refused`, `::test_a_duplicate_predicate_is_refused` |
| A never-recorded predicate FAILs rather than vanishing | UNIT | `tests/test_phase4_gate.py::test_a_never_recorded_predicate_is_reported_as_failed_and_blocks_pass` |
| Overall PASS requires every frozen predicate to PASS | UNIT | `tests/test_phase4_gate.py::test_overall_pass_requires_every_frozen_predicate_to_pass` |
| The gate's local literals match the runner's frozen values | UNIT | `tests/test_phase4_gate.py::test_the_gates_local_literals_match_the_runners_frozen_values` |
| A complete artifact must carry every frozen predicate, exactly once, consistently | UNIT | `tests/test_phase4_gate.py::test_a_complete_artifact_must_carry_every_frozen_predicate`, `::test_a_complete_artifact_refuses_a_duplicated_predicate`, `::test_a_complete_artifact_refuses_an_overall_that_contradicts_its_predicates` |
| The gate is model-free | DESIGNATED GATE + UNIT | artifact `model_calls: 0`, `provider_spend_eur_micros: 0`; `tests/test_phase4_gate.py::test_the_gate_is_model_free` |
| The test suite never creates or mutates the official root outputs | UNIT | `tests/test_phase4_gate.py::test_the_gate_tests_leave_repository_root_outputs_unchanged` |

## 12. Suite-level evidence

| Property | Value |
|---|---|
| Tests passing | 984 |
| Tests skipped | 0 |
| Line coverage | 93.1% |
| `runner/loop.py`, `runner/breakers.py`, `runner/state.py` | 100% |
| `runner/iteration_log.py` | 99.2% (one unreachable defensive guard, disclosed rather than papered over) |
| CI | every push, ubuntu-latest, Python 3.12 |
| Dependency check | `python -m pip check` clean |
| Tier 0 artifact validator | PASS |
| Phase-1 freeze guard | PASS |

## 13. Residual — what is NOT tested, and what these results do not prove

- **GitHub Actions scheduling belongs to Phase 5.** No scheduler
  migration has happened. The standing scheduled task remains
  stub-mode and unedited.
- **Five consecutive Actions-scheduled live runs have not occurred.**
  There is no Actions-scheduled operating history to measure.
- **The official Sonnet gate belongs to Phase 5.** It has not run.
- **The final `RUNBOOK.md`, `MONITORING.md` and `SLO.md` do not
  exist.** The runbook and monitoring documents in this repository are
  explicitly Phase-4 drafts.
- **The Phase-4 loop technical gate is model-free.** It therefore
  establishes nothing about provider uptime, provider reliability,
  model-quality performance, or how the loop behaves with a real model
  in it. No provider-capable bounded-loop execution path exists — the
  entry point refuses agent mode fail-closed.
- **No real-model loop cost has been observed.** Every Phase-4 cost
  figure recorded here comes from seeded, model-free `CostRow`s.
- **The loop ceiling equals one per-run cap**, so a real model-calling
  loop would be bounded to roughly one full-cost iteration. That is
  deliberate fail-closed conservatism, disclosed in `adr/0010` rather
  than engineered around; raising it needs a separate dated
  owner-governed decision.
- **A technical-gate PASS is not Phase-4 closure** (`adr/0010` §8), and
  no production or production-ready claim follows from anything in this
  document.
