#!/usr/bin/env python
"""P5-D replacement readiness collector and verifier (ADR-0012 section 22,
Amendments A9 and B; Stage 2C-B6-3a, owner-approved plan revision 4).

Landing this script executes nothing. B6-3b, behind a separate owner GO,
runs ``collect`` once after the single real-runner probe dispatch, writes
``artifacts/phase5_readiness_b63.json`` and stops. The collector is
read-only: it reads local files, git, public GitHub state through the
``gh`` CLI, the downloaded probe artifact, an operator-supplied model
lifecycle snapshot and the local scheduler state. It writes the record and
nothing else, mutates no provider, repository, setting, variable or
scheduler, and never creates a marker, a latch record or a dispatch.

Subcommands:

``collect``  gather facts, evaluate every component, write the record.
``verify``   strict-load a record, print its rows verdict (a rows verdict
             only: closure is the post-push CI of the B6-3b commit, R9).
``write-set`` the R10 write-set proofs: ``pre`` before the B6-3b commit,
             ``post`` after it and before the push.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentinel.phase5 import readiness as rd  # noqa: E402
from sentinel.phase5 import artifact_names  # noqa: E402
from sentinel.phase5.github_evidence import GithubEvidenceClient, parse_http_date  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
REPOSITORY = rd.REPLACEMENT_REPOSITORY
RECORD_PATH = Path("artifacts/phase5_readiness_b63.json")
EXPECTED_WORKFLOWS = frozenset({
    "ci.yml", "sentinel-schedule.yml", "sentinel-rehearsal.yml", "sentinel-wif-probe.yml",
    "sentinel-official-gate.yml", "sentinel-window-control.yml", "sentinel-kill-rehearsal.yml",
    "sentinel-timing-rehearsal.yml", "sentinel-latch-read-probe.yml",
})
PREFLIGHT_ORDER = (
    "load_replacement_latch", "assert_no_replacement_gate_evidence_visible", "replacement_latch_admission",
    "assert_replacement_history_permits", "assert_purpose_armable",
)
EXPECTED_LEDGER_RUN_IDS = ("r-p5c-32783229864", "r-p5d-timing-36903206215")


class CollectError(RuntimeError):
    """A fact could not be gathered. Never carries a token or a local path."""


# ---------------------------------------------------------------------------
# Sources (injectable: tests pass fakes; the real run uses git, gh, powershell)
# ---------------------------------------------------------------------------


class Sources:
    def __init__(self, root: Path = REPO_ROOT) -> None:
        self.root = root

    def git(self, args: Sequence[str]) -> str:
        out = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, check=True)
        return out.stdout.strip()

    def git_bytes(self, args: Sequence[str]) -> bytes:
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, check=True).stdout

    def client(self) -> GithubEvidenceClient:
        token = self.gh_text(["auth", "token"]).strip()
        return GithubEvidenceClient(api_url="https://api.github.com", repository=REPOSITORY, token=token)

    def phase1_frozen_ok(self) -> bool:
        result = subprocess.run([sys.executable, "scripts/check_phase1_frozen.py"], cwd=self.root,
                                capture_output=True, text=True)
        return result.returncode == 0 and "41/41 blobs identical" in result.stdout

    def gh_json(self, args: Sequence[str]) -> object:
        out = subprocess.run(["gh", *args], cwd=self.root, capture_output=True, check=True)
        return json.loads(out.stdout)

    def gh_text(self, args: Sequence[str]) -> str:
        return subprocess.run(["gh", *args], cwd=self.root, capture_output=True, check=True).stdout.decode("utf-8")

    def gh_bytes(self, args: Sequence[str]) -> bytes:
        return subprocess.run(["gh", *args], cwd=self.root, capture_output=True, check=True).stdout

    def server_time(self) -> datetime:
        """GitHub server time: the HTTP Date of a GitHub response."""
        header = self.gh_text(["api", "-i", "rate_limit"])
        for line in header.splitlines():
            if line.lower().startswith("date:"):
                return parse_http_date(line.split(":", 1)[1].strip())
        raise CollectError("GitHub response carried no Date header")

    def scheduler(self) -> dict:
        script = (
            "$t=Get-ScheduledTask -TaskName 'SentinelDailyRun' -TaskPath '\\Sentinel\\';"
            "$i=Get-ScheduledTaskInfo -TaskName 'SentinelDailyRun' -TaskPath '\\Sentinel\\';"
            "@{state=[string]$t.State;enabled=[bool]$t.Settings.Enabled;"
            "actions=$t.Actions.Count;triggers=$t.Triggers.Count}|ConvertTo-Json -Compress"
        )
        out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True, check=True)
        return json.loads(out.stdout)

    def read_bytes(self, relative: str) -> bytes:
        return (self.root / relative).read_bytes()

    def read_text(self, relative: str) -> str:
        return (self.root / relative).read_text(encoding="utf-8")

    def exists(self, relative: str) -> bool:
        return (self.root / relative).exists()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Local facts
# ---------------------------------------------------------------------------


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def runner_facts(runner_text: str, common_text: str) -> dict:
    """AST facts about the official runner and the shared eligibility check."""
    runner = ast.parse(runner_text)
    constants = {}
    for node in runner.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            constants[node.targets[0].id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            constants[node.target.id] = node.value
    purpose = constants.get("PURPOSE")
    envelope = constants.get("ENVELOPE")
    preflight = _function(runner, "cmd_preflight")
    positions: dict[str, int] = {}
    calls: dict[str, int] = {}
    for node in ast.walk(preflight):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in PREFLIGHT_ORDER:
            positions.setdefault(node.func.id, node.lineno * 10000 + node.col_offset)
            calls[node.func.id] = calls.get(node.func.id, 0) + 1
    order_ok = (
        all(name in positions for name in PREFLIGHT_ORDER)
        and [positions[n] for n in PREFLIGHT_ORDER] == sorted(positions[n] for n in PREFLIGHT_ORDER)
        and all(calls[n] == 1 for n in PREFLIGHT_ORDER)
    )
    common = ast.parse(common_text)
    eligibility = _function(common, "assert_replacement_history_permits")
    kwonly = {a.arg: d for a, d in zip(eligibility.args.kwonlyargs, eligibility.args.kw_defaults)}
    return {
        "purpose_is_original": isinstance(purpose, ast.Constant) and purpose.value == "P5D_OFFICIAL_SONNET_GATE",
        "envelope_is_none": isinstance(envelope, ast.Constant) and envelope.value is None,
        "preflight_consults_latch_in_order": order_ok,
        "eligibility_requires_latch": "latch" in kwonly and kwonly["latch"] is None,
        "runner_never_overrides_attempts": "max_model_attempts_per_task" not in runner_text,
    }


def marker_semantics_facts() -> dict:
    from pydantic import ValidationError

    from sentinel.phase5 import artifact_names
    from sentinel.phase5.models import OneShotMarker
    from sentinel.phase5.replacement import REPLACEMENT_PURPOSE

    try:
        OneShotMarker(
            schema_version=1, purpose=REPLACEMENT_PURPOSE, created_at_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
            workflow_identity=rd.OFFICIAL_WORKFLOW, github_run_id="1", run_attempt=1, event="workflow_dispatch",
            source_sha="a" * 40,
        )
        unconstructible = False
    except (ValidationError, TypeError):
        unconstructible = True
    return {
        "unconstructible_without_frozen_fields": unconstructible,
        "canonical_name": artifact_names.oneshot_marker_name(REPLACEMENT_PURPOSE, "123"),
    }


def registry_facts(registry_path: Path) -> dict:
    from sentinel.phase5.oneshot import OneShotAlreadyConsumed, assert_purpose_not_yet_consumed_durably
    from sentinel.phase5.receipts import load_registry
    from sentinel.phase5.replacement import ORIGINAL_PURPOSE, REPLACEMENT_PURPOSE, replacement_history_verdict

    receipts = load_registry(registry_path)
    try:
        assert_purpose_not_yet_consumed_durably(ORIGINAL_PURPOSE, receipts, [])
        original_refused = False
    except OneShotAlreadyConsumed:
        original_refused = True
    return {
        "receipts": len(receipts),
        "replacement_receipts": sum(1 for r in receipts if r.purpose == REPLACEMENT_PURPOSE),
        "history_permits_exactly_one": replacement_history_verdict(receipts, [], REPLACEMENT_PURPOSE).permits_one_replacement,
        "original_refused_durably": original_refused,
    }


def latch_facts_local(latch_path: Path) -> dict:
    from sentinel.phase5.latch import load_latch, latch_verdict, record_sha256

    records = load_latch(latch_path)
    verdict = latch_verdict(records, None)
    return {
        "kinds": [r.record_kind for r in records],
        "file_sha256": sha256_hex(latch_path.read_bytes()),
        "head_sha256": record_sha256(records[-1]),
        "unarmed_refusal_reason": verdict.reason,
    }


def envelope_facts(envelope_path: Path) -> dict:
    from sentinel.phase5.execution_envelope import load_committed_envelope

    env = load_committed_envelope(envelope_path)
    return {
        "envelope_id": env.envelope_id, "envelope_version": env.envelope_version,
        "max_observed_ms": env.max_observed_ms, "outer_seconds": env.outer_seconds,
        "workflow_timeout_minutes": env.workflow_timeout_minutes, "session_duration_s": env.session_duration_s,
        "stall_budget_ms": env.stall_budget_ms,
    }


def job_timeout_minutes(workflow_text: str) -> int | None:
    match = re.search(r"^    timeout-minutes: (\d+)\s*$", workflow_text, flags=re.MULTILINE)
    return int(match.group(1)) if match else None


def ledger_facts(ledger_path: Path, now: datetime) -> dict:
    from sentinel.phase5.cadence import trailing_30d_spend_eur_micros, window_freeze_headroom_ok
    from telemetry.cost_ledger import read_cost_rows

    rows = read_cost_rows(ledger_path)
    p5 = sorted(r.run_id for r in rows if r.run_id.startswith("r-p5"))
    spend = trailing_30d_spend_eur_micros(ledger_path, now)
    return {
        "p5_run_ids": p5, "rows": len(rows), "trailing_30d_spend_eur_micros": spend,
        "headroom_ok": window_freeze_headroom_ok(spend, "DAILY"),
        "timing_row_micros": next((r.cost_eur_micros for r in rows if r.run_id == "r-p5d-timing-36903206215"), None),
    }


# ---------------------------------------------------------------------------
# Git-derived facts
# ---------------------------------------------------------------------------


def name_status(src: Sources, base: str, paths: Sequence[str]) -> dict[str, str]:
    out = src.git(["diff", "--name-status", "--no-renames", base, "HEAD", "--", *paths])
    result = {}
    for line in out.splitlines():
        status, _tab, path = line.partition("\t")
        if path:
            result[path] = status[0]
    return result


def diff_names(src: Sources, base: str, paths: Sequence[str]) -> list[str]:
    out = src.git(["diff", "--name-only", "--no-renames", base, "HEAD", "--", *paths])
    return sorted(line for line in out.splitlines() if line)


def diff_sha256(src: Sources, base: str, path: str) -> str:
    return sha256_hex(src.git_bytes(["diff", "--no-color", base, "HEAD", "--", path]))


def workflow_diff_lines(src: Sources, base: str, workflow: str) -> list[str]:
    out = src.git(["diff", "--no-color", "--unified=0", base, "HEAD", "--", workflow])
    return [ln for ln in out.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]


# ---------------------------------------------------------------------------
# GitHub facts
# ---------------------------------------------------------------------------


def ci_evidence(src: Sources, sha: str) -> dict | None:
    runs = src.gh_json(["run", "list", "--commit", sha, "--workflow", "ci.yml", "--json",
                        "databaseId,conclusion,status,headSha"])
    ok = [r for r in runs if r.get("headSha") == sha and r.get("status") == "completed" and r.get("conclusion") == "success"]
    if len(ok) < 1:
        return None
    return {"sha": sha, "run_id": str(ok[0]["databaseId"]), "head_sha": sha, "conclusion": "success"}


def artifact_digest_facts(src: Sources, specs: Sequence[tuple[str, str, str]]) -> dict:
    facts = {}
    for _name, artifact_id, _digest in specs:
        meta = src.gh_json(["api", f"repos/{REPOSITORY}/actions/artifacts/{artifact_id}"])
        facts[artifact_id] = {"digest": meta.get("digest"), "expired": bool(meta.get("expired"))}
    return facts


def download_artifact_zip(src: Sources, artifact_id: str, expected_digest: str | None) -> bytes:
    data = src.gh_bytes(["api", f"repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip"])
    if expected_digest is not None and "sha256:" + sha256_hex(data) != expected_digest:
        raise CollectError("downloaded artifact digest does not equal the GitHub digest")
    return data


def extract_single(zip_bytes: bytes, member: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        if member not in archive.namelist():
            raise CollectError("artifact does not contain the expected file")
        return archive.read(member)


def github_state(src: Sources) -> dict:
    variables = src.gh_json(["variable", "list", "--json", "name,value"])
    secrets = src.gh_json(["secret", "list", "--json", "name"])
    workflows = src.gh_json(["workflow", "list", "--all", "--json", "name,state,path"])
    perms = src.gh_json(["api", f"repos/{REPOSITORY}/actions/permissions/workflow"])
    oidc = src.gh_json(["api", f"repos/{REPOSITORY}/actions/oidc/customization/sub"])
    return {
        "variables": {v["name"]: v["value"] for v in variables},
        "secrets": sorted(s["name"] for s in secrets),
        "workflows": sorted((w["path"].rsplit("/", 1)[-1], w["state"]) for w in workflows),
        "actions_workflow_permissions": perms,
        "oidc_customization": {"use_default": oidc.get("use_default"), "use_immutable_subject": oidc.get("use_immutable_subject")},
    }


def official_run_numbers(src: Sources, now: datetime) -> list[int]:
    runs = src.client().list_workflow_runs_counted(
        rd.OFFICIAL_WORKFLOW, created_after=datetime(2026, 1, 1, tzinfo=timezone.utc),
        created_before=now + timedelta(hours=24),
    )
    return sorted(r.run_number for r in runs if r.run_number is not None)


def artifact_prefix_counts(src: Sources) -> dict:
    client = src.client()
    oneshot = client.list_artifacts(artifact_names.ONESHOT_PREFIX)
    return {
        "replacement": sum(1 for a in oneshot if "p5d-replacement-sonnet-gate" in a.name),
        "gate_evidence": len(client.list_artifacts(artifact_names.GATE_EVIDENCE_PREFIX)),
        "oneshot": len(oneshot),
    }


# ---------------------------------------------------------------------------
# Component assembly
# ---------------------------------------------------------------------------


class _Builder:
    def __init__(self, now: datetime) -> None:
        self.now = now.replace(microsecond=0)
        self.components: dict[str, rd.Component] = {}

    def add(self, component: str, outcome: rd.Outcome, *, state: str = "T0") -> None:
        self.components[component] = rd.Component(
            component=component, status=outcome.status, evidence_state=state, collected_at_utc=self.now,
            evidence=outcome.evidence, reason=outcome.reason,
        )

    def defer(self, component: str, definition: str, evidence: dict, reason: str) -> None:
        self.components[component] = rd.Component(
            component=component, status="DEFERRED", evidence_state="PREARM_BASELINE", collected_at_utc=self.now,
            evidence={"frozen_definition": definition, **evidence}, reason=reason,
        )


def _ok(**evidence) -> rd.Outcome:
    return rd.Outcome("PASS", dict(evidence), None)


def _bad(reason: str, **evidence) -> rd.Outcome:
    return rd.Outcome("FAIL", dict(evidence), reason)


def _all_true(label: str, checks: Mapping[str, bool], **evidence) -> rd.Outcome:
    failed = sorted(k for k, v in checks.items() if v is not True)
    return _bad(f"{label}: " + ", ".join(failed), **checks, **evidence) if failed else _ok(**checks, **evidence)


def build_record(src: Sources, *, base_sha: str, probe: rd.ProbeEvidence, b5_identity: dict,
                 lifecycle: Mapping, adjudications: Sequence[rd.Adjudication], scheduler: Mapping,
                 now: datetime) -> rd.ReadinessRecord:
    """Evaluate every component from facts gathered through ``src``."""
    b = _Builder(now)
    ci = ci_evidence(src, base_sha)
    if ci is None:
        raise CollectError("no exact-SHA CI success exists for the B6-3a SHA")
    ci_ok = True
    runner_text = src.read_text("scripts/run_phase5_official_gate.py")
    common_text = src.read_text("scripts/_phase5_common.py")
    workflow_text = src.read_text(rd.OFFICIAL_WORKFLOW)
    local = runner_facts(runner_text, common_text)
    registry = registry_facts(src.root / "artifacts/phase5_receipt_registry.jsonl")
    latch = latch_facts_local(src.root / "artifacts/phase5_replacement_latch.jsonl")
    envelope = envelope_facts(src.root / "artifacts/phase5_execution_envelope.json")
    marker = marker_semantics_facts()
    head = src.git(["rev-parse", "HEAD"])
    origin = src.git(["rev-parse", "origin/main"])
    clean = src.git(["status", "--porcelain"]) == ""
    gh = github_state(src)
    run_numbers = official_run_numbers(src, b.now)
    prefixes = artifact_prefix_counts(src)
    pre = "PREARM_BASELINE"

    def covered(key: str) -> rd.Outcome:
        present = {f: src.exists(f) for f in rd.ROW_TEST_FILES[key]}
        return _all_true("exact-SHA CI or covering test files missing", {"ci_success": ci_ok, **{f"present:{f}": v for f, v in present.items()}},
                         ci_run_id=ci["run_id"])

    # 1 clean runtime and dependency closure
    probe_closure = rd.evaluate_probe_check(probe, "import_closure", expected_sha=base_sha)
    b.add("1", _all_true("closure", {"ci_success": ci_ok, "probe_import_closure": probe_closure.status == "PASS"},
                         probe_run_id=probe.run_id), state=pre)
    # 2 deployment parity and 16g runtime identity, with R7 adjudication
    direct = rd.parse_direct_pins(src.read_text("requirements.txt"))
    classification = rd.classify_runtime_drift(b5_identity, probe.runtime_identity, direct_pins=direct)
    drift = rd.evaluate_runtime_drift(classification, adjudications)
    b.add("2", drift, state=pre)
    b.add("16g", drift, state=pre)
    # 3 artifact access
    art = rd.evaluate_probe_check(probe, "artifact_discovery", expected_sha=base_sha)
    b.add("3", _all_true("artifact access", {
        "probe_artifact_discovery": art.status == "PASS", "no_replacement_marker": prefixes["replacement"] == 0,
        "no_gate_evidence": prefixes["gate_evidence"] == 0}), state=pre)
    # 4 WIF mechanism (4.1 carried) and the deferred replacement rule (4.2)
    wif = rd.evaluate_carry_forward(rd.CARRY_SPECS["wif"], {
        "artifacts": artifact_digest_facts(src, rd.B5_ARTIFACT_DIGESTS),
        "path_diff": diff_names(src, rd.B5_SOURCE_SHA, rd.WIF_MECHANISM_PATHS)})
    b.add("4.1", wif)
    b.defer("4.2", "ADR-0012 Amendment B plan, deferred provider components, rule", {
        "variables": gh["variables"], "oidc_customization": gh["oidc_customization"]},
        "BLOCKED ON POST-ARMING PROVIDER PREPARATION: the replacement rule, variable and cap do not exist yet")
    # 5 work-root behavior
    b.add("5", rd.evaluate_probe_check(probe, "layout", expected_sha=base_sha), state=pre)
    # 6 replacement-marker semantics and 16e
    sem = _all_true("marker semantics", {
        "unconstructible_without_frozen_fields": marker["unconstructible_without_frozen_fields"],
        "canonical_name_is_replacement_slug": marker["canonical_name"] == "sentinel-p5-oneshot-p5d-replacement-sonnet-gate-r123",
        "history_permits_exactly_one": registry["history_permits_exactly_one"]}, canonical_name=marker["canonical_name"])
    b.add("6", sem)
    b.add("16e", sem)
    # 7, 18, 19, 22: exact-SHA CI covers the named test files
    for key in ("7", "18", "19", "22"):
        b.add(key, covered(key), state=pre)
    # 8 and 9 and 16d: envelope
    env_outcome = rd.evaluate_envelope(envelope, workflow_timeout_minutes=job_timeout_minutes(workflow_text))
    b.add("8", env_outcome)
    anchor = rd.evaluate_probe_check(probe, "anchor", expected_sha=base_sha)
    b.add("9", rd.Outcome("PASS" if env_outcome.status == "PASS" and anchor.status == "PASS" else "FAIL",
                          {**env_outcome.evidence, "anchor_resolved": anchor.status == "PASS"},
                          None if env_outcome.status == anchor.status == "PASS" else "envelope or job-start anchor failed"), state=pre)
    b.add("16d", env_outcome)
    # 10 to 14 and 23: carry-forward by mechanical diff (D4); rows 10, 11, 12 also need CI coverage
    def carried(key: str) -> rd.Outcome:
        spec = rd.CARRY_SPECS[key]
        facts = {"artifacts": artifact_digest_facts(src, spec.artifacts),
                 "path_diff": diff_names(src, spec.source_sha, spec.path_set)}
        if spec.workflow_diff_allowed:
            facts["workflow_diff_lines"] = workflow_diff_lines(src, spec.source_sha, rd.OFFICIAL_WORKFLOW)
        return rd.evaluate_carry_forward(spec, facts)

    for comp, key in (("13", "b2"), ("23", "b2"), ("14", "b5")):
        b.add(comp, carried(key))
    for comp, key in (("10", "pc"), ("11", "b2files"), ("12", "b2")):
        c, cov = carried(key), covered(comp)
        b.add(comp, rd.Outcome("PASS" if c.status == cov.status == "PASS" else "FAIL",
                               {"carry_forward": c.evidence, "ci_coverage": cov.evidence},
                               None if c.status == cov.status == "PASS" else
                               "; ".join(x for x in (c.reason, cov.reason) if x)), state=pre)
    # 15 and 16c: frozen quality surface
    quality = rd.evaluate_quality_surface({
        "changed": name_status(src, rd.ORIGINAL_RUN_SOURCE_SHA, rd.QUALITY_PATHS),
        "diff_sha256": {p: diff_sha256(src, rd.ORIGINAL_RUN_SOURCE_SHA, p) for p in rd.QUALITY_ALLOWED_DIFFS},
        "frozen_manifest_identical": src.phase1_frozen_ok(),
        "runner_never_overrides_attempts": local["runner_never_overrides_attempts"],
        "model_literal_pinned": _literals_pinned(src)})
    b.add("15", quality)
    b.add("16c", quality)
    # 17 original marker never reset
    b.add("17", _all_true("original marker", {
        "registry_has_receipts": registry["receipts"] == 4,
        "original_refused_durably": registry["original_refused_durably"],
        "probe_original_marker": rd.evaluate_probe_check(probe, "original_marker", expected_sha=base_sha).status == "PASS"}),
        state=pre)
    # 20 cost-class and accounting
    led = ledger_facts(src.root / "telemetry/cost_ledger.jsonl", now)
    b.add("20", _all_true("ledger", {
        "expected_run_ids": led["p5_run_ids"] == list(EXPECTED_LEDGER_RUN_IDS),
        "timing_row_1061616": led["timing_row_micros"] == 1061616, "headroom_ok": led["headroom_ok"]},
        trailing_30d_spend_eur_micros=led["trailing_30d_spend_eur_micros"]))
    # 21 durable receipts
    registry_sha = sha256_hex(src.read_bytes("artifacts/phase5_receipt_registry.jsonl"))
    b.add("21", _all_true("registry", {"four_receipts": registry["receipts"] == 4,
                                      "sha256_unchanged": registry_sha == rd.EXPECTED_REGISTRY_SHA256}), state=pre)
    # 24 and 25: the latch
    b.add("24", rd.evaluate_probe_for_row24(probe, expected_sha=base_sha), state=pre)
    latch_all = {**latch, **{k: local[k] for k in ("eligibility_requires_latch", "preflight_consults_latch_in_order",
                                                    "purpose_is_original", "envelope_is_none")},
                 "enforcement_tests_green": ci_ok and src.exists("tests/test_phase5_latch.py"),
                 "history_permits_exactly_one": registry["history_permits_exactly_one"],
                 "official_run_numbers": run_numbers, "replacement_prefix_artifacts": prefixes["replacement"],
                 "gate_evidence_prefix_artifacts": prefixes["gate_evidence"],
                 "replacement_receipts": registry["replacement_receipts"]}
    b.add("25", rd.evaluate_latch_row(latch_all), state=pre)
    # 16 sub-checks
    b.add("16a", _all_true("source", {"head_equals_origin": head == origin, "head_equals_b63a_sha": head == base_sha,
                                      "clean_tree": clean, "ci_success": ci_ok}, head=head))
    wf_files = {w[0] for w in gh["workflows"]}
    b.add("16b", _all_true("workflows", {
        "workflow_set_expected": wf_files == EXPECTED_WORKFLOWS,
        "settings_default_read": gh["actions_workflow_permissions"].get("default_workflow_permissions") == "read"},
        official_workflow_sha256=sha256_hex(src.read_bytes(rd.OFFICIAL_WORKFLOW)),
        finalizer_sha256=sha256_hex(src.read_bytes("scripts/run_phase5_gate_finalizer.py"))), state=pre)
    pins_equal = all(b5_identity_dist(b5_identity).get(n) == v for n, v in direct.items())
    b.add("16f", _all_true("dependency pins", {"direct_pins_equal_b5": pins_equal,
                                               "requirements_present": src.exists("requirements.txt")},
                           requirements_sha256=sha256_hex(src.read_bytes("requirements.txt"))))
    b.defer("16h", "ADR-0012 Amendment B plan, deferred provider components, rule",
            {"variables": gh["variables"]}, "BLOCKED ON POST-ARMING PROVIDER PREPARATION")
    b.defer("16i", "ADR-0012 Amendment B plan, deferred provider components, cap",
            {}, "BLOCKED ON POST-ARMING PROVIDER PREPARATION")
    b.add("16j", _ok(ci_run_id=ci["run_id"], sha=base_sha), state=pre)
    lifecycle_outcome = rd.evaluate_model_lifecycle(lifecycle, now=b.now)
    env_probe = rd.evaluate_probe_check(probe, "env_scan", expected_sha=base_sha)
    a8_sha = sha256_hex(src.read_bytes("artifacts/phase5_a8_model_binding.json"))
    overrides_in_workflow = rd.scan_text_for_override_names(workflow_text)
    b.add("16k", _all_true("A8", {
        "lifecycle_ok": lifecycle_outcome.status == "PASS", "probe_env_clean": env_probe.status == "PASS",
        "workflow_sets_no_override": not overrides_in_workflow,
        "a8_binding_unchanged": a8_sha == rd.EXPECTED_A8_BINDING_SHA256}), state=pre)
    sched_ok = scheduler.get("enabled") is False and scheduler.get("state") == "Disabled"
    schedule_active = ("sentinel-schedule.yml", "active") in gh["workflows"]
    b.add("16l", _all_true("scheduler", {"daily_run_disabled": sched_ok, "github_schedule_workflow_active": schedule_active}),
          state=pre)

    rows = []
    for row in rd.ROW_DEFS:
        comps = tuple(b.components[c] for c in row.components)
        rows.append(rd.RowEntry(row_id=row.row_id, status=rd.aggregate(c.status for c in comps), components=comps))
    return rd.ReadinessRecord(
        schema_version=1, matrix_version=1, stage="B6-3b", recorded_at_utc=b.now, base_source_sha=base_sha,
        closure=rd.CLOSURE_PENDING, ci=(rd.CiEvidence(**ci),), rows=tuple(rows),
        adjudications=tuple(adjudications),
    )


def b5_identity_dist(identity: Mapping) -> dict[str, str]:
    return {rd.normalize_distribution(n): v for n, v in identity["distributions"]}


def _literals_pinned(src: Sources) -> bool:
    runner = src.read_text("scripts/run_phase5_official_gate.py")
    config = src.read_text("agents/checker/config.py")
    return (
        "GATE_TOTAL_EUR_MICROS = 5_000_000" in runner and "GATE_RESERVE_EUR_MICROS = 1_000_000" in runner
        and "claude-sonnet-5" in config and "MAX_MODEL_ATTEMPTS_PER_TASK = 2" in config
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _load_adjudications(path: Path | None) -> list[rd.Adjudication]:
    if path is None:
        return []
    return [rd.Adjudication(**item) for item in json.loads(path.read_text(encoding="utf-8"))]


def cmd_collect(args: argparse.Namespace, src: Sources | None = None) -> int:
    src = src or Sources()
    base = args.base_source_sha
    now = src.server_time()
    probe_run = args.probe_run_id
    artifact = src.gh_json(["api", f"repos/{REPOSITORY}/actions/runs/{probe_run}/artifacts"])["artifacts"]
    named = [a for a in artifact if a["name"] == f"sentinel-p5-latchprobe-r{probe_run}-a1"]
    if len(named) != 1:
        raise CollectError("exactly one probe evidence artifact is required")
    zip_bytes = download_artifact_zip(src, str(named[0]["id"]), named[0].get("digest"))
    probe = rd.ProbeEvidence.model_validate_json(extract_single(zip_bytes, "phase5_latch_read_probe.json"))
    b5 = download_artifact_zip(src, rd.B5_ARTIFACT_DIGESTS[0][1], rd.B5_ARTIFACT_DIGESTS[0][2])
    identity_bytes = extract_single(b5, "phase5_timing_runtime_identity.json")
    if sha256_hex(identity_bytes) != rd.B5_IDENTITY_FILE_SHA256:
        raise CollectError("B5 identity file digest differs from the recorded value")
    record = build_record(
        src, base_sha=base, probe=probe, b5_identity=json.loads(identity_bytes),
        lifecycle=json.loads(args.lifecycle_snapshot.read_text(encoding="utf-8")),
        adjudications=_load_adjudications(args.adjudications), scheduler=src.scheduler(), now=now,
    )
    out = src.root / RECORD_PATH
    out.write_bytes(rd.record_bytes(record))
    eligibility = rd.evaluate_arming_eligibility(record)
    print(f"RECORD written: sha256={rd.record_sha256(record)} rows_eligible={eligibility.eligible}")
    for reason in eligibility.reasons:
        print(f"  not eligible: {reason}")
    return 0 if eligibility.eligible else 1


def cmd_verify(args: argparse.Namespace) -> int:
    record = rd.ReadinessRecord.model_validate_json(Path(args.record).read_bytes())
    if rd.record_bytes(record) != Path(args.record).read_bytes():
        print("record is not in canonical form", file=sys.stderr)
        return 2
    eligibility = rd.evaluate_arming_eligibility(record)
    print(f"ROWS VERDICT: {'ARMING-ELIGIBLE' if eligibility.eligible else 'NOT ELIGIBLE'} (closure {record.closure})")
    for reason in eligibility.reasons:
        print(f"  {reason}")
    return 0 if eligibility.eligible else 1


def _git_paths(src: Sources) -> tuple[set[str], set[str], set[str]]:
    dirty = set(src.git(["diff", "--name-only"]).splitlines())
    untracked = set(src.git(["ls-files", "--others", "--exclude-standard"]).splitlines())
    staged = set(src.git(["diff", "--cached", "--name-only"]).splitlines())
    return dirty, untracked, staged


def cmd_write_set(args: argparse.Namespace, src: Sources | None = None) -> int:
    src = src or Sources()
    try:
        if args.phase == "pre":
            dirty, untracked, staged = _git_paths(src)
            rd.assert_precommit_write_set(dirty, untracked, staged)
        else:
            base = args.base_source_sha
            names = src.git(["diff", "--name-only", f"{base}..HEAD"]).splitlines()
            count = int(src.git(["rev-list", "--count", f"{base}..HEAD"]))
            parent = src.git(["rev-parse", "HEAD~1"])
            rd.assert_postcommit_write_set(names, count, parent, base)
    except rd.ReadinessError as exc:
        print(f"WRITE SET FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"WRITE SET OK ({args.phase})")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect")
    collect.add_argument("--base-source-sha", required=True)
    collect.add_argument("--probe-run-id", required=True)
    collect.add_argument("--lifecycle-snapshot", type=Path, required=True)
    collect.add_argument("--adjudications", type=Path, default=None)
    sub.add_parser("time")
    verify = sub.add_parser("verify")
    verify.add_argument("record")
    wset = sub.add_parser("write-set")
    wset.add_argument("phase", choices=("pre", "post"))
    wset.add_argument("--base-source-sha", default=None)
    args = parser.parse_args(argv)
    try:
        if args.command == "collect":
            return cmd_collect(args)
        if args.command == "time":
            print(Sources().server_time().isoformat())
            return 0
        if args.command == "verify":
            return cmd_verify(args)
        if args.phase == "post" and not args.base_source_sha:
            parser.error("post requires --base-source-sha")
        return cmd_write_set(args)
    except (CollectError, rd.ReadinessError) as exc:
        print(f"READINESS REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
