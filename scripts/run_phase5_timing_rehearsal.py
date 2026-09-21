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
        runs = client.list_workflow_runs(
            WORKFLOW_PATH,
            created_after=DISCOVERY_AFTER,
            created_before=datetime.now(timezone.utc) + DISCOVERY_SKEW,
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
    from agents.checker.fx import resolve_ecb_usd_per_eur

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

    fx_rate = resolve_ecb_usd_per_eur(now=datetime.now(timezone.utc))
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
            events.append(
                "OBSERVATION_ACCOUNTED",
                ordinal=item["ordinal"],
                item_id=item["id"],
                charged_eur_micros=charged,
                reserved_eur_micros=reserved,
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

    args = parser.parse_args(argv)
    try:
        return cmd_preflight(args) if args.command == "preflight" else cmd_execute(args)
    except Phase5ScriptError as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
