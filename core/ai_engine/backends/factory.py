"""Factory function to create AI backends by name."""
from __future__ import annotations

from .base import AIBackend


def create_backend(
    name: str,
    *,
    model: str = "",
    api_key: str = "",
) -> AIBackend:
    """Instantiate an :class:`AIBackend` by its short name.

    Supported names: ``anthropic_sdk``, ``claude_cli``, ``gemini_cli``, ``codex_cli``.
    """
    name = name.strip().lower()

    if name == "anthropic_sdk":
        from .anthropic_sdk import AnthropicSDKBackend

        return AnthropicSDKBackend(api_key=api_key, default_model=model)

    if name == "claude_cli":
        from .claude_cli import ClaudeCLIBackend

        return ClaudeCLIBackend(default_model=model)

    if name == "gemini_cli":
        from .gemini_cli import GeminiCLIBackend

        return GeminiCLIBackend(default_model=model)

    if name == "codex_cli":
        from .codex_cli import CodexCLIBackend

        return CodexCLIBackend(default_model=model)

    raise ValueError(
        f"Unknown AI backend: {name!r}. "
        "Supported: anthropic_sdk, claude_cli, gemini_cli, codex_cli"
    )
