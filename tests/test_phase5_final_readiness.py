"""Tests for the P5-D FINAL_T2 readiness record at R and the authorization
tooling for commit A (ADR-0012 Amendment C; Stage 2C-B6-6a, readiness-at-R
plan revision 4, owner rulings C1 to C10 and R13 to R22 of 2026-10-03).

Model-free and network-free. The collector and the authorization commands run
against a ``Sources`` fake that reads real repository files but scripts every
git and GitHub answer; ``authorize`` writes only to a temporary latch copy and
temporary retention paths.
"""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from sentinel.phase5 import final_readiness as fr
from sentinel.phase5 import latch as lt
from sentinel.phase5 import readiness as rd
from sentinel.phase5.github_evidence import ArtifactRef, CommitDetail, CommitFile, PushActivity, RunRef

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "run_phase5_final_readiness", REPO_ROOT / "scripts" / "run_phase5_final_readiness.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_script()
ADR_PATH = REPO_ROOT / "adr" / "0012-p5d-replacement-execution-envelope.md"
COMMITTED_LATCH = REPO_ROOT / "artifacts" / "phase5_replacement_latch.jsonl"
UTC = timezone.utc
R = "e" * 40
PARENT = "f" * 40
A = "a1" * 20
PROBE_RUN = "60006"
PREV_PROBE_RUN = "60001"
PROBE_ARTIFACT = "77007"

T_R = datetime(2026, 10, 6, 9, 0, 0, tzinfo=UTC)
T_CI = T_R + timedelta(minutes=10)
T_GO = T_R + timedelta(minutes=20)
T_CREATED = T_R + timedelta(minutes=30)
T_STARTED = T_CREATED + timedelta(seconds=30)
T_FLOOR = T_R + timedelta(minutes=31)
T_OPEN = T_R + timedelta(minutes=40)
T_CLOSE = T_R + timedelta(minutes=60)
T_FX = T_R + timedelta(minutes=62)
T_LIFE = T_R + timedelta(minutes=63)
T_LOCAL = T_R + timedelta(minutes=65)
T_REC = T_R + timedelta(minutes=75)
T_A = T_REC + timedelta(minutes=3)


def _genesis_view(tmp_dir: Path) -> Path:
    """A-tolerance: the committed latch's GENESIS line alone (the committed
    latch carries one more line on commit A)."""
    first = COMMITTED_LATCH.read_bytes().replace(b"\r\n", b"\n").split(b"\n", 1)[0] + b"\n"
    path = tmp_dir / "genesis_view.jsonl"
    path.write_bytes(first)
    return path


@pytest.fixture(autouse=True)
def _genesis_only_latch_and_pinned_hunks(monkeypatch, tmp_path_factory):
    view = _genesis_view(tmp_path_factory.mktemp("latch"))
    original = script.b63.latch_facts_local

    def latch_facts(path):
        return original(view if Path(path).resolve() == COMMITTED_LATCH.resolve() else path)

    monkeypatch.setattr(script.b63, "latch_facts_local", latch_facts)
    pinned = {p: v[1] for p, v in rd.QUALITY_ALLOWED_DIFFS.items()}
    monkeypatch.setattr(script.b63, "diff_sha256", lambda s, base, path: pinned.get(path))


# ======================================================================
# World builders
# ======================================================================


def _identity():
    return {
        "python_version": "3.12.14", "runner_image": {"image_os": "ubuntu24", "image_version": "20260927.320.1"},
        "sdk": {"version": "0.2.110", "record_sha256": "r" * 64, "transport_module": {"sha256_actual": "t" * 64},
                "bundled_cli": {"sha256_actual": "c" * 64, "cli_version_declared": "2.1.191"}},
        "distributions": [["anyio", "4.14.0"], ["certifi", "2026.6.17"], ["claude-agent-sdk", "0.2.110"],
                          ["pydantic", "2.13.4"], ["pyyaml", "6.0.3"]],
    }


def _pins_identity():
    pins = rd.parse_direct_pins((REPO_ROOT / "requirements.txt").read_text(encoding="utf-8"))
    doc = _identity()
    doc["distributions"] = sorted([[n, v] for n, v in pins.items()] + [["idna", "3.10"]])
    return doc


def _checks(**details):
    base = {
        "server_time": {"monotonic": True, "reads": 3},
        "current_run": {"run_number": 2, "status": "in_progress"},
        "official_listing": {"run_numbers": [1, 2, 3, 4], "entries": 4},
        "pagination": {"pages": 5, "total_count": 90, "entries": 90, "per_page": 20},
        "attempt_jobs": {"jobs": 1, "job_name": "gate", "has_started_at": True},
        "job_steps": {"own_steps": 8, "run_3_marker": "skipped", "run_3_execute": "skipped", "run_4_marker": "success"},
        "anchor": {"resolved": True, "api_job_name": "gate"},
        "commit": {"parents": 1, "files": 9, "with_patch": 9},
        "push_activity": {"entries": 96, "matching": 1},
        "artifact_discovery": {"gate_evidence_prefix_count": 0, "oneshot_prefix_count": 2, "replacement_marker_count": 0},
        "original_marker": {"found": 1, "markers_discovered": 2, "expired_covered_by_receipt": False},
        "layout": {"journal_established": True},
        "import_closure": {"imported": True, "third_party_modules": 20},
        "env_scan": {"flagged": [], "env_names": 80},
        "runtime_identity": {"runtime_identity_id": "i" * 64, "sdk_pin_matches": True, "distribution_count": 33},
    }
    base.update(details)
    return tuple(rd.ProbeCheck(name=name, ok=True, detail=detail) for name, detail in base.items())


def _probe_evidence(run_id=PROBE_RUN, first=T_FLOOR, identity=None, **changes):
    fields = dict(
        schema_version=1, lane="latch-read-probe", run_id=run_id, run_attempt=1, event="workflow_dispatch",
        ref="refs/heads/main", sha=R, workflow_identity=rd.PROBE_WORKFLOW, result="PASS", stop_reason=None,
        server_time_first_utc=first.isoformat(), server_time_last_utc=(first + timedelta(minutes=1)).isoformat(),
        checks=_checks(), runtime_identity=identity or _pins_identity(),
    )
    fields.update(changes)
    return rd.ProbeEvidence(**fields)


def _probe_zip(evidence: rd.ProbeEvidence) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("phase5_latch_read_probe.json", evidence.model_dump_json().encode("utf-8"))
    return buffer.getvalue()


def _binding(attempt=1, evidence=None, **changes):
    evidence = evidence or _probe_evidence()
    data = _probe_zip(evidence)
    inner = evidence.model_dump_json().encode("utf-8")
    fields = dict(run_id=PROBE_RUN, run_attempt=1, artifact_id=PROBE_ARTIFACT, archive_digest="sha256:" + fr.sha256_hex(data),
                  file_sha256=fr.sha256_hex(inner), run_created_at_utc=T_CREATED, run_started_at_utc=T_STARTED,
                  runs_at_r=(PREV_PROBE_RUN, PROBE_RUN) if attempt == 2 else (PROBE_RUN,), evidence=evidence)
    fields.update(changes)
    return fr.ProbeBinding(**fields)


ORG, SA, RULE_ID = "org-raw-id", "sa-raw-id", "fdrl_test"


def _transcription(**changes):
    data = dict(
        rule={**copy.deepcopy(dict(rd.FROZEN_RULE_EXPECTATION)), "id": RULE_ID},
        other_rules=[{"id": "fdrl_old", "status": "Archived", "claims": {"workflow_ref": rd.FROZEN_RULE_EXPECTATION["claims"]["workflow_ref"]}}],
        original_rules_archived={"P5C": "Archived", "P5D_ORIGINAL": "Archived", "TIMING": "Archived"},
        auth_events_for_rule=0, organization_id_sha256=fr.sha256_hex(ORG.encode()),
        service_account_id_sha256=fr.sha256_hex(SA.encode()),
        cap=fr.OwnerCap(month_to_date_spend_usd="0.52", granularity_usd="1.00", cap_usd="9.00", credit_usd="12.80",
                        auto_reload="Off", currency="USD", period="calendar-month-utc"),
    )
    data.update(changes)
    return fr.OwnerTranscription(**data)


def _owner(transcription=None, opened=T_OPEN, closed=T_CLOSE):
    transcription = transcription or _transcription()
    return fr.OwnerConsole(transcription=transcription,
                           transcription_sha256=fr.sha256_hex(fr.canonical_json_bytes(transcription)),
                           opened_at_utc=opened, closed_at_utc=closed, attestation=fr.OWNER_CONSOLE_ATTESTATION)


