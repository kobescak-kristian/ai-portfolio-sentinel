#!/usr/bin/env python
"""P5-D N=24 real-Sonnet timing-rehearsal driver (ADR-0012 section 13 and
Amendment A3/A5/A8; approved plan q77-p5d-repair-stage2cb4-plan-d;
dispatch q77-p5d-repair-stage2cb4-implement-a, Stage 2C-B4).

This stage BUILDS the rehearsal. It never runs it: the workflow that
invokes this driver is ``workflow_dispatch``-only and is not dispatched
by the implementing stage.

What it measures, and only that: the wall-clock duration of one COMPLETE
logical invocation at the production ``query_fn`` seam, including every
internal model turn and every tool turn. It is NOT a quality evaluation.
It imports no fixture, no answer key, no scorer and no threshold; it
derives no GREEN/HONEST_FAIL; it retains no model output.

Production reuse is exact. The real ``agents.checker.harness.run_query``
seam, the real ``ClaudeAgentOptions`` cage, the real prompt builders, the
real single MCP tool, the frozen ``MAX_TURNS`` and tool-call bounds are
all used unchanged, because a timing number from a toy provider call
would not describe production. The only deliberate difference is
``max_model_attempts_per_task=1``: ADR-0012 A3 rule 4 makes any
invocation that reaches its SDK budget ceiling an immediate rehearsal
STOP, so a second attempt must be unreachable BEFORE it would start.

No per-invocation deadline is applied. ``deadline_guarded`` is
deliberately NOT used here: truncating a legitimately slow call would
understate ``max_observed``, which is the one statistic this rehearsal
exists to produce.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._phase5_common import (  # noqa: E402
    REPO_ROOT,
    Phase5ScriptError,
    assert_expected_source_live,
    assert_expected_source_on_disk,
    bounded_read_retry,
    build_evidence_client,
    prepare_fresh_work_root,
    write_json_artifact,
)
from sentinel.phase5.github_context import derive_github_context  # noqa: E402

from agents.checker.process_control import (  # noqa: E402
    ancestors_of,
    descendants_of,
    read_process_stat,
    read_process_table,
)

# ---------------------------------------------------------------------------
# Frozen experiment identity (ADR-0012 section 13; plan-d)
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
LANE = "P5D_TIMING_REHEARSAL"
WORKFLOW_PATH = ".github/workflows/sentinel-timing-rehearsal.yml"

N_OBSERVATIONS = 24
MODEL_ALIAS = "claude-sonnet-5"
SDK_PIN = "claude-agent-sdk==0.2.110"
TIMING_MAX_MODEL_ATTEMPTS = 1

# A3: the production-equivalent per-call reservation, and the hard class-B
# total. Before EVERY invocation the remaining capacity must still grant the
# FULL reservation -- a reduced SDK allowance could end a slow call earlier
# than production would and understate max_observed.
TOTAL_EUR_MICROS = 2_500_000
MAX_PER_CALL_RESERVE_EUR_MICROS = 1_000_000

# Section 7 envelope constants, frozen here only so the pre-registration
# records what B6 will later apply. This driver computes no envelope.
MARGIN_MULTIPLIER = "1.5"
FIXED_OVERHEAD_S = 600
FINALIZATION_RESERVE_S = 480
PLATFORM_CEILING_MIN = 360
FEASIBILITY_MAX_OBSERVED_MS = 148_000

JOB_BACKSTOP_MINUTES = 360
EVIDENCE_UPLOAD_TIMEOUT_MINUTES = 2

STRATUM_STATE = "STATE_SURFACE"
STRATUM_LINK = "LINK_SCANNED_SURFACE"

CHECK_CLASS_STATE = "stale-STATE-marker"
CHECK_CLASS_LINK = "missing-synthetic-label"

# Frozen rank-selected line counts, owner ruling applied: the 4798-line
# extreme tail is excluded prospectively and replaced, in each stratum, by
# the largest observed population value strictly below it.
STATE_LINE_COUNTS = (9, 28, 32, 32, 32, 34, 36, 38, 71, 71, 459, 459)
LINK_LINE_COUNTS = (3, 18, 33, 41, 70, 100, 138, 183, 246, 298, 430, 738)

CORPUS_PATH = REPO_ROOT / "rehearsal" / "timing" / "corpus.json"
PREREGISTRATION_PATH = REPO_ROOT / "rehearsal" / "timing" / "preregistration.json"

EVIDENCE_DIRNAME = "evidence"
EVENTS_FILENAME = "phase5_timing_events.jsonl"
RUNTIME_IDENTITY_FILENAME = "phase5_timing_runtime_identity.json"
TOPOLOGY_FILENAME = "phase5_timing_topology.json"
SUMMARY_FILENAME = "phase5_timing_summary.json"
STOP_FILENAME = "phase5_timing_stop.json"

EVIDENCE_FILENAMES = (
    EVENTS_FILENAME,
    RUNTIME_IDENTITY_FILENAME,
    TOPOLOGY_FILENAME,
    SUMMARY_FILENAME,
    STOP_FILENAME,
)

STOP_REASONS = (
    "SDK_BUDGET_CEILING",
    "UNDER_RESERVATION_REFUSAL",
    "COST_OVERSHOOT",
    "BUDGET_EXHAUSTED",
    "INFRASTRUCTURE_FAULT",
    "AUTH_OR_OIDC_FAULT",
    "TOPOLOGY_ESCAPE",
    "TOPOLOGY_CLI_UNIDENTIFIED",
    "INCOMPLETE_N",
    "FEASIBILITY_FAILURE",
    "PRIOR_RUN_PRESENT",
    "HASH_MISMATCH",
)

EVENT_TYPES = (
    "RUN_STARTED",
    "INVOCATION_STARTED",
    "INVOCATION_FINISHED",
    "OBSERVATION_ACCOUNTED",
    "RUN_FINISHED",
    "STOP",
)

DISCOVERY_AFTER = datetime(2026, 1, 1, tzinfo=timezone.utc)
DISCOVERY_SKEW = timedelta(minutes=5)

RESOLVED_MODEL_UNAVAILABLE = "UNAVAILABLE"
TOPOLOGY_SAMPLE_INTERVAL_S = 0.25


class TimingRehearsalStop(Phase5ScriptError):
    """The rehearsal refused or stopped under a frozen STOP rule."""

    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in STOP_REASONS:
            raise ValueError(f"unknown STOP reason {reason!r}")
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


# ---------------------------------------------------------------------------
# Frozen corpus generation (plan-d section 2.4)
# ---------------------------------------------------------------------------


def canonical_bytes(obj) -> bytes:
    """The one canonical serialization: sorted keys, compact separators,
    UTF-8, no trailing newline. The committed file IS these bytes, so its
    SHA-256 is verifiable by hashing the file."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def body_line(i: int) -> str:
    """Deterministic body line for 1-based line number ``i``. Varied
    markdown shape so tokenization is realistic; encodes no defect to find
    and no expected outcome."""
    remainder = i % 6
    if remainder == 0:
        return f"- entry {i}: status nominal; counter {i * 7 % 101}"
    if remainder == 1:
        return f"{i}. The synthetic record at position {i} references figure F{i % 13}."
    if remainder == 2:
        return f"Paragraph {i} describes a deterministic placeholder value of {i * 3}."
    if remainder == 3:
        return f"| {i} | placeholder-{i % 17} | {i * 11 % 97} |"
    if remainder == 4:
        return f"> Note {i}: this line exists to occupy structural space."
    return ""


