"""Hyperliquid Perpetual Trading Bot — main entry point.

Architecture:
  Loop → Strategy generates candidates → AI advisor reviews → Risk validates → Execute

The bot trades autonomously with codified rules. Supports multiple strategies:
  - mean_reversion: Bollinger + RSI on 15m (original)
  - rsi_div: RSI Divergence on 5m (swing detection)
  - multi: Both strategies merged (default)

AI advisor (Claude Code CLI) is optional: reviews positions and opportunities,
can approve/defer/adjust entries, close/adjust positions.

Usage:
    .venv/bin/python main.py                     # multi-strategy (default)
    .venv/bin/python main.py --strategy rsi_div  # RSI Divergence only
    .venv/bin/python main.py --strategy mean_reversion  # legacy mean reversion
    .venv/bin/python main.py --live               # live (mainnet) trading
    .venv/bin/python main.py --paper              # paper trading (log only, no orders)
    .venv/bin/python main.py --no-ai              # no AI review (pure rule-based)
    .venv/bin/python main.py --once               # run one cycle then exit
    .venv/bin/python main.py --no-dashboard       # disable web dashboard
    .venv/bin/python main.py --dashboard-port 9090
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.pairs import ALL_COINS, CORE_COINS, discover_perp_coins
from config.settings import Settings, load_settings
from core.ai_advisor import AIAdvisor
from core.client import HyperliquidClient
from core.market_data import MarketData
from core.types import Decision
from data.db import Database
from risk.position_tracker import PositionTracker
from risk.risk_manager import RiskManager, ValidationResult
from strategies.base import Strategy
from strategies.cooldown import CooldownTracker
from strategies.mean_reversion import MeanReversionStrategy
from strategies.multi_strategy import MultiStrategy
from strategies.rsi_divergence import RSIDivergenceStrategy
from strategies.trend_filter import TrendFilter
from aiohttp import web

from dashboard.server import create_app as create_dashboard
from utils.logger import setup_logging, get_logger
from utils.telegram import TelegramNotifier

logger = get_logger(__name__)

# Dashboard
DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 8080

# Balance snapshot interval (seconds)
BALANCE_SNAPSHOT_INTERVAL = 300  # 5 minutes

# Funding rate refresh interval (cycles)
FUNDING_REFRESH_INTERVAL = 5  # every 5 cycles (~5 min)


class Bot:
    """Top-level orchestrator that owns all components."""

    def __init__(
        self,
        settings: Settings,
        *,
        paper: bool = False,
        no_ai: bool = False,
        once: bool = False,
        dashboard_port: int = DASHBOARD_PORT,
        no_dashboard: bool = False,
        strategy_mode: str = "multi",
    ) -> None:
        self._settings = settings
        self._paper = paper
        self._no_ai = no_ai
        self._once = once
        self._running = False
        self._dashboard_port = dashboard_port
        self._no_dashboard = no_dashboard
        self._strategy_mode = strategy_mode

        # Components (initialised in start())
        self._client = HyperliquidClient(settings)
        self._db = Database(settings.db_path)
        self._market_data = MarketData(self._client, settings)
        self._advisor = AIAdvisor(model=settings.ai.model, timeout=settings.ai.timeout)
        self._positions = PositionTracker(self._db, settings.risk)
        self._risk = RiskManager(settings.risk, self._client, self._db, self._positions, market_config=settings.market)

        # Autonomous components
        self._trend_filter = TrendFilter(self._market_data)
        self._cooldown = CooldownTracker(settings.risk)

        self._mean_rev = MeanReversionStrategy(
            self._market_data,
            self._trend_filter,
            self._cooldown,
            self._positions,
            settings.risk,
            interval=settings.strategy.mr_interval,
            min_candle_volume_usdc=settings.strategy.min_candle_volume_usdc,
            db=self._db,
        )
        self._rsi_div = RSIDivergenceStrategy(
            self._market_data,
            self._trend_filter,
            self._cooldown,
            self._positions,
            settings.risk,
            settings.strategy,
            db=self._db,
        )

        # Build active strategy based on mode
        if strategy_mode == "mean_reversion":
            self._strategy: Strategy = self._mean_rev
        elif strategy_mode == "rsi_div":
            self._strategy = self._rsi_div
        else:  # "multi" (default)
            sub_strategies: list[Strategy] = []
            for name in settings.strategy.active_strategies:
                if name == "mean_reversion":
                    sub_strategies.append(self._mean_rev)
                elif name == "rsi_divergence":
                    sub_strategies.append(self._rsi_div)
            if not sub_strategies:
                sub_strategies = [self._mean_rev, self._rsi_div]
            self._strategy = MultiStrategy(
                sub_strategies,
                max_decisions=settings.risk.max_open_positions,
            )

        self._telegram = TelegramNotifier(settings.telegram)

        # Dashboard (non-blocking aiohttp server)
        self._dashboard_runner: web.AppRunner | None = None

        # Shutdown event
        self._shutdown_event = asyncio.Event()

        # Active coins (populated dynamically after connect)
        self._active_coins: list[str] = ALL_COINS

        # Funding rate cache
        self._funding_cache: dict[str, float] = {}

        # Bookkeeping
        self._last_balance_snapshot: float = 0.0
        self._cycle_count: int = 0

    # ── Lifecycle ────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize all components."""
        cfg = self._settings.hyperliquid
        mode = "PAPER" if self._paper else ("MAINNET" if not cfg.testnet else "TESTNET")
        logger.info("=" * 60)
        logger.info("  Hyperliquid Trading Bot starting  [%s]", mode)
        logger.info("  AI review: %s  |  Paper: %s  |  Once: %s  |  Strategy: %s", not self._no_ai, self._paper, self._once, self._strategy_mode)
        logger.info("  Leverage: %dx  |  Margin: %s", cfg.default_leverage, cfg.margin_mode)
        logger.info("=" * 60)

        await self._client.connect()
        await self._db.connect()
        await self._telegram.start()

        # Dynamic coin discovery
        market_cfg = self._settings.market
        self._active_coins = await discover_perp_coins(
            self._client,
            min_volume_24h=market_cfg.min_pair_volume,
            max_coins=market_cfg.max_coins,
        )
        logger.info("Active coins: %d", len(self._active_coins))

        # Register coins in DB
        for coin in self._active_coins:
            try:
                await self._db.upsert_coin(coin)
            except Exception:
                logger.debug("Failed to upsert coin %s", coin, exc_info=True)

        # Set default leverage only for coins WITHOUT open positions
        # (coins with positions keep their current leverage from HL)
        is_cross = cfg.margin_mode == "cross"
        try:
            hl_positions = await self._client.get_open_positions()
            coins_with_positions = {p["coin"] for p in hl_positions}
        except Exception:
            hl_positions = []
            coins_with_positions = set()
        for coin in self._active_coins:
            if coin not in coins_with_positions:
                try:
                    await self._client.update_leverage(coin, cfg.default_leverage, is_cross)
                except Exception:
                    logger.debug("Failed to set leverage for %s", coin, exc_info=True)

        # Market data — start WebSocket feeds with configured intervals
        intervals = list(self._settings.market.intervals)
        await self._market_data.start(self._active_coins, intervals=intervals)

        # Strategies — pass discovered coins
        self._mean_rev.set_coins(self._active_coins)
        self._mean_rev.set_max_funding_rate(cfg.max_funding_rate)
        self._rsi_div.set_coins(self._active_coins)
        self._rsi_div.set_max_funding_rate(cfg.max_funding_rate)
        await self._strategy.start()

        # Risk manager
        self._risk.set_active_pairs(self._active_coins)
        await self._risk.start()

        # Reconcile tracker with Hyperliquid (detect orphaned/phantom positions)
        await self._reconcile_positions()

        # AI advisor (for review mode)
        if not self._no_ai:
            logger.info("AI advisor enabled (model=%s, timeout=%ds)", self._settings.ai.model, self._settings.ai.timeout)

        # Dashboard
        if not self._no_dashboard:
            await self._start_dashboard()

        self._running = True

        await self._telegram.notify_alert(
            "Bot Started",
            f"Mode: {mode} | AI review: {not self._no_ai} | Coins: {len(self._active_coins)}",
        )
        logger.info("All components initialized — entering main loop")

    async def stop(self) -> None:
        """Graceful shutdown."""
        if not self._running and self._cycle_count > 0:
            return
        self._running = False
        logger.info("Shutting down...")

        await self._telegram.notify_alert("Bot Stopping", "Graceful shutdown initiated")

        try:
            await self._strategy.stop()
        except Exception:
            logger.exception("Error stopping strategy")

        # Final balance snapshot
        try:
            await self._risk.snapshot_balance()
        except Exception:
            logger.exception("Error saving final balance snapshot")

        # Infrastructure
        try:
            await self._market_data.stop()
        except Exception:
            logger.exception("Error stopping market data")

        # Dashboard
        await self._stop_dashboard()

        await self._telegram.close()
        await self._client.close()
        await self._db.close()

        logger.info("Shutdown complete")

    # ── Dashboard ─────────────────────────────────────────────

    async def _start_dashboard(self) -> None:
        try:
            app = create_dashboard()
            self._dashboard_runner = web.AppRunner(app)
            await self._dashboard_runner.setup()
            site = web.TCPSite(self._dashboard_runner, DASHBOARD_HOST, self._dashboard_port)
            await site.start()
            logger.info("Dashboard running at http://%s:%d", DASHBOARD_HOST, self._dashboard_port)
        except Exception:
            logger.exception("Failed to start dashboard — continuing without it")
            self._dashboard_runner = None

    async def _stop_dashboard(self) -> None:
        if self._dashboard_runner:
            try:
                await self._dashboard_runner.cleanup()
            except Exception:
                logger.debug("Error stopping dashboard", exc_info=True)
            self._dashboard_runner = None

    # ── Main loop ────────────────────────────────────────────

    async def run(self) -> None:
        """Main loop: decide → validate → execute, every interval."""
        interval = self._settings.ai.decision_interval

        # Give WebSocket feeds a moment to warm up
        logger.info("Waiting 5s for WebSocket feeds to stabilize...")
        try:
            await asyncio.wait_for(self._shutdown_event.wait(), timeout=5)
            return
        except asyncio.TimeoutError:
            pass

        while self._running:
            cycle_start = time.monotonic()
            self._cycle_count += 1

            try:
                await self._tick()
            except Exception:
                logger.exception("Error in main loop cycle #%d", self._cycle_count)

            if self._once:
                logger.info("--once flag: exiting after single cycle")
                break

            elapsed = time.monotonic() - cycle_start
            sleep_time = interval  # wait full interval after tick completes (incl. AI)
            if sleep_time > 0:
                logger.debug("Cycle #%d took %.1fs — sleeping %.0fs", self._cycle_count, elapsed, sleep_time)
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=sleep_time)
                    break
                except asyncio.TimeoutError:
                    pass

    async def _tick(self) -> None:
        """One iteration of the main loop."""
        logger.info("─── Cycle #%d ───", self._cycle_count)
        cycle_start = time.monotonic()

        # Propagate cycle count
        self._risk.set_cycle(self._cycle_count)
        self._positions.set_cycle(self._cycle_count)
        if hasattr(self._strategy, "set_cycle"):
            self._strategy.set_cycle(self._cycle_count)

        # Cycle event counters
        _signals_generated = 0
        _decisions_approved = 0
        _decisions_blocked = 0
        _trades_executed = 0

        # 1. Refresh risk state
        await self._risk.refresh()
        metrics = self._risk.get_risk_metrics()

        if metrics["kill_switch"]:
            logger.critical("Kill switch active: %s — skipping cycle", metrics["kill_reason"])
            self._write_status(metrics)
            return

        # 2. Check SL/TP on open positions
        await self._check_positions()

        # 3. Refresh funding rates periodically
        if self._cycle_count % FUNDING_REFRESH_INTERVAL == 1:
            await self._refresh_funding()

        # 4. Update strategies
        await self._trend_filter.update(self._active_coins)
        await self._strategy.update()

        # 5. Generate autonomous decisions
        candidates = await self._strategy.generate_decisions()

        _signals_generated = sum(1 for d in candidates if d.action in ("BUY", "SHORT"))

        if candidates:
            logger.info(
                "Strategy generated %d candidate(s): %s",
                len(candidates),
                ", ".join(f"{d.action} {d.symbol}" for d in candidates),
            )
        else:
            logger.info("No trading candidates this cycle")

        # 5b. Check deferred opportunities
        if not self._no_ai:
            self._advisor.set_cycle(self._cycle_count)
            mid_prices = {
                c: self._market_data.get_mid_price(c)
                for c in self._active_coins
                if self._market_data.get_mid_price(c)
            }

            # Clean up stale deferred: remove entries whose signal has disappeared
            candidate_syms = {d.symbol for d in candidates if d.symbol}
            stale = [
                sym for sym in self._advisor.deferred_symbols
                if sym not in candidate_syms
            ]
            for sym in stale:
                self._advisor.remove_deferred(sym)
                logger.info("Removed stale deferred %s — signal no longer present", sym)

            # Check which deferred have met their conditions
            ready_symbols = self._advisor.check_deferred(mid_prices)
            for sym in ready_symbols:
                if sym not in candidate_syms:
                    self._advisor.remove_deferred(sym)  # conditions met but signal gone

            # Filter out candidates for symbols still deferred
            deferred_syms = self._advisor.deferred_symbols
            if deferred_syms:
                filtered = [d for d in candidates if d.symbol in deferred_syms]
                if filtered:
                    logger.info(
                        "Filtered %d deferred candidate(s): %s",
                        len(filtered),
                        ", ".join(f"{d.action} {d.symbol}" for d in filtered),
                    )
                candidates = [d for d in candidates if d.symbol not in deferred_syms]

        # 6. AI advisor call
        open_positions = await self._positions.get_open_positions()

        # Enrich positions with current prices, PnL, indicators
        now_utc = datetime.now(timezone.utc)
        for pos in open_positions:
            mid = self._market_data.get_mid_price(pos["symbol"])
            if mid and mid > 0:
                pos["current_price"] = mid
                direction = pos.get("direction", "LONG")
                if direction == "LONG":
                    pos["unrealized_pnl"] = round((mid - pos["entry_price"]) * pos["quantity"], 4)
                    pos["pnl_pct"] = round((mid - pos["entry_price"]) / pos["entry_price"] * 100, 2)
                else:
                    pos["unrealized_pnl"] = round((pos["entry_price"] - mid) * pos["quantity"], 4)
                    pos["pnl_pct"] = round((pos["entry_price"] - mid) / pos["entry_price"] * 100, 2)
            if pos.get("opened_at"):
                opened = datetime.fromisoformat(pos["opened_at"].replace("Z", "+00:00"))
                pos["age_minutes"] = round((now_utc - opened).total_seconds() / 60, 1)
            # Add indicators from market data
            pos["indicators"] = self._get_indicators(pos["symbol"])

        # Build opportunity list from candidates (entries only)
        opportunities = []
        for d in candidates:
            if d.action in ("BUY", "SHORT"):
                opp = {
                    "symbol": d.symbol,
                    "proposed_action": d.action,
                    "confidence": d.confidence,
                    "strategy": d.strategy_type or "unknown",
                    "reasoning": d.reasoning,
                    "current_price": self._market_data.get_mid_price(d.symbol) or 0,
                    "proposed_stop_loss": d.stop_loss,
                    "proposed_take_profit": d.take_profit,
                    "proposed_size_pct": d.size_pct,
                    "indicators": self._get_indicators(d.symbol) if d.symbol else {},
                }
                opportunities.append(opp)

        # Filter out positions with deferred holds
        positions_for_ai = open_positions
        if not self._no_ai:
            # Check if any deferred holds are ready for re-evaluation
            ready_holds = self._advisor.check_deferred_holds(mid_prices)
            # Also remove holds for positions that were closed
            open_syms = {p["symbol"] for p in open_positions}
            for sym in list(self._advisor.deferred_hold_symbols):
                if sym not in open_syms:
                    self._advisor.remove_deferred_hold(sym)
            # Filter out positions still deferred
            held_syms = self._advisor.deferred_hold_symbols
            if held_syms:
                filtered_holds = [p for p in open_positions if p["symbol"] in held_syms]
                if filtered_holds:
                    logger.info(
                        "Skipping %d deferred HOLD position(s): %s",
                        len(filtered_holds),
                        ", ".join(p["symbol"] for p in filtered_holds),
                    )
                positions_for_ai = [p for p in open_positions if p["symbol"] not in held_syms]

        if not self._no_ai and (positions_for_ai or opportunities):
            utilization = metrics.get("capital_utilization", 0)
            account = {
                "balance_usdc": metrics.get("current_balance", 0),
                "daily_pnl_pct": -metrics.get("daily_drawdown_pct", 0),
                "total_pnl": metrics.get("current_balance", 0) - metrics.get("peak_balance", 0),
                "open_position_count": metrics.get("open_positions", 0),
                "max_positions": metrics.get("max_open_positions", 15),
                "capital_utilization_pct": round(utilization * 100, 1),
                "total_margin_used": metrics.get("total_margin_used", 0),
                "available_margin": metrics.get("available_margin", 0),
                "target_utilization_pct": round(metrics.get("target_utilization", 0.5) * 100, 1),
            }
            recent_trades = await self._db.get_recent_trades(20)
            trade_stats = await self._db.get_trade_stats()
            account["win_rate"] = trade_stats.get("win_rate", 0)
            account["consecutive_losses"] = trade_stats.get("consecutive_losses", 0)

            deferred_summary = self._advisor.get_deferred_summary()
            ai_response = await self._advisor.consult(
                positions=positions_for_ai,
                opportunities=opportunities,
                account=account,
                recent_trades=recent_trades,
                trade_stats=trade_stats,
                deferred=deferred_summary,
            )

            # Log AI review events for positions
            for pa in ai_response.get("positions", []):
                ev_type = "DEFERRED" if pa.get("defer") else "AI_REVIEW"
                try:
                    await self._db.insert_event(
                        cycle=self._cycle_count,
                        symbol=pa["symbol"],
                        event_type=ev_type,
                        source="ai_advisor",
                        action=pa["action"],
                        reasoning=pa.get("reasoning"),
                    )
                except Exception:
                    logger.debug("Failed to log %s event", ev_type, exc_info=True)

            # Log AI review events for opportunities
            for oa in ai_response.get("opportunities", []):
                ev_type = "DEFERRED" if oa["action"] == "HOLD" else "AI_REVIEW"
                try:
                    await self._db.insert_event(
                        cycle=self._cycle_count,
                        symbol=oa["symbol"],
                        event_type=ev_type,
                        source="ai_advisor",
                        action=oa["action"],
                        reasoning=oa.get("reasoning"),
                    )
                except Exception:
                    logger.debug("Failed to log %s event", ev_type, exc_info=True)

            # Process position actions from AI
            for pa in ai_response.get("positions", []):
                if pa["action"] == "CLOSE":
                    candidates.append(Decision(
                        action="CLOSE",
                        symbol=pa["symbol"],
                        confidence=1.0,
                        reasoning=f"AI: {pa.get('reasoning', '')}",
                        strategy_type="ai_advisor",
                    ))
                elif pa["action"] == "SCALE_UP":
                    adj = pa.get("adjustments", {})
                    candidates.append(Decision(
                        action="SCALE_UP",
                        symbol=pa["symbol"],
                        confidence=0.8,
                        reasoning=f"AI: {pa.get('reasoning', '')}",
                        strategy_type="ai_advisor",
                        size_pct=adj.get("size_pct"),
                    ))
                elif pa["action"] == "HOLD":
                    defer_cond = pa.get("defer")
                    if defer_cond:
                        self._advisor.defer_hold(pa["symbol"], defer_cond)
                elif pa["action"] == "ADJUST":
                    pos = next((p for p in open_positions if p["symbol"] == pa["symbol"]), None)
                    if pos:
                        adj = pa.get("adjustments", {})
                        if adj.get("stop_loss") is not None or adj.get("take_profit") is not None:
                            await self._positions.update_sl_tp(
                                pos["id"],
                                stop_loss=adj.get("stop_loss"),
                                take_profit=adj.get("take_profit"),
                            )
                        if adj.get("leverage") is not None:
                            await self._client.update_leverage(
                                pa["symbol"],
                                adj["leverage"],
                                is_cross=self._settings.hyperliquid.margin_mode == "cross",
                            )
                            await self._positions.update_leverage(pos["id"], adj["leverage"])

            # Process opportunity actions from AI
            approved: list[Decision] = []
            for oa in ai_response.get("opportunities", []):
                if oa["action"] == "HOLD":
                    # Defer the opportunity
                    orig = next((d for d in candidates if d.symbol == oa["symbol"]), None)
                    original_action = orig.action if orig else "BUY"
                    self._advisor.defer(oa["symbol"], original_action, oa.get("defer", {}))
                    continue

                # BUY or SHORT — find the original candidate and apply adjustments
                orig = next((d for d in candidates if d.symbol == oa["symbol"]), None)
                if orig:
                    adj = oa.get("adjustments", {})
                    if adj.get("stop_loss") is not None:
                        orig.stop_loss = adj["stop_loss"]
                    if adj.get("take_profit") is not None:
                        orig.take_profit = adj["take_profit"]
                    if adj.get("size_pct") is not None:
                        orig.size_pct = adj["size_pct"]
                    if adj.get("leverage") is not None:
                        orig.leverage = adj["leverage"]
                    approved.append(orig)

            # Keep AI-approved entries + all CLOSE/SELL decisions
            candidates = approved + [d for d in candidates if d.action in ("CLOSE", "SELL")]

        # 7. Sort: CLOSE first, then SCALE_UP, then entries
        _ACTION_ORDER = {"SELL": 0, "CLOSE": 0, "SCALE_UP": 1, "BUY": 2, "SHORT": 2, "HOLD": 3}
        candidates.sort(key=lambda d: _ACTION_ORDER.get(d.action, 3))

        # Anti-churning
        close_syms = {d.symbol for d in candidates if d.action in ("SELL", "CLOSE") and d.symbol}
        if close_syms:
            before = len(candidates)
            candidates = [
                d for d in candidates
                if not (d.action in ("BUY", "SHORT") and d.symbol in close_syms)
            ]
            dropped = before - len(candidates)
            if dropped:
                logger.info("Anti-churn: dropped %d entry(s) for symbols being closed: %s", dropped, close_syms)

        closed_this_cycle: set[str] = set()

        # 8. Iterate decisions: validate → execute → record cooldown
        for decision in candidates:
            if decision.action == "HOLD":
                continue

            if decision.action in ("BUY", "SHORT") and decision.symbol in closed_this_cycle:
                logger.info("Anti-churn: skipping %s %s (just closed this cycle)", decision.action, decision.symbol)
                continue

            logger.info(
                "Decision: %s %s (conf=%.2f) — %s",
                decision.action, decision.symbol or "-",
                decision.confidence,
                decision.reasoning[:120],
            )

            validation = await self._risk.validate_decision(decision)
            if not validation.approved:
                logger.info("Risk Manager blocked: %s", validation.reason)
                _decisions_blocked += 1
                continue
            _decisions_approved += 1
            decision = validation.decision
            size = validation.size

            pnl = await self._execute(decision, size)
            if pnl is not None or decision.action in ("BUY", "SHORT", "SCALE_UP"):
                _trades_executed += 1

            if decision.action in ("SELL", "CLOSE") and decision.symbol:
                closed_this_cycle.add(decision.symbol)
                if pnl is not None:
                    self._cooldown.record_trade_result(decision.symbol, pnl > 0)

            await self._risk.refresh()
            metrics = self._risk.get_risk_metrics()
            if metrics["kill_switch"]:
                logger.critical("Kill switch triggered mid-cycle — stopping")
                break

        # 9. Periodic balance snapshot
        now = time.time()
        if now - self._last_balance_snapshot >= BALANCE_SNAPSHOT_INTERVAL:
            await self._risk.snapshot_balance()
            self._last_balance_snapshot = now

        # 10. Check consecutive losses
        await self._risk.check_consecutive_losses()

        # 11. Write live status for dashboard
        self._write_status(metrics)

        # 12. Cycle summary
        try:
            duration = time.monotonic() - cycle_start
            await self._db.insert_cycle_summary(
                cycle=self._cycle_count,
                duration_sec=round(duration, 2),
                balance_usdc=metrics.get("current_balance"),
                drawdown_pct=metrics.get("drawdown_pct"),
                daily_drawdown_pct=metrics.get("daily_drawdown_pct"),
                open_positions=metrics.get("open_positions"),
                capital_utilization=metrics.get("capital_utilization"),
                coins_monitored=len(self._active_coins),
                signals_generated=_signals_generated,
                decisions_approved=_decisions_approved,
                decisions_blocked=_decisions_blocked,
                trades_executed=_trades_executed,
                kill_switch=metrics.get("kill_switch", False),
                daily_paused=metrics.get("daily_paused", False),
            )
        except Exception:
            logger.debug("Failed to write cycle summary", exc_info=True)

    # ── Funding rate refresh ──────────────────────────────────

    async def _refresh_funding(self) -> None:
        """Refresh funding rates and pass to strategy."""
        try:
            # Hyperliquid doesn't have a direct funding endpoint in the basic SDK,
            # but we can get it from clearinghouse state. For now, skip if unavailable.
            pass
        except Exception:
            logger.debug("Failed to refresh funding rates", exc_info=True)

    async def _reconcile_positions(self) -> None:
        """Reconcile position tracker with Hyperliquid on startup.

        Detects:
        - Positions on HL but not in tracker (orphaned) → import into tracker
        - Positions in tracker but not on HL (phantom) → mark closed
        """
        try:
            hl_positions = await self._client.get_open_positions()
            tracker_positions = await self._positions.get_open_positions()

            hl_coins = {p["coin"] for p in hl_positions}
            tracker_coins = {p["symbol"] for p in tracker_positions}

            # Orphaned: on HL but not tracked → import into tracker
            orphaned = hl_coins - tracker_coins
            if orphaned:
                sl_pct = self._settings.risk.stop_loss_pct
                tp_pct = self._settings.risk.take_profit_pct
                for hl_pos in hl_positions:
                    coin = hl_pos["coin"]
                    if coin not in orphaned:
                        continue
                    direction = hl_pos["direction"]
                    entry_px = float(hl_pos["entryPx"])
                    size = float(hl_pos["size"])
                    leverage = int(hl_pos.get("leverage", self._settings.hyperliquid.default_leverage))
                    if direction == "SHORT":
                        sl = entry_px * (1 + sl_pct / 100)
                        tp = entry_px * (1 - tp_pct / 100)
                    else:
                        sl = entry_px * (1 - sl_pct / 100)
                        tp = entry_px * (1 + tp_pct / 100)
                    try:
                        pos_id = await self._positions.open_position(
                            symbol=coin,
                            entry_price=entry_px,
                            quantity=size,
                            stop_loss=sl,
                            take_profit=tp,
                            strategy="reconciled",
                            direction=direction,
                            leverage=leverage,
                        )
                        logger.info(
                            "RECONCILE: Imported orphaned %s %s — entry=%.4f size=%.4f SL=%.4f TP=%.4f (id=%d)",
                            direction, coin, entry_px, size, sl, tp, pos_id,
                        )
                    except ValueError:
                        logger.warning(
                            "RECONCILE: Skipped duplicate %s %s — already in tracker",
                            direction, coin,
                        )

            # Phantom: in tracker but not on HL
            phantom = tracker_coins - hl_coins
            for pos in tracker_positions:
                if pos["symbol"] in phantom:
                    logger.warning(
                        "RECONCILE: Closing phantom position #%d %s %s (not on Hyperliquid)",
                        pos["id"], pos.get("direction", "?"), pos["symbol"],
                    )
                    mid = self._market_data.get_mid_price(pos["symbol"])
                    price = mid or pos["entry_price"]
                    await self._positions.close_position(pos["id"], price, "reconcile_phantom")

            # Sync leverage from HL to tracker for existing positions
            for hl_pos in hl_positions:
                coin = hl_pos["coin"]
                if coin in tracker_coins and coin not in orphaned:
                    hl_lev = int(hl_pos.get("leverage", self._settings.hyperliquid.default_leverage))
                    tracker_pos = next((p for p in tracker_positions if p["symbol"] == coin), None)
                    if tracker_pos and tracker_pos.get("leverage") != hl_lev:
                        await self._positions.update_leverage(tracker_pos["id"], hl_lev)
                        logger.info(
                            "RECONCILE: Synced leverage for %s: %dx → %dx (from Hyperliquid)",
                            coin, tracker_pos.get("leverage", 0), hl_lev,
                        )

            if not orphaned and not phantom:
                logger.info("Position reconciliation OK — no discrepancies")

        except Exception:
            logger.exception("Position reconciliation failed — continuing")

    # ── Position SL/TP monitoring ─────────────────────────────

    async def _check_positions(self) -> None:
        """Check all open positions for SL/TP/trailing/time stop hits."""
        positions = await self._positions.get_open_positions()
        if not positions:
            return

        # Get current prices — prefer WS mid, fallback to REST
        prices: dict[str, float] = {}
        for pos in positions:
            sym = pos["symbol"]
            if sym not in prices:
                mid = self._market_data.get_mid_price(sym)
                if mid and mid > 0:
                    prices[sym] = mid
                else:
                    try:
                        rest_price = await self._client.get_price(sym)
                        if rest_price > 0:
                            prices[sym] = rest_price
                            logger.warning("Using REST fallback price for %s (WS stale)", sym)
                    except Exception:
                        logger.warning("No price available for %s — SL/TP check skipped", sym)

        to_close = await self._positions.check_sl_tp(prices)

        for item in to_close:
            pos = item["position"]
            reason = item["reason"]
            symbol = pos["symbol"]
            price = prices.get(symbol, pos["entry_price"])
            direction = pos.get("direction", "LONG")

            if self._paper:
                logger.info("[PAPER] Auto-close %s %s: %s @ %.4f", direction, symbol, reason, price)
                pnl = await self._positions.close_position(pos["id"], price, reason)
                await self._db.insert_trade(
                    symbol=symbol, side="CLOSE", price=price,
                    quantity=pos["quantity"], pnl=pnl,
                    strategy=pos["strategy"], notes=f"[PAPER] Auto: {reason}",
                )
                self._cooldown.record_trade_result(symbol, pnl > 0)
                await self._telegram.notify_trade(
                    action="CLOSE", symbol=symbol, qty=pos["quantity"], price=price,
                )
                try:
                    await self._db.insert_event(
                        cycle=self._cycle_count, symbol=symbol,
                        event_type="SL_TP_TRIGGER", source="auto_close",
                        action="CLOSE", reasoning=reason,
                        details={"price": round(price, 6), "pnl": round(pnl, 4), "paper": True},
                        position_id=pos["id"],
                    )
                except Exception:
                    logger.debug("Failed to log SL_TP_TRIGGER event", exc_info=True)
            else:
                try:
                    await self._client.close_position(symbol)
                    pnl = await self._positions.close_position(pos["id"], price, reason)
                    await self._db.insert_trade(
                        symbol=symbol, side="CLOSE", price=price,
                        quantity=pos["quantity"], pnl=pnl, strategy=pos["strategy"],
                        notes=f"Auto: {reason}",
                    )
                    self._cooldown.record_trade_result(symbol, pnl > 0)
                    await self._telegram.notify_trade(
                        action="CLOSE", symbol=symbol, qty=pos["quantity"], price=price,
                    )
                    logger.info(
                        "Auto-closed %s %s: %s @ %.4f PnL=%.4f",
                        direction, symbol, reason, price, pnl,
                    )
                    try:
                        await self._db.insert_event(
                            cycle=self._cycle_count, symbol=symbol,
                            event_type="SL_TP_TRIGGER", source="auto_close",
                            action="CLOSE", reasoning=reason,
                            details={"price": round(price, 6), "pnl": round(pnl, 4)},
                            position_id=pos["id"],
                        )
                    except Exception:
                        logger.debug("Failed to log SL_TP_TRIGGER event", exc_info=True)
                except Exception:
                    logger.exception("Failed to auto-close %s", symbol)

    # ── Dashboard status ──────────────────────────────────────

    def _write_status(self, metrics: dict[str, Any]) -> None:
        """Write live bot state to JSON for the dashboard."""
        try:
            cfg = self._settings.hyperliquid
            status = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "cycle_count": self._cycle_count,
                "mode": "testnet" if cfg.testnet else "mainnet",
                "paper": self._paper,
                "no_ai": self._no_ai,
                "leverage": cfg.default_leverage,
                "margin_mode": cfg.margin_mode,
                "risk_metrics": metrics,
                "strategy_mode": self._strategy_mode,
                "strategies": self._strategy.get_state(),
            }
            Path("data/bot_status.json").write_text(
                json.dumps(status, default=str)
            )
        except Exception:
            logger.debug("Failed to write bot_status.json", exc_info=True)

    # ── Indicator helpers ────────────────────────────────────

    def _get_indicators(self, symbol: str) -> dict[str, Any]:
        """Get current indicators for a symbol from strategy state."""
        state = self._strategy.get_state()
        indicators: dict[str, Any] = {}

        # Try direct signals (single strategy)
        signals = state.get("signals", {})
        if symbol in signals:
            sig = signals[symbol]
            indicators = {
                "rsi_15m": sig.get("rsi"),
                "rsi_1h": sig.get("rsi_1h"),
                "trend": sig.get("trend"),
                "bb_upper": sig.get("bb_upper"),
                "bb_lower": sig.get("bb_lower"),
                "volume_ratio": sig.get("volume_ratio"),
            }
            return {k: v for k, v in indicators.items() if v is not None}

        # Try sub_strategies (multi strategy)
        for sub in state.get("sub_strategies", {}).values():
            sub_signals = sub.get("signals", {})
            if symbol in sub_signals:
                sig = sub_signals[symbol]
                indicators = {
                    "rsi_15m": sig.get("rsi"),
                    "rsi_1h": sig.get("rsi_1h"),
                    "trend": sig.get("trend"),
                    "bb_upper": sig.get("bb_upper"),
                    "bb_lower": sig.get("bb_lower"),
                    "volume_ratio": sig.get("volume_ratio"),
                }
                return {k: v for k, v in indicators.items() if v is not None}

        return indicators

    # ── Order execution ──────────────────────────────────────

    async def _execute(self, decision: Decision, size: Any) -> float | None:
        """Execute a validated decision on Hyperliquid. Returns PnL for closes, None otherwise."""
        if decision.action == "HOLD":
            return None

        symbol = decision.symbol
        if not symbol:
            return None

        if self._paper:
            return await self._execute_paper(decision, size)

        try:
            if decision.action == "BUY":
                return await self._execute_entry(decision, size, is_buy=True)
            elif decision.action == "SHORT":
                return await self._execute_entry(decision, size, is_buy=False)
            elif decision.action == "SCALE_UP":
                return await self._execute_scale_up(decision, size)
            elif decision.action in ("SELL", "CLOSE"):
                return await self._execute_close(decision)
        except Exception:
            logger.exception("Order execution failed for %s %s", decision.action, symbol)
            await self._telegram.notify_alert(
                "Order Failed",
                f"{decision.action} {symbol} — check logs",
            )
        return None

    async def _execute_paper(self, decision: Decision, size: Any) -> float | None:
        """Paper trading execution."""
        symbol = decision.symbol
        mid = self._market_data.get_mid_price(symbol)
        price = mid or decision.limit_price or 0
        if not price:
            price = await self._client.get_price(symbol)

        logger.info(
            "[PAPER] Would execute: %s %s size=%.2f%% price=%.4f",
            decision.action, symbol, decision.size_pct or 0, price,
        )

        leverage = self._settings.hyperliquid.default_leverage

        if decision.action == "BUY":
            notional = (size.size_usdc * leverage) if size and size.size_usdc > 0 else 0
            qty = notional / price if price > 0 else 0
            qty = self._client.round_size(symbol, qty)
            await self._positions.open_position(
                symbol=symbol, entry_price=price, quantity=qty,
                stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                strategy=decision.strategy_type or "mean_reversion",
                direction="LONG", leverage=leverage,
            )
            await self._db.insert_trade(
                symbol=symbol, side="BUY", price=price, quantity=qty,
                strategy=decision.strategy_type or "mean_reversion", notes="[PAPER]",
            )
            try:
                await self._db.insert_event(
                    cycle=self._cycle_count, symbol=symbol,
                    event_type="TRADE_ENTRY", source="execution", action="BUY",
                    confidence=decision.confidence,
                    details={"price": round(price, 6), "quantity": qty, "paper": True},
                )
            except Exception:
                logger.debug("Failed to log TRADE_ENTRY event", exc_info=True)
            return None

        elif decision.action == "SHORT":
            notional = (size.size_usdc * leverage) if size and size.size_usdc > 0 else 0
            qty = notional / price if price > 0 else 0
            qty = self._client.round_size(symbol, qty)
            await self._positions.open_position(
                symbol=symbol, entry_price=price, quantity=qty,
                stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                strategy=decision.strategy_type or "mean_reversion",
                direction="SHORT", leverage=leverage,
            )
            await self._db.insert_trade(
                symbol=symbol, side="SHORT", price=price, quantity=qty,
                strategy=decision.strategy_type or "mean_reversion", notes="[PAPER]",
            )
            try:
                await self._db.insert_event(
                    cycle=self._cycle_count, symbol=symbol,
                    event_type="TRADE_ENTRY", source="execution", action="SHORT",
                    confidence=decision.confidence,
                    details={"price": round(price, 6), "quantity": qty, "paper": True},
                )
            except Exception:
                logger.debug("Failed to log TRADE_ENTRY event", exc_info=True)
            return None

        elif decision.action == "SCALE_UP":
            pos = await self._positions.get_position_for_symbol(symbol)
            if pos:
                direction = pos.get("direction", "LONG")
                leverage = pos.get("leverage", self._settings.hyperliquid.default_leverage)
                notional = (size.size_usdc * leverage) if size and size.size_usdc > 0 else 0
                qty = notional / price if price > 0 else 0
                qty = self._client.round_size(symbol, qty)
                if qty > 0:
                    await self._positions.scale_position(pos["id"], qty, price)
                    await self._db.insert_trade(
                        symbol=symbol, side="BUY" if direction == "LONG" else "SHORT",
                        price=price, quantity=qty,
                        strategy="ai_advisor", notes="[PAPER] SCALE_UP",
                    )
                    logger.info("[PAPER] SCALE_UP %s %s +%.6f @ %.4f", direction, symbol, qty, price)
                    try:
                        await self._db.insert_event(
                            cycle=self._cycle_count, symbol=symbol,
                            event_type="POSITION_SCALED", source="execution", action="SCALE_UP",
                            details={"price": round(price, 6), "quantity": qty, "paper": True},
                            position_id=pos["id"],
                        )
                    except Exception:
                        logger.debug("Failed to log POSITION_SCALED event", exc_info=True)
            return None

        elif decision.action in ("SELL", "CLOSE"):
            pos = await self._positions.get_position_for_symbol(symbol)
            if pos:
                pnl = await self._positions.close_position(pos["id"], price, "strategy_exit")
                await self._db.insert_trade(
                    symbol=symbol, side="CLOSE", price=price,
                    quantity=pos["quantity"], pnl=pnl,
                    strategy=decision.strategy_type or "mean_reversion", notes="[PAPER]",
                )
                try:
                    await self._db.insert_event(
                        cycle=self._cycle_count, symbol=symbol,
                        event_type="TRADE_EXIT", source="execution", action="CLOSE",
                        details={"price": round(price, 6), "pnl": round(pnl, 4), "paper": True},
                        position_id=pos["id"],
                    )
                except Exception:
                    logger.debug("Failed to log TRADE_EXIT event", exc_info=True)
                return pnl
            else:
                await self._db.insert_trade(
                    symbol=symbol, side=decision.action, price=price,
                    quantity=0, strategy=decision.strategy_type or "mean_reversion",
                    notes="[PAPER] no position",
                )
        return None

    async def _execute_entry(self, decision: Decision, size: Any, is_buy: bool) -> float | None:
        """Execute a BUY or SHORT entry on Hyperliquid."""
        symbol = decision.symbol
        price = await self._client.get_price(symbol)
        leverage = decision.leverage or self._settings.hyperliquid.default_leverage

        # Ensure leverage is set on Hyperliquid BEFORE placing the order
        try:
            is_cross = self._settings.hyperliquid.margin_mode == "cross"
            await self._client.update_leverage(symbol, leverage, is_cross)
        except Exception:
            logger.warning("Failed to set leverage for %s — proceeding with exchange default", symbol)

        # notional = margin * leverage, qty = notional / price
        notional = (size.size_usdc * leverage) if size and size.size_usdc > 0 else 0
        raw_qty = notional / price if price > 0 else 0
        qty = self._client.round_size(symbol, raw_qty)
        if qty <= 0:
            logger.warning("Computed qty is 0 after rounding — skipping")
            return None

        direction = "LONG" if is_buy else "SHORT"
        action_name = "BUY" if is_buy else "SHORT"

        result = await self._client.place_market_order(
            coin=symbol, is_buy=is_buy, size=qty,
        )

        # Record position — CRITICAL: if this fails, position is live on HL but invisible
        try:
            await self._positions.open_position(
                symbol=symbol, entry_price=price, quantity=qty,
                stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                strategy=decision.strategy_type or "mean_reversion",
                direction=direction, leverage=leverage,
            )
        except Exception:
            logger.critical(
                "POSITION TRACKER FAILED after order placed! %s %s qty=%.6f is LIVE on HL but untracked",
                action_name, symbol, qty,
            )
            raise

        await self._db.insert_trade(
            symbol=symbol, side=action_name, price=price,
            quantity=qty, strategy=decision.strategy_type or "mean_reversion",
        )
        await self._telegram.notify_trade(
            action=action_name, symbol=symbol, qty=qty, price=price,
        )
        logger.info(
            "%s executed: %s qty=%.6f price=%.4f SL=%.4f TP=%.4f lev=%dx",
            action_name, symbol, qty, price,
            decision.stop_loss or 0, decision.take_profit or 0, leverage,
        )
        try:
            await self._db.insert_event(
                cycle=self._cycle_count,
                symbol=symbol,
                event_type="TRADE_ENTRY",
                source="execution",
                action=action_name,
                confidence=decision.confidence,
                details={
                    "price": round(price, 6),
                    "quantity": qty,
                    "leverage": leverage,
                    "stop_loss": decision.stop_loss,
                    "take_profit": decision.take_profit,
                    "strategy": decision.strategy_type,
                },
            )
        except Exception:
            logger.debug("Failed to log TRADE_ENTRY event", exc_info=True)
        return None

    async def _execute_scale_up(self, decision: Decision, size: Any) -> float | None:
        """Execute a SCALE_UP: add to an existing position in the same direction."""
        symbol = decision.symbol
        pos = await self._positions.get_position_for_symbol(symbol)

        if not pos:
            logger.warning("SCALE_UP %s: no position in tracker — skipping", symbol)
            return None

        direction = pos.get("direction", "LONG")
        is_buy = direction == "LONG"
        leverage = pos.get("leverage", self._settings.hyperliquid.default_leverage)
        price = await self._client.get_price(symbol)

        # Ensure leverage matches
        try:
            is_cross = self._settings.hyperliquid.margin_mode == "cross"
            await self._client.update_leverage(symbol, leverage, is_cross)
        except Exception:
            logger.warning("Failed to set leverage for SCALE_UP %s", symbol)

        notional = (size.size_usdc * leverage) if size and size.size_usdc > 0 else 0
        raw_qty = notional / price if price > 0 else 0
        qty = self._client.round_size(symbol, raw_qty)
        if qty <= 0:
            logger.warning("SCALE_UP %s: computed qty is 0 after rounding", symbol)
            return None

        result = await self._client.place_market_order(
            coin=symbol, is_buy=is_buy, size=qty,
        )

        try:
            await self._positions.scale_position(pos["id"], qty, price)
        except Exception:
            logger.critical(
                "SCALE_UP tracker failed after order placed! %s %s qty=%.6f is LIVE on HL but untracked",
                direction, symbol, qty,
            )
            raise

        await self._db.insert_trade(
            symbol=symbol, side="BUY" if is_buy else "SHORT", price=price,
            quantity=qty, strategy="ai_advisor", notes="SCALE_UP",
        )
        await self._telegram.notify_trade(
            action="SCALE_UP", symbol=symbol, qty=qty, price=price,
        )
        logger.info(
            "SCALE_UP executed: %s %s +%.6f @ %.4f lev=%dx",
            direction, symbol, qty, price, leverage,
        )
        try:
            await self._db.insert_event(
                cycle=self._cycle_count,
                symbol=symbol,
                event_type="POSITION_SCALED",
                source="execution",
                action="SCALE_UP",
                details={
                    "direction": direction,
                    "price": round(price, 6),
                    "quantity": qty,
                    "leverage": leverage,
                },
                position_id=pos["id"],
            )
        except Exception:
            logger.debug("Failed to log POSITION_SCALED event", exc_info=True)
        return None

    async def _execute_close(self, decision: Decision) -> float | None:
        """Close a position on Hyperliquid. Returns PnL."""
        symbol = decision.symbol
        pos = await self._positions.get_position_for_symbol(symbol)

        if not pos:
            logger.warning("CLOSE %s: no position in tracker — skipping exchange call", symbol)
            return None

        # Close via SDK market_close
        await self._client.close_position(symbol)

        mid = self._market_data.get_mid_price(symbol)
        fill_price = mid or await self._client.get_price(symbol)

        pnl = None
        if pos:
            pnl = await self._positions.close_position(
                pos["id"], fill_price, "strategy_exit",
            )

        await self._db.insert_trade(
            symbol=symbol, side="CLOSE", price=fill_price,
            quantity=pos["quantity"] if pos else 0, pnl=pnl,
            strategy=decision.strategy_type or "mean_reversion",
            notes=decision.action,
        )
        await self._telegram.notify_trade(
            action="CLOSE", symbol=symbol,
            qty=pos["quantity"] if pos else 0, price=fill_price,
        )
        logger.info(
            "CLOSE executed: %s @ %.4f PnL=%s",
            symbol, fill_price,
            f"{pnl:.4f}" if pnl is not None else "N/A",
        )
        try:
            await self._db.insert_event(
                cycle=self._cycle_count,
                symbol=symbol,
                event_type="TRADE_EXIT",
                source="execution",
                action="CLOSE",
                details={
                    "price": round(fill_price, 6),
                    "pnl": round(pnl, 4) if pnl is not None else None,
                    "quantity": pos["quantity"] if pos else 0,
                    "strategy": decision.strategy_type,
                    "reasoning": decision.reasoning[:200] if decision.reasoning else None,
                },
                position_id=pos["id"] if pos else None,
            )
        except Exception:
            logger.debug("Failed to log TRADE_EXIT event", exc_info=True)
        return pnl


