"""Live, unauthenticated GitHub inventory (BLUEPRINT §6 P2).

Derives the operator's public repositories at run time from the
public GitHub API — no hand-maintained list. Reads no environment
variable and never sets an ``Authorization`` header: this is a
source-scannable, test-assertable invariant (no credential for any
monitored repository).

**C8-round-2 — required-path and readme-structure applicability are
derived per repository, per run**, from that repository's own live
``.githooks/pre-push`` and (conditionally) its own gate-file content —
never from Sentinel's own convention, another repo's convention, a
static list, or any private governance record. ``.githooks/pre-push``
is the one flat requirement (owner-approved). A gate file is required
only where that repo's own active (non-commented) pre-push invokes
one. ``STATE.md`` is required only where that repo's own gate file's
own content declares the check. readme-structure is applicable only
where a gate file is actively invoked, and enforces presence-only of
that repo's own declared ``REQUIRED_README_SECTIONS`` list — never
Sentinel's own ADR-0004 order.
"""

from __future__ import annotations

import ast
import json
import re

from checks.base import normalize_text
from sentinel.config import (
    GITHUB_API_ROOT,
    GITHUB_API_VERSION,
    GITHUB_RAW_ROOT,
    LIVE_FLAT_REQUIRED_FILES,
    MAX_INVENTORY_PAGES,
    MAX_MARKDOWN_FILES_PER_REPO,
    USER_AGENT_TEMPLATE,
    is_pages_repo,
    is_profile_repo,
)
from sentinel.inventory.base import (
    Content,
    ConfirmedAbsent,
    FetchResult,
    RepoPolicy,
    RepoSurface,
    Unknown,
)
from sentinel.net.client import HttpClient, HttpError

_GATE_INVOCATION = re.compile(r"^(?!\s*#).*\bpython\b.*?(\.githooks/\S+\.py)")


class InventoryUnavailable(RuntimeError):
    """The live repo listing could not be completed successfully — the
    whole run must abort rather than silently treating an incomplete
    inventory as authoritative (a GitHub outage must never look like a
    clean portfolio)."""


def user_agent(github_user: str, version: str = "0.2") -> str:
    return USER_AGENT_TEMPLATE.format(version=version, login=github_user)


def _api_headers(github_user: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": user_agent(github_user),
    }


def _find_active_gate_invocation(pre_push_text: str) -> str | None:
    """Deterministic text parse — no prose interpretation. A line
    invoking a `.githooks/*.py` script, skipped if commented out."""
    for line in pre_push_text.split("\n"):
        match = _GATE_INVOCATION.search(line)
        if match:
            return match.group(1)
    return None


def _declares_state_md_required(gate_text: str) -> bool:
    """AST match for the exact shape ``(ROOT / "STATE.md").exists()`` —
    a textual/regex heuristic is rejected in favor of AST matching
    since a comment or docstring containing the substring must not
    false-positive."""
    try:
        tree = ast.parse(gate_text)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "exists"
        ):
            for sub in ast.walk(node.func.value):
                if isinstance(sub, ast.Constant) and sub.value == "STATE.md":
                    return True
    return False


def _extract_required_readme_sections(gate_text: str) -> tuple[str, ...]:
    """AST match for a module-level ``REQUIRED_README_SECTIONS = [...]``
    assignment of string literals — this repo's own declared policy,
    read live, never Sentinel's own ADR-0004 list."""
    try:
        tree = ast.parse(gate_text)
    except SyntaxError:
        return ()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.List, ast.Tuple)):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "REQUIRED_README_SECTIONS" not in names:
                continue
            values = [
                elt.value
                for elt in node.value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ]
            if values:
                return tuple(values)
    return ()


def list_public_repos(http: HttpClient, github_user: str, *, timeout: float) -> list[dict]:
    """Paginated, unauthenticated repo listing. Raises
    ``InventoryUnavailable`` on any failure or non-terminating
    pagination — this aborts the whole run rather than silently
    resolving findings against an incomplete repo list."""
    repos: list[dict] = []
    headers = _api_headers(github_user)
    for page in range(1, MAX_INVENTORY_PAGES + 1):
        url = (
            f"{GITHUB_API_ROOT}/users/{github_user}/repos"
            f"?type=owner&sort=full_name&direction=asc&per_page=100&page={page}"
        )
        try:
            response = http.get(url, headers=headers, timeout=timeout)
        except HttpError as exc:
            raise InventoryUnavailable(f"repo listing request failed: {exc}") from exc
        if response.status != 200:
            raise InventoryUnavailable(f"repo listing returned HTTP {response.status}")
        try:
            page_repos = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise InventoryUnavailable(f"repo listing response was not valid JSON: {exc}") from exc
        if not isinstance(page_repos, list):
            raise InventoryUnavailable("repo listing response was not a JSON array")
        if not page_repos:
            break
        repos.extend(page_repos)
        if len(page_repos) < 100:
            break
    else:
        raise InventoryUnavailable(
            f"repo listing did not terminate within {MAX_INVENTORY_PAGES} pages"
        )
    return [r for r in repos if not r.get("fork") and not r.get("archived") and not r.get("disabled")]