def generate_text(item_id: str, line_count: int) -> str:
    """Exactly ``line_count`` LF-joined lines with NO trailing newline, so
    ``len(text.split("\\n")) == line_count`` -- which is what the production
    prompt builder numbers."""
    header = [
        f"# Synthetic timing surface {item_id}",
        "",
        "Generated for the Q-77 P5-D timing rehearsal. Synthetic content;",
        "not a quality fixture, not scored, and carrying no expected outcome.",
        "",
    ]
    closing = f"<!-- end of synthetic timing surface {item_id} -->"
    if line_count < 6:
        lines = header[: line_count - 1] + [closing]
    else:
        lines = list(header) + [body_line(i) for i in range(6, line_count)] + [closing]
    return "\n".join(lines)


def build_corpus() -> dict:
    """The frozen 24-item corpus. Strata interleave so warm-up or
    throttling drift loads both equally."""
    items = []
    for k in range(1, 13):
        for stratum, prefix, counts, check_class, path, ordinal in (
            (STRATUM_STATE, "tim-state", STATE_LINE_COUNTS, CHECK_CLASS_STATE, "STATE.md", 2 * k - 1),
            (STRATUM_LINK, "tim-link", LINK_LINE_COUNTS, CHECK_CLASS_LINK, "README.md", 2 * k),
        ):
            item_id = f"{prefix}-{k:02d}"
            line_count = counts[k - 1]
            items.append(
                {
                    "id": item_id,
                    "ordinal": ordinal,
                    "stratum": stratum,
                    "check_class": check_class,
                    "surface": f"sentinel-timing/{item_id}",
                    "path": path,
                    "line_count": line_count,
                    "text": generate_text(item_id, line_count),
                }
            )
    items.sort(key=lambda entry: entry["ordinal"])
    return {"schema_version": SCHEMA_VERSION, "lane": LANE, "n": N_OBSERVATIONS, "items": items}


def build_preregistration(corpus: dict, corpus_sha256: str) -> dict:
    """Everything B5 must not be able to reinterpret after seeing timings."""
    return {
        "schema_version": SCHEMA_VERSION,
        "lane": LANE,
        "adr": "ADR-0012 section 13; Amendment A3 (budget), A5 (cancellation), A8 (identity)",
        "approved_plan": "q77-p5d-repair-stage2cb4-plan-d",
        "n": N_OBSERVATIONS,
        "corpus_sha256": corpus_sha256,
        "items": [
            {
                "id": item["id"],
                "ordinal": item["ordinal"],
                "stratum": item["stratum"],
                "check_class": item["check_class"],
                "surface": item["surface"],
                "path": item["path"],
                "line_count": item["line_count"],
                "text_sha256": sha256_hex(item["text"].encode("utf-8")),
            }
            for item in corpus["items"]
        ],
        "strata": {
            STRATUM_STATE: {
                "n": 12,
                "check_class": CHECK_CLASS_STATE,
                "line_counts": list(STATE_LINE_COUNTS),
                "total_lines": sum(STATE_LINE_COUNTS),
            },
            STRATUM_LINK: {
                "n": 12,
                "check_class": CHECK_CLASS_LINK,
                "line_counts": list(LINK_LINE_COUNTS),
                "total_lines": sum(LINK_LINE_COUNTS),
            },
        },
        "total_corpus_lines": sum(STATE_LINE_COUNTS) + sum(LINK_LINE_COUNTS),
        "tail_ruling": (
            "The 4798-line extreme tail observed in both production populations is "
            "excluded prospectively by owner ruling; in each stratum the final "
            "rank-selected anchor is replaced by the largest observed population "
            "value strictly below it. This is one frozen ruling for this corpus, "
            "not a general outlier-removal algorithm, and is not a post-hoc timing "
            "adjustment -- no provider timing exists."
        ),
        "execution": {
            "model": MODEL_ALIAS,
            "sdk_pin": SDK_PIN,
            "query_seam": "agents.checker.harness.run_query",
            "seam_wrapper": "CagedCheckerStub.query_fn",
            "prompt_builders": [
                "agents.checker.prompts.build_system_prompt",
                "agents.checker.prompts.build_user_prompt",
            ],
            "max_turns": 10,
            "max_tool_calls_per_check": 5,
            "max_model_attempts_per_task": TIMING_MAX_MODEL_ATTEMPTS,
            "production_default_max_model_attempts_per_task": 2,
            "sequential": True,
            "deadline_guarded": False,
            "measurement_boundary": (
                "one complete logical invocation at the query_fn seam, including "
                "all internal model and tool turns"
            ),
            "clock": "time.perf_counter_ns",
            "statistic": "max_observed",
        },
        "envelope_inputs": {
            "margin_multiplier": MARGIN_MULTIPLIER,
            "fixed_overhead_s": FIXED_OVERHEAD_S,
            "finalization_reserve_s": FINALIZATION_RESERVE_S,
            "platform_ceiling_min": PLATFORM_CEILING_MIN,
            "feasibility_max_observed_ms": FEASIBILITY_MAX_OBSERVED_MS,
        },
        "budget": {
            "total_eur_micros": TOTAL_EUR_MICROS,
            "max_per_call_reserve_eur_micros": MAX_PER_CALL_RESERVE_EUR_MICROS,
            "start_control": (
                "Before EVERY invocation, remaining capacity must still grant the FULL "
                "per-call reservation; otherwise STOP before provider contact. "
                "Equivalently, cumulative accounted consumption before any invocation "
                "must not exceed 1500000 micro-EUR."
            ),
        },
        "stop_reasons": list(STOP_REASONS),
        "no_discard": "No observation is discarded. Every observation counts.",
        "no_second_rehearsal": "No automatic second rehearsal is authorized.",
        "durability": {
            "events_file": EVENTS_FILENAME,
            "event_types": list(EVENT_TYPES),
            "invocation_started_fsynced_before_provider_call": True,
            "interrupted_invocation_rule": (
                "started_count != finished_count means an invocation was interrupted: "
                "STOP as INCOMPLETE_N / INFRASTRUCTURE_FAULT. It is never read as "
                "'only k-1 invocations occurred'."
            ),
        },
        "topology": {
            "identity": "(pid, starttime) pairs, never bare PIDs",
            "ancestry": "PPID closure only; SID/PGID are supplemental metadata and never redefine ancestry",
            "cli_identification": "/proc/<pid>/exe against the captured bundled-CLI path; never /proc cmdline",
            "child_subreaper": False,
            "pass": "CLI and all observed descendants remain inside the controlled PPID closure and the final survivor scan is empty",
            "stop": ["TOPOLOGY_ESCAPE", "TOPOLOGY_CLI_UNIDENTIFIED"],
            "residual_if_unclosed": "REAL_CLI_TOPOLOGY_UNOBSERVED",
        },
        "data_contract": {
            "retained": [
                "item id", "stratum", "ordinal", "elapsed_ms", "duration_ms",
                "duration_api_ms", "utc timestamps", "reserved_eur_micros",
                "charged_eur_micros", "sdk subtype/is_error/num_turns",
                "token counts", "failure class", "runtime identity reference",
                "resolved model identifier key set", "topology observations",
            ],
            "discarded": [
                "model response content", "ResultMessage.result", "transcripts",
                "content blocks", "prompt text", "/proc cmdline",
                "emitted finding content (counted only)",
            ],
            "no_scoring": True,
            "no_answer_key": True,
            "no_threshold_application": True,
            "no_quality_disposition": True,
        },
        "workflow": {
            "path": WORKFLOW_PATH,
            "trigger": "workflow_dispatch",
            "job_backstop_minutes": JOB_BACKSTOP_MINUTES,
            "evidence_upload_timeout_minutes": EVIDENCE_UPLOAD_TIMEOUT_MINUTES,
            "evidence_files": list(EVIDENCE_FILENAMES),
        },
        "exactly_once": {
            "run_attempt": 1,
            "prior_run_refusal": "any other visible run of this workflow stops the rehearsal",
            "discovery_after": DISCOVERY_AFTER.isoformat(),
            "durable_receipt_added": False,
        },
    }


