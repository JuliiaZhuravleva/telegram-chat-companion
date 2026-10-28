"""Tests for src.services.ai.capabilities — capability matrix lookups."""

from src.services.ai.capabilities import (
    DEFAULT_MODELS,
    DEPRECATED_MODELS,
    EXPENSIVE_MODELS,
    PROVIDER_CAPABILITIES,
    get_providers_for_capability,
    provider_for_model,
    provider_supports,
)


class TestProviderCapabilities:
    """Verify the capability matrix is well-formed."""

    EXPECTED_KEYS = {"text_generation", "embeddings", "vision", "transcription", "function_calling"}
    EXPECTED_PROVIDERS = {"openai", "gemini", "grok", "deepseek", "anthropic", "cloudflare"}

    def test_known_providers_exist(self):
        assert set(PROVIDER_CAPABILITIES.keys()) == self.EXPECTED_PROVIDERS

    def test_all_providers_have_all_capability_keys(self):
        for provider, caps in PROVIDER_CAPABILITIES.items():
            assert set(caps.keys()) == self.EXPECTED_KEYS, f"{provider} missing keys"

    def test_all_capability_values_are_bool(self):
        for provider, caps in PROVIDER_CAPABILITIES.items():
            for key, value in caps.items():
                assert isinstance(value, bool), f"{provider}.{key} is not bool"


class TestGetProvidersForCapability:
    """Tests for get_providers_for_capability()."""

    def test_text_generation_returns_all_five_vendors(self):
        providers = get_providers_for_capability("text_generation")
        assert len(providers) == 5

    def test_an_opt_in_provider_is_never_a_default_chain_member(self):
        # This list is the chain a task with no config walks. Cloudflare serves
        # text, but only to a caller naming an @cf/ model -- a chat reply must
        # never silently degrade to it.
        assert "cloudflare" not in get_providers_for_capability("text_generation")
        assert provider_supports("cloudflare", "text_generation") is True

    def test_transcription_returns_only_openai(self):
        providers = get_providers_for_capability("transcription")
        assert providers == ["openai"]

    def test_embeddings_excludes_grok_and_anthropic(self):
        providers = get_providers_for_capability("embeddings")
        assert "grok" not in providers
        assert "anthropic" not in providers

    def test_unknown_capability_returns_empty(self):
        assert get_providers_for_capability("telepathy") == []


class TestProviderSupports:
    """Tests for provider_supports()."""

    def test_openai_supports_transcription(self):
        assert provider_supports("openai", "transcription") is True

    def test_gemini_does_not_support_transcription(self):
        assert provider_supports("gemini", "transcription") is False

    def test_unknown_provider_returns_false(self):
        assert provider_supports("unknown_provider", "text_generation") is False

    def test_unknown_capability_returns_false(self):
        assert provider_supports("openai", "unknown_cap") is False

    def test_anthropic_supports_vision(self):
        assert provider_supports("anthropic", "vision") is True

    def test_deepseek_does_not_support_vision(self):
        assert provider_supports("deepseek", "vision") is False


class TestDefaultModels:
    """Verify DEFAULT_MODELS structure."""

    def test_all_providers_in_capabilities_have_default_models(self):
        for provider in PROVIDER_CAPABILITIES:
            assert provider in DEFAULT_MODELS, f"{provider} missing from DEFAULT_MODELS"

    def test_all_providers_have_text_model(self):
        for provider, models in DEFAULT_MODELS.items():
            assert "text" in models, f"{provider} missing 'text' default model"


class TestDeprecatedModels:
    """Verify DEPRECATED_MODELS is a non-empty list of strings."""

    def test_is_list_of_strings(self):
        assert isinstance(DEPRECATED_MODELS, list)
        for model in DEPRECATED_MODELS:
            assert isinstance(model, str)

    def test_deprecated_models_not_in_defaults(self):
        all_default_models = set()
        for models in DEFAULT_MODELS.values():
            all_default_models.update(models.values())
        for deprecated in DEPRECATED_MODELS:
            assert deprecated not in all_default_models, (
                f"Deprecated model {deprecated} is still in DEFAULT_MODELS"
            )


class TestExpensiveModels:
    """Cost policy: expensive models must not be used as defaults."""

    def test_default_text_models_are_not_expensive(self):
        """The 'text' key (default model) must always be cheap."""
        for provider, models in DEFAULT_MODELS.items():
            text_model = models.get("text")
            assert text_model not in EXPENSIVE_MODELS, (
                f"{provider} default text model '{text_model}' is in EXPENSIVE_MODELS"
            )

    def test_default_embeddings_are_not_expensive(self):
        for provider, models in DEFAULT_MODELS.items():
            embed_model = models.get("embeddings")
            if embed_model:
                assert embed_model not in EXPENSIVE_MODELS, (
                    f"{provider} default embeddings '{embed_model}' is in EXPENSIVE_MODELS"
                )

    def test_default_vision_models_are_not_expensive(self):
        for provider, models in DEFAULT_MODELS.items():
            vision_model = models.get("vision")
            if vision_model:
                assert vision_model not in EXPENSIVE_MODELS, (
                    f"{provider} default vision '{vision_model}' is in EXPENSIVE_MODELS"
                )

    def test_expensive_list_is_nonempty(self):
        assert len(EXPENSIVE_MODELS) > 0


class TestProviderForModel:
    """A named model is routed to the provider that owns the name."""

    def test_every_default_model_belongs_to_its_own_provider(self):
        for provider, models in DEFAULT_MODELS.items():
            for model in models.values():
                assert provider_for_model(model) == provider, f"{model} not mapped to {provider}"

    def test_the_models_callers_name(self):
        assert provider_for_model("gpt-5-nano") == "openai"
        assert provider_for_model("o4-mini") == "openai"

    def test_an_unknown_name_has_no_owner(self):
        assert provider_for_model("mock-model") is None
        assert provider_for_model("") is None
