"""Anthropic SDK backend — uses tool_use for schema enforcement."""
from __future__ import annotations

import asyncio
from typing import Any

import anthropic

from utils.logger import get_logger

from .base import AIBackend

logger = get_logger(__name__)


class AnthropicSDKBackend(AIBackend):
    """Calls the Anthropic Messages API via the official Python SDK.

    Schema enforcement is achieved by declaring a ``tool`` whose
    ``input_schema`` matches the desired output format and forcing
    the model to call it with ``tool_choice``.
    """

    def __init__(self, api_key: str, default_model: str = "") -> None:
        self._api_key = api_key
        self._default_model = default_model
        self._client: anthropic.AsyncAnthropic | None = None

    async def start(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        logger.info("AnthropicSDKBackend started (model=%s)", self._default_model)

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None

    async def call(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        timeout: int,
        max_tokens: int,
        model: str | None = None,
    ) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Backend not started")

        model = model or self._default_model
        tool = {
            "name": "trading_decision",
            "description": "Submit your trading decision as structured JSON.",
            "input_schema": schema,
        }

        response = await asyncio.wait_for(
            self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                tools=[tool],
                tool_choice={"type": "tool", "name": "trading_decision"},
            ),
            timeout=timeout,
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "trading_decision":
                return block.input  # type: ignore[return-value]

        raise ValueError("No tool_use block in Anthropic response")
