"""The two judgment-stub checker adapters (stale-STATE-marker,
missing-synthetic-label) and the stub injection boundary itself."""

from __future__ import annotations

from checks.base import CheckContext, Confirmed, Inconclusive, ObservedFinding
from checks.judgment.stale_state import check_stale_state_marker
from checks.judgment.stubs import JudgmentRequest, NullJudgmentStub, ScriptedJudgmentStub
from checks.judgment.synthetic_label import check_missing_synthetic_label
from sentinel.net.links import StaticLinkResolver
from tests.conftest import make_in_memory_fetch


def _ctx(checker_class, files, judgment, detail_path="STATE.md"):
    return CheckContext(
        owner="acme",
        detail_path=detail_path,
        fetch=make_in_memory_fetch(files),
        link_resolver=StaticLinkResolver(mapping={}),
        judgment=judgment,
    )


def test_null_stub_is_called_but_returns_nothing():
    judgment = NullJudgmentStub()
    ctx = _ctx("stale-STATE-marker", {"STATE.md": "content"}, judgment)
    outcome = check_stale_state_marker(ctx)
    assert isinstance(outcome, Confirmed)
    assert outcome.findings == []


def test_null_stub_called_even_when_file_absent():
    """The judgment task still reaches DONE with zero findings even
    when the underlying file is confirmed absent — text=None is
    passed through, not treated as an error."""
    judgment = NullJudgmentStub()
    ctx = _ctx("stale-STATE-marker", {}, judgment)
    outcome = check_stale_state_marker(ctx)
    assert isinstance(outcome, Confirmed)
    assert outcome.findings == []


def test_scripted_stub_returns_findings_through_the_normal_path():
    surface = "acme/STATE.md"
    scripted_finding = ObservedFinding(
        surface=surface, check_class="stale-STATE-marker", location="STATE.md:1",
        detail="STATE.md not updated in 90 days", normalized_content="stale",
    )
    judgment = ScriptedJudgmentStub(script={(surface, "stale-STATE-marker"): [scripted_finding]})
    ctx = _ctx("stale-STATE-marker", {"STATE.md": "old"}, judgment)
    outcome = check_stale_state_marker(ctx)
    assert isinstance(outcome, Confirmed)
    assert outcome.findings == [scripted_finding]
    assert len(judgment.calls) == 1
    assert judgment.calls[0].check_class == "stale-STATE-marker"
    assert judgment.calls[0].text == "old"


def test_scripted_stub_raising_propagates_for_pipeline_containment():
    """The checker itself does not catch a raising stub — that's the
    pipeline's job (sentinel.pipeline.execute_run wraps every checker
    call and converts any exception to Inconclusive, which then routes
    the task to DEAD_LETTER; see
    tests/test_failures.py::test_failed_task_routes_to_dead_letter_atomically
    for the end-to-end proof)."""
    import pytest

    judgment = ScriptedJudgmentStub(script={("acme/STATE.md", "stale-STATE-marker"): RuntimeError("boom")})
    ctx = _ctx("stale-STATE-marker", {"STATE.md": "x"}, judgment)
    with pytest.raises(RuntimeError):
        check_stale_state_marker(ctx)


def test_scripted_stub_lying_about_scope_is_rejected():
    """A stub result naming a different surface/check_class than the
    task it was invoked for must not corrupt the ledger — the
    checker rejects it as inconclusive rather than trusting it."""
    wrong_finding = ObservedFinding(
        surface="other/README.md", check_class="stale-STATE-marker", location="README.md",
        detail="wrong scope", normalized_content="x",
    )
    judgment = ScriptedJudgmentStub(script={("acme/STATE.md", "stale-STATE-marker"): [wrong_finding]})
    ctx = _ctx("stale-STATE-marker", {"STATE.md": "x"}, judgment)
    outcome = check_stale_state_marker(ctx)
    assert isinstance(outcome, Inconclusive)


def test_missing_synthetic_label_adapter_same_shape():
    judgment = NullJudgmentStub()
    ctx = _ctx("missing-synthetic-label", {"README.md": "content"}, judgment, detail_path="README.md")
    outcome = check_missing_synthetic_label(ctx)
    assert isinstance(outcome, Confirmed)
    assert outcome.findings == []


def test_unknown_fetch_dead_letters_before_judgment_is_even_consulted():
    class NeverCalled:
        def judge(self, request):
            raise AssertionError("must not be called when the fetch itself is unknown")

    from sentinel.inventory.base import Unknown

    ctx = CheckContext(
        owner="acme", detail_path="STATE.md",
        fetch=lambda path: Unknown(reason="timeout"),
        link_resolver=StaticLinkResolver(mapping={}), judgment=NeverCalled(),
    )
    outcome = check_stale_state_marker(ctx)
    assert isinstance(outcome, Inconclusive)


def test_judgment_request_shape():
    req = JudgmentRequest(surface="acme/STATE.md", check_class="stale-STATE-marker", path="STATE.md", text="x")
    assert req.text == "x"
    req_absent = JudgmentRequest(surface="acme/STATE.md", check_class="stale-STATE-marker", path="STATE.md", text=None)
    assert req_absent.text is None