def load_frozen(path: Path, label: str) -> tuple[dict, str]:
    """Strict read: the committed file must BE the canonical bytes."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise TimingRehearsalStop("HASH_MISMATCH", f"{label} is unreadable: {type(exc).__name__}") from exc
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise TimingRehearsalStop("HASH_MISMATCH", f"{label} is not valid JSON") from exc
    if canonical_bytes(obj) != data:
        raise TimingRehearsalStop("HASH_MISMATCH", f"{label} is not in canonical form")
    return obj, sha256_hex(data)


def verify_frozen_inputs() -> tuple[dict, dict, str, str]:
    corpus, corpus_sha = load_frozen(CORPUS_PATH, "corpus.json")
    prereg, prereg_sha = load_frozen(PREREGISTRATION_PATH, "preregistration.json")
    if corpus != build_corpus():
        raise TimingRehearsalStop("HASH_MISMATCH", "corpus.json does not match the frozen generator")
    if prereg.get("corpus_sha256") != corpus_sha:
        raise TimingRehearsalStop("HASH_MISMATCH", "preregistration corpus_sha256 does not match corpus.json")
    if len(corpus.get("items", ())) != N_OBSERVATIONS:
        raise TimingRehearsalStop("INCOMPLETE_N", "corpus does not contain exactly 24 items")
    return corpus, prereg, corpus_sha, prereg_sha


# ---------------------------------------------------------------------------
# Durable, content-free event stream
# ---------------------------------------------------------------------------


class EventLog:
    """Append-only fsynced timing events. Deliberately not the Phase-5
    operational journal: that vocabulary is gate-specific and closed."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.started = 0
        self.finished = 0

    def append(self, event: str, **fields) -> None:
        if event not in EVENT_TYPES:
            raise ValueError(f"unknown timing event {event!r}")
        record = {"event": event, "recorded_at_utc": datetime.now(timezone.utc).isoformat(), **fields}
        line = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if event == "INVOCATION_STARTED":
            self.started += 1
        elif event == "INVOCATION_FINISHED":
            self.finished += 1


def adjudicate_events(path: Path) -> dict:
    """Read the durable stream back and apply the frozen rule.

    ``started_count != finished_count`` means an invocation began and was
    interrupted -- the runner was killed while the provider call was in
    flight. That is INCOMPLETE_N / INFRASTRUCTURE_FAULT. It is NEVER read
    as "only k-1 invocations occurred", because the START record proves
    the k-th call started and consumed its full reservation."""
    started: list = []
    finished: list = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("event") == "INVOCATION_STARTED":
            started.append(record.get("ordinal"))
        elif record.get("event") == "INVOCATION_FINISHED":
            finished.append(record.get("ordinal"))
    interrupted = sorted(set(started) - set(finished))
    complete = len(started) == len(finished) == N_OBSERVATIONS
    return {
        "started_count": len(started),
        "finished_count": len(finished),
        "interrupted_ordinals": interrupted,
        "complete": complete,
        "stop_reason": None if complete else "INCOMPLETE_N",
    }


# ---------------------------------------------------------------------------
# Topology sampling (PPID ancestry; never cmdline)
# ---------------------------------------------------------------------------


def process_identity(pid: int, proc_root: Path) -> "tuple[int, int] | None":
    stat = read_process_stat(pid, proc_root)
    return None if stat is None else (stat.pid, stat.starttime)


def exe_target(pid: int, proc_root: Path) -> "str | None":
    """Resolve ``/proc/<pid>/exe``. Never reads cmdline: prompt material
    can appear there and must never enter evidence."""
    try:
        return os.readlink(str(Path(proc_root) / str(pid) / "exe"))
    except OSError:
        return None


def sample_topology(root_pid: int, cli_path: "str | None", proc_root: Path) -> dict:
    table = read_process_table(proc_root)
    closure = {stat.pid for stat, _depth in descendants_of(root_pid, table)}
    observed = []
    cli_pids = []
    for pid in sorted(closure):
        stat = table.get(pid)
        if stat is None:
            continue
        target = exe_target(pid, proc_root)
        entry = {
            "pid": stat.pid,
            "starttime": stat.starttime,
            "ppid": stat.ppid,
            "in_ppid_descendant_closure": True,
            "ancestors_reach_root": root_pid in ancestors_of(stat.pid, table),
        }
        if cli_path and target and Path(target).name == Path(cli_path).name:
            entry["is_bundled_cli"] = True
            cli_pids.append(stat.pid)
        observed.append(entry)
    return {
        "root_pid": root_pid,
        "closure_size": len(closure),
        "processes": observed,
        "bundled_cli_pids": cli_pids,
    }


