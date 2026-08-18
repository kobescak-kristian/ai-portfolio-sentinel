"""Model routing, caging bounds, and run-budget constants for the
caged checker agent (BLUEPRINT.md §3, §6 P3, §7; dispatch q77-p3-a;
budget values amended by adr/0005-phase3-gate-remediation.md).

Currency note: BLUEPRINT §7 states per-run caps in EUR ("iteration/
dev/live run <= EUR 0.75 (Haiku)"); the Claude Agent SDK's own
``max_budget_usd`` is USD. These constants are therefore EUR-first —
``RUN_BUDGET_EUR_MICROS`` is the frozen contract this cage enforces —
and every USD figure the SDK is given is *derived* from it at run time
via the resolved FX rate (see ``fx.py``, ``budget.py``), never
hardcoded in USD.
"""

from __future__ import annotations

# Haiku is the only model this phase's dev gate uses (BLUEPRINT §6 P3:
# "Caged checker agent (Haiku dev)"). The Sonnet official gate is a
# later, separate phase concern and is not wired here.
MODEL = "claude-haiku-4-5-20251001"

# Bounded turns per judgment call. The request's text is supplied
# in-context (no fetch tool) so a judgment call is normally: reason,
# call emit_finding once per genuine defect, done — this cap leaves
# room for a rejected tool call to be retried within the same turn
# budget without being generous enough to let a run drift. Unchanged
# by adr/0005: observed maxima were 3 turns, and this is a
# runaway-stop, not a value fitted to the fixture bed.
MAX_TURNS = 10

# Independent, per-call tool-invocation circuit breaker (BLUEPRINT §3:
# "call-count circuit breaker"), separate from MAX_TURNS so a model
# that spams tool calls within a single turn is still capped. Also
# unchanged by adr/0005 (observed maximum 2 attempts).
MAX_TOOL_CALLS_PER_CHECK = 5

# One shared EUR budget for the *entire* Sentinel run's judgment
# checking, not per request (dispatch q77-p3-a binding decision 3).
# Integer micro-euros, matching contracts.schemas.CostRow.cost_eur_micros.
# Amended 500_000 -> 750_000 by adr/0005: the corrected 23-call
# workload's point projection from persisted completed-call costs
# (560,220 micro-EUR) already exceeded EUR 0.50 with zero failures.
RUN_BUDGET_EUR_MICROS = 750_000  # EUR 0.75

# No single call may reserve more than this fraction of the run
# budget, so one expensive call cannot exhaust the shared cap before
# other judgment tasks get a chance to run at all. Amended
# 100_000 -> 150_000 by adr/0005, keeping the 20% failed-call burn
# fraction of the run budget unchanged.
MAX_PER_CALL_RESERVE_EUR_MICROS = 150_000  # EUR 0.15

# The SDK checks its own max_budget_usd *after* each API call
# completes (documented behavior: the estimate can overshoot by up to
# one call's cost before the SDK halts). This safety margin keeps the
# USD allowance handed to the SDK conservative enough that even a
# one-call overshoot should not push the run's *charged* total past
# RUN_BUDGET_EUR_MICROS. A margin, not a guarantee — the coordinator's
# own post-call accounting (budget.py) is the actual enforcement point
# for the EUR 0.75 ceiling, this only reduces how often it's tested.
# Deliberately NOT relaxed by adr/0005: raising it would buy execution
# through the back door.
SDK_ALLOWANCE_SAFETY_MARGIN = "0.70"  # Decimal string; see budget.py

MCP_SERVER_NAME = "sentinelchecker"
TOOL_NAME = "emit_finding"
QUALIFIED_TOOL_NAME = f"mcp__{MCP_SERVER_NAME}__{TOOL_NAME}"

# Auth-mode label recorded on every audit row. The Claude Agent SDK
# documents no API to positively confirm OAuth-vs-API-key auth before
# a query (checked against current docs, 2026-08 — see THREAT_MODEL.md);
# this label reflects that the enumerated override variables (auth.py)
# were confirmed absent, not an independently verified provider
# assertion. Documented as a residual limitation, not hidden.
AUTH_MODE_LABEL = "operator-subscription-oauth-assumed"
