"""Abstract base class for AI backends."""
from __future__ import annotations

import abc
from typing import Any


class AIBackend(abc.ABC):
    """Interface that every AI backend must implement.

    Lifecycle: ``start()`` → N × ``call()`` → ``close()``.
    """

    @abc.abstractmethod
    async def start(self) -> None:
        """Initialise any resources (API clients, connections)."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Release resources."""

    @abc.abstractmethod
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
        """Send a prompt and return a schema-conforming JSON dict.

        Parameters
        ----------
        system_prompt : str
            System-level instructions.
        user_prompt : str
            User-level message containing the market snapshot.
        schema : dict
            JSON-Schema the output must conform to.
        timeout : int
            Maximum seconds to wait for a response.
        max_tokens : int
            Token budget for the response.
        model : str | None
            Override the default model for this call.

        Returns
        -------
        dict
            Parsed JSON matching *schema*.
        """
