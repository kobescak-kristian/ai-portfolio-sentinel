"""Frozen Phase-2 policy constants and the run configuration shape.

Nothing in this module reads an environment variable or a private
governance record — every constant here is either a fixed, documented
engineering choice or a direct restatement of a frozen upstream
contract (fixtures/MANIFEST.md's required-file set, ADR 0004's check
classes). Live-mode policy (required files, README structure) is
*not* configured here — it is derived per repository, per run, from
that repository's own public policy surfaces (see
sentinel/inventory/github_live.py and the C8-round-2 plan design).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# --- HTTP timeouts / retry budget (net/client.py, net/links.py) -----------

HTTP_TIMEOUT_SECONDS: float = 10.0
HTTP_MAX_ATTEMPTS: int = 3
HTTP_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 2.0)
HTTP_RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
MAX_HTTP_REQUESTS_PER_RUN: int = 500
MAX_INVENTORY_PAGES: int = 20  # unbounded-pagination guard

# --- GitHub live inventory -------------------------------------------------

GITHUB_API_ROOT = "https://api.github.com"
GITHUB_RAW_ROOT = "https://raw.githubusercontent.com"
GITHUB_API_VERSION = "2022-11-28"
USER_AGENT_TEMPLATE = "ai-portfolio-sentinel/{version} (+https://github.com/{login}/ai-portfolio-sentinel)"
MAX_MARKDOWN_FILES_PER_REPO: int = 25

# Structural exclusions from the scaffold-repo check classes
# (missing-required-file, readme-structure, number-mismatch) — a
# GitHub *profile* repo (name == the account login) and the account's
# Pages site repo are not scaffold repos. Both exclusions are
# platform-structural facts, re-derivable for any account; neither is
# a hand-maintained name list.


def is_profile_repo(repo_name: str, github_user: str) -> bool:
    return repo_name == github_user


def is_pages_repo(repo_name: str, github_user: str) -> bool:
    return repo_name == f"{github_user}.github.io"


# --- Fixture/eval required-file set (FROZEN — fixtures/MANIFEST.md) -------
#
# This tuple must never change: it is load-bearing for the frozen
# Phase 1 eval gate's quantized counts. Live mode does NOT use this
# constant — see derive_required_paths() in
# sentinel/inventory/github_live.py.

FIXTURE_REQUIRED_FILES: tuple[str, ...] = (
    "STATE.md",
    ".githooks/pre-push",
    "evals/eval_config.yaml",
)

# Fixture/eval link-scanned paths (fixtures/MANIFEST.md baseline shape).
FIXTURE_LINK_SCANNED_PATHS: tuple[str, ...] = ("README.md", "EVAL_RESULTS.md")

# --- readme-structure (ADR 0004, fixture/eval mode only) ------------------

ADR_0004_REQUIRED_README_SECTIONS: tuple[str, ...] = (
    "## Problem",
    "## Solution",
    "## System",
    "## Outcome",
    "## Version Log",
)

# --- Live required-path derivation (C8-round-2) ---------------------------
#
# The pre-push hook is the one flat requirement for every project
# repo in live mode (owner-approved). Gate-file and STATE.md
# requirements are derived per repository, per run, from that
# repository's own live policy content — never a static list.

LIVE_FLAT_REQUIRED_FILES: tuple[str, ...] = (".githooks/pre-push",)


@dataclass(frozen=True)
class RunConfig:
    """Everything a single ``execute_run`` invocation needs, resolved
    from CLI flags before the pipeline starts."""

    run_kind: str  # "dev" | "eval" | "live"
    source: str  # "fixtures" | "live"
    db_path: Path
    findings_path: Path
    log_path: Path
    cost_ledger_path: Path = Path("telemetry/cost_ledger.jsonl")
    fixtures_root: Path = Path("fixtures/repos")
    github_user: str | None = None
    site_repo: str | None = None
    http_timeout_seconds: float = HTTP_TIMEOUT_SECONDS
    max_http_requests: int = MAX_HTTP_REQUESTS_PER_RUN
    recover: bool = True
    fail_run_on_task_failure: bool = True
    log_level: str = "INFO"
    run_id: str | None = None  # explicit override for reproducible verification runs
