"""Test the 3-tier AI Decision Engine with a fake market snapshot.

Usage:
    python -m scripts.test_ai_engine
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_settings
from core.ai_engine import AIEngine
from data.db import Database
from utils.logger import setup_logging, get_logger

logger = get_logger(__name__)

FAKE_SNAPSHOT = {
    "timestamp": "2026-02-10T20:30:00+00:00",
    "markets": {
        "BTCUSDC": {
            "bid": 68792.93,
            "ask": 68795.78,
            "spread_pct": 0.004,
            "indicators": {
                "15m": {
                    "rsi": 42.3,
                    "bollinger": {"upper": 69500.0, "mid": 68800.0, "lower": 68100.0},
                    "macd": {"macd": -15.2, "signal": -8.7, "histogram": -6.5},
                    "ema": {"ema9": 68850.0, "ema21": 68900.0, "ema50": 69100.0},
                    "recent_candles": [
                        {"time": "2026-02-10T19:30:00", "open": 68950.0, "high": 69020.0, "low": 68800.0, "close": 68830.0, "volume": 125.4},
                        {"time": "2026-02-10T19:45:00", "open": 68830.0, "high": 68900.0, "low": 68750.0, "close": 68780.0, "volume": 98.2},
                        {"time": "2026-02-10T20:00:00", "open": 68780.0, "high": 68850.0, "low": 68700.0, "close": 68820.0, "volume": 110.7},
                        {"time": "2026-02-10T20:15:00", "open": 68820.0, "high": 68880.0, "low": 68790.0, "close": 68795.0, "volume": 87.3},
                        {"time": "2026-02-10T20:30:00", "open": 68795.0, "high": 68810.0, "low": 68770.0, "close": 68793.0, "volume": 45.1},
                    ],
                },
                "1h": {
                    "rsi": 38.7,
                    "bollinger": {"upper": 70200.0, "mid": 69100.0, "lower": 68000.0},
                    "macd": {"macd": -120.5, "signal": -85.3, "histogram": -35.2},
                    "ema": {"ema9": 69000.0, "ema21": 69200.0, "ema50": 69800.0},
                },
                "4h": {
                    "rsi": 35.1,
                    "bollinger": {"upper": 71500.0, "mid": 69500.0, "lower": 67500.0},
                    "macd": {"macd": -350.0, "signal": -200.0, "histogram": -150.0},
                    "ema": {"ema9": 69300.0, "ema21": 69800.0, "ema50": 70500.0},
                },
            },
        },
        "ETHUSDC": {
            "bid": 2006.60,
            "ask": 2007.09,
            "spread_pct": 0.024,
            "indicators": {
                "15m": {
                    "rsi": 55.2,
                    "bollinger": {"upper": 2030.0, "mid": 2010.0, "lower": 1990.0},
                    "macd": {"macd": 1.5, "signal": 0.8, "histogram": 0.7},
                    "ema": {"ema9": 2008.0, "ema21": 2005.0, "ema50": 2000.0},
                },
                "1h": {
                    "rsi": 48.3,
                    "bollinger": {"upper": 2050.0, "mid": 2015.0, "lower": 1980.0},
                    "macd": {"macd": -2.1, "signal": -1.0, "histogram": -1.1},
                    "ema": {"ema9": 2010.0, "ema21": 2015.0, "ema50": 2030.0},
                },
                "4h": {
                    "rsi": 44.0,
                    "bollinger": {"upper": 2080.0, "mid": 2020.0, "lower": 1960.0},
                    "macd": {"macd": -8.0, "signal": -5.0, "histogram": -3.0},
                    "ema": {"ema9": 2015.0, "ema21": 2025.0, "ema50": 2050.0},
                },
            },
        },
    },
    "portfolio": {
        "balances": {"USDC": 10000.0, "BNB": 1.0},
        "open_positions": [],
    },
    "trade_history": [],
    "trade_stats": {
        "total_trades": 0,
        "win_rate": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "consecutive_losses": 0,
    },
}


async def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)

    logger.info("=== 3-Tier AI Engine Test ===")

    db = Database(settings.project_root / "data" / "test_ai.db")
    await db.connect()

    engine = AIEngine(settings, db)
    await engine.start()

    try:
        # Test 1: Normal flow (Tier 1 → Tier 2, possibly → Tier 3)
        logger.info("--- Test 1: Normal decision flow ---")
        decision = await engine.decide(FAKE_SNAPSHOT)
        _print_decision(decision)

        # Verify DB
        decisions = await db.get_recent_decisions(limit=1)
        if decisions:
            d = decisions[0]
            logger.info("DB: id=%s, action=%s, tier=%s", d["id"], d["action"], d.get("tier"))

        # Test 2: Pre-screen block (low balance)
        logger.info("--- Test 2: PreScreen block (low balance) ---")
        low_balance_snapshot = {**FAKE_SNAPSHOT}
        low_balance_snapshot["portfolio"] = {
            "balances": {"USDC": 10.0},  # Below minimum
            "open_positions": [],
        }
        decision2 = await engine.decide(low_balance_snapshot)
        _print_decision(decision2)
        assert decision2.action == "HOLD", f"Expected HOLD, got {decision2.action}"
        assert "PreScreen" in decision2.reasoning
        logger.info("PreScreen block test PASSED")

        logger.info("=== All tests passed! ===")

    finally:
        await engine.close()
        await db.close()
        # Cleanup test db
        import os
        try:
            os.remove(settings.project_root / "data" / "test_ai.db")
        except OSError:
            pass


def _print_decision(decision) -> None:
    logger.info("Action:      %s", decision.action)
    logger.info("Symbol:      %s", decision.symbol or "-")
    logger.info("Confidence:  %.2f", decision.confidence)
    logger.info("Tier:        %s", decision.tier.value)
    logger.info("Reasoning:   %s", decision.reasoning[:200])
    if decision.size_pct is not None:
        logger.info("Size:        %.1f%%", decision.size_pct)
    if decision.stop_loss is not None:
        logger.info("Stop loss:   %.2f", decision.stop_loss)
    if decision.take_profit is not None:
        logger.info("Take profit: %.2f", decision.take_profit)


if __name__ == "__main__":
    asyncio.run(main())
