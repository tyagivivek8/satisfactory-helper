from __future__ import annotations

from typing import Final

SUPPORTED_CODEX_CLI_VERSION: Final = "0.151.0"

PROVIDER_MODELS: Final[dict[str, tuple[dict[str, str], ...]]] = {
    "codex": (
        {"id": "", "label": "Automatic (Codex default)"},
        {"id": "gpt-5.6", "label": "GPT-5.6"},
        {"id": "gpt-5.6-sol", "label": "GPT-5.6 Sol"},
        {"id": "gpt-5.6-terra", "label": "GPT-5.6 Terra"},
        {"id": "gpt-5.6-luna", "label": "GPT-5.6 Luna"},
        {"id": "gpt-5.3-codex", "label": "GPT-5.3 Codex"},
    ),
    "claude": (
        {"id": "", "label": "Automatic (Claude default)"},
        {"id": "opus", "label": "Claude Opus"},
        {"id": "sonnet", "label": "Claude Sonnet"},
        {"id": "haiku", "label": "Claude Haiku"},
        {"id": "fable", "label": "Claude Fable"},
    ),
}


def codex_version_is_supported(output: str) -> bool:
    return output.strip() in {
        SUPPORTED_CODEX_CLI_VERSION,
        f"codex-cli {SUPPORTED_CODEX_CLI_VERSION}",
    }


def model_is_allowed(provider: str, model: str | None) -> bool:
    if not model:
        return True
    return any(option["id"] == model for option in PROVIDER_MODELS.get(provider, ()))

