"""ECB daily reference-rate resolution (dispatch q77-p3-a, binding
decision 3): the one authoritative USD-per-EUR rate used to convert
the run's EUR budget into a conservative SDK-facing USD allowance.

Resolved once per run, before the first model call, and recorded on
every ``agent_calls`` audit row (source, rate date, retrieval
timestamp, exact Decimal rate) — never invented, never cached across
runs, never silently defaulted. If no valid rate can be resolved the
run fails before any model call (``FxResolutionError``).

Deliberately does *not* reuse ``sentinel.net.client`` (that module is
outside this dispatch's authorized Sentinel write surface, and its
production ``UrllibHttpClient`` relies on the OS default certificate
store, which this machine's store does not complete for
``ecb.europa.eu`` even though it does for ``api.github.com`` —
confirmed live, 2026-08-05). This module owns a small, dedicated,
read-only fetch instead, verified with the actively-maintained
``certifi`` CA bundle (pinned as a direct ``agents`` dependency).
Parsing is separated from fetching so unit tests exercise the parser
against canned bytes and never touch the network (conftest.py's
autouse ``block_network`` fixture would fail any that tried).
"""

from __future__ import annotations

import ssl
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Callable

import certifi

ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
FX_SOURCE = "ecb-eurofxref-daily"
# The feed's own default XML namespace is ecb.int, not ecb.europa.eu —
# verified against the live document (2026-08-05); do not "correct"
# this to the .eu host without re-checking the live feed first.
_ECB_NS = {"ecb": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}
_USER_AGENT = "ai-portfolio-sentinel-phase3/1.0 (+read-only FX reference lookup)"


class FxResolutionError(RuntimeError):
    """No authoritative USD-per-EUR rate could be resolved. Per the
    binding decision, this fails the run closed — before any model
    call — never falling back to an invented, cached or stale rate."""


@dataclass(frozen=True)
class FxRate:
    source: str
    rate_date: str  # ECB's own reference date, e.g. "2026-08-05"
    retrieved_at_utc: datetime
    usd_per_eur: Decimal  # exact decimal, never a binary float


def fetch_ecb_daily_xml(*, timeout: float = 10.0) -> bytes:
    """The real network fetch: a single bounded-timeout GET verified
    against certifi's CA bundle. No retry — this is a once-per-run
    lookup of a document that updates once per ECB business day; a
    transient failure here correctly fails the run closed rather than
    masking a real connectivity problem with silent retries."""
    context = ssl.create_default_context(cafile=certifi.where())
    request = urllib.request.Request(ECB_DAILY_URL, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            if response.status != 200:
                raise FxResolutionError(
                    f"ECB reference-rate fetch returned HTTP {response.status}"
                )
            return response.read()
    except urllib.error.URLError as exc:
        raise FxResolutionError(f"ECB reference-rate fetch failed: {exc}") from exc
    except TimeoutError as exc:
        raise FxResolutionError(f"ECB reference-rate fetch timed out: {exc}") from exc
    except OSError as exc:
        raise FxResolutionError(f"ECB reference-rate fetch failed: {exc}") from exc


def parse_ecb_daily_xml(xml_bytes: bytes, *, now: datetime) -> FxRate:
    """Pure parsing/validation — no network access. Unit-testable
    against canned bytes."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise FxResolutionError(
            f"ECB reference-rate response is not valid XML: {exc}"
        ) from exc

    # <gesmes:Envelope>...<Cube><Cube time="YYYY-MM-DD"><Cube currency="USD" rate="1.xxxx"/>...
    time_cube = root.find(".//ecb:Cube[@time]", _ECB_NS)
    if time_cube is None or not time_cube.attrib.get("time"):
        raise FxResolutionError("ECB reference-rate response has no dated Cube element")
    rate_date = time_cube.attrib["time"]

    usd_rate_text: str | None = None
    for currency_cube in time_cube.findall("ecb:Cube[@currency='USD']", _ECB_NS):
        usd_rate_text = currency_cube.attrib.get("rate")
        break
    if not usd_rate_text:
        raise FxResolutionError("ECB reference-rate response has no USD entry")

    try:
        usd_per_eur = Decimal(usd_rate_text)
    except InvalidOperation as exc:
        raise FxResolutionError(f"ECB USD rate {usd_rate_text!r} is not a valid decimal") from exc
    if usd_per_eur <= 0:
        raise FxResolutionError(f"ECB USD rate {usd_rate_text!r} is not positive")

    return FxRate(
        source=FX_SOURCE,
        rate_date=rate_date,
        retrieved_at_utc=now,
        usd_per_eur=usd_per_eur,
    )


def resolve_ecb_usd_per_eur(
    *, now: datetime, timeout: float = 10.0, fetch: Callable[[], bytes] | None = None
) -> FxRate:
    """Fetch and parse in one call. ``fetch`` is an injectable seam for
    tests (defaults to the real network fetch); tests must always pass
    a canned ``fetch`` so no test ever reaches the live network."""
    fetch_fn = fetch or (lambda: fetch_ecb_daily_xml(timeout=timeout))
    xml_bytes = fetch_fn()
    return parse_ecb_daily_xml(xml_bytes, now=now)
