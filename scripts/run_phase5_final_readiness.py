#!/usr/bin/env python
"""P5-D FINAL_T2 readiness at R, authorization of commit A and its
confirmation (ADR-0012 Amendment C; Stage 2C-B6-6a, owner-approved
readiness-at-R plan revision 4).

Landing this script executes nothing. Every command below runs only inside
Stage 2C-B6-6b, behind the owner's pre-issued conditional GO. Nothing may be
committed between the readiness source commit R and commit A, so every tool
the final GO needs lands here, in R.

Subcommands:

``collect-final``  read-only: gather the attempt's facts (the probe-at-R
                   artifact, git, GitHub, the local scheduler, the owner's
                   Console transcription, FX, model lifecycle), evaluate every
                   component and write the FINAL_T2 DRAFT (never authoritative).
``verify-final``   ``--draft``: strict-load and evaluate a draft against fresh
                   GitHub server time. ``--record``: strict-load a frozen record,
                   evaluate it, and check its binding to the latch's
                   ``owner_go_ref`` and to the retained copies (closure use).
``authorize``      the single governed freeze point (R13): fresh server time,
                   freeze, two durable retained copies, digest, ``owner_go_ref``,
                   ATTEMPT_AUTHORIZED append. No discretionary prompt (R17).
``confirm-a``      after the push: the independent binding, latch-only child of
                   R, T_A freshness and head-of-main checks (R15, R22).
``pre-dispatch``   immediately before dispatch: HEAD == origin/main == remote
                   main == A, clean tree, exact-SHA CI success on A (R22).
``write-set``      after committing A and before pushing it: one commit on R,
                   only the latch file, one added line.

GitHub server time only (HTTP ``Date`` and REST timestamps); never a local
clock and never a git committer date. No provider, OIDC or model call; no
marker; no workflow dispatch.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from scripts import run_phase5_readiness as b63  # noqa: E402
from sentinel.phase5 import final_readiness as fr  # noqa: E402
from sentinel.phase5 import latch as lt  # noqa: E402
from sentinel.phase5 import readiness as rd  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
REPOSITORY = rd.REPLACEMENT_REPOSITORY
LATCH_REL = lt.LATCH_PATH.as_posix()
FINAL_RECORD_PATH = Path("artifacts/phase5_readiness_final.json")
PROBE_WORKFLOW_FILE = "sentinel-latch-read-probe.yml"
OFFICIAL_RUNNER = "scripts/run_phase5_official_gate.py"
G_RUN, G_REST, G_LOCAL, G_OWNER, G_WEB = (
    "RUNNER_ARTIFACT", "GITHUB_REST_OWNER_TOKEN", "LOCAL_MACHINE", "OWNER_CONSOLE", "AGENT_WEB")


class FinalCollectError(RuntimeError):
    """A fact could not be gathered or a precondition failed. Never carries a
    token or a local path."""


class Sources(b63.Sources):
    """The B6-3 sources plus the reads this stage adds."""

    def remote_main_sha(self) -> str:
        return self.client().get_main_head_sha()


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# Anchors: T_R, CI on R, the probe at R
# ---------------------------------------------------------------------------


def push_timestamp(src: Sources, *, before: str, after: str) -> datetime:
    entries = [e for e in src.client().list_push_activity(lt.MAIN_REF) if e.after == after]
    if len(entries) != 1 or entries[0].before != before or entries[0].activity_type != "push":
        raise FinalCollectError("push activity for the commit is missing or ambiguous")
    return entries[0].timestamp


def ci_success(src: Sources, sha: str) -> fr.CiR:
    runs = src.gh_json(["run", "list", "--commit", sha, "--workflow", "ci.yml", "--json",
                        "databaseId,conclusion,status,headSha,event,updatedAt"])
    ok = [r for r in runs if r.get("headSha") == sha and r.get("status") == "completed"
          and r.get("conclusion") == "success" and r.get("event") == "push"]
    if not ok:
        raise FinalCollectError("no exact-SHA CI success exists for the commit")
    first = min(ok, key=lambda r: _utc(r["updatedAt"]))
    return fr.CiR(sha=sha, run_id=str(first["databaseId"]), head_sha=sha, conclusion="success",
                  completed_at_utc=_utc(first["updatedAt"]).replace(microsecond=0))


def probe_binding(src: Sources, *, r: str, run_id: str, attempt: int, previous_run_id: str | None) -> fr.ProbeBinding:
    run = src.gh_json(["api", f"repos/{REPOSITORY}/actions/runs/{run_id}"])
    if not (run.get("path") == rd.PROBE_WORKFLOW and run.get("head_sha") == r and run.get("event") == "workflow_dispatch"
            and run.get("run_attempt") == 1 and run.get("status") == "completed" and run.get("conclusion") == "success"):
        raise FinalCollectError("the probe run is not a completed, successful attempt-1 dispatch at R")
    listed = src.gh_json(["run", "list", "--workflow", PROBE_WORKFLOW_FILE, "--commit", r, "--json",
                          "databaseId,createdAt"])
    runs_at_r = tuple(str(x["databaseId"]) for x in sorted(listed, key=lambda x: _utc(x["createdAt"])))
    if attempt == 2 and (previous_run_id is None or runs_at_r[:1] != (previous_run_id,)):
        raise FinalCollectError("attempt 2 must name attempt 1's probe as the only earlier probe at R")
    if attempt == 1 and previous_run_id is not None:
        raise FinalCollectError("attempt 1 has no previous probe")
    artifacts = src.gh_json(["api", f"repos/{REPOSITORY}/actions/runs/{run_id}/artifacts"])["artifacts"]
    named = [a for a in artifacts if a["name"] == f"sentinel-p5-latchprobe-r{run_id}-a1"]
    if len(named) != 1 or not named[0].get("digest"):
        raise FinalCollectError("exactly one digested probe evidence artifact is required")
    zip_bytes = b63.download_artifact_zip(src, str(named[0]["id"]), named[0]["digest"])
    inner = b63.extract_single(zip_bytes, "phase5_latch_read_probe.json")
    return fr.ProbeBinding(
        run_id=run_id, run_attempt=1, artifact_id=str(named[0]["id"]), archive_digest=named[0]["digest"],
        file_sha256=fr.sha256_hex(inner), run_created_at_utc=_utc(run["created_at"]),
        run_started_at_utc=_utc(run["run_started_at"]), runs_at_r=runs_at_r,
        evidence=rd.ProbeEvidence.model_validate_json(inner),
    )


# ---------------------------------------------------------------------------
# Armed local facts
# ---------------------------------------------------------------------------


def armed_runner_facts(runner_text: str, common_text: str) -> dict:
    tree = ast.parse(runner_text)
    purpose = envelope_id = envelope_version = None
    for node in tree.body:
        target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else (
            node.target if isinstance(node, ast.AnnAssign) else None)
        if not isinstance(target, ast.Name) or node.value is None:
            continue
        if target.id == "PURPOSE" and isinstance(node.value, ast.Constant):
            purpose = node.value.value
        if target.id == "ENVELOPE" and isinstance(node.value, ast.Call):
            kw = {k.arg: k.value for k in node.value.keywords}
            envelope_id = getattr(kw.get("envelope_id"), "value", None)
            envelope_version = getattr(kw.get("envelope_version"), "value", None)
    fields = b63._function(tree, "_replacement_marker_fields")
    bound = False
    for node in ast.walk(fields):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Call) and getattr(node.value.func, "id", "") == "dict":
            kw = {k.arg: getattr(k.value, "id", None) for k in node.value.keywords}
            bound = kw == {"replacement_of_run_id": "REPLACEMENT_OF_RUN_ID", "owner_ruling_id": "OWNER_RULING_ID"}
    base = b63.runner_facts(runner_text, common_text)
    return {
        "runner_purpose": purpose, "runner_envelope_id": envelope_id, "runner_envelope_version": envelope_version,
        "marker_fields_bound": bound, "preflight_consults_latch_in_order": base["preflight_consults_latch_in_order"],
        "eligibility_requires_latch": base["eligibility_requires_latch"],
        "runner_never_overrides_attempts": base["runner_never_overrides_attempts"],
    }


def official_workflow_facts(workflow_text: str) -> dict:
    data = yaml.safe_load(workflow_text)
    steps = {s.get("id"): s for s in data["jobs"]["gate"]["steps"] if s.get("id")}
    marker_name = (steps.get("marker") or {}).get("with", {}).get("name")
    return {
        "workflow_marker_name_is_replacement":
            marker_name == "sentinel-p5-oneshot-p5d-replacement-sonnet-gate-r${{ github.run_id }}",
        "concurrency_group": data["concurrency"]["group"],
        "cancel_in_progress": data["concurrency"]["cancel-in-progress"],
        "rule_variable": data["jobs"]["gate"]["env"]["ANTHROPIC_FEDERATION_RULE_ID"],
    }


def latch_refuses_rerun(latch_text: str) -> bool:
    return 'if facts.ctx_run_attempt != 1:\n        return _refuse(state, "RERUN_ATTEMPT")' in latch_text


# ---------------------------------------------------------------------------
# Draft assembly
# ---------------------------------------------------------------------------


def _ok(**evidence) -> rd.Outcome:
    return rd.Outcome("PASS", dict(evidence), None)


def _all_true(label: str, checks: Mapping[str, bool], **evidence) -> rd.Outcome:
    return b63._all_true(label, checks, **evidence)


def _both(a: rd.Outcome, b: rd.Outcome, **evidence) -> rd.Outcome:
    ok = a.status == b.status == "PASS"
    return rd.Outcome("PASS" if ok else "FAIL", {**evidence, "a": a.evidence, "b": b.evidence},
                      None if ok else "; ".join(x for x in (a.reason, b.reason) if x))


def build_final_draft(
    src: Sources, *, r: str, attempt: int, probe: fr.ProbeBinding, conditional_go: fr.ConditionalGo,
    owner_console: fr.OwnerConsole, fx: fr.FxReading, lifecycle: Mapping, adjudications: Sequence[fr.FinalAdjudication],
    b5_identity: dict, scheduler: Mapping, t_local: datetime, t_r: datetime, ci: fr.CiR,
) -> fr.FinalReadinessDraft:
    t_local = t_local.replace(microsecond=0)
    lifecycle_at = _utc(lifecycle.get("read_at_utc", "")) if lifecycle.get("read_at_utc") else t_local
    stamps = {G_RUN: probe.t_floor, G_REST: t_local, G_LOCAL: t_local, G_OWNER: owner_console.opened_at_utc,
              G_WEB: min(fx.retrieved_at_utc, lifecycle_at)}
    ev = probe.evidence
    runner_text = src.read_text(OFFICIAL_RUNNER)
    common_text = src.read_text("scripts/_phase5_common.py")
    workflow_text = src.read_text(rd.OFFICIAL_WORKFLOW)
    armed = armed_runner_facts(runner_text, common_text)
    wf = official_workflow_facts(workflow_text)
    registry = b63.registry_facts(src.root / "artifacts/phase5_receipt_registry.jsonl")
    latch_local = b63.latch_facts_local(src.root / LATCH_REL)
    envelope = b63.envelope_facts(src.root / "artifacts/phase5_execution_envelope.json")
    marker = b63.marker_semantics_facts()
    head, origin, remote = src.git(["rev-parse", "HEAD"]), src.git(["rev-parse", "origin/main"]), src.remote_main_sha()
    clean = src.git(["status", "--porcelain"]) == ""
    gh = b63.github_state(src)
    run_numbers = b63.official_run_numbers(src, t_local)
    prefixes = b63.artifact_prefix_counts(src)
    out: dict[str, tuple[rd.Outcome, tuple[str, ...]]] = {}

    def covered(key: str) -> rd.Outcome:
        present = {f: src.exists(f) for f in rd.ROW_TEST_FILES[key]}
        return _all_true("exact-SHA CI or covering test files missing",
                         {"ci_success": True, **{f"present:{f}": v for f, v in present.items()}}, ci_run_id=ci.run_id)

    def carried(key: str) -> rd.Outcome:
        spec = rd.CARRY_SPECS[key]
        facts = {"artifacts": b63.artifact_digest_facts(src, spec.artifacts),
                 "path_diff": b63.diff_names(src, spec.source_sha, spec.path_set)}
        if spec.workflow_diff_allowed:
            facts["workflow_diff_lines"] = b63.workflow_diff_lines(src, spec.source_sha, rd.OFFICIAL_WORKFLOW)
        return fr.evaluate_armed_carry_forward(spec, facts)

    out["1"] = (_all_true("closure", {"ci_success": True, "probe_import_closure":
                rd.evaluate_probe_check(ev, "import_closure", expected_sha=r).status == "PASS"}, probe_run_id=probe.run_id),
                (G_REST, G_RUN))
    direct = rd.parse_direct_pins(src.read_text("requirements.txt"))
    drift = rd.evaluate_runtime_drift(rd.classify_runtime_drift(b5_identity, ev.runtime_identity, direct_pins=direct),
                                      adjudications)
    image = ((ev.runtime_identity or {}).get("runner_image") or {}).get("image_os") or ""
    drift_family = rd.Outcome(drift.status if image == "ubuntu24" else "FAIL", {**drift.evidence, "image_os": image},
                              drift.reason if image == "ubuntu24" else "runner image is not the ubuntu24 family")
    out["2"] = out["16g"] = (drift_family, (G_RUN, G_REST))
    out["3"] = (_all_true("artifact access", {
        "probe_artifact_discovery": rd.evaluate_probe_check(ev, "artifact_discovery", expected_sha=r).status == "PASS",
        "no_replacement_marker": prefixes["replacement"] == 0, "no_gate_evidence": prefixes["gate_evidence"] == 0}),
        (G_RUN, G_REST))
    out["4.1"] = (rd.evaluate_carry_forward(rd.CARRY_SPECS["wif"], {
        "artifacts": b63.artifact_digest_facts(src, rd.B5_ARTIFACT_DIGESTS),
        "path_diff": b63.diff_names(src, rd.B5_SOURCE_SHA, rd.WIF_MECHANISM_PATHS)}), (G_REST, G_LOCAL))
    transcription = owner_console.transcription
    rule = rd.evaluate_replacement_rule(fr.rule_readback(
        transcription, variable_value=gh["variables"].get(fr.REPLACEMENT_RULE_VARIABLE),
        oidc_customization=gh["oidc_customization"]))
    rule_ids = _both(rule, fr.evaluate_identifiers(transcription, gh["variables"]),
                     workflow_reads_replacement_variable=wf["rule_variable"] == "${{ vars." + fr.REPLACEMENT_RULE_VARIABLE + " }}")
    if wf["rule_variable"] != "${{ vars." + fr.REPLACEMENT_RULE_VARIABLE + " }}":
        rule_ids = rd.Outcome("FAIL", rule_ids.evidence, "official workflow does not read the replacement variable")
    out["4.2"] = out["16h"] = (rule_ids, (G_OWNER, G_REST))
    out["5"] = (rd.evaluate_probe_check(ev, "layout", expected_sha=r), (G_RUN,))
    out["6"] = out["16e"] = (fr.evaluate_armed_marker_semantics({
        **marker, "history_permits_exactly_one": registry["history_permits_exactly_one"],
        "runner_purpose": armed["runner_purpose"], "marker_fields_bound": armed["marker_fields_bound"],
        "workflow_marker_name_is_replacement": wf["workflow_marker_name_is_replacement"]}), (G_LOCAL,))
    for key in ("7", "19", "22"):
        out[key] = (covered(key), (G_REST, G_LOCAL))
    env_outcome = rd.evaluate_envelope(envelope, workflow_timeout_minutes=b63.job_timeout_minutes(workflow_text))
    out["8"] = (env_outcome, (G_LOCAL,))
    out["9"] = (_both(env_outcome, rd.evaluate_probe_check(ev, "anchor", expected_sha=r)), (G_LOCAL, G_RUN))
    out["16d"] = (fr.evaluate_armed_envelope_binding(env_outcome, armed), (G_LOCAL,))
    for comp, key in (("13", "b2"), ("23", "b2"), ("14", "b5")):
        out[comp] = (carried(key), (G_REST, G_LOCAL))
    for comp, key in (("10", "pc"), ("11", "b2files"), ("12", "b2")):
        out[comp] = (_both(carried(key), covered(comp)), (G_REST, G_LOCAL))
    quality = rd.evaluate_quality_surface({
        "changed": b63.name_status(src, rd.ORIGINAL_RUN_SOURCE_SHA, rd.QUALITY_PATHS),
        "diff_sha256": {p: b63.diff_sha256(src, rd.ORIGINAL_RUN_SOURCE_SHA, p) for p in rd.QUALITY_ALLOWED_DIFFS},
        "frozen_manifest_identical": src.phase1_frozen_ok(),
        "runner_never_overrides_attempts": armed["runner_never_overrides_attempts"],
        "model_literal_pinned": b63._literals_pinned(src)})
    out["15"] = out["16c"] = (quality, (G_LOCAL,))
    out["16a"] = (_all_true("source", {"head_equals_r": head == r, "origin_equals_r": origin == r,
                                       "remote_main_equals_r": remote == r, "clean_tree": clean, "ci_success": True},
                            head=head), (G_LOCAL, G_REST))
    out["16b"] = (_all_true("workflows", {
        "workflow_set_expected": {w[0] for w in gh["workflows"]} == b63.EXPECTED_WORKFLOWS,
        "settings_default_read": gh["actions_workflow_permissions"].get("default_workflow_permissions") == "read"},
        official_workflow_sha256=b63.sha256_hex(src.read_bytes(rd.OFFICIAL_WORKFLOW)),
        finalizer_sha256=b63.sha256_hex(src.read_bytes("scripts/run_phase5_gate_finalizer.py"))), (G_REST, G_LOCAL))
    pins_equal = all(b63.b5_identity_dist(b5_identity).get(n) == v for n, v in direct.items())
    out["16f"] = (_all_true("dependency pins", {"direct_pins_equal_b5": pins_equal,
                                                "requirements_present": src.exists("requirements.txt")},
                            requirements_sha256=b63.sha256_hex(src.read_bytes("requirements.txt"))), (G_LOCAL, G_REST))
    out["16i"] = (fr.evaluate_cap_final(transcription, fx, now=t_local), (G_OWNER, G_WEB))
    out["16j"] = (_ok(ci_run_id=ci.run_id, sha=r), (G_REST,))
    lifecycle_outcome = rd.evaluate_model_lifecycle(lifecycle, now=t_local)
    out["16k"] = (_all_true("A8", {
        "lifecycle_ok": lifecycle_outcome.status == "PASS",
        "probe_env_clean": rd.evaluate_probe_check(ev, "env_scan", expected_sha=r).status == "PASS",
        "workflow_sets_no_override": not rd.scan_text_for_override_names(workflow_text),
        "a8_binding_unchanged": b63.sha256_hex(src.read_bytes("artifacts/phase5_a8_model_binding.json"))
        == rd.EXPECTED_A8_BINDING_SHA256}), (G_WEB, G_RUN, G_LOCAL))
    out["16l"] = (_all_true("scheduler", {
        "daily_run_disabled": scheduler.get("enabled") is False and scheduler.get("state") == "Disabled",
        "github_schedule_workflow_active": ("sentinel-schedule.yml", "active") in gh["workflows"]}), (G_LOCAL, G_REST))
    out["17"] = (_all_true("original marker", {
        "registry_has_receipts": registry["receipts"] == 4, "original_refused_durably": registry["original_refused_durably"],
        "probe_original_marker": rd.evaluate_probe_check(ev, "original_marker", expected_sha=r).status == "PASS"}),
        (G_LOCAL, G_RUN))
    out["18"] = (fr.evaluate_armed_retry_row({
        "ci_coverage": covered("18").status == "PASS", "concurrency_group": wf["concurrency_group"],
        "cancel_in_progress": wf["cancel_in_progress"],
        "latch_refuses_rerun_attempt": latch_refuses_rerun(src.read_text("sentinel/phase5/latch.py"))}), (G_REST, G_LOCAL))
    led = b63.ledger_facts(src.root / "telemetry/cost_ledger.jsonl", t_local)
    out["20"] = (_all_true("ledger", {
        "expected_run_ids": led["p5_run_ids"] == list(b63.EXPECTED_LEDGER_RUN_IDS),
        "timing_row_1061616": led["timing_row_micros"] == 1061616, "headroom_ok": led["headroom_ok"]},
        trailing_30d_spend_eur_micros=led["trailing_30d_spend_eur_micros"]), (G_LOCAL,))
    registry_sha = b63.sha256_hex(src.read_bytes("artifacts/phase5_receipt_registry.jsonl"))
    out["21"] = (_all_true("registry", {"four_receipts": registry["receipts"] == 4,
                                        "sha256_unchanged": registry_sha == rd.EXPECTED_REGISTRY_SHA256}), (G_LOCAL,))
    out["24"] = (rd.evaluate_probe_for_row24(ev, expected_sha=r), (G_RUN,))
    out["25"] = (fr.evaluate_armed_latch_row({
        **latch_local, "eligibility_requires_latch": armed["eligibility_requires_latch"],
        "preflight_consults_latch_in_order": armed["preflight_consults_latch_in_order"],
        "enforcement_tests_green": src.exists("tests/test_phase5_latch.py"),
        "history_permits_exactly_one": registry["history_permits_exactly_one"], "official_run_numbers": run_numbers,
        "replacement_prefix_artifacts": prefixes["replacement"], "gate_evidence_prefix_artifacts": prefixes["gate_evidence"],
        "replacement_receipts": registry["replacement_receipts"], "runner_purpose": armed["runner_purpose"],
        "runner_envelope_id": armed["runner_envelope_id"]}), (G_LOCAL, G_REST))

    return fr.FinalReadinessDraft(
        schema_version=1, stage="B6-6b", attempt=attempt, readiness_source_sha=r, t_r_utc=t_r,
        t_floor_utc=probe.t_floor, ci_r=ci, conditional_go=conditional_go, probe=probe, owner_console=owner_console,
        fx=fx, lifecycle=dict(lifecycle), provenance_stamps=stamps, rows=fr.assemble_rows(out, stamps),
        adjudications=tuple(adjudications),
    )


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def _json_file(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_conditional_go(path: Path) -> fr.ConditionalGo:
    data = _json_file(path)
    return fr.ConditionalGo(conditional_go_ref=data["conditional_go_ref"], issued_at_utc=_utc(data["issued_at_utc"]),
                            terms_sha256=fr.sha256_hex(str(data["terms"]).encode("utf-8")))


def load_owner_console(path: Path) -> fr.OwnerConsole:
    data = _json_file(path)
    transcription = fr.OwnerTranscription(**data["transcription"])
    return fr.OwnerConsole(
        transcription=transcription, transcription_sha256=fr.sha256_hex(fr.canonical_json_bytes(transcription)),
        opened_at_utc=_utc(data["opened_at_utc"]), closed_at_utc=_utc(data["closed_at_utc"]),
        attestation=fr.OWNER_CONSOLE_ATTESTATION if data.get("attested") is True else "NOT ATTESTED",
    )


def load_fx(path: Path) -> fr.FxReading:
    data = _json_file(path)
    return fr.FxReading(usd_per_eur=str(data["usd_per_eur"]), reference_date=data["reference_date"],
                        source_url=data["source_url"], retrieved_at_utc=_utc(data["retrieved_at_utc"]))


def load_adjudications(path: Path | None) -> list[fr.FinalAdjudication]:
    if path is None:
        return []
    return [fr.FinalAdjudication(**{**item, "ruled_at_utc": _utc(item["ruled_at_utc"])})
            for item in json.loads(Path(path).read_text(encoding="utf-8"))]


def load_draft(path: Path) -> fr.FinalReadinessDraft:
    raw = Path(path).read_bytes()
    draft = fr.FinalReadinessDraft.model_validate_json(raw)
    if fr.draft_bytes(draft) != raw:
        raise FinalCollectError("draft is not in canonical form")
    return draft


# ---------------------------------------------------------------------------
# Retention destinations (R18)
# ---------------------------------------------------------------------------


def _norm(path: Path | str) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def default_temp_roots() -> list[str]:
    roots = [tempfile.gettempdir()] + [os.environ[k] for k in ("TEMP", "TMP", "TMPDIR") if os.environ.get(k)]
    return sorted({_norm(r) for r in roots})


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("\\/") + os.sep)


SCRATCH_MARKERS = ("/.claude/", "/appdata/local/temp/")


def retention_problems(paths: Sequence[Path], *, repo_root: Path, temp_roots: Sequence[str],
                       scratch_markers: Sequence[str] = SCRATCH_MARKERS) -> list[str]:
    problems = []
    if len(paths) != 2:
        return ["exactly two durable retention paths are required"]
    resolved = [_norm(p) for p in paths]
    repo = _norm(repo_root)
    for index, (raw, path) in enumerate(zip(paths, resolved), start=1):
        lowered = path.replace("\\", "/").lower()
        if not Path(raw).is_absolute():
            problems.append(f"retention path {index} is not absolute")
        if _under(path, repo):
            problems.append(f"retention path {index} is inside the repository")
        if any(_under(path, root) for root in temp_roots):
            problems.append(f"retention path {index} is in OS temporary storage")
        if any(marker in lowered for marker in scratch_markers):
            problems.append(f"retention path {index} is in session scratch or temporary storage")
        if os.path.lexists(raw):
            problems.append(f"retention path {index} already exists")
        if not Path(raw).parent.is_dir():
            problems.append(f"retention path {index} has no existing parent directory")
    if resolved[0] == resolved[1]:
        problems.append("the two retention paths resolve to the same file")
    return problems


def write_retained(paths: Sequence[Path], frozen: bytes) -> None:
    for path in paths:
        with open(path, "xb") as handle:
            handle.write(frozen)
            handle.flush()
            os.fsync(handle.fileno())


def retained_problems(paths: Sequence[Path], frozen: bytes) -> list[str]:
    problems = []
    if len(paths) != 2:
        return ["exactly two retained copies are required"]
    for index, path in enumerate(paths, start=1):
        if not Path(path).is_file() or Path(path).read_bytes() != frozen:
            problems.append(f"retained copy {index} differs from the frozen bytes")
    try:
        if os.path.samefile(paths[0], paths[1]):
            problems.append("the two retained copies are the same file")
    except OSError:
        problems.append("a retained copy cannot be compared")
    return problems


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_collect_final(args: argparse.Namespace, src: Sources | None = None) -> int:
    src = src or Sources()
    r = args.readiness_source_sha
    t_local = src.server_time()
    parent = src.git(["rev-parse", f"{r}^"])
    t_r = push_timestamp(src, before=parent, after=r)
    ci = ci_success(src, r)
    probe = probe_binding(src, r=r, run_id=args.probe_run_id, attempt=args.attempt,
                          previous_run_id=args.previous_probe_run_id)
    b5 = b63.download_artifact_zip(src, rd.B5_ARTIFACT_DIGESTS[0][1], rd.B5_ARTIFACT_DIGESTS[0][2])
    identity_bytes = b63.extract_single(b5, "phase5_timing_runtime_identity.json")
    if fr.sha256_hex(identity_bytes) != rd.B5_IDENTITY_FILE_SHA256:
        raise FinalCollectError("B5 identity file digest differs from the recorded value")
    draft = build_final_draft(
        src, r=r, attempt=args.attempt, probe=probe, conditional_go=load_conditional_go(args.conditional_go),
        owner_console=load_owner_console(args.owner_console), fx=load_fx(args.fx),
        lifecycle=_json_file(args.lifecycle_snapshot), adjudications=load_adjudications(args.adjudications),
        b5_identity=json.loads(identity_bytes), scheduler=src.scheduler(), t_local=t_local, t_r=t_r, ci=ci,
    )
    Path(args.draft_out).write_bytes(fr.draft_bytes(draft))
    eligibility = fr.evaluate_final(draft, upper_utc=t_local.replace(microsecond=0))
    print(f"DRAFT written (not authoritative): eligible={eligibility.eligible}")
    for reason in eligibility.reasons:
        print(f"  not eligible: {reason}")
    return 0 if eligibility.eligible else 1


def cmd_verify_final(args: argparse.Namespace, src: Sources | None = None) -> int:
    if args.draft is not None:
        src = src or Sources()
        draft = load_draft(args.draft)
        eligibility = fr.evaluate_final(draft, upper_utc=src.server_time())
        print(f"DRAFT VERDICT: {'ELIGIBLE' if eligibility.eligible else 'NOT ELIGIBLE'} (a draft is never authoritative)")
        for reason in eligibility.reasons:
            print(f"  {reason}")
        return 0 if eligibility.eligible else 1
    frozen = Path(args.record).read_bytes()
    record = fr.load_frozen_record(frozen)
    eligibility = fr.evaluate_final_record(record)
    problems = list(eligibility.reasons)
    digest = fr.sha256_hex(frozen)
    records = lt.load_latch(Path(args.latch))
    if len(records) == 2:
        try:
            fr.assert_owner_go_ref_binds(records[1].owner_go_ref, frozen, readiness_source_sha=records[1].readiness_source_sha)
        except fr.FinalReadinessError as exc:
            problems.append(str(exc))
    if args.retain:
        problems.extend(retained_problems(args.retain, frozen))
    print(f"RECORD sha256={digest} owner_go_ref={fr.owner_go_ref_for(digest)} "
          f"verdict={'PASS' if not problems else 'FAIL'}")
    for reason in problems:
        print(f"  {reason}")
    return 0 if not problems else 1


def _git_state(src: Sources) -> tuple[str, str, bool]:
    src.git(["fetch", "--quiet", "origin", "main"])
    return src.git(["rev-parse", "HEAD"]), src.git(["rev-parse", "origin/main"]), src.git(["status", "--porcelain"]) == ""


def _restore_latch(src: Sources, latch_path: Path) -> None:
    src.git(["checkout", "--", LATCH_REL])
    if fr.sha256_hex(latch_path.read_bytes()) != rd.EXPECTED_LATCH_FILE_SHA256:
        raise FinalCollectError("latch could not be restored to the R bytes; STOP")


def cmd_authorize(args: argparse.Namespace, src: Sources | None = None, *, latch_path: Path | None = None,
                  temp_roots: Sequence[str] | None = None, scratch_markers: Sequence[str] = SCRATCH_MARKERS) -> int:
    """R13 steps 0 to 8. Any refusal before the append leaves nothing written
    to the latch; a post-append failure restores the latch to the R bytes."""
    src = src or Sources()
    latch_path = latch_path or (src.root / LATCH_REL)
    temp_roots = default_temp_roots() if temp_roots is None else temp_roots
    retain = [Path(p) for p in (args.retain or [])]
    # step 0
    draft = load_draft(args.draft)
    r = draft.readiness_source_sha
    head, origin, clean = _git_state(src)
    problems = []
    if not (head == origin == r == src.remote_main_sha()):
        problems.append("HEAD, origin/main, remote main and the draft's R are not all equal")
    if not clean:
        problems.append("the working tree is not clean")
    records = lt.load_latch(latch_path)
    if len(records) != 1 or fr.sha256_hex(latch_path.read_bytes()) != rd.EXPECTED_LATCH_FILE_SHA256:
        problems.append("the latch is not the committed GENESIS-only latch")
    if args.attempt != draft.attempt:
        problems.append("the attempt number differs from the draft's")
    if not args.conditional_go_ref or args.conditional_go_ref != draft.conditional_go.conditional_go_ref:
        problems.append("the pre-issued conditional_go_ref is missing or differs from the draft's")
    problems.extend(fr.anchor_problems(draft))
    problems.extend(retention_problems(retain, repo_root=REPO_ROOT, temp_roots=temp_roots,
                                       scratch_markers=scratch_markers))
    if problems:
        return _refuse(problems)
    # steps 1 to 3
    recorded_at = src.server_time().replace(microsecond=0)
    eligibility = fr.evaluate_final(draft, upper_utc=recorded_at)
    if not eligibility.eligible:
        return _refuse(list(eligibility.reasons))
    # step 4: the one construction
    frozen = fr.record_bytes(fr.freeze(draft, recorded_at))
    # step 5
    write_retained(retain, frozen)
    problems = retained_problems(retain, frozen)
    # step 6
    digest = fr.sha256_hex(frozen)
    if any(fr.sha256_hex(Path(p).read_bytes()) != digest for p in retain):
        problems.append("a retained copy's SHA-256 differs from the frozen digest")
    if problems:
        return _refuse(problems)
    # step 7
    owner_go_ref = fr.owner_go_ref_for(digest)
    try:
        fr.assert_owner_go_ref_binds(owner_go_ref, frozen, readiness_source_sha=r)
    except fr.FinalReadinessError as exc:
        return _refuse([str(exc)])
    # step 8
    prior = max(b63.official_run_numbers(src, recorded_at))
    target = latch_path
    if args.dry_run:
        target = Path(tempfile.mkdtemp(prefix="p5-latch-dryrun-")) / "latch.jsonl"
        target.write_bytes(latch_path.read_bytes())
    try:
        lt.append_attempt_authorized(target, server_now_utc=recorded_at, readiness_source_sha=r,
                                     owner_go_ref=owner_go_ref, window_closes_at_utc=recorded_at + fr.AUTHORIZATION_WINDOW,
                                     prior_official_gate_run_number=prior)
        appended = lt.load_latch(target)[-1]
        problems = []
        if appended.owner_go_ref != owner_go_ref or appended.readiness_source_sha != r:
            problems.append("the appended line does not carry the frozen owner_go_ref and R")
        if not args.dry_run:
            if src.git(["status", "--porcelain"]).strip() != f"M {LATCH_REL}":
                problems.append("the working tree change is not exactly the latch file")
            if src.git(["diff", "--numstat"]).split() != ["1", "0", LATCH_REL]:
                problems.append("the latch change is not exactly one added line")
    except (lt.LatchError, ValueError) as exc:
        problems = [f"append failed: {type(exc).__name__}"]
    if problems:
        if not args.dry_run:
            _restore_latch(src, latch_path)
        return _refuse(problems)
    label = "DRY RUN (nothing authorized; a temporary latch copy was used)" if args.dry_run else "AUTHORIZED LOCALLY"
    print(f"{label}: owner_go_ref={owner_go_ref} recorded_at_utc={recorded_at.isoformat()} prior_run_number={prior}")
    return 0


def _refuse(problems: Sequence[str]) -> int:
    print("AUTHORIZE REFUSED:", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1


def cmd_confirm_a(args: argparse.Namespace, src: Sources | None = None) -> int:
    """R15 and R22, independently of ``authorize``."""
    src = src or Sources()
    r = args.readiness_source_sha
    retain = [Path(p) for p in args.retain]
    problems: list[str] = []
    copies = [p.read_bytes() if p.is_file() else None for p in retain]
    if len(copies) != 2 or None in copies or copies[0] != copies[1]:
        print("CONFIRM-A FAIL: the two retained copies are missing or differ", file=sys.stderr)
        return 1
    frozen = copies[0]
    try:
        record = fr.load_frozen_record(frozen)
    except fr.FinalReadinessError as exc:
        print(f"CONFIRM-A FAIL: {exc}", file=sys.stderr)
        return 1
    digest = fr.sha256_hex(frozen)
    head, origin, clean = _git_state(src)
    a = head
    records = lt.load_latch(src.root / LATCH_REL)
    if len(records) != 2:
        problems.append("the checkout latch carries no ATTEMPT_AUTHORIZED record")
        return _confirm_fail(problems)
    auth = records[1]
    local_line = lt.record_line_bytes(auth)[:-1].decode("utf-8")
    commit = src.client().get_commit(a)
    if commit.parents != (r,):
        problems.append("commit A is not the direct child of R")
    if len(commit.files) != 1 or commit.files[0].filename != LATCH_REL or commit.files[0].status != "modified" \
            or commit.files[0].additions != 1 or commit.files[0].deletions != 0 or commit.files[0].patch is None:
        problems.append("commit A does not change only the latch file by one added line")
    else:
        added, removed = lt._patch_plus_minus_lines(commit.files[0].patch)
        if removed or added != [local_line]:
            problems.append("the GitHub patch line differs from the checkout's ATTEMPT_AUTHORIZED line")
    if auth.owner_go_ref != fr.OWNER_GO_REF_PREFIX + digest:
        problems.append("owner_go_ref does not equal the prefix plus the recomputed digest")
    if not (auth.readiness_source_sha == r == record.readiness_source_sha):
        problems.append("readiness_source_sha differs from R or from the record's R")
    if auth.recorded_at_utc != record.recorded_at_utc:
        problems.append("the latch recorded_at_utc differs from the record's")
    eligibility = fr.evaluate_final_record(record)
    problems.extend(eligibility.reasons)
    try:
        t_a = push_timestamp(src, before=r, after=a)
        t_r = push_timestamp(src, before=src.git(["rev-parse", f"{r}^"]), after=r)
        problems.extend(fr.post_push_problems(record, t_a=t_a, t_r=t_r))
    except FinalCollectError as exc:
        problems.append(str(exc))
    remote = src.remote_main_sha()
    if not (remote == a and head == origin == a):
        problems.append("remote main, origin/main and HEAD are not all commit A")
    if not clean:
        problems.append("the working tree is not clean")
    if src.git(["rev-list", "--count", f"{a}..origin/main"]) != "0":
        problems.append("a commit exists on main after A")
    if problems:
        return _confirm_fail(problems)
    print(f"CONFIRM-A PASS: A={a} owner_go_ref={auth.owner_go_ref}")
    return 0


def _confirm_fail(problems: Sequence[str]) -> int:
    print("CONFIRM-A FAIL:", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1


def cmd_pre_dispatch(args: argparse.Namespace, src: Sources | None = None) -> int:
    """R22: A is the head of main, the tree is clean, and CI on A succeeded.
    CI success never overrides a moved main."""
    src = src or Sources()
    a = args.commit_a
    head, origin, clean = _git_state(src)
    problems = []
    if not (head == origin == src.remote_main_sha() == a):
        problems.append("HEAD, origin/main and remote main are not all commit A")
    if not clean:
        problems.append("the working tree is not clean")
    if src.git(["rev-list", "--count", f"{a}..origin/main"]) != "0":
        problems.append("a commit exists on main after A")
    if not problems:
        try:
            ci_success(src, a)
        except FinalCollectError as exc:
            problems.append(str(exc))
    if problems:
        print("PRE-DISPATCH FAIL (do not dispatch):", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"PRE-DISPATCH PASS: A={a}")
    return 0


def cmd_write_set(args: argparse.Namespace, src: Sources | None = None) -> int:
    src = src or Sources()
    r = args.readiness_source_sha
    problems = []
    if src.git(["rev-list", "--count", f"{r}..HEAD"]) != "1":
        problems.append("there must be exactly one commit on R")
    if src.git(["rev-parse", "HEAD~1"]) != r:
        problems.append("the commit's parent is not R")
    if src.git(["diff", "--name-only", f"{r}..HEAD"]).splitlines() != [LATCH_REL]:
        problems.append("the commit changes more than the latch file")
    if src.git(["diff", "--numstat", f"{r}..HEAD"]).split() != ["1", "0", LATCH_REL]:
        problems.append("the commit is not exactly one added latch line")
    if problems:
        print("WRITE SET FAIL: " + "; ".join(problems), file=sys.stderr)
        return 1
    print("WRITE SET OK (commit A)")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect-final")
    collect.add_argument("--readiness-source-sha", required=True)
    collect.add_argument("--attempt", type=int, choices=(1, 2), required=True)
    collect.add_argument("--probe-run-id", required=True)
    collect.add_argument("--previous-probe-run-id", default=None)
    collect.add_argument("--conditional-go", type=Path, required=True)
    collect.add_argument("--owner-console", type=Path, required=True)
    collect.add_argument("--fx", type=Path, required=True)
    collect.add_argument("--lifecycle-snapshot", type=Path, required=True)
    collect.add_argument("--adjudications", type=Path, default=None)
    collect.add_argument("--draft-out", type=Path, required=True)
    verify = sub.add_parser("verify-final")
    group = verify.add_mutually_exclusive_group(required=True)
    group.add_argument("--draft", type=Path)
    group.add_argument("--record", type=Path)
    verify.add_argument("--latch", type=Path, default=REPO_ROOT / LATCH_REL)
    verify.add_argument("--retain", type=Path, action="append", default=None)
    auth = sub.add_parser("authorize")
    auth.add_argument("--draft", type=Path, required=True)
    auth.add_argument("--attempt", type=int, choices=(1, 2), required=True)
    auth.add_argument("--conditional-go-ref", default=None)
    auth.add_argument("--retain", type=Path, action="append", default=None)
    auth.add_argument("--dry-run", action="store_true")
    confirm = sub.add_parser("confirm-a")
    confirm.add_argument("--readiness-source-sha", required=True)
    confirm.add_argument("--retain", type=Path, action="append", required=True)
    pre = sub.add_parser("pre-dispatch")
    pre.add_argument("--commit-a", required=True)
    wset = sub.add_parser("write-set")
    wset.add_argument("--readiness-source-sha", required=True)
    sub.add_parser("time")
    args = parser.parse_args(argv)
    commands = {"collect-final": cmd_collect_final, "verify-final": cmd_verify_final, "authorize": cmd_authorize,
                "confirm-a": cmd_confirm_a, "pre-dispatch": cmd_pre_dispatch, "write-set": cmd_write_set}
    try:
        if args.command == "time":
            print(Sources().server_time().isoformat())
            return 0
        return commands[args.command](args)
    except (FinalCollectError, fr.FinalReadinessError, rd.ReadinessError, b63.CollectError, lt.LatchError) as exc:
        print(f"FINAL READINESS REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
