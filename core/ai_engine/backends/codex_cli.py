"""Codex CLI backend — invokes ``codex exec`` as a subprocess."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from utils.logger import get_logger

from .base import AIBackend

logger = get_logger(__name__)


class CodexCLIBackend(AIBackend):
    """Runs ``codex exec`` with ``--output-schema`` for schema enforcement.

    ``--output-schema`` requires a file path, so the schema is written
    to a temp file before invocation.  Codex ``--json`` outputs JSONL
    events on stdout — we parse line by line and extract the final
    message content.
    """

    def __init__(self, default_model: str = "") -> None:
        self._default_model = default_model

    async def start(self) -> None:
        logger.info("CodexCLIBackend ready (model=%s)", self._default_model)

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

        full_prompt = f"{system_prompt}\n\n{user_prompt}"

        # Write schema to temp file (codex --output-schema expects a path)
        schema_file = Path(tempfile.mktemp(suffix=".json", prefix="codex_schema_"))
        schema_file.write_text(json.dumps(schema))

        try:
            cmd = [
                "codex", "exec",
                "--json",
                "--output-schema", str(schema_file),
                "--full-auto",
                "--skip-git-repo-check",
            ]
            if model:
                cmd += ["-m", model]
            # Prompt via stdin (pass "-" or just pipe it)
            cmd.append("-")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=full_prompt.encode()),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                raise

            if proc.returncode != 0:
                err = stderr.decode(errors="replace").strip()
                raise RuntimeError(f"codex CLI exited {proc.returncode}: {err[:500]}")

            # Codex --json outputs JSONL events; the last message event
            # contains the assistant's reply.
            raw = stdout.decode(errors="replace").strip()
            last_content: str | dict | None = None

            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if isinstance(event, dict):
                    # Look for message events with assistant content
                    if event.get("type") == "message":
                        last_content = event.get("content", "")
                    elif event.get("role") == "assistant":
                        last_content = event.get("content", "")

            if last_content is None:
                # Fallback: try parsing entire output as JSON
                return json.loads(raw)

            if isinstance(last_content, str):
                return json.loads(last_content)
            if isinstance(last_content, dict):
                return last_content

            raise ValueError("Could not extract JSON from codex output")

        finally:
            # Clean up temp schema file
            schema_file.unlink(missing_ok=True)
