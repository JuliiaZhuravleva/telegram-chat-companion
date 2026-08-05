"""Tests for OpenAI billing API client."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.services.ai.billing import OpenAIBillingClient


@pytest.fixture
def client():
    return OpenAIBillingClient(api_key="sk-test-admin-key")


def _mock_response(
    status_code: int = 200,
    json_data: dict | None = None,
    text: str = "",
):
    kwargs: dict = {
        "status_code": status_code,
        "request": httpx.Request("GET", "https://example.com"),
    }
    if json_data is not None:
        kwargs["json"] = json_data
    else:
        kwargs["text"] = text
    return httpx.Response(**kwargs)


class TestGetCosts:
    """Tests for OpenAIBillingClient.get_costs()."""

    async def test_successful_single_page(self, client):
        mock_resp = _mock_response(
            json_data={
                "data": [
                    {
                        "start_time": 1700000000,
                        "end_time": 1700086400,
                        "results": [
                            {
                                "amount": {"value": 0.0042},
                                "line_item": "gpt-5-nano",
                            },
                            {
                                "amount": {"value": 0.0018},
                                "line_item": "whisper-1",
                            },
                        ],
                    }
                ],
                "next_page": None,
            }
        )

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            report = await client.get_costs(days=1)

        assert report.error is None
        assert report.total_usd == Decimal("0.0042") + Decimal("0.0018")
        assert len(report.buckets) == 2
        assert report.buckets[0].line_item == "gpt-5-nano"
        assert report.buckets[1].line_item == "whisper-1"

    async def test_empty_data(self, client):
        mock_resp = _mock_response(json_data={"data": [], "next_page": None})

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            report = await client.get_costs(days=1)

        assert report.error is None
        assert report.total_usd == Decimal("0")
        assert report.buckets == []

    async def test_http_403_returns_error(self, client):
        mock_resp = _mock_response(status_code=403, text="Forbidden")

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            report = await client.get_costs(days=1)

        assert report.error is not None
        assert "admin key" in report.error.lower() or "billing access" in report.error.lower()
        assert report.error_code == "no_billing_access"
        assert report.total_usd == Decimal("0")

    async def test_http_401_is_distinct_from_403(self, client):
        """A rejected key and a non-admin key need different advice."""
        mock_resp = _mock_response(status_code=401, text="Unauthorized")

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            report = await client.get_costs(days=1)

        assert report.error_code == "invalid_key"
        assert report.total_usd == Decimal("0")

    async def test_http_500_returns_error(self, client):
        mock_resp = _mock_response(status_code=500, text="Internal Server Error")

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            report = await client.get_costs(days=7)

        assert report.error is not None
        assert "500" in report.error
        assert report.error_code == "http_error"

    async def test_timeout_returns_error(self, client):
        with patch.object(
            client._client,
            "get",
            new_callable=AsyncMock,
            side_effect=httpx.TimeoutException("connection timed out"),
        ):
            report = await client.get_costs(days=1)

        assert report.error is not None
        assert "timeout" in report.error.lower()
        assert report.total_usd == Decimal("0")

    async def test_network_error_returns_error(self, client):
        with patch.object(
            client._client,
            "get",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            report = await client.get_costs(days=1)

        assert report.error is not None
        assert report.total_usd == Decimal("0")

    async def test_pagination(self, client):
        page1 = _mock_response(
            json_data={
                "data": [
                    {
                        "start_time": 1700000000,
                        "end_time": 1700086400,
                        "results": [{"amount": {"value": 0.01}, "line_item": "page1"}],
                    }
                ],
                "next_page": "cursor_abc",
            }
        )
        page2 = _mock_response(
            json_data={
                "data": [
                    {
                        "start_time": 1700086400,
                        "end_time": 1700172800,
                        "results": [{"amount": {"value": 0.02}, "line_item": "page2"}],
                    }
                ],
                "next_page": None,
            }
        )

        with patch.object(
            client._client,
            "get",
            new_callable=AsyncMock,
            side_effect=[page1, page2],
        ):
            report = await client.get_costs(days=7)

        assert report.error is None
        assert report.total_usd == Decimal("0.01") + Decimal("0.02")
        assert len(report.buckets) == 2


class TestCoveredFrom:
    """`covered_from` reports the span the buckets really cover."""

    async def test_returns_earliest_bucket_start(self, client):
        mock_resp = _mock_response(
            json_data={
                "data": [
                    {
                        "start_time": 1700086400,
                        "end_time": 1700172800,
                        "results": [{"amount": {"value": 0.01}}],
                    },
                    {
                        "start_time": 1700000000,
                        "end_time": 1700086400,
                        "results": [{"amount": {"value": 0.02}}],
                    },
                ],
                "next_page": None,
            }
        )

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            report = await client.get_costs(days=1)

        assert report.covered_from == 1700000000

    async def test_counts_zero_spend_days(self, client):
        """A day with no spend still widens the span the total speaks for."""
        mock_resp = _mock_response(
            json_data={
                "data": [
                    {"start_time": 1700000000, "end_time": 1700086400, "results": []},
                    {
                        "start_time": 1700086400,
                        "end_time": 1700172800,
                        "results": [{"amount": {"value": 0.01}}],
                    },
                ],
                "next_page": None,
            }
        )

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            report = await client.get_costs(days=2)

        assert report.covered_from == 1700000000
        assert len(report.buckets) == 1  # only the day that had spend

    async def test_none_when_no_buckets(self, client):
        mock_resp = _mock_response(json_data={"data": [], "next_page": None})

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            report = await client.get_costs(days=1)

        assert report.covered_from is None


class TestUntrustedResponseShape:
    """The response is external input; malformed fields must not escape."""

    @pytest.mark.parametrize(
        "bad_start",
        ["1700000000", None, -1, 0, "garbage", 1700000000000, {"a": 1}],
    )
    async def test_bad_start_time_never_reaches_arithmetic(self, client, bad_start):
        mock_resp = _mock_response(
            json_data={
                "data": [
                    {
                        "start_time": bad_start,
                        "end_time": 1700086400,
                        "results": [{"amount": {"value": 0.01}}],
                    }
                ],
                "next_page": None,
            }
        )

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            report = await client.get_costs(days=1)

        # Either a usable int or nothing — never a string, never negative.
        assert report.covered_from is None or (
            isinstance(report.covered_from, int) and report.covered_from > 0
        )
        assert report.error is None
        assert report.total_usd == Decimal("0.01")

    async def test_unparsable_amount_is_skipped_not_fatal(self, client):
        mock_resp = _mock_response(
            json_data={
                "data": [
                    {
                        "start_time": 1700000000,
                        "end_time": 1700086400,
                        "results": [
                            {"amount": {"value": "not-a-number"}},
                            {"amount": {"value": 0.05}},
                        ],
                    }
                ],
                "next_page": None,
            }
        )

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            report = await client.get_costs(days=1)

        assert report.error is None
        assert report.total_usd == Decimal("0.05")


class TestProjectScoping:
    """The project filter is verified against the response, not trusted."""

    @staticmethod
    def _payload(project_ids: list[str | None]):
        return {
            "data": [
                {
                    "start_time": 1700000000,
                    "end_time": 1700086400,
                    "results": [
                        {"amount": {"value": 0.01}, "project_id": pid} for pid in project_ids
                    ],
                }
            ],
            "next_page": None,
        }

    async def test_sends_filter_and_grouping(self, client):
        mock_resp = _mock_response(json_data=self._payload(["proj_bot"]))

        with patch.object(
            client._client, "get", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_get:
            await client.get_costs(days=1, project_id="proj_bot")

        params = mock_get.call_args.kwargs["params"]
        assert params["project_ids"] == ["proj_bot"]
        # Grouping is what makes an ignored filter detectable.
        assert params["group_by"] == ["project_id"]

    async def test_omits_filter_when_no_project(self, client):
        mock_resp = _mock_response(json_data=self._payload([None]))

        with patch.object(
            client._client, "get", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_get:
            await client.get_costs(days=1)

        params = mock_get.call_args.kwargs["params"]
        assert "project_ids" not in params
        assert "group_by" not in params

    async def test_foreign_project_aborts_instead_of_reporting(self, client):
        """An ignored filter returns org-wide money that looks plausible."""
        mock_resp = _mock_response(json_data=self._payload(["proj_bot", "proj_other"]))

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            report = await client.get_costs(days=1, project_id="proj_bot")

        assert report.error_code == "project_filter_ignored"
        assert report.total_usd == Decimal("0")

    async def test_own_project_only_passes(self, client):
        mock_resp = _mock_response(json_data=self._payload(["proj_bot", "proj_bot"]))

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            report = await client.get_costs(days=1, project_id="proj_bot")

        assert report.error is None
        assert report.project_ids_seen == {"proj_bot"}
        assert report.total_usd == Decimal("0.02")

    async def test_null_project_ids_pass_but_stay_unconfirmed(self, client):
        """Grouping dropped → cannot prove scoping either way; don't hard-fail."""
        mock_resp = _mock_response(json_data=self._payload([None, None]))

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            report = await client.get_costs(days=1, project_id="proj_bot")

        assert report.error is None
        assert report.project_ids_seen == set()


