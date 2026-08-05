"""Portfolio-site surface (BLUEPRINT §11(c)): link participation only.

The site repo (``{github_user}.github.io``) is not a scaffold repo —
it carries no required-file or readme-structure obligations, and
site gate-statement parity is deferred and ungated (BLUEPRINT §11(h),
ADR 0004 §3). This provider only ever contributes broken-link (and,
harmlessly, the two Phase-2 stub classes, which fetch a possibly-
absent README/STATE.md and correctly find nothing) work units — never
missing-required-file or readme-structure.
"""

from __future__ import annotations

from sentinel.inventory.base import FetchResult, RepoPolicy, RepoSurface
from sentinel.inventory.github_live import fetch_markdown_paths, raw_fetch
from sentinel.net.client import HttpClient

SITE_OWNER = "site"


def build_site_surface(
    http: HttpClient,
    github_user: str,
    *,
    timeout: float,
    open_scopes: frozenset[tuple[str, str]] = frozenset(),
    branch: str = "main",
) -> RepoSurface:
    site_repo = f"{github_user}.github.io"
    markdown_paths = fetch_markdown_paths(http, github_user, site_repo, branch, timeout=timeout)
    link_scanned = set(markdown_paths) | {"README.md"}
    prefix = f"{SITE_OWNER}/"
    for surface, check_class in open_scopes:
        if check_class not in ("broken-link", "missing-synthetic-label"):
            continue
        if surface.startswith(prefix):
            link_scanned.add(surface[len(prefix) :])

    policy = RepoPolicy(
        required_paths=(),
        link_scanned_paths=tuple(sorted(link_scanned)),
        readme_structure_applicable=False,
        required_readme_sections=(),
        enforce_readme_order=False,
        policy_source_path=None,
    )

    def fetch(path: str) -> FetchResult:
        return raw_fetch(http, github_user, site_repo, branch, path, timeout=timeout)

    return RepoSurface(owner=SITE_OWNER, fetch=fetch, policy=policy)
