"""Cursor Agent CLI backend for AI Advisor.

Invokes `agent` CLI with --output-format json.  Unlike claude CLI, `agent`
does not support --json-schema, so the schema is embedded in the prompt.
The output is wrapped in {"type":"result","result":"..."} and the result
string may contain markdown fences (```json ... ```).
Uses asyncio.create_subprocess_exec for proper timeout/cancellation.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n(.*?)\n\s*```", re.DOTALL)
_EMPTY = {"positions": [], "opportunities": []}
_KILL_GRACE = 5  # seconds between SIGTERM and SIGKILL


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


async def _kill_process(proc: asyncio.subprocess.Process) -> None:
    """Kill a subprocess and its entire process group reliably."""
    if proc.returncode is not None:
        return  # already exited

    pid = proc.pid
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass

    try:
        await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE)
        return
    except asyncio.TimeoutError:
        pass

    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass

    try:
        await asyncio.wait_for(proc.wait(), timeout=2)
    except asyncio.TimeoutError:
        logger.error("Failed to kill cursor agent process (pid=%d) — may be orphaned", pid)


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

    Uses asyncio subprocess for proper timeout handling and process cleanup.
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

    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )

    except asyncio.TimeoutError:
        logger.warning("cursor agent CLI timed out after %ds — killing process", timeout)
        if proc is not None:
            await _kill_process(proc)
        return _EMPTY

    except asyncio.CancelledError:
        logger.warning("cursor agent CLI call cancelled — killing process")
        if proc is not None:
            await _kill_process(proc)
        raise

    except Exception:
        logger.warning("cursor agent CLI failed to launch", exc_info=True)
        if proc is not None:
            await _kill_process(proc)
        return _EMPTY

    if proc.returncode != 0:
        err = (stderr.decode(errors="replace") if stderr else "")[:500]
        logger.warning("cursor agent CLI exit code %d: %s", proc.returncode, err)
        return _EMPTY

    output = (stdout.decode(errors="replace") if stdout else "").strip()
    if not output:
        err = (stderr.decode(errors="replace") if stderr else "")[:300]
        logger.warning("cursor agent CLI returned empty output (stderr: %s)", err)
        return _EMPTY

    logger.debug("cursor agent raw output (first 500 chars): %.500s", output)

    try:
        response = json.loads(output)
    except json.JSONDecodeError:
        logger.warning("cursor agent: stdout is not valid JSON (first 500 chars): %.500s", output)
        return _EMPTY

    # cursor agent wraps output in {"type":"result","subtype":"success","is_error":false,"result":"..."}
    if isinstance(response, dict) and response.get("type") == "result":
        if response.get("is_error"):
            logger.warning("cursor agent returned error: %s", str(response.get("result", ""))[:500])
            return _EMPTY
        result_val = response.get("result", "")
        if isinstance(result_val, str):
            cleaned = _strip_markdown_fences(result_val)
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError:
                logger.warning("cursor agent: failed to parse result string as JSON: %.200s", cleaned)
                return _EMPTY
        elif isinstance(result_val, dict):
            parsed = result_val
        else:
            logger.warning("cursor agent: unexpected result type: %s", type(result_val))
            return _EMPTY
    elif isinstance(response, dict):
        # Direct dict response (no wrapper)
        parsed = response
    else:
        logger.warning("cursor agent: unexpected response type: %s", type(response))
        return _EMPTY

    if not isinstance(parsed, dict):
        logger.warning("cursor agent returned non-dict: %s", type(parsed))
        return _EMPTY

    return parsed
