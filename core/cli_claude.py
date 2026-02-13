"""Claude Code CLI backend for AI Advisor.

Invokes `claude` CLI with --json-schema for structured output.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


async def invoke_claude_cli(
    payload_json: str,
    *,
    model: str,
    timeout: int,
    schema_path: Path,
    prompt_path: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    """Invoke claude CLI and parse the structured output.

    Uses subprocess.run in a thread with clean env (OAuth token, no API key).
    """
    schema_content = schema_path.read_text()
    prompt = f"Review the following trading state and provide your decisions:\n\n{payload_json}"

    cmd = [
        "claude",
        "-p", prompt,
        "--no-session-persistence",
        "--model", model,
        "--output-format", "json",
        "--json-schema", schema_content,
        "--system-prompt-file", str(prompt_path),
        "--allowedTools", "",
        "--max-turns", "2",
    ]

    logger.debug("Invoking claude CLI (model=%s, payload=%.1f KB)", model, len(payload_json) / 1024)

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            start_new_session=True,
            env=env,
        )
    except subprocess.TimeoutExpired:
        logger.warning("claude CLI subprocess timed out after %ds", timeout)
        return {"positions": [], "opportunities": []}

    if result.returncode != 0:
        logger.warning("claude CLI exit code %d: %s", result.returncode, (result.stderr or "")[:500])
        return {"positions": [], "opportunities": []}

    output = (result.stdout or "").strip()
    if not output:
        logger.warning("claude CLI returned empty output (stderr: %s)", (result.stderr or "")[:300])
        return {"positions": [], "opportunities": []}

    response = json.loads(output)

    # claude --output-format json wraps result in {"result": ..., "structured_output": ...}
    if isinstance(response, dict):
        if "structured_output" in response:
            parsed = response["structured_output"]
        elif "result" in response:
            result_val = response["result"]
            if isinstance(result_val, str):
                try:
                    parsed = json.loads(result_val)
                except json.JSONDecodeError:
                    parsed = response
            elif isinstance(result_val, dict):
                parsed = result_val
            else:
                parsed = response
        else:
            parsed = response
    else:
        parsed = response

    if not isinstance(parsed, dict):
        logger.warning("claude CLI returned non-dict: %s", type(parsed))
        return {"positions": [], "opportunities": []}

    return parsed