class TopologySampler:
    """Samples the controlled PPID closure while an invocation is live."""

    def __init__(self, root_pid: int, cli_path: "str | None", proc_root: Path) -> None:
        self.root_pid = root_pid
        self.cli_path = cli_path
        self.proc_root = proc_root
        self.samples: list = []
        self._stop = threading.Event()
        self._thread: "threading.Thread | None" = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.samples.append(sample_topology(self.root_pid, self.cli_path, self.proc_root))
            except Exception:  # noqa: BLE001 - sampling never breaks the measurement
                pass
            self._stop.wait(TOPOLOGY_SAMPLE_INTERVAL_S)

    def start(self) -> "TopologySampler":
        self._thread = threading.Thread(target=self._run, name="p5-timing-topology", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> dict:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        cli_seen = any(sample["bundled_cli_pids"] for sample in self.samples)
        escaped = [
            entry
            for sample in self.samples
            for entry in sample["processes"]
            if not entry["ancestors_reach_root"]
        ]
        return {
            "samples": len(self.samples),
            "bundled_cli_observed": cli_seen,
            "max_closure_size": max((s["closure_size"] for s in self.samples), default=0),
            "ancestry_escapes": escaped,
        }


def survivor_scan(root_pid: int, proc_root: Path) -> dict:
    table = read_process_table(proc_root)
    survivors = [
        {"pid": stat.pid, "starttime": stat.starttime, "ppid": stat.ppid}
        for stat, _depth in descendants_of(root_pid, table)
    ]
    return {"survivors": survivors, "survivor_count": len(survivors)}


# ---------------------------------------------------------------------------
# Exactly-once: prior-run refusal
# ---------------------------------------------------------------------------


def assert_no_prior_timing_run(client, current_run_id: str) -> int:
    """Any other visible run of the timing workflow stops the rehearsal.
    A previous failed preflight is NOT permission to run again."""
    try:
        # The bounded retry covers ONLY the GitHub read. Everything below it --
        # including a prior run actually being present -- is deterministic and
        # refuses on the first attempt (B5-P0 Part 7).
        runs = bounded_read_retry(
            lambda: client.list_workflow_runs(
                WORKFLOW_PATH,
                created_after=DISCOVERY_AFTER,
                created_before=datetime.now(timezone.utc) + DISCOVERY_SKEW,
            )
        )
    except Exception as exc:  # noqa: BLE001 - incomplete discovery is never "no prior run"
        raise TimingRehearsalStop(
            "PRIOR_RUN_PRESENT", f"prior-run discovery failed: {type(exc).__name__}"
        ) from exc
    others = [run for run in runs if str(run.run_id) != str(current_run_id)]
    if others:
        raise TimingRehearsalStop(
            "PRIOR_RUN_PRESENT",
            f"{len(others)} prior run(s) of the timing workflow are visible",
        )
    return len(runs)


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------


def evidence_dir(work_root: Path) -> Path:
    return Path(work_root) / EVIDENCE_DIRNAME


def cmd_preflight(args: argparse.Namespace) -> int:
    from sentinel.phase5.runtime_identity import (
        RuntimeIdentityError,
        capture_runtime_identity,
        sdk_pin_matches,
    )
    from agents.checker import auth, oidc
    from agents.checker.fx import fetch_ecb_daily_xml, resolve_ecb_usd_per_eur

    env = os.environ
    ctx = derive_github_context(env)
    if ctx.run_attempt != 1:
        raise TimingRehearsalStop("PRIOR_RUN_PRESENT", f"run_attempt is {ctx.run_attempt}, not 1")

    source_sha = assert_expected_source_on_disk(args.expected_source_sha)
    client = build_evidence_client(env)  # pops GITHUB_TOKEN
    visible = assert_no_prior_timing_run(client, ctx.run_id)
    assert_expected_source_live(client, args.expected_source_sha)

    corpus, prereg, corpus_sha, prereg_sha = verify_frozen_inputs()

    oidc.write_placeholder_token_file(env)
    auth.assert_wif_config_ready(env)

    # Retry ONLY the network fetch, through fx.py's existing injectable seam:
    # parse_ecb_daily_xml still runs exactly once on the result, so a malformed
    # or undated ECB response stays a deterministic first-attempt refusal.
    # agents/checker/fx.py itself is deliberately not modified, which keeps the
    # scheduled lane and the official gate on today's exact behaviour.
    fx_rate = resolve_ecb_usd_per_eur(
        now=datetime.now(timezone.utc),
        fetch=lambda: bounded_read_retry(lambda: fetch_ecb_daily_xml(timeout=10.0)),
    )
    prepare_fresh_work_root(args.work_root)
    evidence = evidence_dir(args.work_root)
    evidence.mkdir(parents=False, exist_ok=False)

    write_json_artifact(
        {
            "source": fx_rate.source,
            "rate_date": fx_rate.rate_date,
            "retrieved_at_utc": fx_rate.retrieved_at_utc.isoformat(),
            "usd_per_eur": str(fx_rate.usd_per_eur),
        },
        args.fx_state_path,
    )

    try:
        identity = capture_runtime_identity(env=env)
    except RuntimeIdentityError as exc:
        raise TimingRehearsalStop("INFRASTRUCTURE_FAULT", f"runtime identity capture failed: {exc}") from exc

    write_json_artifact(
        {
            "schema_version": SCHEMA_VERSION,
            "lane": LANE,
            "runtime_identity_id": identity.runtime_identity_id,
            "python_version": identity.python_version,
            "python_implementation": identity.python_implementation,
            "sys_platform": identity.sys_platform,
            "machine": identity.machine,
            "os_release": identity.os_release,
            "runner_image": identity.runner.model_dump(),
            "sdk": {
                "distribution_name": identity.sdk.distribution_name,
                "version": identity.sdk.version,
                "wheel_tags": list(identity.sdk.wheel_tags),
                "record_sha256": identity.sdk.record_sha256,
                "transport_module": identity.sdk.transport_module.model_dump(),
                "bundled_cli": identity.sdk.bundled_cli.model_dump(),
                "cli_selection": identity.sdk.cli_selection,
            },
            "sdk_pin_matches": sdk_pin_matches(identity),
            "distribution_count": len(identity.distributions),
            # ADR-0012 A8 requires the fully RESOLVED dependency set, not a
            # count: once the hosted runner is destroyed the set is otherwise
            # unrecoverable. RuntimeIdentity.distributions is already sorted,
            # unique and length-capped, so this is written verbatim.
            # requirements.txt stays a DIRECT-pin reconciliation surface -- every
            # direct requirement must match this set and claude-agent-sdk must
            # equal the pin exactly -- but this complete set is NOT required to
            # equal the requirements file, because that file is not a transitive
            # lock.
            "distributions": [[name, version] for name, version in identity.distributions],
        },
        evidence / RUNTIME_IDENTITY_FILENAME,
    )

    baseline = sample_topology(os.getpid(), identity.sdk.bundled_cli.record_path, Path("/proc")) \
        if sys.platform.startswith("linux") else {"unsupported_platform": sys.platform}
    write_json_artifact(
        {"schema_version": SCHEMA_VERSION, "lane": LANE, "baseline": baseline, "invocations": []},
        evidence / TOPOLOGY_FILENAME,
    )

    print(
        "PREFLIGHT: lane=%s source=%s prior_runs_visible=%d corpus_sha=%s prereg_sha=%s"
        % (LANE, source_sha, visible, corpus_sha[:16], prereg_sha[:16])
    )
    return 0


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


def _usage_tokens(result) -> "tuple[int | None, int | None]":
    """``(input_tokens, output_tokens)`` from ``ResultMessage.usage`` -- the
    same source ``agents/checker/harness.py`` already persists to the ledger.

    Returns ``None`` for a count the SDK did not expose. ``None`` means
    UNKNOWN and is never collapsed to an observed zero: the frozen data
    contract requires token counts to be RETAINED, and a retention
    requirement is not a PASS condition, so an absent count is not a timing
    failure (plan-d R2)."""
    usage = getattr(result, "usage", None) or {}
    if not isinstance(usage, dict):
        return None, None
    values = []
    for key in ("input_tokens", "output_tokens"):
        value = usage.get(key)
        values.append(value if isinstance(value, int) and not isinstance(value, bool) else None)
    return values[0], values[1]


def _reconcile_tokens(
    ordinal: int,
    finished: "tuple[int | None, int | None]",
    accounted: "tuple[int | None, int | None]",
) -> None:
    """Where BOTH sides expose a number they must agree exactly.

    One side being unknown is fine and common. Two exposed numbers that
    disagree mean the durable event stream and the accounting ledger
    describe different invocations, so the rehearsal fails closed rather
    than publishing an unreconciled observation."""
    for label, lhs, rhs in (
        ("input_tokens", finished[0], accounted[0]),
        ("output_tokens", finished[1], accounted[1]),
    ):
        if lhs is not None and rhs is not None and lhs != rhs:
            raise TimingRehearsalStop(
                "INFRASTRUCTURE_FAULT",
                f"ordinal {ordinal}: {label} contradiction "
                f"(INVOCATION_FINISHED {lhs} != OBSERVATION_ACCOUNTED {rhs})",
            )


def _resolved_model_keys(result) -> list:
    usage = getattr(result, "model_usage", None)
    if isinstance(usage, dict) and usage:
        return sorted(str(key) for key in usage)
    return []


def cmd_execute(args: argparse.Namespace) -> int:
    import functools

    from agents.checker import auth, oidc
    from agents.checker.budget import RunBudgetCoordinator
    from agents.checker.fx import FxRate
    from agents.checker.harness import CagedCheckerStub
    from agents.checker.oidc import health_gated
    from checks.judgment.stubs import JudgmentRequest
    from contracts.schemas import RunRecord
    from sentinel import ledger

    env = os.environ
    evidence = evidence_dir(args.work_root)
    events = EventLog(evidence / EVENTS_FILENAME)
    observations: list = []
    topology_records: list = []
    session = None
    conn = None
    stop_reason: "str | None" = None
    stop_detail = ""

    try:
        ctx = derive_github_context(env)
        corpus, prereg, corpus_sha, prereg_sha = verify_frozen_inputs()
        identity_doc = json.loads((evidence / RUNTIME_IDENTITY_FILENAME).read_text(encoding="utf-8"))
        cli_path = identity_doc["sdk"]["bundled_cli"]["record_path"]
        runtime_identity_id = identity_doc["runtime_identity_id"]

        client = build_evidence_client(env)
        assert_expected_source_live(client, args.expected_source_sha)

        fx_data = json.loads(args.fx_state_path.read_text(encoding="utf-8"))
        fx_rate = FxRate(
            source=fx_data["source"],
            rate_date=fx_data["rate_date"],
            retrieved_at_utc=datetime.fromisoformat(fx_data["retrieved_at_utc"]),
            usd_per_eur=Decimal(fx_data["usd_per_eur"]),
        )

        events.append(
            "RUN_STARTED",
            lane=LANE,
            run_id=ctx.run_id,
            run_attempt=ctx.run_attempt,
            workflow_identity=ctx.workflow_path,
            source_sha=ctx.sha,
            expected_source_sha=args.expected_source_sha,
            corpus_sha256=corpus_sha,
            preregistration_sha256=prereg_sha,
            model=MODEL_ALIAS,
            runtime_identity_id=runtime_identity_id,
            n=N_OBSERVATIONS,
        )

        session = oidc.acquire_oidc(env)
        session.install_and_start(env)

        run_id = f"r-p5d-timing-{ctx.run_id}"
        conn = ledger.open_ledger(args.work_root / "timing.sqlite3")
        with ledger.unit_of_work(conn):
            ledger.insert_run(
                conn,
                RunRecord(
                    schema_version=1, run_id=run_id, run_kind="live", status="RUNNING",
                    started_at_utc=datetime.now(timezone.utc), finished_at_utc=None,
                    tasks_created=0, tasks_terminal=0,
                    findings_new=0, findings_still_open=0, findings_resolved=0,
                ),
            )

        coordinator = RunBudgetCoordinator(
            fx_rate=fx_rate,
            total_eur_micros=TOTAL_EUR_MICROS,
            max_per_call_reserve_eur_micros=MAX_PER_CALL_RESERVE_EUR_MICROS,
        )
        stub = CagedCheckerStub(
            run_id=run_id,
            conn=conn,
            coordinator=coordinator,
            model=MODEL_ALIAS,
            auth_profile=auth.WIF,
            max_model_attempts_per_task=TIMING_MAX_MODEL_ATTEMPTS,
        )

        proc_root = Path("/proc")
        pending: dict = {}
        inner = health_gated(stub.query_fn, session)

        @functools.wraps(inner)
        async def timed(check_class, reservation, state, user_prompt, model=None):
            item = pending["item"]
            sampler = None
            # Every invocation spawns a FRESH Agent-SDK CLI process that performs
            # its own provider exchange, and the provider rejects re-exchanging
            # one assertion. So install a never-exchanged assertion first.
            #
            # Ordering here is load-bearing and is pinned by test:
            #   1. acquisition happens BEFORE the INVOCATION_STARTED append, so a
            #      pre-provider auth failure cannot leave an orphan STARTED record
            #      that the frozen durability rule would misread as INCOMPLETE_N;
            #      it is the distinct frozen reason AUTH_OR_OIDC_FAULT instead.
            #   2. acquisition happens BEFORE started_ns, so this GitHub fetch can
            #      never enter elapsed_ms. The Anthropic-side exchange performed by
            #      the CLI is correctly INSIDE the measured window: it is part of
            #      one complete logical invocation and identical in production.
            try:
                session.prepare_fresh_assertion(env)
            except Exception as exc:  # noqa: BLE001 - pre-provider identity fault
                raise TimingRehearsalStop(
                    "AUTH_OR_OIDC_FAULT",
                    f"ordinal {item['ordinal']}: fresh assertion unavailable: "
                    f"{type(exc).__name__}",
                ) from exc
            # Durable BEFORE the provider call: a kill during this
            # invocation must still prove the invocation started.
            events.append(
                "INVOCATION_STARTED",
                ordinal=item["ordinal"],
                item_id=item["id"],
                stratum=item["stratum"],
                monotonic_ns=time.perf_counter_ns(),
                reserved_eur_micros=reservation.reserved_eur_micros,
                model=MODEL_ALIAS,
                runtime_identity_id=runtime_identity_id,
            )
            if sys.platform.startswith("linux"):
                sampler = TopologySampler(os.getpid(), cli_path, proc_root).start()
            started_ns = time.perf_counter_ns()
            try:
                outcome = await inner(check_class, reservation, state, user_prompt, model)
            finally:
                elapsed_ms = (time.perf_counter_ns() - started_ns) // 1_000_000
                topology = sampler.stop() if sampler is not None else {"samples": 0}
                topology_records.append({"ordinal": item["ordinal"], "item_id": item["id"], **topology})
                pending["elapsed_ms"] = elapsed_ms
                pending["topology"] = topology
            result = getattr(outcome, "result", None)
            # Token counts are recorded HERE, not only at terminal accounting:
            # a kill in between would otherwise lose counts that a ResultMessage
            # already exposed, because the SQLite ledger holding them is
            # ephemeral and is never uploaded. Unknown stays explicitly null.
            finished_tokens = _usage_tokens(result)
            pending["finished_tokens"] = finished_tokens
            events.append(
                "INVOCATION_FINISHED",
                ordinal=item["ordinal"],
                item_id=item["id"],
                elapsed_ms=elapsed_ms,
                sdk_subtype=getattr(result, "subtype", None),
                is_error=getattr(result, "is_error", None),
                num_turns=getattr(result, "num_turns", None),
                duration_ms=getattr(result, "duration_ms", None),
                duration_api_ms=getattr(result, "duration_api_ms", None),
                input_tokens=finished_tokens[0],
                output_tokens=finished_tokens[1],
                resolved_model_keys=_resolved_model_keys(result) or RESOLVED_MODEL_UNAVAILABLE,
                topology_samples=topology.get("samples", 0),
                bundled_cli_observed=topology.get("bundled_cli_observed", False),
            )
            return outcome

        stub.query_fn = timed

        for item in corpus["items"]:
            remaining = coordinator.remaining_eur_micros()
            if remaining < MAX_PER_CALL_RESERVE_EUR_MICROS:
                raise TimingRehearsalStop(
                    "UNDER_RESERVATION_REFUSAL",
                    f"remaining {remaining} < full reservation {MAX_PER_CALL_RESERVE_EUR_MICROS} "
                    f"before ordinal {item['ordinal']}",
                )
            pending.clear()
            pending["item"] = item
            failure_class = None
            try:
                findings = stub.judge(
                    JudgmentRequest(
                        surface=item["surface"],
                        check_class=item["check_class"],
                        path=item["path"],
                        text=item["text"],
                    )
                )
                finding_count = len(findings)
            except Exception as exc:  # noqa: BLE001 - classified below, never swallowed
                failure_class = type(exc).__name__
                finding_count = 0

            calls = ledger.list_agent_calls_for_run(conn, run_id)
            latest = calls[-1] if calls else None
            charged = getattr(latest, "charged_eur_micros", None)
            reserved = getattr(latest, "reserved_eur_micros", None)
            subtype = getattr(latest, "sdk_subtype", None)
            # Authoritative counts from the persisted AgentCallRow. Unknown stays
            # explicitly null and is NOT a timing failure; only a contradiction
            # between two exposed numbers fails closed.
            accounted_tokens = (
                getattr(latest, "input_tokens", None),
                getattr(latest, "output_tokens", None),
            )
            _reconcile_tokens(
                item["ordinal"],
                pending.get("finished_tokens", (None, None)),
                accounted_tokens,
            )
            events.append(
                "OBSERVATION_ACCOUNTED",
                ordinal=item["ordinal"],
                item_id=item["id"],
                charged_eur_micros=charged,
                reserved_eur_micros=reserved,
                input_tokens=accounted_tokens[0],
                output_tokens=accounted_tokens[1],
                sdk_subtype=subtype,
                failure_class=failure_class,
                finding_count=finding_count,
            )
            observations.append(
                {
                    "ordinal": item["ordinal"],
                    "item_id": item["id"],
                    "stratum": item["stratum"],
                    "elapsed_ms": pending.get("elapsed_ms"),
                    "charged_eur_micros": charged,
                    "reserved_eur_micros": reserved,
                    "sdk_subtype": subtype,
                    "failure_class": failure_class,
                }
            )

            if subtype == "error_max_budget_usd":
                raise TimingRehearsalStop("SDK_BUDGET_CEILING", f"ordinal {item['ordinal']}")
            if charged is not None and reserved is not None and charged > reserved:
                raise TimingRehearsalStop("COST_OVERSHOOT", f"ordinal {item['ordinal']}")
            if failure_class is not None:
                raise TimingRehearsalStop("INFRASTRUCTURE_FAULT", f"ordinal {item['ordinal']}: {failure_class}")

        verdict = adjudicate_events(events.path)
        if not verdict["complete"]:
            raise TimingRehearsalStop(
                "INCOMPLETE_N",
                f"started={verdict['started_count']} finished={verdict['finished_count']} "
                f"expected={N_OBSERVATIONS} interrupted={verdict['interrupted_ordinals']}",
            )
        durations = [obs["elapsed_ms"] for obs in observations if obs["elapsed_ms"] is not None]
        if len(durations) != N_OBSERVATIONS:
            raise TimingRehearsalStop("INCOMPLETE_N", f"only {len(durations)} durations recorded")
        max_observed = max(durations)
        if max_observed > FEASIBILITY_MAX_OBSERVED_MS:
            raise TimingRehearsalStop(
                "FEASIBILITY_FAILURE", f"max_observed_ms {max_observed} > {FEASIBILITY_MAX_OBSERVED_MS}"
            )
    except TimingRehearsalStop as exc:
        stop_reason, stop_detail = exc.reason, exc.detail
    except Exception as exc:  # noqa: BLE001 - any fault is a recorded STOP, never a silent pass
        stop_reason, stop_detail = "INFRASTRUCTURE_FAULT", type(exc).__name__
    finally:
        if session is not None:
            session.shutdown(env)
        else:
            oidc.scrub_identity_token_file(env)
        if conn is not None:
            conn.close()

    final_scan = survivor_scan(os.getpid(), Path("/proc")) if sys.platform.startswith("linux") else {
        "survivors": [], "survivor_count": 0, "unsupported_platform": sys.platform
    }
    escapes = [rec for rec in topology_records if rec.get("ancestry_escapes")]
    if stop_reason is None and escapes:
        stop_reason, stop_detail = "TOPOLOGY_ESCAPE", f"{len(escapes)} invocation(s) observed ancestry escape"
    if stop_reason is None and not any(rec.get("bundled_cli_observed") for rec in topology_records):
        stop_reason, stop_detail = "TOPOLOGY_CLI_UNIDENTIFIED", "no sample identified the bundled CLI"
    # Frozen topology.pass requires the CLI and all observed descendants to stay
    # inside the controlled closure AND the final survivor scan to be empty.
    # Without this check a non-empty scan wrote result=PASS alongside
    # c_dynamic_closed=false. Appended AFTER the two checks above so their
    # precedence and detail strings are unchanged.
    #
    # The reason is TOPOLOGY_ESCAPE by elimination: stop_reasons (twelve) and
    # topology.stop (exactly TOPOLOGY_ESCAPE and TOPOLOGY_CLI_UNIDENTIFIED) are
    # frozen preregistration keys, so no new reason may be introduced without
    # moving both frozen hashes. The detail string keeps the two mechanisms --
    # mid-run ancestry escape versus post-shutdown survival -- distinguishable.
    if stop_reason is None and final_scan.get("survivor_count", 0) > 0:
        stop_reason = "TOPOLOGY_ESCAPE"
        stop_detail = (
            f"final survivor scan non-empty: {final_scan['survivor_count']} "
            "survivor(s) after session shutdown"
        )

    topology_doc = json.loads((evidence / TOPOLOGY_FILENAME).read_text(encoding="utf-8"))
    topology_doc["invocations"] = topology_records
    topology_doc["final_survivor_scan"] = final_scan
    topology_doc["c_dynamic_closed"] = bool(
        stop_reason is None and final_scan.get("survivor_count") == 0
    )
    topology_doc["residual"] = None if topology_doc["c_dynamic_closed"] else "REAL_CLI_TOPOLOGY_UNOBSERVED"
    write_json_artifact(topology_doc, evidence / TOPOLOGY_FILENAME)

    if stop_reason is None:
        write_json_artifact(
            {
                "schema_version": SCHEMA_VERSION,
                "lane": LANE,
                "result": "PASS",
                "n": len(observations),
                "observations": observations,
                "observations_ms": [obs["elapsed_ms"] for obs in observations],
                "max_observed_ms": max(obs["elapsed_ms"] for obs in observations),
                "started_count": events.started,
                "finished_count": events.finished,
            },
            evidence / SUMMARY_FILENAME,
        )
        events.append("RUN_FINISHED", result="PASS", n=len(observations))
        print("TIMING: result=PASS n=%d" % len(observations))
        return 0

    write_json_artifact(
        {
            "schema_version": SCHEMA_VERSION,
            "lane": LANE,
            "result": "STOP",
            "reason": stop_reason,
            "detail": stop_detail,
            "observations_completed": len(observations),
            "started_count": events.started,
            "finished_count": events.finished,
        },
        evidence / STOP_FILENAME,
    )
    events.append("STOP", reason=stop_reason, detail=stop_detail, observations=len(observations))
    print("TIMING: result=STOP reason=%s observations=%d" % (stop_reason, len(observations)))
    return 3


# ---------------------------------------------------------------------------
# Governed class-B cost handoff (R4; plan-d Part 3)
# ---------------------------------------------------------------------------


def read_timing_events(path: Path) -> list:
    """Parse the downloaded append-only event stream. A malformed line
    fails closed: partial evidence is never silently narrowed."""
    records = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError as exc:
            raise Phase5ScriptError(f"{path} line {number} is not valid JSON") from exc
    return records


def aggregate_timing_spend(records: list) -> dict:
    """Classify every ordinal the run actually reached and total its charge.

    The four classes are frozen prospectively so nothing is decided after
    seeing the result:

    * **A** -- ``OBSERVATION_ACCOUNTED`` present. Authoritative charge and
      authoritative token counts.
    * **B** -- ``INVOCATION_FINISHED`` but no accounting. The provider call
      completed and the runner died before the ledger was read, so charge the
      FULL reservation and take R2's FINISHED-side counts when present.
    * **C** -- ``INVOCATION_STARTED`` only. Charge the FULL reservation; the
      START record proves the call began and consumed its reservation.
    * **D** -- no ``INVOCATION_STARTED``. No provider contact, contributes
      nothing.

    Classes B and C are charged through ``failures.terminal_charge`` rather
    than a local re-derivation, so this stays the one adopted ADR-0008 rule.
    """
    from agents.checker.failures import terminal_charge

    started, finished, accounted = {}, {}, {}
    for record in records:
        event, ordinal = record.get("event"), record.get("ordinal")
        if ordinal is None:
            continue
        if event == "INVOCATION_STARTED":
            started[ordinal] = record
        elif event == "INVOCATION_FINISHED":
            finished[ordinal] = record
        elif event == "OBSERVATION_ACCOUNTED":
            accounted[ordinal] = record

    basis, unresolved, unresolved_tokens, conservative = {}, [], [], []
    total_charged = total_input = total_output = 0
    for ordinal in sorted(started):
        reserved = started[ordinal].get("reserved_eur_micros")
        if not isinstance(reserved, int):
            raise Phase5ScriptError(
                f"ordinal {ordinal} started without a recorded reservation; refusing to guess"
            )
        if ordinal in accounted:
            row = accounted[ordinal]
            charged = row.get("charged_eur_micros")
            if not isinstance(charged, int):
                # Accounted but with no recoverable charge: conservative rule.
                charged = terminal_charge(
                    completed=False, reserved_eur_micros=reserved, estimate_eur_micros=None
                )
                conservative.append(ordinal)
            basis[ordinal] = "A"
            tokens = (row.get("input_tokens"), row.get("output_tokens"))
        else:
            charged = terminal_charge(
                completed=False, reserved_eur_micros=reserved, estimate_eur_micros=None
            )
            conservative.append(ordinal)
            unresolved.append(ordinal)
            if ordinal in finished:
                basis[ordinal] = "B"
                tokens = (
                    finished[ordinal].get("input_tokens"),
                    finished[ordinal].get("output_tokens"),
                )
            else:
                basis[ordinal] = "C"
                tokens = (None, None)
        total_charged += charged
        # An unknown count contributes 0 to the frozen non-nullable CostRow
        # field, exactly as build_agent_cost_row already does, and the ordinal
        # is named so that 0 is never readable as an observed zero.
        if tokens[0] is None or tokens[1] is None:
            unresolved_tokens.append(ordinal)
        total_input += tokens[0] or 0
        total_output += tokens[1] or 0

    return {
        "accounting_basis": basis,
        "unresolved_ordinals": tuple(sorted(set(unresolved))),
        "unresolved_token_ordinals": tuple(sorted(set(unresolved_tokens))),
        "conservative_full_reservation_ordinals": tuple(sorted(set(conservative))),
        "observations_accounted": len(basis),
        "cost_eur_micros": total_charged,
        "input_tokens": total_input,
        "output_tokens": total_output,
    }


def cmd_cost_evidence(args: argparse.Namespace) -> int:
    """Build the one governed class-B ``CostRow`` from preserved evidence.

    Runs on PASS and on STOP alike, because real money is spent either way.
    Refuses rather than guesses, and emits NO record when the evidence
    establishes no provider-started invocation -- there is no class-B spend
    to record in that case, and inventing a zero row would misdescribe a run
    that never reached the provider.

    The resulting record is appended to the committed ledger by
    ``scripts/record_phase5_cost_evidence.py``, which owns the strict parse,
    the duplicate-run_id refusal and the exactly-once assertion."""
    from datetime import datetime as _datetime

    from contracts.schemas import CostRow
    from sentinel.phase5.evidence_records import TimingCostEvidenceRecord

    evidence = Path(args.evidence_dir)
    events_path = evidence / EVENTS_FILENAME
    if not events_path.exists():
        raise Phase5ScriptError(
            f"{events_path} is absent: a visible run with no trustworthy timing "
            "artifact is B5 CONSUMED / STOP / NO VALID TIMING RESULT, never PASS, "
            "and no CostRow is derivable from it"
        )
    records = read_timing_events(events_path)

    run_started = next((r for r in records if r.get("event") == "RUN_STARTED"), None)
    if run_started is None:
        raise Phase5ScriptError("event stream carries no RUN_STARTED record; refusing to guess")
    for key, expected in (
        ("corpus_sha256", args.corpus_sha256),
        ("preregistration_sha256", args.preregistration_sha256),
    ):
        if expected is not None and run_started.get(key) != expected:
            raise Phase5ScriptError(f"RUN_STARTED {key} does not match the frozen value")

    has_summary = (evidence / SUMMARY_FILENAME).exists()
    has_stop = (evidence / STOP_FILENAME).exists()
    if has_summary and has_stop:
        raise Phase5ScriptError("both a summary and a stop record are present; ambiguous, failing closed")
    terminal_class = "PASS" if has_summary else ("STOP" if has_stop else "NO_ARTIFACT")

    spend = aggregate_timing_spend(records)
    if spend["observations_accounted"] == 0:
        print(
            "NO CLASS-B SPEND DUE: the preserved evidence establishes no "
            "provider-started invocation, so no CostRow is emitted and none is "
            "appended to the committed ledger."
        )
        return 4

    record = TimingCostEvidenceRecord(
        schema_version=1,
        lane=LANE,
        rehearsal_run_id=str(run_started.get("run_id")),
        rehearsal_run_attempt=int(run_started.get("run_attempt")),
        rehearsal_source_sha=str(run_started.get("source_sha")),
        corpus_sha256=str(run_started.get("corpus_sha256")),
        preregistration_sha256=str(run_started.get("preregistration_sha256")),
        terminal_class=terminal_class,
        observations_accounted=spend["observations_accounted"],
        accounting_basis=spend["accounting_basis"],
        unresolved_ordinals=spend["unresolved_ordinals"],
        unresolved_token_ordinals=spend["unresolved_token_ordinals"],
        conservative_full_reservation_ordinals=spend["conservative_full_reservation_ordinals"],
        cost_rows=(
            CostRow(
                schema_version=1,
                run_id=f"r-p5d-timing-{run_started.get('run_id')}",
                recorded_at_utc=_datetime.now(timezone.utc),
                run_kind="live",
                model=str(run_started.get("model", MODEL_ALIAS)),
                input_tokens=spend["input_tokens"],
                output_tokens=spend["output_tokens"],
                cost_eur_micros=spend["cost_eur_micros"],
            ),
        ),
    )
    args.out_path.parent.mkdir(parents=True, exist_ok=True)
    args.out_path.write_text(record.model_dump_json(), encoding="utf-8")
    print(
        "COST EVIDENCE: terminal_class=%s accounted=%d charge_eur_micros=%d "
        "unresolved=%s unresolved_tokens=%s"
        % (
            terminal_class,
            spend["observations_accounted"],
            spend["cost_eur_micros"],
            list(spend["unresolved_ordinals"]),
            list(spend["unresolved_token_ordinals"]),
        )
    )
    return 0


def main(argv: "list[str]") -> int:
    parser = argparse.ArgumentParser(description="P5-D N=24 Sonnet timing rehearsal")
    sub = parser.add_subparsers(dest="command", required=True)

    pre = sub.add_parser("preflight")
    pre.add_argument("--expected-source-sha", required=True)
    pre.add_argument("--work-root", type=Path, required=True)
    pre.add_argument("--fx-state-path", type=Path, required=True)

    exe = sub.add_parser("execute")
    exe.add_argument("--expected-source-sha", required=True)
    exe.add_argument("--work-root", type=Path, required=True)
    exe.add_argument("--fx-state-path", type=Path, required=True)

    # Post-hoc only: reads PRESERVED evidence after the run is terminal and the
    # artifact has been downloaded. Makes no provider, GitHub or network call.
    cost = sub.add_parser("cost-evidence")
    cost.add_argument("--evidence-dir", type=Path, required=True)
    cost.add_argument("--out-path", type=Path, required=True)
    cost.add_argument("--corpus-sha256", default=None)
    cost.add_argument("--preregistration-sha256", default=None)

    args = parser.parse_args(argv)
    handlers = {
        "preflight": cmd_preflight,
        "execute": cmd_execute,
        "cost-evidence": cmd_cost_evidence,
    }
    try:
        return handlers[args.command](args)
    except Phase5ScriptError as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
