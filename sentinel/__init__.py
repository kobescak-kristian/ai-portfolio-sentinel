"""Sentinel deterministic control plane (BLUEPRINT §6 P2).

Zero LLM calls in this package. Everything here is deterministic:
live inventory, ledger persistence, dedup/lifecycle, deterministic
checkers, and a stub boundary for the two judgment classes (real
judgment lands at Phase 3, behind the same seam).
"""

from __future__ import annotations

__version__ = "0.2.0"