def raw_fetch(
    http: HttpClient, github_user: str, repo: str, branch: str, path: str, *, timeout: float
) -> FetchResult:
    url = f"{GITHUB_RAW_ROOT}/{github_user}/{repo}/{branch}/{path}"
    headers = {"User-Agent": user_agent(github_user)}
    try:
        response = http.get(url, headers=headers, timeout=timeout)
    except HttpError as exc:
        return Unknown(reason=str(exc))
    if response.status == 200:
        try:
            return Content(text=normalize_text(response.body))
        except UnicodeDecodeError as exc:
            return Unknown(reason=f"non-utf8 content: {exc}")
    if response.status in (404, 410):
        return ConfirmedAbsent()
    return Unknown(reason=f"HTTP {response.status}")


def fetch_markdown_paths(
    http: HttpClient, github_user: str, repo: str, branch: str, *, timeout: float
) -> list[str]:
    url = f"{GITHUB_API_ROOT}/repos/{github_user}/{repo}/git/trees/{branch}?recursive=1"
    headers = _api_headers(github_user)
    try:
        response = http.get(url, headers=headers, timeout=timeout)
    except HttpError:
        return []
    if response.status != 200:
        return []
    try:
        data = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return []
    entries = data.get("tree", []) if isinstance(data, dict) else []
    paths = [
        e["path"]
        for e in entries
        if isinstance(e, dict) and e.get("type") == "blob" and "path" in e
    ]
    markdown = sorted(p for p in paths if p.endswith(".md") and p.count("/") <= 1)
    return markdown[:MAX_MARKDOWN_FILES_PER_REPO]


def _derive_policy(
    http: HttpClient,
    github_user: str,
    repo: str,
    branch: str,
    *,
    timeout: float,
    open_scopes: frozenset[tuple[str, str]],
) -> RepoPolicy:
    required: list[str] = list(LIVE_FLAT_REQUIRED_FILES)
    policy_source_path: str | None = None
    required_sections: tuple[str, ...] = ()
    readme_structure_applicable = False

    pre_push_probe = raw_fetch(http, github_user, repo, branch, ".githooks/pre-push", timeout=timeout)
    if isinstance(pre_push_probe, Content):
        gate_path = _find_active_gate_invocation(pre_push_probe.text)
        if gate_path:
            policy_source_path = gate_path
            required.append(gate_path)
            gate_probe = raw_fetch(http, github_user, repo, branch, gate_path, timeout=timeout)
            if isinstance(gate_probe, Content):
                if _declares_state_md_required(gate_probe.text):
                    required.append("STATE.md")
                sections = _extract_required_readme_sections(gate_probe.text)
                if sections:
                    required_sections = sections
                    readme_structure_applicable = True

    prefix = f"{repo}/"
    for surface, check_class in open_scopes:
        if not surface.startswith(prefix):
            continue
        path = surface[len(prefix) :]
        if check_class == "missing-required-file" and path not in required:
            required.append(path)

    markdown_paths = fetch_markdown_paths(http, github_user, repo, branch, timeout=timeout)
    link_scanned = set(markdown_paths) | {"README.md"}
    for surface, check_class in open_scopes:
        if not surface.startswith(prefix):
            continue
        path = surface[len(prefix) :]
        if check_class in ("broken-link", "missing-synthetic-label"):
            link_scanned.add(path)

    return RepoPolicy(
        required_paths=tuple(dict.fromkeys(required)),
        link_scanned_paths=tuple(sorted(link_scanned)),
        readme_structure_applicable=readme_structure_applicable,
        required_readme_sections=required_sections,
        enforce_readme_order=False,
        policy_source_path=policy_source_path,
    )


def build_repo_surfaces(
    http: HttpClient,
    github_user: str,
    *,
    timeout: float,
    open_scopes: frozenset[tuple[str, str]] = frozenset(),
) -> list[RepoSurface]:
    raw_repos = list_public_repos(http, github_user, timeout=timeout)
    surfaces: list[RepoSurface] = []
    for repo in sorted(raw_repos, key=lambda r: r.get("name", "")):
        name = repo.get("name", "")
        if not name or is_profile_repo(name, github_user) or is_pages_repo(name, github_user):
            continue
        branch = repo.get("default_branch") or "main"
        policy = _derive_policy(
            http, github_user, name, branch, timeout=timeout, open_scopes=open_scopes
        )

        def fetch(path: str, _repo: str = name, _branch: str = branch) -> FetchResult:
            return raw_fetch(http, github_user, _repo, _branch, path, timeout=timeout)

        surfaces.append(RepoSurface(owner=name, fetch=fetch, policy=policy))
    return surfaces