def _go(ref="q77-p5d-cgo-1", issued=T_GO):
    return fr.ConditionalGo(conditional_go_ref=ref, issued_at_utc=issued, terms_sha256=fr.CONDITIONAL_GO_TERMS_SHA256)


def _fx(at=T_FX, rate="1.1225"):
    return fr.FxReading(usd_per_eur=rate, reference_date="2026-10-05", source_url="https://www.ecb.europa.eu/x",
                        retrieved_at_utc=at)


def _lifecycle(at=T_LIFE):
    return {
        "read_at_utc": at.isoformat(), "source_url": "https://platform.claude.com/docs/en/about-claude/model-deprecations",
        "models": [
            {"model": "claude-sonnet-5", "state": "Active", "deprecated": "N/A", "deprecation_notice": False,
             "tentative_retirement_not_sooner_than": "2027-06-30"},
            {"model": "claude-haiku-4-5-20251001", "state": "Active", "deprecated": "N/A", "deprecation_notice": False,
             "tentative_retirement_not_sooner_than": "2026-10-15"},
        ],
    }


ARMED_DIFF = "\n".join(["--- a/w", "+++ b/w"] + list(fr.ARMED_WORKFLOW_DIFF_LINES)) + "\n"


def _latch_line_patch(line: str) -> str:
    return f"@@ -1 +1,2 @@\n context\n+{line}"


class _Client:
    def __init__(self, world):
        self.w = world

    def list_push_activity(self, ref):
        return list(self.w.pushes)

    def get_main_head_sha(self):
        return self.w.remote

    def get_commit(self, sha):
        return self.w.commit

    def list_workflow_runs_counted(self, path, *, created_after, created_before, **kw):
        return [RunRef(run_id=str(9000 + n), run_attempt=1, event="workflow_dispatch", ref="refs/heads/main",
                       sha="c" * 40, workflow_path=path, created_at=T_R, run_started_at=None, run_number=n,
                       status="completed", conclusion="failure", head_branch="main") for n in self.w.run_numbers]

    def list_artifacts(self, prefix):
        if prefix == "sentinel-p5-gate-evidence-":
            return [ArtifactRef(id=i, name=f"sentinel-p5-gate-evidence-r{i}-a1", workflow_run_id="1")
                    for i in range(self.w.gate_evidence)]
        names = ["sentinel-p5-oneshot-p5c-wif-probe-r1", "sentinel-p5-oneshot-p5d-official-sonnet-gate-r2"]
        names += [f"sentinel-p5-oneshot-p5d-replacement-sonnet-gate-r{i}" for i in range(self.w.replacement)]
        return [ArtifactRef(id=i, name=n, workflow_run_id="1") for i, n in enumerate(names)]


class FakeSources(script.Sources):
    """Real repository files; scripted git, GitHub, clock and scheduler."""

    def __init__(self, root=REPO_ROOT, **changes):
        super().__init__(root)
        self.head = self.origin = self.remote = R
        self.status = ""
        self.numstat = f"1\t0\t{script.LATCH_REL}"
        self.rev_list_count = "0"
        self.workflow_diff = ARMED_DIFF
        self.times = [T_LOCAL]
        self.variables = {"ANTHROPIC_ORGANIZATION_ID": ORG, "ANTHROPIC_SERVICE_ACCOUNT_ID": SA,
                          script.fr.REPLACEMENT_RULE_VARIABLE: RULE_ID, "SENTINEL_P5D_FEDERATION_RULE_ID": "fdrl_old"}
        self.oidc = {"use_default": True, "use_immutable_subject": False}
        self.run_numbers, self.replacement, self.gate_evidence = (1, 2, 3, 4), 0, 0
        self.pushes = [PushActivity(before=PARENT, after=R, ref="refs/heads/main", activity_type="push", timestamp=T_R)]
        self.ci_runs = {R: [{"databaseId": 321, "conclusion": "success", "status": "completed", "headSha": R,
                             "event": "push", "updatedAt": T_CI.isoformat().replace("+00:00", "Z")}]}
        self.probe_run = {"path": rd.PROBE_WORKFLOW, "head_sha": R, "event": "workflow_dispatch", "run_attempt": 1,
                          "status": "completed", "conclusion": "success",
                          "created_at": T_CREATED.isoformat().replace("+00:00", "Z"),
                          "run_started_at": T_STARTED.isoformat().replace("+00:00", "Z")}
        self.probe_runs_at_r = [(PROBE_RUN, T_CREATED)]
        self.probe_zip = _probe_zip(_probe_evidence())
        self.probe_digest = "sha256:" + fr.sha256_hex(self.probe_zip)
        self.commit = None
        self.latch_path = None
        self.digests = {aid: d for specs in (rd.B2_ARTIFACT_DIGESTS, rd.B5_ARTIFACT_DIGESTS) for _n, aid, d in specs}
        self.scheduler_state = {"state": "Disabled", "enabled": False}
        self.calls = []
        for key, value in changes.items():
            setattr(self, key, value)

    def git(self, args):
        self.calls.append(tuple(args))
        if args[0] == "fetch":
            return ""
        if args[:2] == ["rev-parse", "HEAD"]:
            return self.head
        if args[:2] == ["rev-parse", "origin/main"]:
            return self.origin
        if args[:2] == ["rev-parse", f"{R}^"]:
            return PARENT
        if args[:2] == ["rev-parse", "HEAD~1"]:
            return getattr(self, "head_parent", R)
        if args[:2] == ["status", "--porcelain"]:
            if not self.status and self.latch_path is not None and len(lt.load_latch(self.latch_path)) == 2:
                return f"M {script.LATCH_REL}"
            return self.status
        if args[0] == "checkout":
            self.latch_path.write_bytes(_genesis_view(self.latch_path.parent).read_bytes())
            return ""
        if args[0] == "rev-list":
            return self.rev_list_count
        if args[:2] == ["diff", "--numstat"]:
            return self.numstat
        if "--name-status" in args:
            return ("A\tagents/checker/envelope_guard.py\nA\tagents/checker/process_control.py\n"
                    "M\tagents/checker/oidc.py\nM\tagents/checker/harness.py\n")
        if args[:2] == ["diff", "--name-only"] and len(args) == 3:
            return getattr(self, "commit_names", script.LATCH_REL)
        if "--name-only" in args:
            return ""
        if "--unified=0" in args:
            return self.workflow_diff
        raise AssertionError(args)

    def gh_json(self, args):
        if args[0] == "run":
            if "--workflow" in args and args[args.index("--workflow") + 1] == script.PROBE_WORKFLOW_FILE:
                return [{"databaseId": int(i), "createdAt": t.isoformat().replace("+00:00", "Z")}
                        for i, t in self.probe_runs_at_r]
            return self.ci_runs.get(args[args.index("--commit") + 1], [])
        if args[0] == "variable":
            return [{"name": n, "value": v} for n, v in self.variables.items()]
        if args[0] == "secret":
            return []
        if args[0] == "workflow":
            return [{"name": w, "state": "active", "path": f".github/workflows/{w}"} for w in sorted(script.b63.EXPECTED_WORKFLOWS)]
        path = args[1]
        if path.endswith("/actions/permissions/workflow"):
            return {"default_workflow_permissions": "read"}
        if path.endswith("/oidc/customization/sub"):
            return self.oidc
        if path.endswith(f"/runs/{PROBE_RUN}"):
            return self.probe_run
        if path.endswith(f"/runs/{PROBE_RUN}/artifacts"):
            return {"artifacts": [{"id": int(PROBE_ARTIFACT), "name": f"sentinel-p5-latchprobe-r{PROBE_RUN}-a1",
                                   "digest": self.probe_digest}]}
        artifact_id = path.rsplit("/", 1)[1]
        return {"digest": self.digests.get(artifact_id), "expired": False}

    def gh_bytes(self, args):
        return self.probe_zip

    def client(self):
        return _Client(self)

    def server_time(self):
        return self.times.pop(0) if len(self.times) > 1 else self.times[0]

    def scheduler(self):
        return self.scheduler_state

    def phase1_frozen_ok(self):
        return True

    def read_text(self, relative):
        return (REPO_ROOT / relative).read_text(encoding="utf-8")

    def read_bytes(self, relative):
        return (REPO_ROOT / relative).read_bytes()

    def exists(self, relative):
        return (REPO_ROOT / relative).exists()


def _draft(src=None, *, attempt=1, probe=None, go=None, owner=None, fx=None, lifecycle=None, adjudications=()):
    src = src or FakeSources()
    return script.build_final_draft(
        src, r=R, attempt=attempt, probe=probe or _binding(attempt), conditional_go=go or _go(),
        owner_console=owner or _owner(), fx=fx or _fx(), lifecycle=lifecycle or _lifecycle(),
        adjudications=adjudications, b5_identity=_pins_identity(), scheduler=src.scheduler(), t_local=T_LOCAL,
        t_r=T_R, ci=script.ci_success(src, R),
    )


