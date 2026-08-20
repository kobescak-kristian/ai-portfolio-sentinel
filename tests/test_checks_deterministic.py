"""The four real deterministic checkers — table-driven, synthetic
in-memory content only.

**Anti-oracle rule**: expectations here are hand-written literals.
``evals/answer_key.jsonl`` is never imported in this file — it exists
for eval-gate scoring only; using it as an implementation oracle
would invalidate the gate. This file's job is unit coverage of the
algorithms on synthetic content; the checkers were separately
cross-checked against the real frozen fixture corpus (read-only) to
confirm they reproduce the frozen per-class answer-key counts before
this suite was written.
"""

from __future__ import annotations

from datetime import datetime, timezone

from contracts.schemas import CHECK_CLASSES, Finding, compute_content_hash, compute_fingerprint
from checks.base import CheckContext, Confirmed, Inconclusive
from checks.deterministic.files import check_missing_required_file
from checks.deterministic.links import check_broken_link
from checks.deterministic.numbers import check_number_mismatch
from checks.deterministic.readme import check_readme_structure
from checks.judgment.stubs import NullJudgmentStub
from sentinel.config import ADR_0004_REQUIRED_README_SECTIONS
from sentinel.inventory.base import Content, ConfirmedAbsent, Unknown
from sentinel.net.links import StaticLinkResolver
from tests.conftest import make_in_memory_fetch

JUDGMENT = NullJudgmentStub()


def _ctx(files, detail_path, *, link_map=None, sections=ADR_0004_REQUIRED_README_SECTIONS, order=True):
    return CheckContext(
        owner="acme",
        detail_path=detail_path,
        fetch=make_in_memory_fetch(files),
        link_resolver=StaticLinkResolver(mapping=link_map or {}),
        judgment=JUDGMENT,
        required_readme_sections=sections,
        enforce_readme_order=order,
    )


def _validate_as_finding(observed, run_id="r1"):
    """Every finding a checker emits must survive becoming a real
    ledger Finding — this is what proves normalized_content/location
    are contract-legal, not just "looks right"."""
    content_hash = compute_content_hash(observed.location, observed.normalized_content)
    fingerprint = compute_fingerprint(observed.surface, observed.check_class, content_hash)
    return Finding(
        schema_version=1,
        fingerprint=fingerprint,
        surface=observed.surface,
        check_class=observed.check_class,
        content_hash=content_hash,
        location=observed.location,
        detail=observed.detail,
        status="OPEN",
        first_seen_utc=datetime(2026, 8, 4, tzinfo=timezone.utc),
        last_seen_utc=datetime(2026, 8, 4, tzinfo=timezone.utc),
        first_seen_run_id=run_id,
        last_seen_run_id=run_id,
    )


# --- broken-link -------------------------------------------------------


def test_broken_link_fires_only_on_confirmed_dead():
    files = {"README.md": "[a](https://dead.example.invalid)\n[b](https://live.example.invalid)\n"}
    ctx = _ctx(files, "README.md", link_map={"https://dead.example.invalid": "dead", "https://live.example.invalid": "live"})
    outcome = check_broken_link(ctx)
    assert isinstance(outcome, Confirmed)
    assert len(outcome.findings) == 1
    finding = outcome.findings[0]
    assert finding.check_class == "broken-link"
    _validate_as_finding(finding)


def test_broken_link_whole_task_inconclusive_on_any_unknown():
    files = {"README.md": "[a](https://dead.example.invalid)\n[b](https://unknown.example.invalid)\n"}
    ctx = _ctx(files, "README.md", link_map={"https://dead.example.invalid": "dead"})
    outcome = check_broken_link(ctx)
    assert isinstance(outcome, Inconclusive)


def test_broken_link_confirmed_absent_file_is_confirmed_empty():
    ctx = _ctx({}, "README.md")
    outcome = check_broken_link(ctx)
    assert isinstance(outcome, Confirmed)
    assert outcome.findings == []


def test_broken_link_normalized_content_excludes_http_status():
    """A 404->503 flip on the same URL must not mint a new
    fingerprint — normalized_content is the URL only."""
    files = {"README.md": "[a](https://dead.example.invalid)\n"}
    ctx = _ctx(files, "README.md", link_map={"https://dead.example.invalid": "dead"})
    outcome = check_broken_link(ctx)
    assert outcome.findings[0].normalized_content == "url=https://dead.example.invalid"


def test_broken_link_hash_stable_under_unrelated_edit():
    files_a = {"README.md": "prefix\n[a](https://dead.example.invalid)\n"}
    files_b = {"README.md": "prefix line changed entirely\n[a](https://dead.example.invalid)\n"}
    link_map = {"https://dead.example.invalid": "dead"}
    fa = check_broken_link(_ctx(files_a, "README.md", link_map=link_map)).findings[0]
    fb = check_broken_link(_ctx(files_b, "README.md", link_map=link_map)).findings[0]
    hash_a = compute_content_hash(fa.location, fa.normalized_content)
    hash_b = compute_content_hash(fb.location, fb.normalized_content)
    assert hash_a == hash_b


