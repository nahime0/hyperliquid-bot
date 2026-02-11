"""Hyperliquid Perpetual Trading Bot — main entry point.

Architecture:
  Loop → Strategy generates candidates → Risk validates → [AI veto] → Execute

The bot trades autonomously with codified rules (trend filter, mean reversion).
AI is demoted to an optional review/veto role.

Usage:
    .venv/bin/python main.py                     # testnet (default) + dashboard on :8080
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
from core.ai_engine import AIEngine, Decision, Tier
from core.client import HyperliquidClient
from core.market_data import MarketData
from data.db import Database
from risk.position_tracker import PositionTracker
from risk.risk_manager import RiskManager, ValidationResult
from strategies.cooldown import CooldownTracker
from strategies.mean_reversion import MeanReversionStrategy
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
    ) -> None:
        self._settings = settings
        self._paper = paper
        self._no_ai = no_ai
        self._once = once
        self._running = False
        self._dashboard_port = dashboard_port
        self._no_dashboard = no_dashboard

        # Components (initialised in start())
        self._client = HyperliquidClient(settings)
        self._db = Database(settings.db_path)
        self._market_data = MarketData(self._client, settings)
        self._ai = AIEngine(settings, self._db)
        self._positions = PositionTracker(self._db, settings.risk)
        self._risk = RiskManager(settings.risk, self._client, self._db, self._positions)

        # Autonomous components
        self._trend_filter = TrendFilter(self._market_data)
        self._cooldown = CooldownTracker(settings.risk)
        self._mean_rev = MeanReversionStrategy(
            self._market_data,
            self._trend_filter,
            self._cooldown,
            self._positions,
            settings.risk,
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
        logger.info("  AI review: %s  |  Paper: %s  |  Once: %s", not self._no_ai, self._paper, self._once)
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

        # Set leverage for core coins
        is_cross = cfg.margin_mode == "cross"
        for coin in CORE_COINS:
            if coin in self._active_coins:
                try:
                    await self._client.update_leverage(coin, cfg.default_leverage, is_cross)
                except Exception:
                    logger.debug("Failed to set leverage for %s", coin, exc_info=True)

        # Market data — start WebSocket feeds
        await self._market_data.start(self._active_coins)

        # Strategies — pass discovered coins
        self._mean_rev.set_pairs(self._active_coins)
        self._mean_rev.set_max_funding_rate(cfg.max_funding_rate)
        await self._mean_rev.start()

        # Risk manager
        self._risk.set_active_pairs(self._active_coins)
        await self._risk.start()

        # AI engine (for review mode)
        if not self._no_ai:
            await self._ai.start()

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
            await self._mean_rev.stop()
        except Exception:
            logger.exception("Error stopping mean reversion")

        # Final balance snapshot
        try:
            await self._risk.snapshot_balance()
        except Exception:
            logger.exception("Error saving final balance snapshot")

        # AI engine
        if not self._no_ai:
            try:
                await self._ai.close()
            except Exception:
                logger.exception("Error closing AI engine")

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
            sleep_time = max(0, interval - elapsed)
            if sleep_time > 0:
                logger.debug("Cycle #%d took %.1fs — sleeping %.1fs", self._cycle_count, elapsed, sleep_time)
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=sleep_time)
                    break
                except asyncio.TimeoutError:
                    pass

    async def _tick(self) -> None:
        """One iteration of the main loop."""
        logger.info("─── Cycle #%d ───", self._cycle_count)

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
        await self._mean_rev.update()

        # 5. Generate autonomous decisions
        candidates = await self._mean_rev.generate_decisions()

        if candidates:
            logger.info(
                "Strategy generated %d candidate(s): %s",
                len(candidates),
                ", ".join(f"{d.action} {d.symbol}" for d in candidates),
            )
        else:
            logger.info("No trading candidates this cycle")

        # 6. AI review (optional)
        if candidates and not self._no_ai:
            snapshot = await self._build_snapshot(metrics)
            candidates = await self._ai.review_decisions(candidates, snapshot)

        # 7. Sort: CLOSE first, then entries
        _ACTION_ORDER = {"SELL": 0, "CLOSE": 0, "BUY": 1, "SHORT": 1, "HOLD": 2}
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
                continue
            decision = validation.decision
            size = validation.size

            pnl = await self._execute(decision, size)

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

    # ── Funding rate refresh ──────────────────────────────────

    async def _refresh_funding(self) -> None:
        """Refresh funding rates and pass to strategy."""
        try:
            # Hyperliquid doesn't have a direct funding endpoint in the basic SDK,
            # but we can get it from clearinghouse state. For now, skip if unavailable.
            pass
        except Exception:
            logger.debug("Failed to refresh funding rates", exc_info=True)

    # ── Position SL/TP monitoring ─────────────────────────────

    async def _check_positions(self) -> None:
        """Check all open positions for SL/TP/trailing/time stop hits."""
        positions = await self._positions.get_open_positions()
        if not positions:
            return

        # Get current prices from mid prices (no API call)
        prices: dict[str, float] = {}
        for pos in positions:
            sym = pos["symbol"]
            if sym not in prices:
                mid = self._market_data.get_mid_price(sym)
                if mid and mid > 0:
                    prices[sym] = mid

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
                "strategies": {
                    "mean_reversion": self._mean_rev.get_state(),
                },
            }
            Path("data/bot_status.json").write_text(
                json.dumps(status, default=str)
            )
        except Exception:
            logger.debug("Failed to write bot_status.json", exc_info=True)

    # ── Snapshot building ────────────────────────────────────

    async def _build_snapshot(self, risk_metrics: dict[str, Any]) -> dict[str, Any]:
        """Assemble full market snapshot for AI review."""
        balances = {"USDC": risk_metrics.get("current_balance", 0)}
        recent_trades = await self._db.get_recent_trades(20)
        trade_stats = await self._db.get_trade_stats()

        open_positions = await self._positions.get_open_positions()
        now_utc = datetime.now(timezone.utc)
        position_symbols: set[str] = set()
        for pos in open_positions:
            position_symbols.add(pos["symbol"])
            direction = pos.get("direction", "LONG")
            mid = self._market_data.get_mid_price(pos["symbol"])
            if mid and mid > 0:
                pos["current_price"] = mid
                if direction == "LONG":
                    pos["unrealized_pnl"] = round((mid - pos["entry_price"]) * pos["quantity"], 4)
                    pos["pnl_pct"] = round((mid - pos["entry_price"]) / pos["entry_price"] * 100, 2)
                else:
                    pos["unrealized_pnl"] = round((pos["entry_price"] - mid) * pos["quantity"], 4)
                    pos["pnl_pct"] = round((pos["entry_price"] - mid) / pos["entry_price"] * 100, 2)
            if pos.get("opened_at"):
                opened = datetime.fromisoformat(pos["opened_at"].replace("Z", "+00:00"))
                age_min = (now_utc - opened).total_seconds() / 60
                pos["age_minutes"] = round(age_min, 1)

        # Only include coins with active signals or open positions
        mr_state = self._mean_rev.get_state()
        signal_symbols = set(mr_state.get("oversold", []) + mr_state.get("overbought", []))
        snapshot_coins = sorted(signal_symbols | position_symbols | set(CORE_COINS))

        snapshot = await self._market_data.generate_snapshot(
            coins=snapshot_coins,
            balances=balances,
            open_positions=open_positions,
            recent_trades=recent_trades,
            trade_stats=trade_stats,
        )

        snapshot["risk_metrics"] = risk_metrics
        snapshot["strategies"] = {
            "mean_reversion": mr_state,
        }
        snapshot["total_coins_monitored"] = len(self._active_coins)

        return snapshot

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
        leverage = self._settings.hyperliquid.default_leverage

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

        # Record position
        await self._positions.open_position(
            symbol=symbol, entry_price=price, quantity=qty,
            stop_loss=decision.stop_loss, take_profit=decision.take_profit,
            strategy=decision.strategy_type or "mean_reversion",
            direction=direction, leverage=leverage,
        )
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
        return None

    async def _execute_close(self, decision: Decision) -> float | None:
        """Close a position on Hyperliquid. Returns PnL."""
        symbol = decision.symbol
        pos = await self._positions.get_position_for_symbol(symbol)

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