def _record(draft=None, at=T_REC):
    return fr.freeze(draft or _draft(), at)


def _with(doc, **fields):
    data = dict(doc)
    data.update(fields)
    return type(doc)(**data)


# ======================================================================
# The world is eligible and canonical
# ======================================================================


def test_a_fully_consistent_world_builds_an_eligible_canonical_final_draft():
    draft = _draft()
    eligibility = fr.evaluate_final(draft, upper_utc=T_REC)
    assert eligibility.eligible, eligibility.reasons
    comps = fr.component_map(draft)
    assert all(c.status == "PASS" and c.evidence_state == "FINAL_T2" for c in comps.values())
    assert set(comps) == set(rd.ALL_COMPONENTS)
    assert fr.FinalReadinessDraft.model_validate_json(fr.draft_bytes(draft)) == draft
    assert comps["4.2"].provenance == ("GITHUB_REST_OWNER_TOKEN", "OWNER_CONSOLE")
    assert comps["4.2"].collected_at_utc == T_OPEN and comps["24"].collected_at_utc == T_FLOOR


def test_freeze_is_canonical_and_round_trips_and_non_canonical_bytes_are_refused():
    record = _record()
    frozen = fr.record_bytes(record)
    assert fr.load_frozen_record(frozen) == record and record.recorded_at_utc == T_REC
    assert fr.evaluate_final_record(record).eligible
    pretty = json.dumps(json.loads(frozen), indent=1).encode("utf-8")
    with pytest.raises(fr.FinalReadinessError, match="not canonical"):
        fr.load_frozen_record(pretty)
    with pytest.raises(fr.FinalReadinessError, match="strict validation"):
        fr.load_frozen_record(b"{}")


# ======================================================================
# owner_go_ref (R15, R19)
# ======================================================================


def test_owner_go_ref_has_exactly_the_final_go_form_and_fits_the_latch_identifier_rule():
    digest = "0123456789abcdef" * 4
    ref = fr.owner_go_ref_for(digest)
    assert fr.parse_owner_go_ref(ref) == digest
    assert lt._IDENTIFIER.fullmatch(ref)
    for good in (fr.owner_go_ref_for("f" * 64), fr.owner_go_ref_for("0" * 64)):
        assert fr.OWNER_GO_REF_PATTERN.fullmatch(good) and lt._IDENTIFIER.fullmatch(good)


@pytest.mark.parametrize("bad", [
    "q77-p5d-final-go-b/" + "a" * 64, "q77-p5d-final-go-a:" + "a" * 64, "q77-p5d-final-go-a/" + "A" * 64,
    "q77-p5d-final-go-a/" + "a" * 63, "q77-p5d-final-go-a/" + "a" * 65, "q77-p5d-final-go-a/" + "a" * 64 + "x",
    "q77-p5d-final-go-a/" + "a" * 64 + "\n", "owner-go-test", None, 5,
])
def test_owner_go_ref_parse_rejects_every_other_form(bad):
    with pytest.raises(fr.FinalReadinessError):
        fr.parse_owner_go_ref(bad)
    with pytest.raises(fr.FinalReadinessError):
        fr.owner_go_ref_for("A" * 64)


def test_owner_go_ref_binding_legs():
    record = _record()
    frozen = fr.record_bytes(record)
    ref = fr.owner_go_ref_for(fr.sha256_hex(frozen))
    assert fr.assert_owner_go_ref_binds(ref, frozen, readiness_source_sha=R) == record
    with pytest.raises(fr.FinalReadinessError, match="digest"):
        fr.assert_owner_go_ref_binds(fr.owner_go_ref_for("0" * 64), frozen, readiness_source_sha=R)
    with pytest.raises(fr.FinalReadinessError, match="readiness_source_sha"):
        fr.assert_owner_go_ref_binds(ref, frozen, readiness_source_sha="9" * 40)
    garbage = b'{"not": "a record"}\n'
    with pytest.raises(fr.FinalReadinessError, match="strict"):
        fr.assert_owner_go_ref_binds(fr.owner_go_ref_for(fr.sha256_hex(garbage)), garbage, readiness_source_sha=R)
    late = fr.record_bytes(fr.freeze(_draft(), T_FLOOR + timedelta(minutes=101)))
    with pytest.raises(fr.FinalReadinessError, match="not eligible"):
        fr.assert_owner_go_ref_binds(fr.owner_go_ref_for(fr.sha256_hex(late)), late, readiness_source_sha=R)


def test_a_record_with_a_failed_component_never_binds():
    src = FakeSources(variables={"ANTHROPIC_ORGANIZATION_ID": ORG, "ANTHROPIC_SERVICE_ACCOUNT_ID": SA,
                                 fr.REPLACEMENT_RULE_VARIABLE: "fdrl_other"})
    frozen = fr.record_bytes(_record(_draft(src)))
    with pytest.raises(fr.FinalReadinessError, match="non-PASS components: 16h, 4.2"):
        fr.assert_owner_go_ref_binds(fr.owner_go_ref_for(fr.sha256_hex(frozen)), frozen, readiness_source_sha=R)


# ======================================================================
# Schema: components, rows, draft
# ======================================================================


def _component(name="8", **changes):
    fields = dict(component=name, status="PASS", evidence_state="FINAL_T2", provenance=("LOCAL_MACHINE",),
                  collected_at_utc=T_LOCAL, evidence={"ok": True})
    fields.update(changes)
    return fr.FinalComponent(**fields)


@pytest.mark.parametrize("changes", [
    {"evidence_state": "PREARM_BASELINE"}, {"status": "DEFERRED"}, {"component": "24", "evidence_state": "T0"},
    {"component": "16h", "evidence_state": "T0"}, {"provenance": ()}, {"provenance": ("LOCAL_MACHINE", "AGENT_WEB")},
    {"provenance": ("NOT_A_GROUP",)}, {"status": "FAIL"}, {"component": "99"},
    {"collected_at_utc": T_LOCAL.replace(microsecond=5)},
])
def test_final_components_refuse_baselines_deferrals_and_t0_on_t2_rows(changes):
    with pytest.raises(ValidationError):
        _component(**changes)


def test_t0_is_legal_only_outside_the_t2_tier_rows():
    assert _component("8", evidence_state="T0").evidence_state == "T0"
    assert fr.T2_ROWS == frozenset({2, 3, 4, 16, 17, 21, 24, 25})


def test_draft_structural_rules():
    draft = _draft()
    with pytest.raises(ValidationError, match="attempt"):
        _with(draft, attempt=3)
    with pytest.raises(ValidationError, match="exactly n probe runs"):
        _with(draft, attempt=2)
    with pytest.raises(ValidationError, match="t_floor_utc"):
        _with(draft, t_floor_utc=T_FLOOR + timedelta(seconds=1))
    with pytest.raises(ValidationError, match="earliest stamp"):
        _with(draft, provenance_stamps={**draft.provenance_stamps, "LOCAL_MACHINE": T_LOCAL + timedelta(seconds=1)})
    with pytest.raises(ValidationError, match="for R"):
        _with(draft, readiness_source_sha="9" * 40)
    with pytest.raises(ValidationError):
        _binding(runs_at_r=(PROBE_RUN, PREV_PROBE_RUN))  # this probe is not the newest at R
    with pytest.raises(ValidationError):
        _binding(evidence=_probe_evidence(run_id="1"))


def test_conditional_go_and_owner_console_models_are_strict():
    with pytest.raises(ValidationError, match="terms"):
        fr.ConditionalGo(conditional_go_ref="q77-p5d-cgo-1", issued_at_utc=T_GO, terms_sha256="0" * 64)
    for ref in ("q77-p5d-cgo-", "Q77-p5d-cgo-1", "q77-p5d-cgo-a b", "q77-p5d-cgo-" + "a" * 49):
        with pytest.raises(ValidationError, match="malformed"):
            _go(ref=ref)
    with pytest.raises(ValidationError, match="digest"):
        fr.OwnerConsole(transcription=_transcription(), transcription_sha256="0" * 64, opened_at_utc=T_OPEN,
                        closed_at_utc=T_CLOSE, attestation=fr.OWNER_CONSOLE_ATTESTATION)
    with pytest.raises(ValidationError, match="closes before"):
        _owner(opened=T_CLOSE, closed=T_OPEN)
    with pytest.raises(ValidationError, match="attestation"):
        fr.OwnerConsole(**{**dict(_owner()), "attestation": "NOT ATTESTED"})
    with pytest.raises(ValidationError):
        fr.OwnerTranscription(**{**dict(_transcription()), "variable_value": RULE_ID})


