"""Claude CLI backend — invokes ``claude -p`` as a subprocess."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from utils.logger import get_logger

from .base import AIBackend

logger = get_logger(__name__)


class ClaudeCLIBackend(AIBackend):
    """Runs ``claude -p`` with ``--json-schema`` for native schema enforcement.

    The prompt is piped via stdin; the model replies with JSON that
    matches the provided schema (enforced by Claude Code itself).
    """

    def __init__(self, default_model: str = "") -> None:
        self._default_model = default_model

    async def start(self) -> None:
        logger.info("ClaudeCLIBackend ready (model=%s)", self._default_model)

    async def close(self) -> None:
        pass  # nothing to clean up

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
        schema_str = json.dumps(schema)

        cmd = [
            "claude", "-p",
            "--output-format", "json",
            "--json-schema", schema_str,
            "--system-prompt", system_prompt,
        ]
        if model:
            cmd += ["--model", model]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=user_prompt.encode()),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            raise RuntimeError(f"claude CLI exited {proc.returncode}: {err[:500]}")

        raw = stdout.decode(errors="replace").strip()
        data = json.loads(raw)

        # claude --output-format json with --json-schema puts the
        # schema-validated output in "structured_output"
        if isinstance(data, dict) and "structured_output" in data:
            so = data["structured_output"]
            if isinstance(so, str):
                return json.loads(so)
            return so

        # Fallback: check "result" field
        if isinstance(data, dict) and "result" in data:
            result = data["result"]
            if isinstance(result, str):
                try:
                    return json.loads(result)
                except json.JSONDecodeError:
                    pass

        return data
