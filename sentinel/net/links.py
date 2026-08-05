"""Link-liveness resolution.

**C3 — closed status set.** ``dead`` fires only on a confirmed HTTP
404 or 410. Every other transport/HTTP outcome — DNS/connection/TLS
errors, timeouts, 403, 429, 999, 5xx, retry exhaustion, a HEAD
rejection followed by a failed ranged GET, or anything unclassified —
is ``unknown`` and never fires a finding. Fixture/eval runs resolve
exclusively through ``LinkTruthResolver`` and never touch the network
(``evals/SCORING.md`` §4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping, Protocol

from sentinel.net.client import HttpClient, HttpError

LinkStatus = Literal["live", "dead", "unknown"]

_DEAD_STATUSES = frozenset({404, 410})


class LinkResolver(Protocol):
    def resolve(self, url: str) -> LinkStatus: ...


@dataclass
class LinkTruthResolver:
    """Fixture/eval-only resolver: loads the committed corpus map
    (fixtures/link_truth.jsonl) and never makes a network request.
    A URL absent from the map resolves ``unknown`` (never guessed)."""

    truth: Mapping[str, LinkStatus]
    _cache: dict[str, LinkStatus] = field(default_factory=dict, init=False, repr=False)

    @classmethod
    def from_file(cls, path: Path) -> "LinkTruthResolver":
        """A missing file degrades to an empty truth map — every URL
        then resolves ``unknown``, exactly as an individually-absent
        URL already does. This isn't a real-corpus concern (the
        committed ``fixtures/link_truth.jsonl`` always exists there);
        it's what lets a synthetic/throwaway fixture directory in a
        test run without needing a matching link-truth sibling file."""
        truth: dict[str, LinkStatus] = {}
        try:
            handle = open(path, "r", encoding="utf-8")
        except FileNotFoundError:
            return cls(truth=truth)
        with handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                row = json.loads(stripped)
                status = row["status"]
                if status in ("live", "dead"):
                    truth[row["url"]] = status
        return cls(truth=truth)

    def resolve(self, url: str) -> LinkStatus:
        if url in self._cache:
            return self._cache[url]
        status = self.truth.get(url, "unknown")
        self._cache[url] = status
        return status


@dataclass
class StaticLinkResolver:
    """Test double: a fixed url->status map, default ``unknown``."""

    mapping: Mapping[str, LinkStatus]

    def resolve(self, url: str) -> LinkStatus:
        return self.mapping.get(url, "unknown")


@dataclass
class HttpLinkResolver:
    """Live resolver: HEAD, falling back to a ranged GET on
    403/405/501. Applies the C3 closed status map — only a confirmed
    404/410 is ``dead``; every other outcome, including a raised
    HttpError, is ``unknown``."""

    http: HttpClient
    user_agent: str
    timeout: float
    _cache: dict[str, LinkStatus] = field(default_factory=dict, init=False, repr=False)

    def resolve(self, url: str) -> LinkStatus:
        if url in self._cache:
            return self._cache[url]
        status = self._resolve_uncached(url)
        self._cache[url] = status
        return status

    def _resolve_uncached(self, url: str) -> LinkStatus:
        if not (url.startswith("http://") or url.startswith("https://")):
            return "unknown"
        headers = {"User-Agent": self.user_agent}
        try:
            response = self.http.get(url, headers=headers, timeout=self.timeout, method="HEAD")
        except HttpError:
            return "unknown"
        if response.status in _DEAD_STATUSES:
            return "dead"
        if response.status in (403, 405, 501):
            try:
                response = self.http.get(
                    url,
                    headers={**headers, "Range": "bytes=0-0"},
                    timeout=self.timeout,
                    method="GET",
                )
            except HttpError:
                return "unknown"
            if response.status in _DEAD_STATUSES:
                return "dead"
        if 200 <= response.status < 400:
            return "live"
        return "unknown"
