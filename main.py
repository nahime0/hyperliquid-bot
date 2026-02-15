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
from strategies.bb_squeeze import BbSqueezeStrategy
from strategies.breakout import BreakoutStrategy
from strategies.btc_correlation import BtcCorrelationStrategy
from strategies.buy_the_dip import BuyTheDipStrategy
from strategies.cooldown import CooldownTracker
from strategies.ema_crossover import EmaCrossoverStrategy
from strategies.funding_rate import FundingRateStrategy
from strategies.macd_divergence import MacdDivergenceStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.mtf_confluence import MtfConfluenceStrategy
from strategies.multi_strategy import MultiStrategy
from strategies.rsi_divergence import RSIDivergenceStrategy
from strategies.session_momentum import SessionMomentumStrategy
from strategies.trend_filter import TrendFilter
from strategies.trend_following import TrendFollowingStrategy
from strategies.volume_spike import VolumeSpikeStrategy
from utils.logger import setup_logging, get_logger
from utils.telegram import TelegramNotifier

logger = get_logger(__name__)

# Balance snapshot interval (seconds)
BALANCE_SNAPSHOT_INTERVAL = 300  # 5 minutes


class Bot:
    """Top-level orchestrator that owns all components."""

    def __init__(
        self,
        settings: Settings,
        *,
        paper: bool = False,
        no_ai: bool = False,
        once: bool = False,
        strategy_mode: str = "multi",
    ) -> None:
        self._settings = settings
        self._paper = paper
        self._no_ai = no_ai
        self._once = once
        self._running = False
        self._strategy_mode = strategy_mode

        # Components (initialised in start())
        self._client = HyperliquidClient(settings)
        self._db = Database(settings.db_path)
        self._market_data = MarketData(self._client, settings)
        self._advisor = AIAdvisor(config=settings.ai, db=self._db)
        self._positions = PositionTracker(self._db, settings.risk, market_data=self._market_data)
        self._risk = RiskManager(settings.risk, self._client, self._db, self._positions, market_config=settings.market, market_data=self._market_data, ai_min_confidence=settings.ai.min_confidence)

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
        self._trend_follow = TrendFollowingStrategy(
            self._market_data,
            self._trend_filter,
            self._cooldown,
            self._positions,
            settings.risk,
            interval=settings.strategy.trend_interval,
            min_candle_volume_usdc=settings.strategy.min_candle_volume_usdc,
            change_threshold=settings.strategy.tf_change_threshold,
            allow_short=settings.strategy.tf_allow_short,
            db=self._db,
        )

        # 10 new strategies
        self._bb_squeeze = BbSqueezeStrategy(
            self._market_data, self._trend_filter, self._cooldown,
            self._positions, settings.risk, settings.strategy, db=self._db,
        )
        self._breakout = BreakoutStrategy(
            self._market_data, self._trend_filter, self._cooldown,
            self._positions, settings.risk, settings.strategy, db=self._db,
        )
        self._btc_correlation = BtcCorrelationStrategy(
            self._market_data, self._trend_filter, self._cooldown,
            self._positions, settings.risk,
            btc_min_move_pct=settings.strategy.btc_min_move_pct,
            btc_min_lag_pct=settings.strategy.btc_min_lag_pct,
            btc_catch_up_pct=settings.strategy.btc_catch_up_pct,
            db=self._db,
        )
        self._buy_the_dip = BuyTheDipStrategy(
            self._market_data, self._trend_filter, self._cooldown,
            self._positions, settings.risk, settings.strategy, db=self._db,
        )
        self._ema_crossover = EmaCrossoverStrategy(
            self._market_data, self._trend_filter, self._cooldown,
            self._positions, settings.risk, settings.strategy, db=self._db,
        )
        self._funding_rate = FundingRateStrategy(
            self._market_data, self._trend_filter, self._cooldown,
            self._positions, settings.risk, settings.strategy, db=self._db,
        )
        self._macd_divergence = MacdDivergenceStrategy(
            self._market_data, self._trend_filter, self._cooldown,
            self._positions, settings.risk, settings.strategy, db=self._db,
        )
        self._mtf_confluence = MtfConfluenceStrategy(
            self._market_data, self._trend_filter, self._cooldown,
            self._positions, settings.risk, settings.strategy, db=self._db,
        )
        self._session_momentum = SessionMomentumStrategy(
            self._market_data, self._trend_filter, self._cooldown,
            self._positions, settings.risk, settings.strategy, db=self._db,
        )
        self._volume_spike = VolumeSpikeStrategy(
            self._market_data, self._trend_filter, self._cooldown,
            self._positions, settings.risk,
            spike_threshold=settings.strategy.vs_spike_threshold,
            wick_ratio=settings.strategy.vs_wick_ratio,
            db=self._db,
        )

        # Map strategy names to instances
        self._strategy_map: dict[str, Strategy] = {
            "mean_reversion": self._mean_rev,
            "rsi_divergence": self._rsi_div,
            "trend_following": self._trend_follow,
            "bb_squeeze": self._bb_squeeze,
            "breakout": self._breakout,
            "btc_correlation": self._btc_correlation,
            "buy_the_dip": self._buy_the_dip,
            "ema_crossover": self._ema_crossover,
            "funding_rate": self._funding_rate,
            "macd_divergence": self._macd_divergence,
            "mtf_confluence": self._mtf_confluence,
            "session_momentum": self._session_momentum,
            "volume_spike": self._volume_spike,
        }

        # Build active strategy based on mode
        if strategy_mode in self._strategy_map:
            self._strategy: Strategy = self._strategy_map[strategy_mode]
        elif strategy_mode == "rsi_div":
            self._strategy = self._rsi_div
        else:  # "multi" (default)
            sub_strategies: list[Strategy] = []
            for name in settings.strategy.active_strategies:
                if name in self._strategy_map:
                    sub_strategies.append(self._strategy_map[name])
            if not sub_strategies:
                sub_strategies = list(self._strategy_map.values())
            self._strategy = MultiStrategy(sub_strategies)

        self._telegram = TelegramNotifier(settings.telegram)

        # Shutdown event
        self._shutdown_event = asyncio.Event()

        # Lock to prevent concurrent close operations (SL/TP monitor vs tick)
        self._close_lock = asyncio.Lock()

        # Active coins (populated dynamically after connect)
        self._active_coins: list[str] = ALL_COINS

        # Funding rate cache
        self._funding_cache: dict[str, float] = {}

        # Asset context cache (funding, OI, mark price — refreshed each cycle)
        self._asset_ctx_map: dict[str, dict[str, Any]] = {}

        # Open interest snapshots for 4h change calculation
        self._oi_snapshots: dict[str, list[tuple[float, float]]] = {}

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

        # Ensure AI context directory exists
        (Path("data") / "ai_context").mkdir(parents=True, exist_ok=True)

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

        # Strategies — pass discovered coins to all strategy instances
        for strat in self._strategy_map.values():
            if hasattr(strat, "set_coins"):
                strat.set_coins(self._active_coins)
            if hasattr(strat, "set_max_funding_rate"):
                strat.set_max_funding_rate(cfg.max_funding_rate)
        await self._strategy.start()

        # Risk manager
        self._risk.set_active_pairs(self._active_coins)
        await self._risk.start()

        # Reconcile tracker with Hyperliquid (detect orphaned/phantom positions)
        await self._reconcile_positions()

        # AI advisor (for review mode)
        if not self._no_ai:
            await self._advisor.load_deferred()
            logger.info("AI advisor enabled (model=%s, timeout=%ds)", self._settings.ai.model, self._settings.ai.timeout)

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

        await self._telegram.close()
        await self._client.close()
        await self._db.close()

        logger.info("Shutdown complete")

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

        # Launch independent SL/TP monitor
        monitor_task = asyncio.create_task(self._sl_tp_monitor())

        try:
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
        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
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
            await self._write_status(metrics)
            return

        # 2. Refresh mid prices via REST (WS allMids disabled)
        await self._market_data.refresh_mid_prices()

        # 3. Refresh asset contexts (funding + OI for all coins)
        asset_ctx_map = await self._fetch_asset_contexts()

        # 3b. Extract funding rates from asset contexts and pass to strategies
        await self._refresh_funding()

        # 4. Update strategies
        await self._trend_filter.update(self._active_coins)
        await self._strategy.update()

        # 5. Generate autonomous decisions
        # For MultiStrategy with AI: collect raw (unmerged) decisions, filter deferred, then merge
        is_multi = isinstance(self._strategy, MultiStrategy)
        if is_multi and not self._no_ai:
            raw_candidates = await self._strategy.generate_raw_decisions()
            raw_candidates.sort(key=lambda d: d.confidence, reverse=True)
        else:
            raw_candidates = await self._strategy.generate_decisions()

        _signals_generated = sum(1 for d in raw_candidates if d.action in ("BUY", "SHORT"))

        if raw_candidates:
            logger.info(
                "Strategy generated %d raw candidate(s): %s",
                len(raw_candidates),
                ", ".join(f"{d.action} {d.symbol}[{d.strategy_type}]" for d in raw_candidates[:20]),
            )
        else:
            logger.info("No trading candidates this cycle")

        # 5b. Deferred opportunity pipeline (per symbol+strategy_type)
        ready_keys: set[tuple[str, str]] = set()
        ready_symbols: set[str] = set()
        candidates = raw_candidates
        if not self._no_ai:
            self._advisor.set_cycle(self._cycle_count)
            mid_prices = {
                c: self._market_data.get_mid_price(c)
                for c in self._active_coins
                if self._market_data.get_mid_price(c)
            }

            # Step 3: Clean stale deferred — remove entries whose signal has disappeared
            candidate_keys = {
                (d.symbol, d.strategy_type or "unknown")
                for d in raw_candidates if d.symbol
            }
            stale = [
                k for k in self._advisor.deferred_keys
                if k not in candidate_keys
            ]
            for sym, strat in stale:
                await self._advisor.remove_deferred(sym, strat)
                logger.info("Removed stale deferred %s/%s — signal no longer present", sym, strat)

            # Check which deferred have met their conditions
            ready_keys = set(await self._advisor.check_deferred(mid_prices))
            ready_symbols = {k[0] for k in ready_keys}
            for sym, strat in ready_keys:
                if (sym, strat) not in candidate_keys:
                    await self._advisor.remove_deferred(sym, strat)

            # Step 4: Filter out candidates whose (sym, strat) is deferred
            # Exception: if direction is opposite → cancel deferred, let through
            deferred_keys = self._advisor.deferred_keys
            if deferred_keys:
                keep: list[Decision] = []
                filtered_names: list[str] = []
                for d in raw_candidates:
                    key = (d.symbol or "", d.strategy_type or "unknown")
                    if key in deferred_keys:
                        deferred_action = self._advisor.get_deferred_action(d.symbol or "", d.strategy_type)
                        if deferred_action and d.action != deferred_action:
                            # Opposite direction → cancel deferred, let new candidate through
                            await self._advisor.remove_deferred(d.symbol or "", d.strategy_type)
                            logger.info(
                                "Cancelled deferred %s %s/%s — opposite signal: %s",
                                deferred_action, d.symbol, d.strategy_type, d.action,
                            )
                            keep.append(d)
                        else:
                            filtered_names.append(f"{d.action} {d.symbol}[{d.strategy_type}]")
                    else:
                        keep.append(d)
                if filtered_names:
                    logger.info(
                        "Filtered %d deferred candidate(s): %s",
                        len(filtered_names), ", ".join(filtered_names),
                    )
                candidates = keep
            else:
                candidates = list(raw_candidates)

            # Step 5: Merge per coin (only for multi strategy with AI)
            if is_multi:
                candidates = MultiStrategy.merge(candidates)

        # 6. AI advisor call
        open_positions = await self._positions.get_open_positions()

        # Pre-fetch order books for positions + opportunity coins
        ob_coins: set[str] = {pos["symbol"] for pos in open_positions}
        ob_coins |= {d.symbol for d in candidates if d.symbol and d.action in ("BUY", "SHORT")}
        order_books = await self._fetch_order_books(ob_coins) if ob_coins else {}

        # Enrich positions with current prices, PnL, indicators
        # Market context is extracted to a shared top-level market_data dict
        market_data: dict[str, dict[str, Any]] = {}
        now_utc = datetime.now(timezone.utc)
        for pos in open_positions:
            mid = self._market_data.get_mid_price(pos["symbol"])
            if mid and mid > 0:
                pos["price"] = mid
                direction = pos.get("direction", "LONG")
                if direction == "LONG":
                    pos["upnl"] = round((mid - pos["entry_price"]) * pos["quantity"], 4)
                    pos["pnl_pct"] = round((mid - pos["entry_price"]) / pos["entry_price"] * 100, 2)
                else:
                    pos["upnl"] = round((pos["entry_price"] - mid) * pos["quantity"], 4)
                    pos["pnl_pct"] = round((pos["entry_price"] - mid) / pos["entry_price"] * 100, 2)
            # Rename entry_price → entry for compact payload
            if "entry_price" in pos:
                pos["entry"] = pos.pop("entry_price")
            if pos.get("opened_at"):
                opened = datetime.fromisoformat(pos["opened_at"].replace("Z", "+00:00"))
                pos["age_min"] = round((now_utc - opened).total_seconds() / 60, 1)
            sym = pos["symbol"]
            pos["indicators"] = self._get_indicators(sym)
            if sym not in market_data:
                market_data[sym] = self._get_market_context(
                    sym, asset_ctx_map=asset_ctx_map, order_books=order_books,
                )

        # Build opportunity list from candidates (entries only)
        opportunities = []
        for d in candidates:
            if d.action in ("BUY", "SHORT"):
                opp = {
                    "symbol": d.symbol,
                    "action": d.action,
                    "confidence": d.confidence,
                    "strategy": d.strategy_type or "unknown",
                    "reasoning": d.reasoning,
                    "price": self._market_data.get_mid_price(d.symbol) or 0,
                    "sl": d.stop_loss,
                    "tp": d.take_profit,
                    "size_pct": d.size_pct,
                    "expected_move_pct": d.expected_move_pct,
                }
                sym = d.symbol
                if sym:
                    opp["indicators"] = self._get_indicators(sym)
                    if sym not in market_data:
                        market_data[sym] = self._get_market_context(
                            sym, asset_ctx_map=asset_ctx_map, order_books=order_books,
                        )
                opportunities.append(opp)

        # Cap opportunities: score >= 0.7 required (deferred-ready exempt), top 15 by score
        MIN_SCORE_FOR_AI = 0.75
        MAX_OPPORTUNITIES_PER_CYCLE = 15
        deferred_ready_opps = [o for o in opportunities if o["symbol"] in ready_symbols]
        regular_opps = [o for o in opportunities if o["symbol"] not in ready_symbols]
        # Filter: only score >= 0.7 for non-deferred opportunities
        qualified = [o for o in regular_opps if o["confidence"] >= MIN_SCORE_FOR_AI]
        below_threshold = len(regular_opps) - len(qualified)
        # Merge deferred-ready + qualified, sort by score, take top 15
        all_eligible = deferred_ready_opps + qualified
        all_eligible.sort(key=lambda o: o["confidence"], reverse=True)
        opportunities = all_eligible[:MAX_OPPORTUNITIES_PER_CYCLE]
        dropped = len(all_eligible) - len(opportunities)
        if below_threshold > 0 or dropped > 0:
            logger.info(
                "Filtered opportunities: %d sent (deferred_ready=%d, qualified=%d, below_%.0f=%d, cap_dropped=%d)",
                len(opportunities), len(deferred_ready_opps),
                len(qualified), MIN_SCORE_FOR_AI * 100, below_threshold, dropped,
            )

        # Filter out positions with deferred holds
        positions_for_ai = open_positions
        if not self._no_ai:
            # Check if any deferred holds are ready for re-evaluation
            ready_holds = await self._advisor.check_deferred_holds(mid_prices)
            # Also remove holds for positions that were closed
            open_syms = {p["symbol"] for p in open_positions}
            for sym in list(self._advisor.deferred_hold_symbols):
                if sym not in open_syms:
                    await self._advisor.remove_deferred_hold(sym)
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

        # Skip opportunities when at max positions — can't open anything
        max_pos = metrics.get("max_open_positions", 15)
        cur_pos = metrics.get("open_positions", 0)
        if opportunities and cur_pos >= max_pos:
            logger.info(
                "Max positions reached (%d/%d) — dropping %d opportunities, AI will only review existing positions",
                cur_pos, max_pos, len(opportunities),
            )
            opportunities = []

        if not self._no_ai and (positions_for_ai or opportunities):
            utilization = metrics.get("capital_utilization", 0)
            account = {
                "balance_usdc": metrics.get("current_balance", 0),
                "daily_pnl_pct": -metrics.get("daily_drawdown_pct", 0),
                "total_pnl": metrics.get("current_balance", 0) - metrics.get("peak_balance", 0),
                "open_pos": metrics.get("open_positions", 0),
                "max_pos": metrics.get("max_open_positions", 15),
                "util_pct": round(utilization * 100, 1),
                "margin_used": metrics.get("total_margin_used", 0),
                "margin_free": metrics.get("available_margin", 0),
                "target_util_pct": round(metrics.get("target_utilization", 0.5) * 100, 1),
            }
            recent_trades = await self._db.get_recent_trades(10)
            trade_stats = await self._db.get_trade_stats()
            account["win_rate"] = trade_stats.get("win_rate", 0)
            account["consec_losses"] = trade_stats.get("consecutive_losses", 0)
            account["default_leverage"] = self._settings.hyperliquid.default_leverage
            account["usdc_per_position"] = self._settings.risk.usdc_per_position
            account["max_trade_pct"] = self._settings.risk.max_trade_pct
            account["trailing_half_pct"] = self._settings.risk.trailing_half_pct
            account["trailing_breakeven_pct"] = self._settings.risk.trailing_breakeven_pct
            account["trailing_start_pct"] = self._settings.risk.trailing_start_pct

            deferred_summary = self._advisor.get_deferred_summary()
            ai_response = await self._advisor.consult(
                positions=positions_for_ai,
                opportunities=opportunities,
                account=account,
                market_data=market_data,
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
                elif pa["action"] == "FLIP":
                    adj = pa.get("adjustments", {})
                    candidates.append(Decision(
                        action="FLIP",
                        symbol=pa["symbol"],
                        confidence=0.9,
                        reasoning=f"AI: {pa.get('reasoning', '')}",
                        strategy_type="ai_advisor",
                        stop_loss=adj.get("stop_loss"),
                        take_profit=adj.get("take_profit"),
                        size_pct=adj.get("size_pct"),
                        leverage=adj.get("leverage"),
                    ))
                elif pa["action"] == "HOLD":
                    defer_cond = pa.get("defer")
                    if defer_cond:
                        await self._advisor.defer_hold(pa["symbol"], defer_cond)
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
                    # Defer the opportunity — keyed by (symbol, strategy_type)
                    orig = next((d for d in candidates if d.symbol == oa["symbol"]), None)
                    original_action = orig.action if orig else "BUY"
                    strategy_type = orig.strategy_type if orig else "unknown"
                    orig_confidence = orig.confidence if orig else 0.0
                    await self._advisor.defer(oa["symbol"], original_action, oa.get("defer", {}), strategy_type, orig_confidence)
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
                    # Entry price gate — pick whichever field is relevant for the action
                    if adj.get("max_entry_price") is not None and orig.action == "BUY":
                        orig.entry_price_limit = adj["max_entry_price"]
                    elif adj.get("min_entry_price") is not None and orig.action == "SHORT":
                        orig.entry_price_limit = adj["min_entry_price"]
                    approved.append(orig)

            # Keep AI-approved entries + all CLOSE/SELL/SCALE_UP/FLIP decisions
            candidates = approved + [d for d in candidates if d.action in ("CLOSE", "SELL", "SCALE_UP", "FLIP")]
        else:
            if not self._no_ai:
                # AI enabled but nothing to review — drop unapproved entry candidates
                candidates = [d for d in candidates if d.action in ("CLOSE", "SELL", "SCALE_UP", "FLIP")]

        # 6b. Protect positions with profitable SL from premature strategy exits
        # When trailing SL is already in profit, only AI or SL/TP monitor should close
        if not self._no_ai:
            protected_syms: set[str] = set()
            for pos in open_positions:
                sl = pos.get("stop_loss")
                entry = pos.get("entry") or pos.get("entry_price")
                direction = pos.get("direction", "LONG")
                if sl is not None and entry:
                    if (direction == "LONG" and sl >= entry) or (direction == "SHORT" and sl <= entry):
                        protected_syms.add(pos["symbol"])
            if protected_syms:
                before = len(candidates)
                candidates = [
                    d for d in candidates
                    if not (d.action in ("CLOSE", "SELL") and d.symbol in protected_syms
                            and d.strategy_type != "ai_advisor")
                ]
                dropped = before - len(candidates)
                if dropped:
                    logger.info(
                        "Protected %d profitable-SL position(s) from strategy exit: %s",
                        dropped, ", ".join(protected_syms),
                    )

        # 7. Sort: CLOSE/FLIP first, then SCALE_UP, then entries
        _ACTION_ORDER = {"SELL": 0, "CLOSE": 0, "FLIP": 0, "SCALE_UP": 1, "BUY": 2, "SHORT": 2, "HOLD": 3}
        candidates.sort(key=lambda d: _ACTION_ORDER.get(d.action, 3))

        # Anti-churning (FLIP excluded — it's an intentional reopen)
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

            async with self._close_lock:
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
        await self._write_status(metrics)

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
        """Extract funding rates from asset contexts and pass to strategies.

        Called from _tick() after _fetch_asset_contexts() populates _asset_ctx_map.
        Each asset context has a "funding" field with the current funding rate.
        """
        funding_rates: dict[str, float] = {}
        for symbol, ctx in self._asset_ctx_map.items():
            rate = ctx.get("funding")
            if rate is not None:
                try:
                    funding_rates[symbol] = float(rate)
                except (ValueError, TypeError):
                    pass

        if funding_rates:
            self._funding_cache = funding_rates
            # Propagate to strategy (MultiStrategy propagates to all sub-strategies)
            if hasattr(self._strategy, "set_funding_rates"):
                self._strategy.set_funding_rates(funding_rates)
            logger.debug(
                "Funding rates updated: %d coins, extremes: %s",
                len(funding_rates),
                ", ".join(
                    f"{s}={r:.6f}" for s, r in sorted(
                        funding_rates.items(), key=lambda x: abs(x[1]), reverse=True,
                    )[:5]
                ),
            )

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
                            "RECONCILE: Imported orphaned %s %s — entry=%g size=%g SL=%g TP=%g (id=%d)",
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

    async def _sl_tp_monitor(self) -> None:
        """Independent SL/TP check every ~2 seconds."""
        logger.info("SL/TP monitor started (interval: 2s)")
        while self._running:
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=2)
                break  # shutdown signalled
            except asyncio.TimeoutError:
                pass
            try:
                await self._market_data.refresh_mid_prices()
                # Check for manual close requests from webapp
                async with self._close_lock:
                    await self._process_ask_close()
                open_pos = await self._positions.get_open_positions()
                n = len(open_pos)
                if n:
                    logger.info("SL/TP check: %d open position(s)", n)
                async with self._close_lock:
                    await self._check_positions()
            except Exception:
                logger.exception("SL/TP monitor error")
        logger.info("SL/TP monitor stopped")

    async def _check_positions(self) -> None:
        """Check all open positions for SL/TP/trailing/time stop hits."""
        positions = await self._positions.get_open_positions()
        if not positions:
            return

        # Get current prices from REST cache (refreshed by monitor before this call)
        prices: dict[str, float] = {}
        for pos in positions:
            sym = pos["symbol"]
            if sym not in prices:
                mid = self._market_data.get_mid_price(sym)
                if mid and mid > 0:
                    prices[sym] = mid
                else:
                    logger.warning("No price for %s — SL/TP skipped (mid_prices has %d coins)", sym, len(self._market_data._mid_prices))

        if prices:
            for sym, px in prices.items():
                logger.info("SL/TP price %s: %g", sym, px)

        to_close = await self._positions.check_sl_tp(prices)

        for item in to_close:
            pos = item["position"]
            reason = item["reason"]
            action = item.get("action", "CLOSE")
            symbol = pos["symbol"]
            price = prices.get(symbol, pos["entry_price"])
            direction = pos.get("direction", "LONG")

            # ── PARTIAL_CLOSE handling ──
            if action == "PARTIAL_CLOSE":
                close_pct = self._settings.risk.partial_tp_pct
                try:
                    pnl, closed_qty, new_id = await self._positions.partial_close(
                        pos["id"], close_pct, price, reason,
                    )
                    if not self._paper:
                        # Partial close on exchange
                        sz = self._client.round_size(symbol, closed_qty)
                        if sz > 0:
                            await self._client.close_position(symbol, sz=sz)
                    prefix = "[PAPER] " if self._paper else ""
                    await self._db.insert_trade(
                        symbol=symbol, side="CLOSE", price=price,
                        quantity=closed_qty, pnl=pnl,
                        strategy=pos["strategy"],
                        notes=f"{prefix}partial_tp: {close_pct:.0f}%",
                    )
                    logger.info(
                        "%sPartial TP %s %s: closed %.6f @ %g PnL=%.4f → remaining #%d",
                        prefix, direction, symbol, closed_qty, price, pnl, new_id,
                    )
                    try:
                        await self._db.insert_event(
                            cycle=self._cycle_count, symbol=symbol,
                            event_type="PARTIAL_TP", source="auto_close",
                            action="PARTIAL_CLOSE", reasoning=reason,
                            details={
                                "price": round(price, 6), "pnl": round(pnl, 4),
                                "closed_qty": closed_qty, "close_pct": close_pct,
                                "new_position_id": new_id,
                                "paper": self._paper,
                            },
                            position_id=pos["id"],
                        )
                    except Exception:
                        logger.debug("Failed to log PARTIAL_TP event", exc_info=True)
                except Exception:
                    logger.exception("Failed to partial close %s", symbol)
                continue

            # ── Full CLOSE handling ──
            if self._paper:
                logger.info("[PAPER] Auto-close %s %s: %s @ %g", direction, symbol, reason, price)
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
                        "Auto-closed %s %s: %s @ %g PnL=%.4f",
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

    # ── Manual close (ask_close from webapp) ────────────────

    async def _process_ask_close(self) -> None:
        """Close positions flagged with ask_close by the webapp."""
        try:
            to_close = await self._db.get_ask_close_positions()
        except Exception:
            logger.debug("Failed to query ask_close positions", exc_info=True)
            return

        for pos in to_close:
            symbol = pos["symbol"]
            direction = pos.get("direction", "LONG")
            price = self._market_data.get_mid_price(symbol)
            if not price or price <= 0:
                logger.warning("ask_close: no price for %s — skipping", symbol)
                continue

            reason = "manual_close"
            try:
                if not self._paper:
                    await self._client.close_position(symbol)
                pnl = await self._positions.close_position(pos["id"], price, reason)
                await self._db.insert_trade(
                    symbol=symbol, side="CLOSE", price=price,
                    quantity=pos["quantity"], pnl=pnl,
                    strategy=pos["strategy"],
                    notes=f"{'[PAPER] ' if self._paper else ''}Manual close from webapp",
                )
                self._cooldown.record_trade_result(symbol, pnl > 0)
                await self._telegram.notify_trade(
                    action="CLOSE", symbol=symbol, qty=pos["quantity"], price=price,
                )
                logger.info(
                    "%sManual close %s %s @ %g PnL=%.4f (ask_close)",
                    "[PAPER] " if self._paper else "", direction, symbol, price, pnl,
                )
                try:
                    await self._db.insert_event(
                        cycle=self._cycle_count, symbol=symbol,
                        event_type="MANUAL_CLOSE", source="webapp",
                        action="CLOSE", reasoning="Manual close requested from webapp",
                        details={"price": round(price, 6), "pnl": round(pnl, 4), "paper": self._paper},
                        position_id=pos["id"],
                    )
                except Exception:
                    logger.debug("Failed to log MANUAL_CLOSE event", exc_info=True)
            except Exception:
                logger.exception("Failed to manual-close %s (ask_close)", symbol)

    # ── Dashboard status ──────────────────────────────────────

    async def _write_status(self, metrics: dict[str, Any]) -> None:
        """Write live bot state to DB for the dashboard."""
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
            await self._db.set_state("bot_status", json.dumps(status, default=str))
        except Exception:
            logger.debug("Failed to write bot_status to DB", exc_info=True)

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

    def _write_ai_context_file(self, context_data: dict[str, dict[str, Any]]) -> str:
        """Write market context data to a JSON file for the AI advisor.

        The file contains market data per symbol, keyed by symbol.
        price_action_5m is already in [O, H, L, C, V] array format.
        Returns the absolute path.
        """
        ctx_path = Path("data") / "ai_context" / "market_data.json"
        ctx_path.write_text(json.dumps(context_data, default=str))
        return str(ctx_path.resolve())

    async def _fetch_asset_contexts(self) -> dict[str, dict[str, Any]]:
        """Fetch funding rates, OI, mark prices for all coins (single API call)."""
        try:
            meta, ctxs = await self._client.get_meta_and_asset_ctxs()
            universe = meta.get("universe", [])
            mapping: dict[str, dict[str, Any]] = {}
            now = time.time()
            for asset, ctx in zip(universe, ctxs):
                coin = asset["name"]
                mapping[coin] = ctx
                # Update OI snapshot cache
                oi = float(ctx.get("openInterest", 0))
                if coin not in self._oi_snapshots:
                    self._oi_snapshots[coin] = []
                self._oi_snapshots[coin].append((now, oi))
                # Trim to ~5h of history
                cutoff = now - 5 * 3600
                self._oi_snapshots[coin] = [
                    (t, v) for t, v in self._oi_snapshots[coin] if t >= cutoff
                ]
            self._asset_ctx_map = mapping
            return mapping
        except Exception:
            logger.warning("Failed to fetch asset contexts", exc_info=True)
            return self._asset_ctx_map

    async def _fetch_order_books(self, coins: set[str]) -> dict[str, dict[str, Any]]:
        """Fetch L2 order book snapshots for specific coins (concurrent)."""
        books: dict[str, dict[str, Any]] = {}
        sem = asyncio.Semaphore(5)

        async def _fetch_one(coin: str) -> None:
            async with sem:
                try:
                    books[coin] = await self._client.get_l2_snapshot(coin)
                except Exception:
                    logger.debug("Failed to fetch L2 for %s", coin, exc_info=True)

        await asyncio.gather(*[_fetch_one(c) for c in coins])
        return books

    def _get_oi_change_4h(self, coin: str) -> float | None:
        """Compute OI change % over the last ~4 hours from cached snapshots."""
        snapshots = self._oi_snapshots.get(coin, [])
        if len(snapshots) < 2:
            return None
        now = time.time()
        target_time = now - 4 * 3600
        best = min(snapshots, key=lambda x: abs(x[0] - target_time))
        # Must have data within 1h of the 4h-ago target
        if abs(best[0] - target_time) > 3600:
            return None
        current_oi = snapshots[-1][1]
        old_oi = best[1]
        if old_oi <= 0:
            return None
        return round((current_oi - old_oi) / old_oi * 100, 3)

    def _get_market_context(self, symbol: str, *, asset_ctx_map: dict[str, dict[str, Any]] | None = None, order_books: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        """Build enriched market context for AI advisor.

        Returns price action (last 12 5m candles), % changes (1h/4h/24h),
        support/resistance levels (4h/24h), and 4h trend from 1h EMAs.
        """
        ctx: dict[str, Any] = {}
        price = self._market_data.get_mid_price(symbol)
        if not price or price <= 0:
            return ctx

        # ── Price action: last 6 candles 5m (~30 min) ──
        candles_5m = self._market_data.get_candles(symbol, "5m")
        if candles_5m is not None and len(candles_5m) >= 6:
            last_n = candles_5m.tail(6)
            ctx["price_action_5m"] = [
                [
                    round(float(row["open"]), 6),
                    round(float(row["high"]), 6),
                    round(float(row["low"]), 6),
                    round(float(row["close"]), 6),
                    int(float(row["volume"])),
                ]
                for _, row in last_n.iterrows()
            ]

        # ── % changes: 1h, 4h, 24h ──
        if candles_5m is not None and len(candles_5m) >= 12:
            try:
                price_1h_ago = float(candles_5m["close"].iloc[-12])
                ctx["change_1h_pct"] = round((price - price_1h_ago) / price_1h_ago * 100, 3)
            except (IndexError, ZeroDivisionError):
                pass
            if len(candles_5m) >= 48:
                try:
                    price_4h_ago = float(candles_5m["close"].iloc[-48])
                    ctx["change_4h_pct"] = round((price - price_4h_ago) / price_4h_ago * 100, 3)
                except (IndexError, ZeroDivisionError):
                    pass

        candles_1h = self._market_data.get_candles(symbol, "1h")
        if candles_1h is not None and len(candles_1h) >= 24:
            try:
                price_24h_ago = float(candles_1h["close"].iloc[-24])
                ctx["change_24h_pct"] = round((price - price_24h_ago) / price_24h_ago * 100, 3)
            except (IndexError, ZeroDivisionError):
                pass

        # ── Support / Resistance: high/low over 4h and 24h windows ──
        if candles_1h is not None:
            if len(candles_1h) >= 4:
                tail4 = candles_1h.tail(4)
                ctx["support_4h"] = round(float(tail4["low"].min()), 6)
                ctx["resistance_4h"] = round(float(tail4["high"].max()), 6)
            if len(candles_1h) >= 24:
                tail24 = candles_1h.tail(24)
                ctx["support_24h"] = round(float(tail24["low"].min()), 6)
                ctx["resistance_24h"] = round(float(tail24["high"].max()), 6)

        # ── Trend 4h: EMA12/EMA26 on 1h candles ──
        if candles_1h is not None and len(candles_1h) >= 30:
            close_1h = candles_1h["close"]
            ema12 = close_1h.ewm(span=12, adjust=False).mean()
            ema26 = close_1h.ewm(span=26, adjust=False).mean()
            ema12_now = float(ema12.iloc[-1])
            ema26_now = float(ema26.iloc[-1])
            # Slope: EMA12 change over last 5 candles (5h) as % of price
            ema12_5ago = float(ema12.iloc[-5]) if len(ema12) >= 5 else ema12_now
            slope = (ema12_now - ema12_5ago) / price * 100

            if ema12_now > ema26_now and price > ema12_now and slope > 0:
                trend_4h = "BULLISH"
            elif ema12_now < ema26_now and price < ema12_now and slope < 0:
                trend_4h = "BEARISH"
            else:
                trend_4h = "NEUTRAL"

            # Override: real price action contradicts EMA signal
            change_4h = ctx.get("change_4h_pct")
            change_24h = ctx.get("change_24h_pct")
            if trend_4h == "BULLISH" and change_4h is not None and change_4h < -2.0:
                trend_4h = "NEUTRAL"
            if trend_4h == "BEARISH" and change_4h is not None and change_4h > 2.0:
                trend_4h = "NEUTRAL"
            # Strong 24h override: unmistakable trend direction
            if change_24h is not None and change_24h < -8.0:
                trend_4h = "BEARISH"
            if change_24h is not None and change_24h > 8.0:
                trend_4h = "BULLISH"

            ctx["trend_4h"] = trend_4h

        # ── Trend 1h: EMA50/EMA200 from TrendFilter (structural trend) ──
        tf_state = self._trend_filter.get_state(symbol)
        if tf_state:
            ctx["trend_1h"] = tf_state["trend"]

        # ── Order book: top 3 bid/ask levels ──
        if order_books and symbol in order_books:
            try:
                levels = order_books[symbol].get("levels", [])
                if len(levels) >= 2:
                    bids = levels[0][:3]
                    asks = levels[1][:3]
                    ctx["order_book"] = {
                        "bids": [{"price": float(b["px"]), "size": float(b["sz"])} for b in bids],
                        "asks": [{"price": float(a["px"]), "size": float(a["sz"])} for a in asks],
                    }
            except (KeyError, ValueError, TypeError):
                pass

        # ── Funding rate (current) ──
        if asset_ctx_map and symbol in asset_ctx_map:
            actx = asset_ctx_map[symbol]
            try:
                funding = float(actx.get("funding", 0))
                ctx["funding_rate"] = round(funding, 8)
            except (ValueError, TypeError):
                pass
            try:
                ctx["open_interest"] = round(float(actx.get("openInterest", 0)), 4)
            except (ValueError, TypeError):
                pass

        # ── OI change % over ~4h ──
        oi_change = self._get_oi_change_4h(symbol)
        if oi_change is not None:
            ctx["oi_change_4h_pct"] = oi_change

        return ctx

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
            elif decision.action == "FLIP":
                return await self._execute_flip(decision, size)
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
            "[PAPER] Would execute: %s %s size=%.2f%% price=%g",
            decision.action, symbol, decision.size_pct or 0, price,
        )

        # Entry price gate — skip if price moved beyond AI-specified limit
        if decision.entry_price_limit is not None and decision.action in ("BUY", "SHORT"):
            if decision.action == "BUY" and price > decision.entry_price_limit:
                logger.warning(
                    "[PAPER] Price gate BLOCKED BUY %s: price %g > max_entry %g",
                    symbol, price, decision.entry_price_limit,
                )
                return None
            if decision.action == "SHORT" and price < decision.entry_price_limit:
                logger.warning(
                    "[PAPER] Price gate BLOCKED SHORT %s: price %g < min_entry %g",
                    symbol, price, decision.entry_price_limit,
                )
                return None

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
                    logger.info("[PAPER] SCALE_UP %s %s +%.6f @ %g", direction, symbol, qty, price)
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

        elif decision.action == "FLIP":
            pos = await self._positions.get_position_for_symbol(symbol)
            if pos:
                old_direction = pos.get("direction", "LONG")
                new_direction = "SHORT" if old_direction == "LONG" else "LONG"
                # Close existing
                pnl = await self._positions.close_position(pos["id"], price, "flip")
                await self._db.insert_trade(
                    symbol=symbol, side="CLOSE", price=price,
                    quantity=pos["quantity"], pnl=pnl,
                    strategy="ai_advisor", notes="[PAPER] FLIP close",
                )
                logger.info("[PAPER] FLIP close %s %s @ %g PnL=%.4f", old_direction, symbol, price, pnl)
                self._cooldown.record_trade_result(symbol, pnl > 0)
                # Open new in opposite direction
                new_leverage = decision.leverage or pos.get("leverage", self._settings.hyperliquid.default_leverage)
                notional = (size.size_usdc * new_leverage) if size and size.size_usdc > 0 else 0
                qty = notional / price if price > 0 else 0
                qty = self._client.round_size(symbol, qty)
                if qty > 0:
                    new_side = "BUY" if new_direction == "LONG" else "SHORT"
                    await self._positions.open_position(
                        symbol=symbol, entry_price=price, quantity=qty,
                        stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                        strategy="ai_advisor", direction=new_direction, leverage=new_leverage,
                    )
                    await self._db.insert_trade(
                        symbol=symbol, side=new_side, price=price, quantity=qty,
                        strategy="ai_advisor", notes=f"[PAPER] FLIP open {new_direction}",
                    )
                    logger.info("[PAPER] FLIP open %s %s qty=%.6f @ %g lev=%dx", new_direction, symbol, qty, price, new_leverage)
                try:
                    await self._db.insert_event(
                        cycle=self._cycle_count, symbol=symbol,
                        event_type="TRADE_EXIT", source="execution", action="FLIP",
                        details={"price": round(price, 6), "pnl": round(pnl, 4), "old_direction": old_direction, "paper": True},
                        position_id=pos["id"],
                    )
                    await self._db.insert_event(
                        cycle=self._cycle_count, symbol=symbol,
                        event_type="TRADE_ENTRY", source="execution", action="FLIP",
                        details={"price": round(price, 6), "new_direction": new_direction, "quantity": qty, "paper": True},
                    )
                except Exception:
                    logger.debug("Failed to log FLIP events", exc_info=True)
                return pnl
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

        # Entry price gate — skip if price moved beyond AI-specified limit
        if decision.entry_price_limit is not None:
            if is_buy and price > decision.entry_price_limit:
                logger.warning(
                    "Price gate BLOCKED BUY %s: price %g > max_entry %g (moved +%.2f%%)",
                    symbol, price, decision.entry_price_limit,
                    (price / decision.entry_price_limit - 1) * 100,
                )
                return None
            if not is_buy and price < decision.entry_price_limit:
                logger.warning(
                    "Price gate BLOCKED SHORT %s: price %g < min_entry %g (moved -%.2f%%)",
                    symbol, price, decision.entry_price_limit,
                    (1 - price / decision.entry_price_limit) * 100,
                )
                return None

        leverage = decision.leverage or self._settings.hyperliquid.default_leverage
        leverage = min(leverage, self._settings.risk.max_leverage)

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
            "%s executed: %s qty=%.6f price=%g SL=%g TP=%g lev=%dx",
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
            "SCALE_UP executed: %s %s +%.6f @ %g lev=%dx",
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

    async def _execute_flip(self, decision: Decision, size: Any) -> float | None:
        """Execute a FLIP: close losing position + open in opposite direction."""
        symbol = decision.symbol
        pos = await self._positions.get_position_for_symbol(symbol)

        if not pos:
            logger.warning("FLIP %s: no position in tracker — skipping", symbol)
            return None

        old_direction = pos.get("direction", "LONG")
        new_direction = "SHORT" if old_direction == "LONG" else "LONG"
        is_buy = new_direction == "LONG"

        # Step 1: Close existing position
        await self._client.close_position(symbol)
        mid = self._market_data.get_mid_price(symbol)
        fill_price = mid or await self._client.get_price(symbol)
        pnl = await self._positions.close_position(pos["id"], fill_price, "flip")

        await self._db.insert_trade(
            symbol=symbol, side="CLOSE", price=fill_price,
            quantity=pos["quantity"], pnl=pnl,
            strategy="ai_advisor", notes="FLIP close",
        )
        self._cooldown.record_trade_result(symbol, pnl > 0)
        logger.info("FLIP close %s %s @ %g PnL=%.4f", old_direction, symbol, fill_price, pnl)

        try:
            await self._db.insert_event(
                cycle=self._cycle_count, symbol=symbol,
                event_type="TRADE_EXIT", source="execution", action="FLIP",
                details={"price": round(fill_price, 6), "pnl": round(pnl, 4) if pnl else None, "old_direction": old_direction},
                position_id=pos["id"],
            )
        except Exception:
            logger.debug("Failed to log FLIP TRADE_EXIT event", exc_info=True)

        # Step 2: Open new position in opposite direction
        new_leverage = decision.leverage or pos.get("leverage", self._settings.hyperliquid.default_leverage)
        new_leverage = min(new_leverage, self._settings.risk.max_leverage)
        price = await self._client.get_price(symbol)

        try:
            is_cross = self._settings.hyperliquid.margin_mode == "cross"
            await self._client.update_leverage(symbol, new_leverage, is_cross)
        except Exception:
            logger.warning("Failed to set leverage for FLIP %s", symbol)

        notional = (size.size_usdc * new_leverage) if size and size.size_usdc > 0 else 0
        raw_qty = notional / price if price > 0 else 0
        qty = self._client.round_size(symbol, raw_qty)

        if qty <= 0:
            logger.critical(
                "FLIP %s: closed %s but new qty is 0 — position closed but NOT reopened!",
                symbol, old_direction,
            )
            return pnl

        try:
            await self._client.place_market_order(coin=symbol, is_buy=is_buy, size=qty)
        except Exception:
            logger.critical(
                "FLIP %s: closed %s but OPEN failed — position is FLAT on HL, not tracked!",
                symbol, old_direction,
            )
            raise

        try:
            await self._positions.open_position(
                symbol=symbol, entry_price=price, quantity=qty,
                stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                strategy="ai_advisor", direction=new_direction, leverage=new_leverage,
            )
        except Exception:
            logger.critical(
                "FLIP %s: opened %s on HL but tracker FAILED — position LIVE but untracked!",
                symbol, new_direction,
            )
            raise

        new_side = "BUY" if is_buy else "SHORT"
        await self._db.insert_trade(
            symbol=symbol, side=new_side, price=price,
            quantity=qty, strategy="ai_advisor", notes=f"FLIP open {new_direction}",
        )
        await self._telegram.notify_trade(
            action="FLIP", symbol=symbol, qty=qty, price=price,
        )
        logger.info(
            "FLIP executed: %s → %s %s qty=%.6f @ %g lev=%dx",
            old_direction, new_direction, symbol, qty, price, new_leverage,
        )

        try:
            await self._db.insert_event(
                cycle=self._cycle_count, symbol=symbol,
                event_type="TRADE_ENTRY", source="execution", action="FLIP",
                details={
                    "price": round(price, 6), "quantity": qty,
                    "new_direction": new_direction, "leverage": new_leverage,
                    "old_direction": old_direction,
                },
            )
        except Exception:
            logger.debug("Failed to log FLIP TRADE_ENTRY event", exc_info=True)

        return pnl

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
            "CLOSE executed: %s @ %g PnL=%s",
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
    parser.add_argument(
        "--strategy",
        choices=[
            "multi", "mean_reversion", "rsi_div", "trend_following",
            "bb_squeeze", "breakout", "btc_correlation", "buy_the_dip",
            "ema_crossover", "funding_rate", "macd_divergence",
            "mtf_confluence", "session_momentum", "volume_spike",
        ],
        default="multi",
        help="Strategy mode: multi (default) or single strategy name",
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