# ── Signal handling ──────────────────────────────────────────


def _setup_signals(bot: Bot, loop: asyncio.AbstractEventLoop) -> None:
    """Register SIGINT/SIGTERM for graceful shutdown."""
    def _handler(sig: int, _frame: Any) -> None:
        logger.info("Received signal %s — initiating shutdown", signal.Signals(sig).name)
        bot._running = False
        loop.call_soon_threadsafe(bot._shutdown_event.set)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _handler)


# ── CLI ──────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hyperliquid Perpetual Trading Bot")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--testnet", action="store_true", default=True, help="Use testnet (default)")
    mode.add_argument("--live", action="store_true", help="Use mainnet")
    parser.add_argument("--paper", action="store_true", help="Paper trading (log decisions, no real orders)")
    parser.add_argument("--no-ai", action="store_true", help="Disable AI review, use pure rule-based trading")
    parser.add_argument("--once", action="store_true", help="Run one cycle then exit")
    parser.add_argument("--no-dashboard", action="store_true", help="Disable the web dashboard")
    parser.add_argument("--dashboard-port", type=int, default=DASHBOARD_PORT, help=f"Dashboard port (default: {DASHBOARD_PORT})")
    parser.add_argument(
        "--strategy",
        choices=["multi", "mean_reversion", "rsi_div"],
        default="multi",
        help="Strategy mode: multi (default), mean_reversion, rsi_div",
    )
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    settings = load_settings()
    setup_logging(settings.log_level)

    # Override testnet based on CLI flags
    if args.live:
        from dataclasses import replace
        settings = replace(settings, hyperliquid=replace(settings.hyperliquid, testnet=False))
        logger.warning("*** MAINNET TRADING MODE ***")

    bot = Bot(
        settings,
        paper=args.paper,
        no_ai=args.no_ai,
        once=args.once,
        dashboard_port=args.dashboard_port,
        no_dashboard=args.no_dashboard,
        strategy_mode=args.strategy,
    )

    loop = asyncio.get_running_loop()
    _setup_signals(bot, loop)

    try:
        await bot.start()
        await bot.run()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received")
    finally:
        await bot.stop()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