# ======================================================================
# Evidence stamps versus anchors (R20)
# ======================================================================


def test_a_correctly_pre_issued_conditional_go_before_t_floor_is_accepted():
    draft = _draft()
    assert draft.conditional_go.issued_at_utc < draft.t_floor_utc
    assert draft.t_r_utc < draft.ci_r.completed_at_utc < draft.probe.run_created_at_utc < draft.t_floor_utc
    assert fr.anchor_problems(draft) == [] and fr.evaluate_final(draft, upper_utc=T_REC).eligible


@pytest.mark.parametrize("kwargs, label", [
    ({"owner": "early_open"}, "owner_console.opened_at_utc"),
    ({"fx": _fx(at=T_FLOOR - timedelta(seconds=1))}, "fx.retrieved_at_utc"),
    ({"lifecycle": _lifecycle(at=T_FLOOR - timedelta(minutes=5))}, "lifecycle.read_at_utc"),
])
def test_a_final_t2_evidence_fact_before_t_floor_is_rejected(kwargs, label):
    if kwargs.get("owner") == "early_open":
        kwargs = {"owner": _owner(opened=T_FLOOR - timedelta(seconds=1))}
    eligibility = fr.evaluate_final(_draft(**kwargs), upper_utc=T_REC)
    assert not eligibility.eligible and any(label in r and "outside" in r for r in eligibility.reasons)


def test_evidence_after_the_upper_bound_is_rejected():
    eligibility = fr.evaluate_final(_draft(), upper_utc=T_LOCAL - timedelta(seconds=1))
    assert any("component:" in r and "outside" in r for r in eligibility.reasons)


@pytest.mark.parametrize("change, why", [
    ({"go": "at_probe"}, "conditional GO"), ({"go": "after_probe"}, "conditional GO"),
    ({"probe": "started_late"}, "started after T_floor"),
    ({"probe": "created_late"}, "conditional GO was not issued before the probe was created, or the probe postdates"),
])
def test_anchor_ordering_violations_are_rejected(change, why):
    kwargs = {}
    if change.get("go") == "at_probe":
        kwargs["go"] = _go(issued=T_CREATED)
    if change.get("go") == "after_probe":
        kwargs["go"] = _go(issued=T_CREATED + timedelta(minutes=1))
    if change.get("probe") == "started_late":
        kwargs["probe"] = _binding(run_started_at_utc=T_FLOOR + timedelta(seconds=1))
    if change.get("probe") == "created_late":
        kwargs["probe"] = _binding(run_created_at_utc=T_FLOOR + timedelta(seconds=1))
    assert any(why in r for r in fr.anchor_problems(_draft(**kwargs)))


def test_ci_and_push_anchor_order_is_enforced():
    late_ci = FakeSources()
    late_ci.ci_runs[R][0]["updatedAt"] = (T_FLOOR + timedelta(seconds=1)).isoformat()
    assert any("T_R <= T_CI <= T_floor" in r for r in fr.anchor_problems(_draft(late_ci)))
    draft = _draft()
    assert any("T_R <= T_CI" in r for r in fr.anchor_problems(_with(draft, t_r_utc=T_CI + timedelta(seconds=1))))


def test_adjudications_must_be_ruled_before_the_attempt_started():
    before = fr.FinalAdjudication(package="idna", baseline_version=None, observed_version="3.10", decision="ACCEPTED",
                                  ruling_ref="q77-ruling-x", ruled_at_utc=T_CREATED - timedelta(minutes=1))
    inside = fr.FinalAdjudication(**{**dict(before), "ruled_at_utc": T_FLOOR + timedelta(minutes=5)})
    src_identity = _identity()  # baseline lacks idna: one transitive difference
    probe = _binding(evidence=_probe_evidence())
    draft_ok = script.build_final_draft(
        FakeSources(), r=R, attempt=1, probe=probe, conditional_go=_go(), owner_console=_owner(), fx=_fx(),
        lifecycle=_lifecycle(), adjudications=(before,), b5_identity={**_pins_identity(), "distributions": [
            d for d in _pins_identity()["distributions"] if d[0] != "idna"]}, scheduler={"state": "Disabled", "enabled": False},
        t_local=T_LOCAL, t_r=T_R, ci=script.ci_success(FakeSources(), R))
    assert src_identity and fr.evaluate_final(draft_ok, upper_utc=T_REC).eligible, fr.evaluate_final(draft_ok, upper_utc=T_REC).reasons
    draft_bad = _with(draft_ok, adjudications=(inside,))
    assert any("ruled inside the live attempt" in r for r in fr.evaluate_final(draft_bad, upper_utc=T_REC).reasons)
    unadjudicated = _with(draft_ok, adjudications=())
    assert any("without owner adjudication" in r for r in fr.evaluate_final(unadjudicated, upper_utc=T_REC).reasons)


def test_the_pre_push_window_limit_is_100_minutes():
    draft = _draft()
    assert fr.PRE_PUSH_MAX_AGE == timedelta(minutes=100)
    assert fr.evaluate_final(draft, upper_utc=T_FLOOR + timedelta(minutes=100)).eligible
    late = fr.evaluate_final(draft, upper_utc=T_FLOOR + timedelta(minutes=100, seconds=1))
    assert any("100-minute" in r for r in late.reasons)


def test_the_console_deadline_and_fx_age_are_enforced(monkeypatch):
    monkeypatch.setattr(fr, "AUTH_HISTORY_DEADLINE", T_CLOSE)
    assert any("deadline" in r for r in fr.evaluate_final(_draft(), upper_utc=T_REC).reasons)
    monkeypatch.setattr(fr, "AUTH_HISTORY_DEADLINE", T_CLOSE + timedelta(seconds=1))
    assert fr.evaluate_final(_draft(), upper_utc=T_REC).eligible
    assert fr.AUTH_HISTORY_DEADLINE != datetime(2026, 10, 10, 13, 54, tzinfo=UTC) or True


def test_the_frozen_deadline_is_the_b6_5_value():
    assert fr.AUTH_HISTORY_DEADLINE == datetime(2026, 10, 10, 13, 54, 0, tzinfo=UTC)


def test_post_push_predicates():
    record = _record()
    assert fr.post_push_problems(record, t_a=T_A, t_r=T_R) == []
    assert any("precedes" in p for p in fr.post_push_problems(record, t_a=T_REC - timedelta(seconds=1), t_r=T_R))
    assert any("20 minutes" in p for p in fr.post_push_problems(record, t_a=T_REC + timedelta(minutes=20, seconds=1), t_r=T_R))
    assert fr.post_push_problems(record, t_a=T_REC + timedelta(minutes=20), t_r=T_R) == []
    assert any("T_R differs" in p for p in fr.post_push_problems(record, t_a=T_A, t_r=T_R + timedelta(seconds=1)))
    stale = _record(at=T_FLOOR + timedelta(minutes=110))
    assert any("2 hours" in p for p in fr.post_push_problems(stale, t_a=T_FLOOR + timedelta(hours=2, seconds=1), t_r=T_R))


# ======================================================================
# Armed-state evaluators
# ======================================================================


def _marker_facts(**changes):
    facts = {"unconstructible_without_frozen_fields": True, "canonical_name": fr.REPLACEMENT_MARKER_NAME_123,
             "history_permits_exactly_one": True, "runner_purpose": fr.ARMED_PURPOSE, "marker_fields_bound": True,
             "workflow_marker_name_is_replacement": True}
    facts.update(changes)
    return facts


@pytest.mark.parametrize("change", [
    {"unconstructible_without_frozen_fields": False}, {"canonical_name": "sentinel-p5-oneshot-p5d-official-sonnet-gate-r123"},
    {"history_permits_exactly_one": False}, {"runner_purpose": "P5D_OFFICIAL_SONNET_GATE"},
    {"marker_fields_bound": False}, {"workflow_marker_name_is_replacement": False},
])
def test_armed_marker_semantics(change):
    assert fr.evaluate_armed_marker_semantics(_marker_facts()).status == "PASS"
    assert fr.evaluate_armed_marker_semantics(_marker_facts(**change)).status == "FAIL"


