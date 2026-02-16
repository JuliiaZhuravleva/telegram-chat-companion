"""
OpenAI Costs API client for cross-checking our calculated costs.

Uses GET /v1/organization/costs endpoint.
Requires an Admin API key (not a regular API key).
See: https://platform.openai.com/docs/api-reference/usage
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

_COSTS_URL = "https://api.openai.com/v1/organization/costs"


@dataclass
class OpenAICostBucket:
    """Cost data for a single time bucket."""

    start_time: int
    end_time: int
    amount_usd: Decimal
    line_item: str | None = None


@dataclass
class OpenAICostReport:
    """Aggregated cost report from OpenAI."""

    total_usd: Decimal
    buckets: list[OpenAICostBucket]
    error: str | None = None


class OpenAIBillingClient:
    """Client for querying OpenAI's Costs API."""

    def __init__(self, api_key: str) -> None:
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def get_costs(self, days: int = 7) -> OpenAICostReport:
        """Query OpenAI costs for the last N days.

        Returns an OpenAICostReport with total and per-bucket costs.
        On any error, returns a report with error message (never raises).
        """
        start_time = int(time.time()) - (days * 86400)
        params: dict[str, Any] = {
            "start_time": start_time,
            "bucket_width": "1d",
            "limit": min(days, 30),
        }

        buckets: list[OpenAICostBucket] = []
        total = Decimal("0")
        page_cursor: str | None = None

        try:
            while True:
                if page_cursor:
                    params["page"] = page_cursor

                resp = await self._client.get(_COSTS_URL, params=params)

                if resp.status_code == 403:
                    return OpenAICostReport(
                        total_usd=Decimal("0"),
                        buckets=[],
                        error="API key lacks billing access (admin key required)",
                    )

                if resp.status_code != 200:
                    return OpenAICostReport(
                        total_usd=Decimal("0"),
                        buckets=[],
                        error=f"OpenAI API error: HTTP {resp.status_code}",
                    )

                data = resp.json()

                for bucket_data in data.get("data", []):
                    for result in bucket_data.get("results", []):
                        amount_raw = result.get("amount", {})
                        amount = Decimal(str(amount_raw.get("value", 0)))
                        total += amount
                        buckets.append(OpenAICostBucket(
                            start_time=bucket_data.get("start_time", 0),
                            end_time=bucket_data.get("end_time", 0),
                            amount_usd=amount,
                            line_item=result.get("line_item"),
                        ))

                page_cursor = data.get("next_page")
                if not page_cursor:
                    break

        except httpx.TimeoutException:
            return OpenAICostReport(
                total_usd=Decimal("0"),
                buckets=[],
                error="OpenAI billing API timeout",
            )
        except Exception as e:
            logger.warning("OpenAI billing API error", error=str(e))
            return OpenAICostReport(
                total_usd=Decimal("0"),
                buckets=[],
                error=f"Unexpected error: {type(e).__name__}",
            )

        return OpenAICostReport(total_usd=total, buckets=buckets)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
