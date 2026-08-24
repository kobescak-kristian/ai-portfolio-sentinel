"""Phase-5 one-shot attempt markers (ADR-0011 §3; dispatch P16).

Purpose-wide uniqueness: any existing marker for a purpose — regardless
of what happened afterward during OIDC/provider execution — consumes
that one-shot permanently. No outcome field exists on the marker itself
and none is added here; a later attempt after a consumed one-shot
requires a new prospective governed ruling, entirely outside this
package's scope. Filtering is by ``purpose`` only, so a source-SHA
change never resets a purpose's one-shot state.
"""

from __future__ import annotations

from typing import Sequence

from .models import OneShotMarker, sha256_hex_of_model


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


def is_eligible_marker_creation(candidate: OneShotMarker) -> bool:
    """``run_attempt > 1`` is never eligible to create provider activity."""
    return candidate.run_attempt == 1