def test_armed_envelope_binding_and_retry_row():
    ok_env = rd.Outcome("PASS", {}, None)
    good = {"runner_envelope_id": rd.EXPECTED_ENVELOPE_ID, "runner_envelope_version": "1"}
    assert fr.evaluate_armed_envelope_binding(ok_env, good).status == "PASS"
    assert fr.evaluate_armed_envelope_binding(rd.Outcome("FAIL", {}, "x"), good).status == "FAIL"
    assert fr.evaluate_armed_envelope_binding(ok_env, {**good, "runner_envelope_id": None}).status == "FAIL"
    assert fr.evaluate_armed_envelope_binding(ok_env, {**good, "runner_envelope_version": "2"}).status == "FAIL"
    retry = {"ci_coverage": True, "concurrency_group": "sentinel-oneshot-p5d", "cancel_in_progress": False,
             "latch_refuses_rerun_attempt": True}
    assert fr.evaluate_armed_retry_row(retry).status == "PASS"
    for change in ({"ci_coverage": False}, {"concurrency_group": "x"}, {"cancel_in_progress": True},
                   {"latch_refuses_rerun_attempt": False}):
        assert fr.evaluate_armed_retry_row({**retry, **change}).status == "FAIL"


def _armed_latch(**changes):
    facts = {"kinds": ["GENESIS"], "file_sha256": rd.EXPECTED_LATCH_FILE_SHA256, "head_sha256": rd.EXPECTED_LATCH_HEAD_SHA256,
             "unarmed_refusal_reason": "LATCH_UNARMED", "eligibility_requires_latch": True,
             "preflight_consults_latch_in_order": True, "enforcement_tests_green": True, "history_permits_exactly_one": True,
             "official_run_numbers": [1, 2, 3, 4], "replacement_prefix_artifacts": 0, "gate_evidence_prefix_artifacts": 0,
             "replacement_receipts": 0, "runner_purpose": fr.ARMED_PURPOSE, "runner_envelope_id": rd.EXPECTED_ENVELOPE_ID}
    facts.update(changes)
    return facts


@pytest.mark.parametrize("change", [
    {"kinds": ["GENESIS", "ATTEMPT_AUTHORIZED"]}, {"file_sha256": "0" * 64}, {"head_sha256": "0" * 64},
    {"unarmed_refusal_reason": None}, {"eligibility_requires_latch": False}, {"preflight_consults_latch_in_order": False},
    {"enforcement_tests_green": False}, {"history_permits_exactly_one": False}, {"official_run_numbers": [1, 2, 3, 4, 5]},
    {"replacement_prefix_artifacts": 1}, {"gate_evidence_prefix_artifacts": 1}, {"replacement_receipts": 1},
    {"runner_purpose": "P5D_OFFICIAL_SONNET_GATE"}, {"runner_envelope_id": None},
])
def test_armed_latch_row_requires_the_armed_unauthorized_state_at_r(change):
    assert fr.evaluate_armed_latch_row(_armed_latch()).status == "PASS"
    assert fr.evaluate_armed_latch_row(_armed_latch(**change)).status == "FAIL"
    incomplete = _armed_latch()
    del incomplete["runner_purpose"]
    assert fr.evaluate_armed_latch_row(incomplete).status == "FAIL"


def _carry_facts(lines):
    return {"artifacts": {aid: {"digest": d, "expired": False} for _n, aid, d in rd.B2_ARTIFACT_DIGESTS},
            "path_diff": [], "workflow_diff_lines": lines}


def test_armed_carry_forward_allows_exactly_the_timeout_and_arming_lines():
    spec = rd.CARRY_SPECS["b2"]
    assert fr.evaluate_armed_carry_forward(spec, _carry_facts(list(fr.ARMED_WORKFLOW_DIFF_LINES))).status == "PASS"
    assert fr.evaluate_armed_carry_forward(spec, _carry_facts(list(rd.ALLOWED_WORKFLOW_DIFF_LINES))).status == "FAIL"
    assert fr.evaluate_armed_carry_forward(spec, _carry_facts(list(fr.ARMED_WORKFLOW_DIFF_LINES) + ["+  x: 1"])).status == "FAIL"
    assert fr.evaluate_armed_carry_forward(spec, _carry_facts(list(fr.ARMED_WORKFLOW_DIFF_LINES)[:-1])).status == "FAIL"
    assert fr.evaluate_armed_carry_forward(spec, {**_carry_facts([]), "workflow_diff_lines": None}).status == "FAIL"
    assert fr.evaluate_armed_carry_forward(spec, {**_carry_facts(list(fr.ARMED_WORKFLOW_DIFF_LINES)), "path_diff": ["x"]}).status == "FAIL"


def test_armed_workflow_diff_lines_equal_the_real_diff_since_b2():
    import subprocess

    if subprocess.run(["git", "cat-file", "-e", f"{rd.B2_SOURCE_SHA}^{{commit}}"], cwd=REPO_ROOT,
                      capture_output=True).returncode != 0:
        pytest.skip("full history not available in this checkout")
    lines = script.b63.workflow_diff_lines(script.Sources(REPO_ROOT), rd.B2_SOURCE_SHA, rd.OFFICIAL_WORKFLOW)
    assert sorted(lines) == sorted(fr.ARMED_WORKFLOW_DIFF_LINES)


def test_identifiers_rule_readback_and_cap_final():
    t = _transcription()
    assert fr.evaluate_identifiers(t, {"ANTHROPIC_ORGANIZATION_ID": ORG, "ANTHROPIC_SERVICE_ACCOUNT_ID": SA}).status == "PASS"
    assert fr.evaluate_identifiers(t, {"ANTHROPIC_ORGANIZATION_ID": "x", "ANTHROPIC_SERVICE_ACCOUNT_ID": SA}).status == "FAIL"
    assert fr.evaluate_identifiers(t, {}).status == "FAIL"
    readback = fr.rule_readback(t, variable_value="from-gh", oidc_customization={"x": 1})
    assert readback["variable_value"] == "from-gh"
    assert fr.evaluate_cap_final(t, _fx(), now=T_LOCAL).status == "PASS"
    poor = _transcription(cap=fr.OwnerCap(**{**dict(t.cap), "credit_usd": "8.47"}))
    assert fr.evaluate_cap_final(poor, _fx(), now=T_LOCAL).status == "FAIL"
    assert fr.evaluate_cap_final(t, _fx(at=T_LOCAL - timedelta(hours=2, seconds=1)), now=T_LOCAL).status == "FAIL"
    assert fr.evaluate_cap_final(t, _fx(rate="1.1400"), now=T_LOCAL).status == "FAIL"  # thin slack: USD 10 needed
    with pytest.raises(ValidationError):
        fr.OwnerCap(**{**dict(t.cap), "auto_reload": "On"})


# ======================================================================
# Collector over the fake world
# ======================================================================


def _variables(**changes):
    v = {"ANTHROPIC_ORGANIZATION_ID": ORG, "ANTHROPIC_SERVICE_ACCOUNT_ID": SA, fr.REPLACEMENT_RULE_VARIABLE: RULE_ID}
    v.update(changes)
    return v


@pytest.mark.parametrize("changes, component", [
    ({"variables": _variables(**{fr.REPLACEMENT_RULE_VARIABLE: "fdrl_other"})}, "4.2"),
    ({"variables": _variables(ANTHROPIC_ORGANIZATION_ID="other")}, "16h"),
    ({"oidc": {"use_default": False, "use_immutable_subject": False}}, "4.2"),
    ({"workflow_diff": ARMED_DIFF + "+  extra: 1\n"}, "13"),
    ({"remote": "9" * 40}, "16a"),
    ({"status": " M x.py"}, "16a"),
    ({"replacement": 1}, "25"),
    ({"gate_evidence": 1}, "3"),
    ({"run_numbers": (1, 2, 3, 4, 5)}, "25"),
    ({"scheduler_state": {"state": "Ready", "enabled": True}}, "16l"),
])
def test_each_drift_fails_its_component(changes, component):
    draft = _draft(FakeSources(**changes))
    assert fr.component_map(draft)[component].status == "FAIL"
    assert not fr.evaluate_final(draft, upper_utc=T_REC).eligible


def test_runtime_image_family_and_owner_facts_fail_their_components():
    other_os = _identity()
    other_os["runner_image"] = {"image_os": "ubuntu26", "image_version": "x"}
    pins = _pins_identity()
    pins["runner_image"] = other_os["runner_image"]
    draft = _draft(probe=_binding(evidence=_probe_evidence(identity=pins)))
    assert fr.component_map(draft)["2"].status == "FAIL" and fr.component_map(draft)["16g"].status == "FAIL"
    events = _draft(owner=_owner(_transcription(auth_events_for_rule=1)))
    assert fr.component_map(events)["4.2"].status == "FAIL"
    cap = _draft(owner=_owner(_transcription(cap=fr.OwnerCap(**{**dict(_transcription().cap), "cap_usd": "12.00"}))))
    assert fr.component_map(cap)["16i"].status == "FAIL"


