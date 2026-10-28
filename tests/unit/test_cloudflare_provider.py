"""Tests for CloudflareProvider (Workers AI, OpenAI-compatible chat completions)."""

import json

import httpx
import pytest

from src.services.ai.base import AIProviderError, RateLimitError
from src.services.ai.providers.cloudflare import DEFAULT_MODEL, CloudflareProvider


def _provider(handler) -> CloudflareProvider:
    """A provider whose HTTP client answers through `handler` and records nothing else."""
    p = CloudflareProvider("cf-test-token", account_id="acc0123")
    p._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer cf-test-token"},
    )
    return p


def _ok(content: str | None = '{"a": 1}', finish_reason: str = "stop", **extra) -> dict:
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}
        ],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 300, "neurons": 44.6},
        **extra,
    }


class TestRequestShape:
    async def test_payload_url_and_auth(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=_ok())

        p = _provider(handler)
        await p.generate_text(
            "classify this",
            max_tokens=4096,
            temperature=0.2,
            response_mime_type="application/json",
        )

        req = seen[0]
        assert str(req.url) == (
            "https://api.cloudflare.com/client/v4/accounts/acc0123/ai/v1/chat/completions"
        )
        assert req.headers["Authorization"] == "Bearer cf-test-token"
        body = json.loads(req.content)
        assert body["model"] == DEFAULT_MODEL
        assert body["messages"] == [{"role": "user", "content": "classify this"}]
        # Workers AI reads `max_tokens`; OpenAI's newer name would be ignored and
        # leave a thinking model with its small default budget.
        assert body["max_tokens"] == 4096
        assert "max_completion_tokens" not in body
        assert body["temperature"] == 0.2
        assert body["response_format"] == {"type": "json_object"}

    async def test_no_json_mode_unless_asked(self):
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(200, json=_ok("plain"))

        await _provider(handler).generate_text("hi", system_prompt="be brief")

        assert "response_format" not in seen[0]
        assert seen[0]["messages"][0] == {"role": "system", "content": "be brief"}


class TestResponse:
    async def test_success_maps_usage(self):
        p = _provider(lambda _r: httpx.Response(200, json=_ok('{"x": true}')))

        result = await p.generate_text("q", model="@cf/meta/llama-x")

        assert result.text == '{"x": true}'
        assert result.provider == "cloudflare"
        assert result.model == "@cf/meta/llama-x"
        assert result.tokens_input == 1200
        assert result.tokens_output == 300
        assert result.finish_reason == "stop"

    async def test_empty_content_after_thinking_is_retriable(self):
        # Measured on Gemma 4: the budget can run out inside reasoning_content.
        p = _provider(lambda _r: httpx.Response(200, json=_ok(None, finish_reason="length")))

        with pytest.raises(AIProviderError, match="finish_reason=length") as exc:
            await p.generate_text("q")

        assert exc.value.retriable is True

    async def test_content_filter_is_not_retriable(self):
        p = _provider(lambda _r: httpx.Response(200, json=_ok("", finish_reason="content_filter")))

        with pytest.raises(AIProviderError) as exc:
            await p.generate_text("q")

        assert exc.value.retriable is False

    async def test_no_choices(self):
        p = _provider(lambda _r: httpx.Response(200, json={"choices": []}))

        with pytest.raises(AIProviderError, match="no choices"):
            await p.generate_text("q")


class TestErrors:
    async def test_rate_limit(self):
        p = _provider(lambda _r: httpx.Response(429, headers={"retry-after": "12"}, text="slow"))

        with pytest.raises(RateLimitError) as exc:
            await p.generate_text("q")

        assert exc.value.retry_after == 12.0

    async def test_server_error_is_retriable(self):
        p = _provider(lambda _r: httpx.Response(503, text="down"))

        with pytest.raises(AIProviderError, match="503") as exc:
            await p.generate_text("q")

        assert exc.value.retriable is True

    async def test_client_error_is_not_retriable(self):
        p = _provider(lambda _r: httpx.Response(401, text="bad token"))

        with pytest.raises(AIProviderError, match="401") as exc:
            await p.generate_text("q")

        assert exc.value.retriable is False

    async def test_timeout_is_retriable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        with pytest.raises(AIProviderError, match="timed out") as exc:
            await _provider(handler).generate_text("q")

        assert exc.value.retriable is True


class TestUnsupported:
    @pytest.mark.parametrize(
        ("method", "args"),
        [
            ("generate_embedding", ("text",)),
            ("analyze_image", (b"img", "describe")),
            ("transcribe_audio", (b"audio",)),
        ],
    )
    async def test_other_capabilities_refuse(self, method, args):
        p = _provider(lambda _r: httpx.Response(500))

        with pytest.raises(AIProviderError, match="does not serve") as exc:
            await getattr(p, method)(*args)

        assert exc.value.retriable is False