# --- number-mismatch -----------------------------------------------------


def test_number_mismatch_fires_on_divergent_figures():
    files = {
        "README.md": "- Accuracy: 91.9 percent\n",
        "EVAL_RESULTS.md": "- Accuracy: 90.6 percent\n",
    }
    ctx = _ctx(files, "README.md")
    outcome = check_number_mismatch(ctx)
    assert isinstance(outcome, Confirmed)
    assert len(outcome.findings) == 1
    _validate_as_finding(outcome.findings[0])


def test_number_mismatch_clean_when_equal():
    files = {"README.md": "- Coverage: 81.1 percent\n", "EVAL_RESULTS.md": "- Coverage: 81.1 percent\n"}
    outcome = check_number_mismatch(_ctx(files, "README.md"))
    assert outcome.findings == []


def test_number_mismatch_uses_decimal_not_float():
    files = {"README.md": "- Latency: 50 ms\n", "EVAL_RESULTS.md": "- Latency: 50.0 ms\n"}
    outcome = check_number_mismatch(_ctx(files, "README.md"))
    assert outcome.findings == []  # Decimal("50") == Decimal("50.0")


def test_number_mismatch_skips_ambiguous_duplicate_labels():
    files = {
        "README.md": "- Accuracy: 91.9 percent\n- Accuracy: 50 percent\n",
        "EVAL_RESULTS.md": "- Accuracy: 90.6 percent\n",
    }
    outcome = check_number_mismatch(_ctx(files, "README.md"))
    assert outcome.findings == []  # ambiguous in README, skipped entirely


def test_number_mismatch_confirmed_absent_counterpart_is_clean():
    outcome = check_number_mismatch(_ctx({"README.md": "- Accuracy: 1 percent\n"}, "README.md"))
    assert outcome.findings == []


def test_number_mismatch_unknown_dead_letters():
    def fetch(path):
        if path == "README.md":
            return Content(text="- Accuracy: 1 percent\n")
        return Unknown(reason="timeout")

    ctx = CheckContext(
        owner="acme", detail_path="README.md", fetch=fetch,
        link_resolver=StaticLinkResolver(mapping={}), judgment=JUDGMENT,
    )
    outcome = check_number_mismatch(ctx)
    assert isinstance(outcome, Inconclusive)


# --- missing-required-file ------------------------------------------------


def test_missing_required_file_fires_on_confirmed_absent():
    ctx = _ctx({}, "STATE.md")
    outcome = check_missing_required_file(ctx)
    assert isinstance(outcome, Confirmed)
    assert len(outcome.findings) == 1
    assert outcome.findings[0].location == "STATE.md"  # bare, no line suffix
    _validate_as_finding(outcome.findings[0])


def test_missing_required_file_clean_when_present():
    ctx = _ctx({"STATE.md": "present"}, "STATE.md")
    outcome = check_missing_required_file(ctx)
    assert outcome.findings == []


def test_missing_required_file_unknown_dead_letters():
    ctx = CheckContext(
        owner="acme", detail_path="STATE.md",
        fetch=lambda path: Unknown(reason="timeout"),
        link_resolver=StaticLinkResolver(mapping={}), judgment=JUDGMENT,
    )
    outcome = check_missing_required_file(ctx)
    assert isinstance(outcome, Inconclusive)


def test_missing_required_file_repeated_absence_same_content():
    """Confirmed absence must re-emit the identical ObservedFinding
    every run — this is what lets the lifecycle layer advance rather
    than treat it as unobserved."""
    ctx = _ctx({}, "STATE.md")
    f1 = check_missing_required_file(ctx).findings[0]
    f2 = check_missing_required_file(ctx).findings[0]
    assert f1.normalized_content == f2.normalized_content
    assert f1.location == f2.location


# --- readme-structure ------------------------------------------------------


def test_readme_structure_missing_header_location_matches_scoring_semantics():
    text = "## Solution\ns\n## Outcome\no\n## Version Log\nv\n"
    outcome = check_readme_structure(_ctx({"README.md": text}, "README.md"))
    assert isinstance(outcome, Confirmed)
    missing_problem = [f for f in outcome.findings if "Problem" in f.detail][0]
    assert missing_problem.location == "README.md:1"  # nearest following present header (Solution)
    _validate_as_finding(missing_problem)


def test_readme_structure_missing_final_header_maps_to_preceding():
    text = "## Problem\np\n## Solution\ns\n## System\nsy\n## Outcome\no\n"
    outcome = check_readme_structure(_ctx({"README.md": text}, "README.md"))
    assert len(outcome.findings) == 1
    assert "Version Log" in outcome.findings[0].detail
    assert outcome.findings[0].location == "README.md:7"  # line of "## Outcome"