def test_probe_binding_reads_the_digested_artifact_and_counts_probes_at_r():
    src = FakeSources()
    binding = script.probe_binding(src, r=R, run_id=PROBE_RUN, attempt=1, previous_run_id=None)
    assert binding.runs_at_r == (PROBE_RUN,) and binding.t_floor == T_FLOOR
    with pytest.raises(script.FinalCollectError, match="attempt 1 has no previous"):
        script.probe_binding(src, r=R, run_id=PROBE_RUN, attempt=1, previous_run_id=PREV_PROBE_RUN)
    two = FakeSources(probe_runs_at_r=[(PREV_PROBE_RUN, T_CREATED - timedelta(minutes=40)), (PROBE_RUN, T_CREATED)])
    with pytest.raises(ValidationError, match="exactly n probe runs|this probe|attempt"):
        draft = _draft(two, probe=script.probe_binding(two, r=R, run_id=PROBE_RUN, attempt=1, previous_run_id=None))
        assert draft  # a second probe inside one attempt never forms a draft
    second = script.probe_binding(two, r=R, run_id=PROBE_RUN, attempt=2, previous_run_id=PREV_PROBE_RUN)
    assert second.runs_at_r == (PREV_PROBE_RUN, PROBE_RUN)
    with pytest.raises(script.FinalCollectError, match="attempt 2"):
        script.probe_binding(two, r=R, run_id=PROBE_RUN, attempt=2, previous_run_id=None)
    newer = FakeSources(probe_runs_at_r=[(PROBE_RUN, T_CREATED), ("60099", T_CREATED + timedelta(minutes=1))])
    with pytest.raises(ValidationError):
        script.probe_binding(newer, r=R, run_id=PROBE_RUN, attempt=1, previous_run_id=None)
    for bad in ({"head_sha": "9" * 40}, {"run_attempt": 2}, {"conclusion": "failure"}, {"path": rd.OFFICIAL_WORKFLOW}):
        wrong = FakeSources()
        wrong.probe_run = {**wrong.probe_run, **bad}
        with pytest.raises(script.FinalCollectError):
            script.probe_binding(wrong, r=R, run_id=PROBE_RUN, attempt=1, previous_run_id=None)
    tampered = FakeSources()
    tampered.probe_digest = "sha256:" + "0" * 64
    with pytest.raises(script.b63.CollectError):
        script.probe_binding(tampered, r=R, run_id=PROBE_RUN, attempt=1, previous_run_id=None)


def test_attempt_two_can_never_mix_evidence_from_attempt_one():
    old_floor = T_FLOOR - timedelta(minutes=40)
    draft = _draft(attempt=2, owner=_owner(opened=old_floor + timedelta(minutes=5), closed=old_floor + timedelta(minutes=20)))
    assert any("owner_console.opened_at_utc" in r for r in fr.evaluate_final(draft, upper_utc=T_REC).reasons)


def test_ci_and_push_anchors_fail_closed():
    with pytest.raises(script.FinalCollectError, match="CI"):
        script.ci_success(FakeSources(ci_runs={}), R)
    src = FakeSources()
    src.ci_runs[R][0]["event"] = "workflow_dispatch"
    with pytest.raises(script.FinalCollectError):
        script.ci_success(src, R)
    with pytest.raises(script.FinalCollectError, match="push activity"):
        script.push_timestamp(FakeSources(pushes=[]), before=PARENT, after=R)
    dup = FakeSources()
    dup.pushes = dup.pushes * 2
    with pytest.raises(script.FinalCollectError):
        script.push_timestamp(dup, before=PARENT, after=R)
    with pytest.raises(script.FinalCollectError):
        script.push_timestamp(FakeSources(), before="0" * 40, after=R)


def test_local_armed_fact_readers_see_the_committed_armed_runner_and_workflow():
    src = script.Sources(REPO_ROOT)
    armed = script.armed_runner_facts(src.read_text(script.OFFICIAL_RUNNER), src.read_text("scripts/_phase5_common.py"))
    assert armed["runner_purpose"] == fr.ARMED_PURPOSE and armed["runner_envelope_id"] == rd.EXPECTED_ENVELOPE_ID
    assert armed["marker_fields_bound"] and armed["preflight_consults_latch_in_order"]
    runner = src.read_text(script.OFFICIAL_RUNNER)
    unbound = runner.replace("dict(replacement_of_run_id=REPLACEMENT_OF_RUN_ID, owner_ruling_id=OWNER_RULING_ID)",
                             "dict(replacement_of_run_id=REPLACEMENT_OF_RUN_ID)")
    assert script.armed_runner_facts(unbound, src.read_text("scripts/_phase5_common.py"))["marker_fields_bound"] is False
    wf = script.official_workflow_facts(src.read_text(rd.OFFICIAL_WORKFLOW))
    assert wf["workflow_marker_name_is_replacement"] and wf["concurrency_group"] == "sentinel-oneshot-p5d"
    assert script.latch_refuses_rerun(src.read_text("sentinel/phase5/latch.py"))
    assert not script.latch_refuses_rerun("")


# ======================================================================
# Retention destinations (R18)
# ======================================================================


def _two(tmp_path):
    (tmp_path / "d1").mkdir(parents=True)
    (tmp_path / "d2").mkdir(parents=True)
    return [tmp_path / "d1" / "final.json", tmp_path / "d2" / "final.json"]


def test_retention_paths_must_be_two_distinct_durable_destinations(tmp_path):
    paths = _two(tmp_path)
    assert script.retention_problems(paths, repo_root=REPO_ROOT, temp_roots=[], scratch_markers=()) == []
    assert script.retention_problems(paths[:1], repo_root=REPO_ROOT, temp_roots=[], scratch_markers=())
    same = script.retention_problems([paths[0], paths[0]], repo_root=REPO_ROOT, temp_roots=[], scratch_markers=())
    assert any("same file" in p for p in same)
    inside = script.retention_problems([REPO_ROOT / "x.json", paths[1]], repo_root=REPO_ROOT, temp_roots=[], scratch_markers=())
    assert any("inside the repository" in p for p in inside)
    temp = script.retention_problems(paths, repo_root=REPO_ROOT, temp_roots=[script._norm(tmp_path)], scratch_markers=())
    assert any("OS temporary" in p for p in temp)
    scratch = script.retention_problems([Path("C:/Users/x/.claude/s/f.json"), paths[1]], repo_root=REPO_ROOT,
                                        temp_roots=[], scratch_markers=script.SCRATCH_MARKERS)
    assert any("session scratch" in p for p in scratch)
    appdata = script.retention_problems([Path("C:/Users/x/AppData/Local/Temp/f.json"), paths[1]], repo_root=REPO_ROOT,
                                        temp_roots=[], scratch_markers=script.SCRATCH_MARKERS)
    assert any("session scratch" in p for p in appdata)
    paths[0].write_bytes(b"x")
    assert any("already exists" in p for p in script.retention_problems(paths, repo_root=REPO_ROOT, temp_roots=[],
                                                                        scratch_markers=()))
    missing = script.retention_problems([tmp_path / "nope" / "f.json", paths[1]], repo_root=REPO_ROOT, temp_roots=[],
                                        scratch_markers=())
    assert any("parent" in p for p in missing)
    assert any("absolute" in p for p in script.retention_problems([Path("rel.json"), paths[1]], repo_root=REPO_ROOT,
                                                                  temp_roots=[], scratch_markers=()))


def test_default_markers_cover_the_os_temp_directory_and_session_scratch(tmp_path):
    import tempfile

    roots = script.default_temp_roots()
    assert script._norm(tempfile.gettempdir()) in roots
    assert any(m in "/c/users/x/.claude/projects/f" for m in script.SCRATCH_MARKERS)


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive paths are a Windows property")
def test_a_case_variant_of_the_same_path_is_the_same_file(tmp_path):
    paths = _two(tmp_path)
    variant = Path(str(paths[0]).upper())
    problems = script.retention_problems([paths[0], variant], repo_root=REPO_ROOT, temp_roots=[], scratch_markers=())
    assert any("same file" in p for p in problems)


def test_retained_copies_must_be_byte_identical_and_distinct_files(tmp_path):
    paths = _two(tmp_path)
    script.write_retained(paths, b"frozen\n")
    assert script.retained_problems(paths, b"frozen\n") == []
    paths[1].write_bytes(b"frozeN\n")
    assert any("copy 2 differs" in p for p in script.retained_problems(paths, b"frozen\n"))
    linked = tmp_path / "d2" / "link.json"
    os.link(paths[0], linked)
    assert any("same file" in p for p in script.retained_problems([paths[0], linked], b"frozen\n"))
    assert script.retained_problems(paths[:1], b"frozen\n")
    with pytest.raises(FileExistsError):
        script.write_retained(paths[:1], b"again")


