"""
OpenAI Costs API client for cross-checking our calculated costs.

Uses GET /v1/organization/costs endpoint.

Requires an organization Admin API key — ``OPENAI_ADMIN_API_KEY``, not the
``OPENAI_API_KEY`` the providers use. The two are not interchangeable in either
direction: /v1/organization/* rejects a project key, and /v1/chat/completions
rejects an admin key. Create one at
https://platform.openai.com/settings/organization/admin-keys (owners only).

The endpoint reports ORGANIZATION-wide by default, so callers must also pass
``project_id`` (``OPENAI_PROJECT_ID``) to scope the figures to this bot. See
``get_costs`` for how that filter is verified rather than trusted.

Documented parameters used here (verified against the API reference):
``start_time`` (unix seconds, inclusive), ``bucket_width`` (only ``1d`` is
supported — sub-day comparison is impossible by design), ``limit`` (1..180
buckets), ``project_ids`` (filter), ``group_by`` (``project_id``, ``line_item``,
``api_key_id``) and ``page`` (cursor echoing the previous ``next_page``).

See: https://platform.openai.com/docs/api-reference/usage
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

_COSTS_URL = "https://api.openai.com/v1/organization/costs"

# The API caps `limit` at 180 buckets; anything above is rejected outright.
_MAX_BUCKETS = 180


def _as_timestamp(value: Any) -> int | None:
    """Coerce an untrusted bucket timestamp to a sane unix time, else None.

    Values here come straight off the wire. Callers do arithmetic with them, so
    a string (``"1700000000"``) would raise a TypeError halfway up the stack,
    and a negative value would silently turn into a multi-decade window.
    """
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    # Reject clearly impossible stamps rather than propagating them: 0 is the
    # struct default, negatives predate the epoch, and anything beyond ~2100 is
    # a unit mix-up (milliseconds) rather than a real bucket boundary.
    if ts <= 0 or ts > 4_102_444_800:
        return None
    return ts


@dataclass
class OpenAICostBucket:
    """Cost data for a single time bucket."""

    start_time: int
    end_time: int
    amount_usd: Decimal
    line_item: str | None = None
    project_id: str | None = None


@dataclass
class OpenAICostReport:
    """Aggregated cost report from OpenAI."""

    total_usd: Decimal
    buckets: list[OpenAICostBucket]
    error: str | None = None
    # Stable identifier for the failure, so callers can localise the message.
    # ``error`` stays as the English fallback for anything not in the map.
    error_code: str | None = None
    # Earliest bucket boundary in the response, taken from the raw buckets
    # rather than from `buckets` above: OpenAI keeps zero-spend days in the
    # payload with an empty `results` list, and those still count towards the
    # span the total covers. Callers comparing against their own numbers need
    # the real span, not the one they requested.
    covered_from: int | None = None
    # Distinct project_ids seen in the response, when grouping was honoured.
    project_ids_seen: set[str] = field(default_factory=set)


class OpenAIBillingClient:
    """Client for querying OpenAI's Costs API."""

    def __init__(self, api_key: str) -> None:
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def get_costs(self, days: int = 7, project_id: str | None = None) -> OpenAICostReport:
        """Query OpenAI costs for the last N days, scoped to one project.

        Returns an OpenAICostReport with total and per-bucket costs.
        On any error, returns a report with error message (never raises).

        When ``project_id`` is given the request both filters (``project_ids``)
        and groups (``group_by=project_id``). The grouping is not redundant: it
        makes each row carry the project it belongs to, which is the only way to
        tell a working filter from an ignored one. A silently ignored filter
        would return org-wide spend that looks like a plausible project figure,
        so any foreign project_id in the response is reported as an error
        instead of being averaged into the total.
        """
        start_time = int(time.time()) - (days * 86400)
        params: dict[str, Any] = {
            "start_time": start_time,
            "bucket_width": "1d",
            "limit": max(1, min(days, _MAX_BUCKETS)),
        }
        if project_id:
            params["project_ids"] = [project_id]
            params["group_by"] = ["project_id"]

        buckets: list[OpenAICostBucket] = []
        total = Decimal("0")
        covered_from: int | None = None
        project_ids_seen: set[str] = set()
        page_cursor: str | None = None

        try:
            while True:
                if page_cursor:
                    params["page"] = page_cursor

                resp = await self._client.get(_COSTS_URL, params=params)

                # 401 = the key itself is not accepted (wrong, revoked, typo);
                # 403 = the key is valid but is not an organization Admin key.
                # Distinct causes, distinct fixes — do not collapse them.
                if resp.status_code == 401:
                    return OpenAICostReport(
                        total_usd=Decimal("0"),
                        buckets=[],
                        error="OpenAI rejected the admin key (invalid or revoked)",
                        error_code="invalid_key",
                    )

                if resp.status_code == 403:
                    return OpenAICostReport(
                        total_usd=Decimal("0"),
                        buckets=[],
                        error="API key lacks billing access (admin key required)",
                        error_code="no_billing_access",
                    )

                if resp.status_code != 200:
                    return OpenAICostReport(
                        total_usd=Decimal("0"),
                        buckets=[],
                        error=f"OpenAI API error: HTTP {resp.status_code}",
                        error_code="http_error",
                    )

                data = resp.json()

                for bucket_data in data.get("data", []):
                    bucket_start = _as_timestamp(bucket_data.get("start_time"))
                    bucket_end = _as_timestamp(bucket_data.get("end_time"))
                    # Track the span before looking at results: a zero-spend day
                    # arrives with `results: []` yet still widens the window the
                    # total speaks for.
                    if bucket_start is not None:
                        covered_from = min(covered_from or bucket_start, bucket_start)

                    for result in bucket_data.get("results", []):
                        amount_raw = result.get("amount") or {}
                        try:
                            amount = Decimal(str(amount_raw.get("value", 0)))
                        except (InvalidOperation, TypeError, ValueError):
                            logger.warning(
                                "OpenAI billing: unparsable amount", raw=str(amount_raw)[:100]
                            )
                            continue
                        total += amount
                        row_project = result.get("project_id")
                        if row_project:
                            project_ids_seen.add(str(row_project))
                        buckets.append(
                            OpenAICostBucket(
                                start_time=bucket_start or 0,
                                end_time=bucket_end or 0,
                                amount_usd=amount,
                                line_item=result.get("line_item"),
                                project_id=row_project,
                            )
                        )

                page_cursor = data.get("next_page")
                if not page_cursor:
                    break

        except httpx.TimeoutException:
            return OpenAICostReport(
                total_usd=Decimal("0"),
                buckets=[],
                error="OpenAI billing API timeout",
                error_code="timeout",
            )
        except Exception as e:
            # str(e) is safe to log here: the credential travels in the
            # Authorization header, and the URL carries only start_time /
            # bucket_width / limit / project_ids. Revisit if auth ever moves
            # into the query string.
            logger.warning("OpenAI billing API error", error=str(e))
            return OpenAICostReport(
                total_usd=Decimal("0"),
                buckets=[],
                error=f"Unexpected error: {type(e).__name__}",
                error_code="unexpected",
            )

        # A filter that was accepted but not applied is the dangerous outcome:
        # the number looks right and is silently org-wide. Only a *foreign*
        # project_id proves that, so an all-null response (grouping ignored)
        # stays a pass — see `project_ids_seen` for what the caller can check.
        if project_id and project_ids_seen - {project_id}:
            logger.warning(
                "OpenAI billing: project filter ignored",
                expected=project_id,
                seen=sorted(project_ids_seen),
            )
            return OpenAICostReport(
                total_usd=Decimal("0"),
                buckets=[],
                error="OpenAI ignored the project filter — figures would be org-wide",
                error_code="project_filter_ignored",
            )

        return OpenAICostReport(
            total_usd=total,
            buckets=buckets,
            covered_from=covered_from,
            project_ids_seen=project_ids_seen,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
