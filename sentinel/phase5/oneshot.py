"""Phase-5 one-shot attempt markers (ADR-0011 §3; dispatch P16).

Purpose-wide uniqueness: any existing marker for a purpose — regardless
of what happened afterward during OIDC/provider execution — consumes
that one-shot permanently. No outcome field exists on the marker itself
and none is added here; a later attempt after a consumed one-shot
requires a new prospective governed ruling, entirely outside this
package's scope. Filtering is by ``purpose`` only, so a source-SHA
change never resets a purpose's one-shot state.

Durable-history-first discovery (ADR-0012 Amendment A1; dispatch
q77-p5d-repair-stage2-implement-a): a live Actions marker artifact
expires after at most 90 days, so live-marker discovery alone can no
longer be trusted as the sole consumption truth. Consumption truth
comes FIRST from the committed, hash-chained receipt registry
(``sentinel/phase5/receipts.py``) — artifact expiry never resets it —
and only then, defensively, from whatever live markers still exist.
"""

from __future__ import annotations

from typing import Sequence

from .models import OneShotMarker, sha256_hex_of_model
from .receipts import Phase5Receipt, is_purpose_consumed


class OneShotAlreadyConsumed(Exception):
    """A valid marker already exists for this purpose."""


class OneShotDiscoveryAmbiguous(Exception):
    """More than one differing-bytes marker was found for one purpose —
    fails closed rather than picking one."""


def assert_purpose_not_yet_consumed(purpose: str, candidates: Sequence[OneShotMarker]) -> None:
    matching = [marker for marker in candidates if marker.purpose == purpose]
    if not matching:
        return
    if len(matching) > 1 and len({sha256_hex_of_model(marker) for marker in matching}) > 1:
        raise OneShotDiscoveryAmbiguous(purpose)
    raise OneShotAlreadyConsumed(purpose)


def assert_purpose_not_yet_consumed_durably(
    purpose: str,
    receipts: Sequence[Phase5Receipt],
    candidates: Sequence[OneShotMarker],
) -> None:
    """Durable-history-first one-shot uniqueness (dispatch
    q77-p5d-repair-stage2-implement-a). Checks the committed receipt
    registry BEFORE any live artifact discovery: a durably consumed
    purpose raises ``OneShotAlreadyConsumed`` even if every live
    marker artifact for it has since expired and is no longer
    discoverable. When the registry shows no consumption, falls
    through to the existing live-marker check
    (``assert_purpose_not_yet_consumed``) so a marker that was created
    but has not yet received its durable receipt is still caught.
    Callers are responsible for loading ``receipts`` from a registry
    that itself fails closed on a missing or corrupt file
    (``sentinel.phase5.receipts.load_registry``) — this function never
    treats an empty ``receipts`` sequence as proof of non-consumption
    beyond what it actually shows."""
    if is_purpose_consumed(receipts, purpose):
        raise OneShotAlreadyConsumed(purpose)
    assert_purpose_not_yet_consumed(purpose, candidates)


def is_eligible_marker_creation(candidate: OneShotMarker) -> bool:
    """``run_attempt > 1`` is never eligible to create provider activity."""
    return candidate.run_attempt == 1