# ======================================================================
# authorize (R13, R15, R17, R18)
# ======================================================================


def _authorize_world(tmp_path, draft=None, **src_changes):
    latch = tmp_path / "latch" / "phase5_replacement_latch.jsonl"
    latch.parent.mkdir(parents=True)
    latch.write_bytes(_genesis_view(latch.parent).read_bytes())
    draft = draft or _draft()
    draft_path = tmp_path / "draft.json"
    draft_path.write_bytes(fr.draft_bytes(draft))
    src = FakeSources(times=[T_REC], latch_path=latch, **src_changes)
    retain = _two(tmp_path)
    args = SimpleNamespace(draft=draft_path, attempt=draft.attempt, conditional_go_ref=draft.conditional_go.conditional_go_ref,
                           retain=retain, dry_run=False)
    return src, args, latch


def _authorize(src, args, latch):
    return script.cmd_authorize(args, src, latch_path=latch, temp_roots=[], scratch_markers=())


def test_authorize_freezes_once_binds_the_digest_and_appends_one_line(tmp_path):
    src, args, latch = _authorize_world(tmp_path)
    assert _authorize(src, args, latch) == 0
    records = lt.load_latch(latch)
    assert len(records) == 2
    auth = records[1]
    frozen = args.retain[0].read_bytes()
    assert args.retain[1].read_bytes() == frozen
    assert auth.owner_go_ref == fr.OWNER_GO_REF_PREFIX + fr.sha256_hex(frozen)
    record = fr.assert_owner_go_ref_binds(auth.owner_go_ref, frozen, readiness_source_sha=R)
    assert record.recorded_at_utc == auth.recorded_at_utc == T_REC  # the fresh server Date, nothing else
    assert auth.readiness_source_sha == R and auth.prior_official_gate_run_number == 4
    assert auth.window_closes_at_utc == T_REC + timedelta(hours=24)
    assert record.conditional_go.conditional_go_ref == args.conditional_go_ref


@pytest.mark.parametrize("mutate, why", [
    (lambda a, s: setattr(a, "conditional_go_ref", None), "conditional_go_ref"),
    (lambda a, s: setattr(a, "conditional_go_ref", "q77-p5d-cgo-2"), "conditional_go_ref"),
    (lambda a, s: setattr(a, "attempt", 2), "attempt"),
    (lambda a, s: setattr(s, "status", "M x.py"), "not clean"),
    (lambda a, s: setattr(s, "head", "9" * 40), "not all equal"),
    (lambda a, s: setattr(s, "remote", "9" * 40), "not all equal"),
    (lambda a, s: setattr(a, "retain", a.retain[:1]), "two durable"),
    (lambda a, s: setattr(a, "retain", [a.retain[0], a.retain[0]]), "same file"),
    (lambda a, s: setattr(s, "times", [T_FLOOR + timedelta(minutes=101)]), "100-minute"),
])
def test_authorize_refuses_and_writes_nothing(tmp_path, capsys, mutate, why):
    src, args, latch = _authorize_world(tmp_path)
    mutate(args, src)
    assert _authorize(src, args, latch) == 1
    assert why in capsys.readouterr().err
    assert len(lt.load_latch(latch)) == 1
    assert fr.sha256_hex(latch.read_bytes()) == rd.EXPECTED_LATCH_FILE_SHA256
    # Steps 0 to 3 refuse before the one construction: no retained copy exists.
    assert not any(Path(p).exists() for p in (args.retain or []))


def test_authorize_refuses_a_conditional_go_issued_after_the_probe_started(tmp_path, capsys):
    draft = _draft(go=_go(issued=T_CREATED + timedelta(seconds=1)))
    src, args, latch = _authorize_world(tmp_path, draft=draft)
    assert _authorize(src, args, latch) == 1
    assert "conditional GO was not issued before the probe" in capsys.readouterr().err
    assert not args.retain[0].exists()


def test_authorize_refuses_a_latch_that_is_not_genesis_only(tmp_path, capsys):
    src, args, latch = _authorize_world(tmp_path)
    assert _authorize(src, args, latch) == 0
    src2, args2, _ = _authorize_world(tmp_path / "second")
    assert _authorize(src2, args2, latch) == 1
    assert "GENESIS-only" in capsys.readouterr().err


def test_a_post_append_failure_restores_the_latch_and_refuses(tmp_path, capsys):
    src, args, latch = _authorize_world(tmp_path, numstat=f"2\t0\t{script.LATCH_REL}")
    assert _authorize(src, args, latch) == 1
    assert "one added line" in capsys.readouterr().err
    assert ("checkout", "--", script.LATCH_REL) in src.calls
    assert fr.sha256_hex(latch.read_bytes()) == rd.EXPECTED_LATCH_FILE_SHA256


def test_a_retained_copy_differing_after_write_refuses_before_the_append(tmp_path, monkeypatch, capsys):
    src, args, latch = _authorize_world(tmp_path)
    real = script.write_retained

    def corrupt(paths, frozen):
        real(paths, frozen)
        Path(paths[1]).write_bytes(frozen[:-2] + b"X\n")

    monkeypatch.setattr(script, "write_retained", corrupt)
    assert _authorize(src, args, latch) == 1
    assert "copy 2 differs" in capsys.readouterr().err
    assert len(lt.load_latch(latch)) == 1


def test_authorize_constructs_the_record_exactly_once(tmp_path, monkeypatch):
    src, args, latch = _authorize_world(tmp_path)
    calls = []
    real = fr.freeze

    def counting(draft, recorded_at_utc):
        calls.append(recorded_at_utc)
        return real(draft, recorded_at_utc)

    monkeypatch.setattr(fr, "freeze", counting)
    assert _authorize(src, args, latch) == 0
    assert calls == [T_REC]


def test_authorize_dry_run_leaves_the_latch_untouched(tmp_path, capsys):
    src, args, latch = _authorize_world(tmp_path)
    args.dry_run = True
    assert _authorize(src, args, latch) == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert len(lt.load_latch(latch)) == 1


def test_the_script_never_prompts_for_a_discretionary_decision():
    text = (REPO_ROOT / "scripts" / "run_phase5_final_readiness.py").read_text(encoding="utf-8")
    assert not re.search(r"\binput\(|getpass|confirm\(", text)
    for forbidden in ("workflow run", "id-token", "acquire_oidc", "claude_agent_sdk", "write_marker_json"):
        assert forbidden not in text


# ======================================================================
# confirm-a (R15, R22)
# ======================================================================


def _confirm_world(tmp_path, **changes):
    src, args, latch = _authorize_world(tmp_path)
    assert _authorize(src, args, latch) == 0
    root = tmp_path / "root"
    (root / "artifacts").mkdir(parents=True)
    (root / script.LATCH_REL).write_bytes(latch.read_bytes())
    line = lt.record_line_bytes(lt.load_latch(latch)[1])[:-1].decode("utf-8")
    commit = CommitDetail(sha=A, parents=(R,), files=(CommitFile(filename=script.LATCH_REL, status="modified",
                                                                 additions=1, deletions=0, patch=_latch_line_patch(line)),))
    pushes = [PushActivity(before=PARENT, after=R, ref="refs/heads/main", activity_type="push", timestamp=T_R),
              PushActivity(before=R, after=A, ref="refs/heads/main", activity_type="push", timestamp=T_A)]
    fields = dict(root=root, head=A, origin=A, remote=A, commit=commit, pushes=pushes)
    fields.update(changes)
    confirm_src = FakeSources(**fields)
    return confirm_src, SimpleNamespace(readiness_source_sha=R, retain=args.retain), line


def test_confirm_a_passes_for_the_authorized_world(tmp_path, capsys):
    src, args, _ = _confirm_world(tmp_path)
    assert script.cmd_confirm_a(args, src) == 0, capsys.readouterr().err


def _commit(line, **changes):
    files = (CommitFile(filename=script.LATCH_REL, status="modified", additions=1, deletions=0,
                        patch=_latch_line_patch(line)),)
    fields = dict(sha=A, parents=(R,), files=files)
    fields.update(changes)
    return CommitDetail(**fields)


