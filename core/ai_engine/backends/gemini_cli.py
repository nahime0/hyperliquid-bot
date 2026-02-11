"""Gemini CLI backend — invokes ``gemini`` as a subprocess."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from utils.logger import get_logger

from .base import AIBackend

logger = get_logger(__name__)

# Strip markdown fences that Gemini sometimes wraps around JSON
_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort extraction of JSON from Gemini output."""
    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown code fences
    m = _FENCE_RE.search(text)
    if m:
        return json.loads(m.group(1))

    raise json.JSONDecodeError("Cannot extract JSON from Gemini output", text, 0)


class GeminiCLIBackend(AIBackend):
    """Runs ``gemini`` CLI — no native ``--json-schema``, so the schema
    is embedded in the prompt with strict formatting instructions.

    Uses ``-p`` for non-interactive prompt, ``-o json`` for JSON output.
    """

    def __init__(self, default_model: str = "") -> None:
        self._default_model = default_model

    async def start(self) -> None:
        logger.info("GeminiCLIBackend ready (model=%s)", self._default_model)

    async def close(self) -> None:
        pass

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
        model = model or self._default_model

        # Embed schema in prompt since Gemini CLI has no --json-schema
        schema_instruction = (
            "\n\nIMPORTANT: You MUST respond with ONLY valid JSON matching this schema, "
            "no markdown fences, no explanation:\n"
            f"```json\n{json.dumps(schema, indent=2)}\n```\n"
        )
        full_prompt = system_prompt + "\n\n" + user_prompt + schema_instruction

        cmd = ["gemini", "-p", full_prompt, "-o", "json"]
        if model:
            cmd += ["-m", model]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            raise RuntimeError(f"gemini CLI exited {proc.returncode}: {err[:500]}")

        raw = stdout.decode(errors="replace").strip()

        # Gemini -o json wraps output in {"response": "...", "session_id": ..., "stats": ...}
        data = _extract_json(raw)
        if isinstance(data, dict) and "response" in data:
            inner = data["response"]
            if isinstance(inner, str):
                return _extract_json(inner)
            if isinstance(inner, dict):
                return inner

        return data
