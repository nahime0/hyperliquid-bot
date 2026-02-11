"""3-tier AI decision engine with pluggable backends and multi-pair analysis."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from config.settings import Settings
from data.db import Database
from utils.logger import get_logger

from .backends import AIBackend, create_backend
from .prescreen import PreScreen
from .types import Decision, Tier

logger = get_logger(__name__)

_FULL_PROMPT_PATH = "prompts/trading_decision.md"
_SCREENING_PROMPT_PATH = "prompts/screening_decision.md"
_REVIEW_PROMPT_PATH = "prompts/review_decision.md"
_FULL_SCHEMA_PATH = "schemas/decision.json"
_SCREENING_SCHEMA_PATH = "schemas/screening_decision.json"
_MULTI_SCREENING_SCHEMA_PATH = "schemas/multi_screening_decision.json"
_MULTI_DECISION_SCHEMA_PATH = "schemas/multi_decision.json"
_REVIEW_SCHEMA_PATH = "schemas/review_decision.json"

# Maximum number of Tier-3 escalations per cycle (controls cost)
_MAX_TIER3_ESCALATIONS = 3


class AIEngine:
    """3-tier AI decision engine: PreScreen -> Screening -> Analysis.

    Screening and analysis tiers each use a configurable :class:`AIBackend`.
    """

    def __init__(self, settings: Settings, db: Database) -> None:
        self._settings = settings
        self._db = db
        self._prescreen = PreScreen(settings, db)

        # Backends (created in start())
        self._screening_backend: AIBackend | None = None
        self._analysis_backend: AIBackend | None = None

        # Cached templates and schemas
        self._full_template: str | None = None
        self._screening_template: str | None = None
        self._review_template: str | None = None
        self._full_schema: dict[str, Any] | None = None
        self._screening_schema: dict[str, Any] | None = None
        self._multi_screening_schema: dict[str, Any] | None = None
        self._multi_decision_schema: dict[str, Any] | None = None
        self._review_schema: dict[str, Any] | None = None

    # ── Lifecycle ───────────────────────────────────────────

    async def start(self) -> None:
        """Initialize AI backends."""
        ai = self._settings.ai

        # Resolve effective models / timeouts (fallback to legacy fields)
        screening_model = ai.screening_model or ai.haiku_model
        screening_timeout = ai.screening_timeout or ai.haiku_timeout
        analysis_model = ai.analysis_model or ai.opus_model
        analysis_timeout = ai.analysis_timeout or ai.opus_timeout

        self._screening_backend = create_backend(
            ai.screening_backend,
            model=screening_model,
            api_key=ai.anthropic_api_key,
        )
        await self._screening_backend.start()

        # Share the same backend instance if both tiers use the same config
        if (
            ai.analysis_backend == ai.screening_backend
            and analysis_model == screening_model
        ):
            self._analysis_backend = self._screening_backend
        else:
            self._analysis_backend = create_backend(
                ai.analysis_backend,
                model=analysis_model,
                api_key=ai.anthropic_api_key,
            )
            await self._analysis_backend.start()

        logger.info(
            "AI engine started — screening: %s/%s, analysis: %s/%s",
            ai.screening_backend, screening_model,
            ai.analysis_backend, analysis_model,
        )

    async def close(self) -> None:
        """Shut down backends."""
        if self._screening_backend:
            await self._screening_backend.close()
        if self._analysis_backend and self._analysis_backend is not self._screening_backend:
            await self._analysis_backend.close()
        self._screening_backend = None
        self._analysis_backend = None
        logger.info("AI engine stopped")

    # ── Main entry point ────────────────────────────────────

    async def decide(
        self,
        snapshot: dict[str, Any],
        open_position_symbols: set[str] | None = None,
    ) -> list[Decision]:
        """Orchestrate 3-tier decision: PreScreen -> Screening -> Analysis.

        Returns a *list* of decisions (one per symbol with an opportunity).
        The list may be empty (equivalent to HOLD).
        """
        snapshot_hash = _compute_snapshot_hash(snapshot)
        held = open_position_symbols or set()
        balance = snapshot.get("risk_metrics", {}).get("current_balance", 0)

        try:
            # ── Tier 1: Pre-screening (0ms) ─────────────────
            passed, prescreen_decision = await self._prescreen.check(snapshot, snapshot_hash)
            if not passed and prescreen_decision is not None:
                await self._log_decision(prescreen_decision, snapshot_hash)
                return [prescreen_decision]

            # ── Tier 2: Screening (multi-pair) ──────────────
            candidates = await self._screen(snapshot)

            if not candidates:
                hold = Decision(
                    action="HOLD", confidence=1.0,
                    reasoning="Screening found no opportunities",
                    tier=Tier.HAIKU,
                )
                await self._log_decision(hold, snapshot_hash)
                return [hold]

            # Log all screening results
            for c in candidates:
                await self._log_decision(c, snapshot_hash)

            # Filter: only actionable (non-HOLD)
            actionable = [c for c in candidates if c.action != "HOLD"]
            if not actionable:
                return candidates  # all HOLDs

            # Validate SELL/CLOSE against held positions
            validated: list[Decision] = []
            for c in actionable:
                if c.action in ("SELL", "CLOSE"):
                    if c.symbol and c.symbol in held:
                        validated.append(c)
                    else:
                        logger.info(
                            "Screening %s %s — no position to close, skipping",
                            c.action, c.symbol or "-",
                        )
                else:
                    validated.append(c)

            if not validated:
                hold = Decision(
                    action="HOLD", confidence=0.5,
                    reasoning="All screening opportunities filtered out",
                    tier=Tier.HAIKU,
                )
                await self._log_decision(hold, snapshot_hash)
                return [hold]

            # ── Tier 3: Deep analysis per candidate ─────────
            # SELL/CLOSE can be executed directly (fast exit, no Opus needed)
            final: list[Decision] = []
            escalation_count = 0

            for c in validated:
                # Direct exit for SELL/CLOSE
                if c.action in ("SELL", "CLOSE"):
                    final.append(c)
                    continue

                # BUY with low confidence — skip
                if c.confidence < self._settings.ai.haiku_hold_confidence:
                    logger.info(
                        "Screening BUY %s confidence %.2f below %.2f — skipping",
                        c.symbol or "-", c.confidence,
                        self._settings.ai.haiku_hold_confidence,
                    )
                    continue

                # Skip Opus when balance < $500 (cost too high vs bankroll)
                if 0 < balance < 500:
                    logger.info(
                        "Balance $%.0f < $500 — accepting screening BUY directly",
                        balance,
                    )
                    final.append(c)
                    continue

                # Cap Tier 3 escalations
                if escalation_count >= _MAX_TIER3_ESCALATIONS:
                    logger.info(
                        "Tier-3 cap reached (%d) — accepting screening BUY for %s",
                        _MAX_TIER3_ESCALATIONS, c.symbol or "-",
                    )
                    final.append(c)
                    continue

                # Escalate to deep analysis
                logger.info(
                    "Escalating %s to analysis (confidence=%.2f)",
                    c.symbol or "-", c.confidence,
                )
                analysis = await self._analyze(snapshot, c.symbol, c.reasoning)
                await self._log_decision(analysis, snapshot_hash)
                escalation_count += 1

                # Enforce minimum confidence
                if analysis.action != "HOLD" and analysis.confidence < self._settings.ai.min_confidence:
                    logger.info(
                        "Analysis confidence %.2f below threshold %.2f — skipping",
                        analysis.confidence, self._settings.ai.min_confidence,
                    )
                    continue

                if analysis.action != "HOLD":
                    final.append(analysis)

            if not final:
                hold = Decision(
                    action="HOLD", confidence=0.5,
                    reasoning="No candidates survived analysis",
                    tier=Tier.OPUS,
                )
                await self._log_decision(hold, snapshot_hash)
                return [hold]

            return final

        except Exception as exc:
            logger.error("AI engine error: %s", exc)
            fallback = Decision(
                action=self._settings.ai.fallback_on_error,
                confidence=0.0,
                reasoning=f"AI error: {exc}",
                tier=Tier.FALLBACK,
            )
            await self._log_decision(fallback, snapshot_hash)
            return [fallback]

    # ── Tier 2: Screening (multi-pair) ────────────────────────

    async def _screen(self, snapshot: dict[str, Any]) -> list[Decision]:
        """Multi-pair screening — returns decisions for all pairs with opportunities."""
        if self._screening_backend is None:
            raise RuntimeError("AI engine not started")

        system_prompt, user_prompt = self._build_screening_prompts(snapshot)
        schema = self._load_multi_screening_schema()
        ai = self._settings.ai
        timeout = ai.screening_timeout or ai.haiku_timeout

        try:
            data = await self._screening_backend.call(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                timeout=timeout,
                max_tokens=1024,
            )
            decisions = _parse_multi_screening_response(data)
            for d in decisions:
                d.tier = Tier.HAIKU
            logger.info(
                "Screening returned %d decision(s): %s",
                len(decisions),
                ", ".join(f"{d.action} {d.symbol or '-'}" for d in decisions),
            )
            return decisions

        except Exception as exc:
            logger.warning("Screening failed: %s", exc, exc_info=True)
            return [Decision(
                action="UNKNOWN", confidence=0.0,
                reasoning=f"Screening error: {exc}", tier=Tier.HAIKU,
            )]

    # ── Tier 3: Analysis (per-symbol) ─────────────────────────

    async def _analyze(
        self,
        snapshot: dict[str, Any],
        symbol: str | None,
        screening_reasoning: str | None = None,
    ) -> Decision:
        """Deep analysis for a single symbol via the analysis backend."""
        if self._analysis_backend is None:
            raise RuntimeError("AI engine not started")

        system_prompt, user_prompt = self._build_full_prompts(snapshot)
        schema = self._load_full_schema()
        ai = self._settings.ai
        timeout = ai.analysis_timeout or ai.opus_timeout

        # Focus the analysis on the specific symbol
        if symbol:
            user_prompt += f"\n\n### Focus\nAnalizza specificamente il pair: **{symbol}**\n"
        if screening_reasoning:
            user_prompt += (
                f"\n\n### Analisi preliminare (screening)\n{screening_reasoning}\n\n"
                "Valuta questa analisi preliminare e fornisci la tua decisione completa."
            )

        try:
            data = await self._analysis_backend.call(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                timeout=timeout,
                max_tokens=1024,
            )
            decision = _parse_full_response(data)
            decision.tier = Tier.OPUS
            logger.info(
                "Analysis: %s %s (confidence=%.2f)",
                decision.action, decision.symbol or "-", decision.confidence,
            )
            return decision

        except asyncio.TimeoutError:
            timeout_val = timeout
            logger.warning("Analysis timed out after %ds", timeout_val)
            return Decision(
                action=self._settings.ai.fallback_on_error,
                confidence=0.0,
                reasoning=f"Analysis timeout after {timeout_val}s",
                tier=Tier.OPUS,
            )

        except Exception as exc:
            logger.error("Analysis failed: %s", exc)
            return Decision(
                action=self._settings.ai.fallback_on_error,
                confidence=0.0,
                reasoning=f"Analysis error: {exc}",
                tier=Tier.OPUS,
            )

    # ── Review mode (Phase 11) ────────────────────────────────

    async def review_decisions(
        self,
        candidates: list[Decision],
        snapshot: dict[str, Any],
    ) -> list[Decision]:
        """Review bot-generated candidates. AI can only veto BUYs.

        SELL/CLOSE decisions pass through automatically.
        On AI error: fail-open (approve all candidates).
        """
        if self._screening_backend is None:
            logger.warning("AI engine not started — approving all candidates")
            return candidates

        # Separate: SELLs always pass, only BUYs go to review
        sells = [d for d in candidates if d.action in ("SELL", "CLOSE")]
        buys = [d for d in candidates if d.action == "BUY"]

        if not buys:
            return candidates  # nothing to review

        try:
            review_result = await self._call_review(buys, snapshot)
            approved_symbols = set(review_result.get("approved", []))
            vetoed_map = {v["symbol"]: v["reason"] for v in review_result.get("vetoed", [])}

            approved_buys: list[Decision] = []
            for d in buys:
                if d.symbol in vetoed_map:
                    logger.info(
                        "AI VETO: %s — %s", d.symbol, vetoed_map[d.symbol],
                    )
                elif d.symbol in approved_symbols:
                    approved_buys.append(d)
                else:
                    # Not mentioned — approve by default (fail-open)
                    logger.debug("AI review: %s not mentioned — approving", d.symbol)
                    approved_buys.append(d)

            logger.info(
                "AI review: %d/%d BUYs approved, %d vetoed",
                len(approved_buys), len(buys), len(vetoed_map),
            )
            return sells + approved_buys

        except Exception as exc:
            logger.warning("AI review failed (%s) — fail-open, approving all", exc)
            return candidates

    async def _call_review(
        self,
        buys: list[Decision],
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        """Call the screening backend for a review decision."""
        template = self._load_review_template()
        system_prompt = _extract_section(template, "SYSTEM_PROMPT_START", "SYSTEM_PROMPT_END")
        user_prompt = _extract_section(template, "USER_PROMPT_START", "USER_PROMPT_END")

        # Format candidates
        candidates_text = "\n".join(
            f"- **{d.action} {d.symbol}** (confidence={d.confidence:.2f}): {d.reasoning}"
            for d in buys
        )
        user_prompt = user_prompt.replace("{candidates}", candidates_text)

        snapshot_json = json.dumps(snapshot, indent=2, default=str)
        user_prompt = user_prompt.replace("{market_snapshot}", snapshot_json)

        schema = self._load_review_schema()
        ai = self._settings.ai
        timeout = ai.screening_timeout or ai.haiku_timeout

        data = await self._screening_backend.call(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            timeout=timeout,
            max_tokens=512,
        )
        return data

    # ── Prompt building ─────────────────────────────────────

    def _build_full_prompts(self, snapshot: dict[str, Any]) -> tuple[str, str]:
        """Build system + user prompts for analysis (Tier 3)."""
        template = self._load_full_template()
        system_prompt = _extract_section(template, "SYSTEM_PROMPT_START", "SYSTEM_PROMPT_END")
        user_prompt = _extract_section(template, "USER_PROMPT_START", "USER_PROMPT_END")

        risk = self._settings.risk
        risk_rules = (
            f"- Massimo per singolo trade: {risk.max_trade_pct}% del bankroll\n"
            f"- Stop loss: -{risk.stop_loss_pct}% per trade\n"
            f"- Take profit: +{risk.take_profit_pct}% per trade\n"
            f"- Max drawdown giornaliero: -{risk.max_daily_drawdown_pct}% → pausa automatica\n"
            f"- Max drawdown totale: -{risk.max_total_drawdown_pct}% → kill switch\n"
            f"- Max posizioni aperte contemporanee: {risk.max_open_positions}\n"
            f"- Bilancio minimo: {risk.min_balance_usdc} USDC"
        )
        system_prompt = system_prompt.replace("{risk_rules}", risk_rules)

        snapshot_json = json.dumps(snapshot, indent=2, default=str)
        user_prompt = user_prompt.replace("{market_snapshot}", snapshot_json)

        return system_prompt, user_prompt

    def _build_screening_prompts(self, snapshot: dict[str, Any]) -> tuple[str, str]:
        """Build condensed prompts for screening (Tier 2)."""
        template = self._load_screening_template()
        system_prompt = _extract_section(template, "SYSTEM_PROMPT_START", "SYSTEM_PROMPT_END")
        user_prompt = _extract_section(template, "USER_PROMPT_START", "USER_PROMPT_END")

        snapshot_json = json.dumps(snapshot, indent=2, default=str)
        user_prompt = user_prompt.replace("{market_snapshot}", snapshot_json)

        return system_prompt, user_prompt

    # ── Template / schema loading ───────────────────────────

    def _load_full_template(self) -> str:
        if self._full_template is None:
            path = self._settings.project_root / _FULL_PROMPT_PATH
            self._full_template = path.read_text(encoding="utf-8")
        return self._full_template

    def _load_screening_template(self) -> str:
        if self._screening_template is None:
            path = self._settings.project_root / _SCREENING_PROMPT_PATH
            self._screening_template = path.read_text(encoding="utf-8")
        return self._screening_template

    def _load_full_schema(self) -> dict[str, Any]:
        if self._full_schema is None:
            path = self._settings.project_root / _FULL_SCHEMA_PATH
            self._full_schema = json.loads(path.read_text(encoding="utf-8"))
        return self._full_schema

    def _load_screening_schema(self) -> dict[str, Any]:
        if self._screening_schema is None:
            path = self._settings.project_root / _SCREENING_SCHEMA_PATH
            self._screening_schema = json.loads(path.read_text(encoding="utf-8"))
        return self._screening_schema

    def _load_multi_screening_schema(self) -> dict[str, Any]:
        if self._multi_screening_schema is None:
            path = self._settings.project_root / _MULTI_SCREENING_SCHEMA_PATH
            self._multi_screening_schema = json.loads(path.read_text(encoding="utf-8"))
        return self._multi_screening_schema

    def _load_multi_decision_schema(self) -> dict[str, Any]:
        if self._multi_decision_schema is None:
            path = self._settings.project_root / _MULTI_DECISION_SCHEMA_PATH
            self._multi_decision_schema = json.loads(path.read_text(encoding="utf-8"))
        return self._multi_decision_schema

    def _load_review_template(self) -> str:
        if self._review_template is None:
            path = self._settings.project_root / _REVIEW_PROMPT_PATH
            self._review_template = path.read_text(encoding="utf-8")
        return self._review_template

    def _load_review_schema(self) -> dict[str, Any]:
        if self._review_schema is None:
            path = self._settings.project_root / _REVIEW_SCHEMA_PATH
            self._review_schema = json.loads(path.read_text(encoding="utf-8"))
        return self._review_schema

    # ── DB logging ──────────────────────────────────────────

    async def _log_decision(self, decision: Decision, snapshot_hash: str) -> None:
        """Persist the decision to the database."""
        try:
            row_id = await self._db.insert_ai_decision(
                action=decision.action,
                confidence=decision.confidence,
                reasoning=decision.reasoning if self._settings.ai.log_reasoning else None,
                symbol=decision.symbol,
                snapshot_hash=snapshot_hash,
                raw_response=decision.raw_response if self._settings.ai.log_reasoning else None,
                tier=decision.tier.value,
            )
            logger.info(
                "Decision #%d [%s]: %s %s (confidence=%.2f)",
                row_id, decision.tier.value,
                decision.action, decision.symbol or "-", decision.confidence,
            )
        except Exception as exc:
            logger.error("Failed to log decision: %s", exc)


# ── Module-level helpers ────────────────────────────────────

def _compute_snapshot_hash(snapshot: dict[str, Any]) -> str:
    blob = json.dumps(snapshot, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def _extract_section(text: str, start_marker: str, end_marker: str) -> str:
    lines = text.splitlines()
    capturing = False
    result: list[str] = []
    for line in lines:
        if start_marker in line:
            capturing = True
            continue
        if end_marker in line:
            break
        if capturing:
            result.append(line)
    return "\n".join(result).strip()


def _parse_screening_response(data: dict[str, Any]) -> Decision:
    """Parse a single screening response (minimal fields)."""
    action = data.get("action", "HOLD")
    if action not in ("BUY", "SELL", "HOLD", "CLOSE"):
        action = "HOLD"

    return Decision(
        action=action,
        confidence=max(0.0, min(1.0, float(data.get("confidence", 0.0)))),
        reasoning=data.get("reasoning", "No reasoning"),
        symbol=data.get("symbol"),
    )


def _parse_multi_screening_response(data: dict[str, Any]) -> list[Decision]:
    """Parse multi-pair screening response (``{"decisions": [...]}``).

    Falls back to single-decision parsing if the response has no
    ``decisions`` array (backward compat with single-decision backends).
    """
    if "decisions" in data and isinstance(data["decisions"], list):
        decisions = []
        for item in data["decisions"]:
            decisions.append(_parse_screening_response(item))
        return decisions

    # Fallback: treat as single decision
    return [_parse_screening_response(data)]


def _parse_full_response(data: dict[str, Any]) -> Decision:
    """Parse a full analysis response (all fields)."""
    action = data.get("action", "HOLD")
    if action not in ("BUY", "SELL", "HOLD", "CLOSE"):
        action = "HOLD"

    return Decision(
        action=action,
        confidence=max(0.0, min(1.0, float(data.get("confidence", 0.0)))),
        reasoning=data.get("reasoning", "No reasoning"),
        symbol=data.get("symbol"),
        size_pct=data.get("size_pct"),
        order_type=data.get("order_type"),
        limit_price=data.get("limit_price"),
        stop_loss=data.get("stop_loss"),
        take_profit=data.get("take_profit"),
        strategy_type=data.get("strategy_type"),
        raw_response=json.dumps(data),
    )