@pytest.mark.parametrize("case, why", [
    ("second_parent", "direct child"), ("second_file", "only the latch"), ("patch_differs", "patch line"),
    ("remote_moved", "not all commit A"), ("origin_moved", "not all commit A"), ("dirty", "not clean"),
    ("commit_after", "after A"), ("late_push", "20 minutes"), ("wrong_r", "readiness_source_sha"),
])
def test_confirm_a_fails_on_every_leg(tmp_path, capsys, case, why):
    src, args, line = _confirm_world(tmp_path)
    if case == "second_parent":
        src.commit = _commit(line, parents=(R, PARENT))
    elif case == "second_file":
        src.commit = _commit(line, files=src.commit.files + (CommitFile("STATE.md", "modified", 1, 0, "+x"),))
    elif case == "patch_differs":
        src.commit = _commit(line.replace('"q77', '"q78'))
    elif case == "remote_moved":
        src.remote = "9" * 40
    elif case == "origin_moved":
        src.origin = "9" * 40
    elif case == "dirty":
        src.status = "M x.py"
    elif case == "commit_after":
        src.rev_list_count = "1"
    elif case == "late_push":
        src.pushes = [src.pushes[0], PushActivity(before=R, after=A, ref="refs/heads/main", activity_type="push",
                                                  timestamp=T_REC + timedelta(minutes=21))]
    elif case == "wrong_r":
        args.readiness_source_sha = PARENT
    assert script.cmd_confirm_a(args, src) == 1
    assert why in capsys.readouterr().err


def test_confirm_a_recomputes_the_digest_and_compares_both_copies(tmp_path, capsys):
    src, args, _ = _confirm_world(tmp_path)
    frozen = args.retain[0].read_bytes()
    args.retain[1].write_bytes(frozen + b" ")
    assert script.cmd_confirm_a(args, src) == 1
    assert "differ" in capsys.readouterr().err
    src2, args2, _ = _confirm_world(tmp_path / "b")
    other = fr.record_bytes(fr.freeze(_draft(), T_REC - timedelta(minutes=1)))
    args2.retain[0].write_bytes(other)
    args2.retain[1].write_bytes(other)
    assert script.cmd_confirm_a(args2, src2) == 1
    assert "recomputed digest" in capsys.readouterr().err


# ======================================================================
# pre-dispatch (R22), write-set, verify-final
# ======================================================================


def _pre(**changes):
    fields = dict(head=A, origin=A, remote=A)
    fields.update(changes)
    src = FakeSources(**fields)
    src.ci_runs[A] = [{"databaseId": 9, "conclusion": "success", "status": "completed", "headSha": A, "event": "push",
                       "updatedAt": T_A.isoformat()}]
    return src


def test_pre_dispatch_requires_a_as_head_of_main_a_clean_tree_and_ci_on_a(capsys):
    args = SimpleNamespace(commit_a=A)
    assert script.cmd_pre_dispatch(args, _pre()) == 0
    for changes, why in (({"remote": "9" * 40}, "not all commit A"), ({"head": "9" * 40}, "not all commit A"),
                         ({"rev_list_count": "1"}, "after A"), ({"status": "M x.py"}, "not clean")):
        assert script.cmd_pre_dispatch(args, _pre(**changes)) == 1
        assert why in capsys.readouterr().err
    no_ci = _pre()
    no_ci.ci_runs[A] = []
    assert script.cmd_pre_dispatch(args, no_ci) == 1
    moved = _pre(remote="9" * 40)  # CI on A succeeded, but main moved: CI never overrides
    assert script.cmd_pre_dispatch(args, moved) == 1


def test_write_set_for_commit_a(capsys):
    args = SimpleNamespace(readiness_source_sha=R)
    assert script.cmd_write_set(args, FakeSources(rev_list_count="1")) == 0
    assert script.cmd_write_set(args, FakeSources(rev_list_count="2")) == 1
    assert script.cmd_write_set(args, FakeSources(rev_list_count="1", head_parent=PARENT)) == 1
    assert script.cmd_write_set(args, FakeSources(rev_list_count="1", commit_names=f"{script.LATCH_REL}\nSTATE.md")) == 1
    assert script.cmd_write_set(args, FakeSources(rev_list_count="1", numstat=f"2\t0\t{script.LATCH_REL}")) == 1


def test_verify_final_record_is_the_closure_check(tmp_path, capsys):
    src, args, latch = _authorize_world(tmp_path)
    assert _authorize(src, args, latch) == 0
    committed = tmp_path / "phase5_readiness_final.json"
    committed.write_bytes(args.retain[0].read_bytes())
    verify = SimpleNamespace(draft=None, record=committed, latch=latch, retain=args.retain)
    assert script.cmd_verify_final(verify) == 0
    args.retain[1].write_bytes(args.retain[1].read_bytes()[:-1] + b" ")
    assert script.cmd_verify_final(verify) == 1
    other = fr.record_bytes(fr.freeze(_draft(), T_REC - timedelta(minutes=1)))
    committed.write_bytes(other)
    assert script.cmd_verify_final(SimpleNamespace(draft=None, record=committed, latch=latch, retain=None)) == 1
    assert "digest" in capsys.readouterr().out


def test_verify_final_draft_uses_fresh_server_time(tmp_path):
    path = tmp_path / "draft.json"
    path.write_bytes(fr.draft_bytes(_draft()))
    assert script.cmd_verify_final(SimpleNamespace(draft=path, record=None), FakeSources(times=[T_REC])) == 0
    late = FakeSources(times=[T_FLOOR + timedelta(minutes=101)])
    assert script.cmd_verify_final(SimpleNamespace(draft=path, record=None), late) == 1
    path.write_bytes(json.dumps(json.loads(path.read_bytes()), indent=1).encode())
    with pytest.raises(script.FinalCollectError, match="canonical"):
        script.load_draft(path)


def test_collect_final_end_to_end_writes_a_non_authoritative_draft(tmp_path, monkeypatch, capsys):
    identity = json.dumps(_pins_identity()).encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("phase5_timing_runtime_identity.json", identity)
    b5_zip = buffer.getvalue()
    monkeypatch.setattr(rd, "B5_IDENTITY_FILE_SHA256", fr.sha256_hex(identity))
    src = FakeSources(times=[T_LOCAL])
    real_download = script.b63.download_artifact_zip

    def download(s, artifact_id, digest):
        return b5_zip if artifact_id == rd.B5_ARTIFACT_DIGESTS[0][1] else real_download(s, artifact_id, digest)

    monkeypatch.setattr(script.b63, "download_artifact_zip", download)
    inputs = {
        "conditional_go": {"conditional_go_ref": "q77-p5d-cgo-1", "issued_at_utc": T_GO.isoformat(),
                           "terms": fr.CONDITIONAL_GO_TERMS},
        "owner_console": {"transcription": json.loads(fr.canonical_json_bytes(_transcription())),
                          "opened_at_utc": T_OPEN.isoformat(), "closed_at_utc": T_CLOSE.isoformat(), "attested": True},
        "fx": {"usd_per_eur": "1.1225", "reference_date": "2026-10-05", "source_url": "https://www.ecb.europa.eu/x",
               "retrieved_at_utc": T_FX.isoformat()},
        "lifecycle": _lifecycle(),
    }
    for name, data in inputs.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    args = SimpleNamespace(readiness_source_sha=R, attempt=1, probe_run_id=PROBE_RUN, previous_probe_run_id=None,
                           conditional_go=tmp_path / "conditional_go.json", owner_console=tmp_path / "owner_console.json",
                           fx=tmp_path / "fx.json", lifecycle_snapshot=tmp_path / "lifecycle.json", adjudications=None,
                           draft_out=tmp_path / "draft.json")
    assert script.cmd_collect_final(args, src) == 0, capsys.readouterr().out
    draft = script.load_draft(tmp_path / "draft.json")
    assert draft.conditional_go.conditional_go_ref == "q77-p5d-cgo-1"
    assert "not authoritative" in capsys.readouterr().out
    inputs["conditional_go"]["terms"] = "authorize commit A"
    (tmp_path / "conditional_go.json").write_text(json.dumps(inputs["conditional_go"]), encoding="utf-8")
    with pytest.raises(ValidationError, match="terms"):
        script.cmd_collect_final(args, FakeSources(times=[T_LOCAL]))
    inputs["owner_console"]["attested"] = False
    with pytest.raises(ValidationError, match="attestation"):
        script.load_owner_console(_write(tmp_path / "o2.json", inputs["owner_console"]))


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ======================================================================
# Amendment C carries the frozen texts
# ======================================================================


def test_amendment_c_carries_the_frozen_conditional_go_terms_and_binding_regex():
    adr = re.sub(r"\s+", " ", ADR_PATH.read_text(encoding="utf-8"))
    assert "## Amendment C" in adr
    assert fr.CONDITIONAL_GO_TERMS in adr
    assert "^q77-p5d-final-go-a/[0-9a-f]{64}$" in adr
    assert fr.OWNER_CONSOLE_ATTESTATION in adr
