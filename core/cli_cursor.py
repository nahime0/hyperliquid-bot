"""Cursor Agent CLI backend for AI Advisor.

Invokes `agent` CLI with --output-format json.  Unlike claude CLI, `agent`
does not support --json-schema, so the schema is embedded in the prompt.
The output is wrapped in {"type":"result","result":"..."} and the result
string may contain markdown fences (```json ... ```).
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n(.*?)\n\s*```", re.DOTALL)


def _strip_markdown_fences(text: str) -> str:
    """Extract JSON from markdown code fences if present.

    Handles: ```json ... ```, ``` ... ```, ```JSON ... ```,
    and text before/after fences.  Falls back to finding the
    outermost { ... } or [ ... ] if no fences found.
    """
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()

    # No fences — try to extract raw JSON object/array
    stripped = text.strip()
    # Find first { and last } for object
    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        return stripped[first_brace:last_brace + 1]
    # Find first [ and last ] for array
    first_bracket = stripped.find("[")
    last_bracket = stripped.rfind("]")
    if first_bracket != -1 and last_bracket > first_bracket:
        return stripped[first_bracket:last_bracket + 1]

    return stripped


async def invoke_cursor_cli(
    payload_json: str,
    *,
    model: str,
    timeout: int,
    schema_path: Path,
    prompt_path: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    """Invoke cursor agent CLI and parse the output.

    The prompt embeds the system prompt + JSON schema + payload since
    cursor agent doesn't support --json-schema or --system-prompt-file.
    """
    system_prompt = prompt_path.read_text()
    schema_content = schema_path.read_text()

    prompt = (
        f"{system_prompt}\n\n"
        f"## Output Format\n\n"
        f"You MUST respond with valid JSON matching this schema (no markdown, no commentary):\n\n"
        f"```json\n{schema_content}\n```\n\n"
        f"## Current Trading State\n\n"
        f"Review the following and provide your decisions:\n\n{payload_json}"
    )

    cmd = [
        "agent",
        "-p", prompt,
        "--model", model,
        "--output-format", "json",
    ]

    logger.debug("Invoking cursor agent CLI (model=%s, payload=%.1f KB)", model, len(payload_json) / 1024)

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
        logger.warning("cursor agent CLI subprocess timed out after %ds", timeout)
        return {"positions": [], "opportunities": []}

    if result.returncode != 0:
        logger.warning("cursor agent CLI exit code %d: %s", result.returncode, (result.stderr or "")[:500])
        return {"positions": [], "opportunities": []}

    output = (result.stdout or "").strip()
    if not output:
        logger.warning("cursor agent CLI returned empty output (stderr: %s)", (result.stderr or "")[:300])
        return {"positions": [], "opportunities": []}

    response = json.loads(output)

    # cursor agent wraps output in {"type":"result","subtype":"success","is_error":false,"result":"..."}
    if isinstance(response, dict) and response.get("type") == "result":
        if response.get("is_error"):
            logger.warning("cursor agent returned error: %s", str(response.get("result", ""))[:500])
            return {"positions": [], "opportunities": []}
        result_val = response.get("result", "")
        if isinstance(result_val, str):
            cleaned = _strip_markdown_fences(result_val)
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError:
                logger.warning("cursor agent: failed to parse result string as JSON: %.200s", cleaned)
                return {"positions": [], "opportunities": []}
        elif isinstance(result_val, dict):
            parsed = result_val
        else:
            logger.warning("cursor agent: unexpected result type: %s", type(result_val))
            return {"positions": [], "opportunities": []}
    elif isinstance(response, dict):
        # Direct dict response (no wrapper)
        parsed = response
    else:
        logger.warning("cursor agent: unexpected response type: %s", type(response))
        return {"positions": [], "opportunities": []}

    if not isinstance(parsed, dict):
        logger.warning("cursor agent returned non-dict: %s", type(parsed))
        return {"positions": [], "opportunities": []}

    return parsed
