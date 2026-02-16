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
        mock_resp = _mock_response(
            json_data={"data": [], "next_page": None}
        )

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
        assert report.total_usd == Decimal("0")

    async def test_http_500_returns_error(self, client):
        mock_resp = _mock_response(status_code=500, text="Internal Server Error")

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            report = await client.get_costs(days=7)

        assert report.error is not None
        assert "500" in report.error

    async def test_timeout_returns_error(self, client):
        with patch.object(
            client._client, "get", new_callable=AsyncMock,
            side_effect=httpx.TimeoutException("connection timed out"),
        ):
            report = await client.get_costs(days=1)

        assert report.error is not None
        assert "timeout" in report.error.lower()
        assert report.total_usd == Decimal("0")

    async def test_network_error_returns_error(self, client):
        with patch.object(
            client._client, "get", new_callable=AsyncMock,
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
            client._client, "get", new_callable=AsyncMock,
            side_effect=[page1, page2],
        ):
            report = await client.get_costs(days=7)

        assert report.error is None
        assert report.total_usd == Decimal("0.01") + Decimal("0.02")
        assert len(report.buckets) == 2


class TestClose:
    async def test_close_calls_aclose(self, client):
        with patch.object(client._client, "aclose", new_callable=AsyncMock) as mock_close:
            await client.close()

        mock_close.assert_awaited_once()