class TestLimitBounds:
    async def test_limit_stays_within_api_range(self, client):
        mock_resp = _mock_response(json_data={"data": [], "next_page": None})

        for days in (0, 1, 7, 365):
            with patch.object(
                client._client, "get", new_callable=AsyncMock, return_value=mock_resp
            ) as mock_get:
                await client.get_costs(days=days)
            limit = mock_get.call_args.kwargs["params"]["limit"]
            assert 1 <= limit <= 180, f"days={days} produced limit={limit}"

    async def test_limit_covers_bucket_alignment(self, client):
        """An N-day window straddles N+1 UTC-aligned buckets.

        A limit of exactly `days` made every ordinary call paginate.
        """
        mock_resp = _mock_response(json_data={"data": [], "next_page": None})

        for days in (1, 7):
            with patch.object(
                client._client, "get", new_callable=AsyncMock, return_value=mock_resp
            ) as mock_get:
                await client.get_costs(days=days)
            assert mock_get.call_args.kwargs["params"]["limit"] > days


class TestPaginationTermination:
    """The loop must end even when the API's cursor misbehaves."""

    @staticmethod
    def _page(cursor):
        return _mock_response(
            json_data={
                "data": [
                    {
                        "start_time": 1700000000,
                        "end_time": 1700086400,
                        "results": [{"amount": {"value": 0.01}}],
                    }
                ],
                "next_page": cursor,
            }
        )

    async def test_repeated_cursor_aborts(self, client):
        """Positive control: a cursor that never advances used to loop forever.

        Left unguarded it also re-adds the same page's money to the total on
        every turn, i.e. silently inflates the figure this feature exists to
        check.
        """
        calls = 0

        def never_advances(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            assert calls < 50, "pagination still unbounded"
            return self._page("SAME_CURSOR")

        with patch.object(
            client._client, "get", new_callable=AsyncMock, side_effect=never_advances
        ):
            report = await client.get_costs(days=1)

        assert report.error_code == "pagination_stuck"
        assert report.total_usd == Decimal("0")  # never a partial/inflated number
        assert calls <= 3

    async def test_page_cap_aborts_endless_distinct_cursors(self, client):
        """A cursor that always advances is just as unbounded."""
        calls = 0

        def always_new(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            assert calls < 100, "page cap not enforced"
            return self._page(f"cursor_{calls}")

        with patch.object(client._client, "get", new_callable=AsyncMock, side_effect=always_new):
            report = await client.get_costs(days=1)

        assert report.error_code == "pagination_stuck"
        assert calls <= 21

    async def test_normal_multipage_still_works(self, client):
        """Negative control: honest pagination must not trip the guard."""
        pages = [self._page("cursor_a"), self._page("cursor_b"), self._page(None)]

        with patch.object(client._client, "get", new_callable=AsyncMock, side_effect=pages):
            report = await client.get_costs(days=1)

        assert report.error is None
        assert report.total_usd == Decimal("0.03")
        assert len(report.buckets) == 3


class TestClose:
    async def test_close_calls_aclose(self, client):
        with patch.object(client._client, "aclose", new_callable=AsyncMock) as mock_close:
            await client.close()

        mock_close.assert_awaited_once()
