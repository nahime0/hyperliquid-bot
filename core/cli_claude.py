"""Claude Code CLI backend for AI Advisor.

Invokes `claude` CLI with --json-schema for structured output.
Uses asyncio.create_subprocess_exec for proper timeout/cancellation.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

_EMPTY = {"positions": [], "opportunities": []}
_KILL_GRACE = 5  # seconds between SIGTERM and SIGKILL


async def _kill_process(proc: asyncio.subprocess.Process) -> None:
    """Kill a subprocess and its entire process group reliably."""
    if proc.returncode is not None:
        return  # already exited

    pid = proc.pid
    try:
        # Kill entire process group (start_new_session=True → pgid == pid)
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass

    # Give it a moment to exit gracefully
    try:
        await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE)
        return
    except asyncio.TimeoutError:
        pass

    # Force kill
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass

    try:
        await asyncio.wait_for(proc.wait(), timeout=2)
    except asyncio.TimeoutError:
        logger.error("Failed to kill claude CLI process (pid=%d) — may be orphaned", pid)


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

    Uses asyncio subprocess for proper timeout handling and process cleanup.
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
        logger.warning("claude CLI timed out after %ds — killing process", timeout)
        if proc is not None:
            await _kill_process(proc)
        return _EMPTY

    except asyncio.CancelledError:
        logger.warning("claude CLI call cancelled — killing process")
        if proc is not None:
            await _kill_process(proc)
        raise

    except Exception:
        logger.warning("claude CLI failed to launch", exc_info=True)
        if proc is not None:
            await _kill_process(proc)
        return _EMPTY

    if proc.returncode != 0:
        err = (stderr.decode(errors="replace") if stderr else "")[:500]
        logger.warning("claude CLI exit code %d: %s", proc.returncode, err)
        return _EMPTY

    output = (stdout.decode(errors="replace") if stdout else "").strip()
    if not output:
        err = (stderr.decode(errors="replace") if stderr else "")[:300]
        logger.warning("claude CLI returned empty output (stderr: %s)", err)
        return _EMPTY

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
        return _EMPTY

    return parsed
