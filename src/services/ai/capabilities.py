"""
AI Provider capability matrix.

Defines what capabilities each provider supports,
used for automatic fallback and feature availability checks.

COST POLICY:
  - Default models (DEFAULT_MODELS["text"]) MUST be the cheapest tier (nano/flash).
  - Expensive models (EXPENSIVE_MODELS) require explicit user approval.
  - Never use expensive models in configs, tests, or defaults without reason.
"""

from typing import TypedDict


class ProviderCapabilities(TypedDict):
    """Capabilities for a single provider."""

    text_generation: bool
    embeddings: bool
    vision: bool
    transcription: bool
    function_calling: bool


# Capability matrix for all supported providers (January 2026)
PROVIDER_CAPABILITIES: dict[str, ProviderCapabilities] = {
    "openai": {
        "text_generation": True,  # gpt-5-nano, gpt-5-mini, gpt-5.2
        "embeddings": True,  # text-embedding-3-small, text-embedding-3-large
        "vision": True,  # gpt-5-nano+ (built-in multimodal)
        "transcription": True,  # gpt-4o-mini-transcribe, whisper-1
        "function_calling": True,
    },
    "gemini": {
        "text_generation": True,  # gemini-3-flash-preview, gemini-3-pro-preview
        "embeddings": True,  # gemini-embedding-001 (768-dim, free)
        "vision": True,  # built into gemini-3-flash
        "transcription": False,
        "function_calling": True,
    },
    "grok": {
        "text_generation": True,  # grok-4-1-fast, grok-4
        "embeddings": False,
        "vision": True,  # grok-2-vision-1212
        "transcription": False,
        "function_calling": True,
    },
    "deepseek": {
        "text_generation": True,  # deepseek-v3.2, deepseek-r1-0528
        "embeddings": True,
        "vision": False,
        "transcription": False,
        "function_calling": True,
    },
    "anthropic": {
        "text_generation": True,  # claude-sonnet-4, claude-opus-4-5
        "embeddings": False,
        "vision": True,  # claude-sonnet-4+ (built-in)
        "transcription": False,
        "function_calling": True,
    },
}

# Default models for each provider (January 2026)
# IMPORTANT: "text" key MUST be the cheapest model (nano/flash tier).
# "text_premium" is available but requires explicit approval.
DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "openai": {
        "text": "gpt-5-nano",  # cheapest
        "text_premium": "gpt-5.2",  # EXPENSIVE — requires approval
        "embeddings": "text-embedding-3-small",
        "vision": "gpt-5-nano",  # nano has multimodal
        "transcription": "gpt-4o-mini-transcribe",
    },
    "gemini": {
        "text": "gemini-3-flash-preview",  # cheapest
        "text_premium": "gemini-3-pro-preview",  # EXPENSIVE — requires approval
        "embeddings": "gemini-embedding-001",  # free
        "vision": "gemini-3-flash-preview",
    },
    "grok": {
        "text": "grok-4-1-fast",
        "text_premium": "grok-4",  # EXPENSIVE — requires approval
        "vision": "grok-2-vision-1212",
    },
    "deepseek": {
        "text": "deepseek-v3.2",
        "text_reasoning": "deepseek-r1-0528",
    },
    "anthropic": {
        "text": "claude-sonnet-4",
        "text_premium": "claude-opus-4-5",  # EXPENSIVE — requires approval
        "vision": "claude-sonnet-4",
    },
}

# EXPENSIVE models — require explicit user approval before use.
# Never set these as defaults in config or code.
EXPENSIVE_MODELS = [
    # OpenAI
    "gpt-5.2",
    "gpt-5-mini",  # use gpt-5-nano instead
    "o4-mini",
    # Gemini
    "gemini-3-pro-preview",
    # Anthropic
    "claude-opus-4-5",
    # Grok
    "grok-4",
    # Embeddings
    "text-embedding-3-large",  # use text-embedding-3-small or gemini-embedding-001
]

# DEPRECATED models — do not use at all
DEPRECATED_MODELS = [
    "text-embedding-004",  # Gemini - shutdown 14 Jan 2026
    "gemini-2.0-flash",  # shutdown 31 Mar 2026
    "gemini-1.5-flash",  # already retired
    "gemini-1.5-pro",  # already retired
    "gpt-4o",  # replaced by gpt-5.2
    "gpt-4o-mini",  # replaced by gpt-5-mini
    "gpt-4.1",  # replaced by gpt-5.2
]


def get_providers_for_capability(capability: str) -> list[str]:
    """Get list of providers that support a capability."""
    return [
        provider for provider, caps in PROVIDER_CAPABILITIES.items() if caps.get(capability, False)
    ]


def provider_supports(provider: str, capability: str) -> bool:
    """Check if a provider supports a capability."""
    if provider not in PROVIDER_CAPABILITIES:
        return False
    return bool(PROVIDER_CAPABILITIES[provider].get(capability, False))