def test_readme_structure_order_violation_at_most_one():
    text = "## Problem\np\n## System\nsy\n## Solution\ns\n## Outcome\no\n## Version Log\nv\n"
    outcome = check_readme_structure(_ctx({"README.md": text}, "README.md"))
    assert len(outcome.findings) == 1
    assert "appears where" in outcome.findings[0].detail


def test_readme_structure_clean_when_all_present_in_order():
    text = "\n".join(ADR_0004_REQUIRED_README_SECTIONS)
    outcome = check_readme_structure(_ctx({"README.md": text}, "README.md"))
    assert outcome.findings == []


def test_readme_structure_live_mode_presence_only_no_order_check():
    text = "## Solution\n## Problem\n"  # reordered but both present
    ctx = _ctx({"README.md": text}, "README.md", sections=("## Problem", "## Solution"), order=False)
    outcome = check_readme_structure(ctx)
    assert outcome.findings == []  # live mode never enforces order


def test_readme_structure_not_applicable_when_no_sections_declared():
    ctx = _ctx({"README.md": "anything"}, "README.md", sections=(), order=False)
    outcome = check_readme_structure(ctx)
    assert outcome.findings == []


def test_readme_structure_confirmed_absent_file_is_clean():
    ctx = _ctx({}, "README.md")
    outcome = check_readme_structure(ctx)
    assert outcome.findings == []


# --- cross-cutting ----------------------------------------------------------


def test_all_four_checkers_emit_only_frozen_check_classes():
    text = "## Solution\n"
    contexts = [
        (check_broken_link, _ctx({"README.md": "[a](https://x.invalid)"}, "README.md", link_map={"https://x.invalid": "dead"})),
        (check_number_mismatch, _ctx({"README.md": "- A: 1\n", "EVAL_RESULTS.md": "- A: 2\n"}, "README.md")),
        (check_missing_required_file, _ctx({}, "STATE.md")),
        (check_readme_structure, _ctx({"README.md": text}, "README.md")),
    ]
    for checker, ctx in contexts:
        outcome = checker(ctx)
        assert isinstance(outcome, Confirmed)
        for finding in outcome.findings:
            assert finding.check_class in CHECK_CLASSES


def test_checkers_are_deterministic_across_repeated_calls():
    ctx = _ctx({"README.md": "## Solution\n"}, "README.md")
    fp1 = [f.normalized_content for f in check_readme_structure(ctx).findings]
    fp2 = [f.normalized_content for f in check_readme_structure(ctx).findings]
    assert fp1 == fp2


# --- adr/0006 regression guard (T5) -----------------------------------------


def test_t5_deterministic_checker_identity_is_unchanged_by_adr_0006():
    """adr/0006 changes judgment identity ONLY. The four deterministic
    checkers keep their exact ``normalized_content`` semantics, pinned
    here as hand-written literals in this file's anti-oracle style
    (no answer key is read). Altering or "harmonizing" any deterministic
    checker's identity string breaks this test.

    The judgment classes' own identity rule is proven in
    tests/test_bounds.py (T1-T4, T8) and tests/test_lifecycle.py
    (T6, T7)."""
    # broken-link -- URL only; HTTP status deliberately excluded.
    link = check_broken_link(
        _ctx({"README.md": "[a](https://dead.example.invalid)\n"}, "README.md",
             link_map={"https://dead.example.invalid": "dead"})
    ).findings[0]
    assert link.normalized_content == "url=https://dead.example.invalid"

    # number-mismatch -- casefolded label plus both figures.
    number = check_number_mismatch(
        _ctx({"README.md": "- Accuracy: 91.9 percent\n",
              "EVAL_RESULTS.md": "- Accuracy: 90.6 percent\n"}, "README.md")
    ).findings[0]
    assert number.normalized_content == "label=accuracy|readme=91.9|eval_results=90.6"

    # missing-required-file -- the required path.
    absent = check_missing_required_file(_ctx({}, "STATE.md")).findings[0]
    assert absent.normalized_content == "required_path=STATE.md"

    # readme-structure -- both defect shapes.
    missing_header = check_readme_structure(
        _ctx({"README.md": "## Solution\ns\n## System\nsy\n## Outcome\no\n## Version Log\nv\n"}, "README.md")
    ).findings
    assert [f.normalized_content for f in missing_header] == [
        "defect=missing-header|header=## Problem"
    ]

    order_defect = check_readme_structure(
        _ctx({"README.md": "## Problem\np\n## System\nsy\n## Solution\ns\n## Outcome\no\n## Version Log\nv\n"}, "README.md")
    ).findings
    assert [f.normalized_content for f in order_defect] == [
        "defect=header-order|header=## System|expected=## Solution"
    ]

    # None of these carries a "reason=" identity, and every one of them
    # still round-trips into a contract-legal ledger Finding.
    for finding in (link, number, absent, *missing_header, *order_defect):
        assert not finding.normalized_content.startswith("reason=")
        _validate_as_finding(finding)
