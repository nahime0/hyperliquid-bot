"""Phase 12 full-market backtest — 50+ USDC pairs, 6 months.

Replicates the actual Phase 11 autonomous strategy:
  - Trend filter: EMA50 > EMA200 on 1h, price > EMA50, slope > 0
  - Entry: RSI(14) < 30 on 15m + price <= BB_lower + volume_ratio >= 1.0 + RSI_1h < 65
  - Exit: RSI > 70 on 15m + price >= BB_upper
  - Trailing stop: break-even at +1%, trail at +1.5%, tight at +2.5%
  - Time stop: 4h with PnL < 0.5%
  - Cooldown: 30min per-symbol after loss
  - SL=2%, TP=3%, max 5 positions, 5% per trade

Usage:
    .venv/bin/python scripts/backtest_phase12.py
    .venv/bin/python scripts/backtest_phase12.py --pairs 30 --months 6
    .venv/bin/python scripts/backtest_phase12.py --skip-download  # use existing data
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import ta

from utils.logger import setup_logging, get_logger

logger = get_logger(__name__)

HISTORY_DIR = Path(__file__).resolve().parent.parent / "data" / "history"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "backtest_results"

# ── Strategy parameters (matching Phase 11 .env) ──────────────

SL_PCT = 2.0
TP_PCT = 3.0
TRADE_SIZE_PCT = 5.0
MAX_POSITIONS = 5
FEE_RATE = 0.00075  # 0.075% with BNB
SLIPPAGE_PCT = 0.02  # 0.02%
INITIAL_BALANCE = 112.0  # current bankroll

# Entry thresholds
RSI_OVERSOLD = 30.0
RSI_PERIOD = 14
BB_PERIOD = 20
BB_STD = 2.0
MIN_VOL_RATIO = 1.0
RSI_1H_MAX = 65.0

# Exit thresholds
RSI_OVERBOUGHT = 70.0

# Trend filter (EMA on 1h)
EMA_FAST = 50
EMA_SLOW = 200
SLOPE_BARS = 5

# Trailing stop
TRAIL_BREAKEVEN_PCT = 1.0
TRAIL_START_PCT = 1.5
TRAIL_DIST_PCT = 1.0
TRAIL_TIGHT_PCT = 2.5
TRAIL_TIGHT_DIST_PCT = 0.75

# Time stop
TIME_STOP_CANDLES = 16  # 4h in 15m candles
TIME_STOP_MIN_PNL_PCT = 0.5

# Cooldown
COOLDOWN_CANDLES = 2  # 30min in 15m candles

# Stablecoins to exclude
EXCLUDE_BASES = {
    "USDT", "USDC", "BUSD", "TUSD", "FDUSD", "USD1",
    "USDE", "DAI", "USDP", "GUSD", "FRAX", "PYUSD",
    "EURI", "EUR", "AEUR", "PAXG", "U",
}


# ── Data classes ──────────────────────────────────────────────

@dataclass
class Position:
    symbol: str
    entry_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    entry_candle_idx: int
    max_price: float = 0.0
    trailing_sl: float = 0.0

    def __post_init__(self) -> None:
        self.max_price = self.entry_price
        self.trailing_sl = self.stop_loss


@dataclass
class Trade:
    symbol: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    fee: float
    entry_idx: int
    exit_idx: int
    exit_reason: str
    holding_candles: int = 0


@dataclass
class BacktestResult:
    initial_balance: float
    final_balance: float
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[int, float]] = field(default_factory=list)

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> list[Trade]:
        return [t for t in self.trades if t.pnl > 0]

    @property
    def win_rate(self) -> float:
        return len(self.wins) / self.total_trades if self.total_trades else 0.0

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def total_return_pct(self) -> float:
        return ((self.final_balance / self.initial_balance) - 1) * 100 if self.initial_balance > 0 else 0.0

    @property
    def total_fees(self) -> float:
        return sum(t.fee for t in self.trades)

    @property
    def profit_factor(self) -> float:
        gp = sum(t.pnl for t in self.wins)
        gl = abs(sum(t.pnl for t in self.trades if t.pnl <= 0))
        return gp / gl if gl > 0 else float("inf")

    @property
    def max_drawdown_pct(self) -> float:
        peak = 0.0
        max_dd = 0.0
        for _, eq in self.equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @property
    def sharpe_ratio(self) -> float:
        if len(self.trades) < 2:
            return 0.0
        returns = [t.pnl / (t.entry_price * t.quantity) for t in self.trades if t.quantity > 0]
        if len(returns) < 2:
            return 0.0
        avg = sum(returns) / len(returns)
        var = sum((r - avg) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(var) if var > 0 else 0.0
        if std == 0:
            return 0.0
        # Annualize assuming ~3 trades/day
        return (avg / std) * math.sqrt(365 * 3)

    @property
    def avg_holding_candles(self) -> float:
        if not self.trades:
            return 0.0
        return sum(t.holding_candles for t in self.trades) / len(self.trades)

    @property
    def max_consecutive_losses(self) -> int:
        max_cl = 0
        cur = 0
        for t in self.trades:
            if t.pnl <= 0:
                cur += 1
                max_cl = max(max_cl, cur)
            else:
                cur = 0
        return max_cl

    def trades_by_symbol(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for t in self.trades:
            counts[t.symbol] = counts.get(t.symbol, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))

    def pnl_by_symbol(self) -> dict[str, float]:
        pnls: dict[str, float] = {}
        for t in self.trades:
            pnls[t.symbol] = pnls.get(t.symbol, 0.0) + t.pnl
        return dict(sorted(pnls.items(), key=lambda x: x[1], reverse=True))


# ── Pair discovery ────────────────────────────────────────────

async def discover_top_pairs(max_pairs: int = 50) -> list[str]:
    """Discover top USDC pairs by volume from Binance."""
    from binance import AsyncClient

    client = await AsyncClient.create()
    try:
        info = await client.get_exchange_info()
        usdc_syms: set[str] = set()
        for s in info.get("symbols", []):
            if (
                s.get("quoteAsset") == "USDC"
                and s.get("status") == "TRADING"
                and s.get("isSpotTradingAllowed", False)
                and s.get("baseAsset") not in EXCLUDE_BASES
            ):
                usdc_syms.add(s["symbol"])

        stats = await client.get_ticker()
        vol_map: dict[str, float] = {}
        for st in stats:
            sym = st.get("symbol", "")
            if sym in usdc_syms:
                vol_map[sym] = float(st.get("quoteVolume", 0))

        ranked = sorted(vol_map.items(), key=lambda x: x[1], reverse=True)
        result = [sym for sym, vol in ranked[:max_pairs] if vol >= 50_000]
        return result
    finally:
        await client.close_connection()


# ── Data download ─────────────────────────────────────────────

async def download_pair(symbol: str, interval: str, months: int) -> Path | None:
    """Download klines for one pair. Returns path or None on error."""
    from binance import AsyncClient

    out_path = HISTORY_DIR / f"{symbol}_{interval}.csv"
    if out_path.exists():
        # Check if file is recent enough (within 1 day)
        mtime = datetime.fromtimestamp(out_path.stat().st_mtime, tz=timezone.utc)
        if (datetime.now(timezone.utc) - mtime).days < 1:
            logger.debug("Skipping %s %s — already downloaded", symbol, interval)
            return out_path

    start_str = f"{months} months ago UTC"
    client = await AsyncClient.create()
    try:
        klines = await client.get_historical_klines(symbol, interval, start_str)
    except Exception as e:
        logger.warning("Failed %s %s: %s", symbol, interval, e)
        return None
    finally:
        await client.close_connection()

    if not klines:
        return None

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ]
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(klines)

    logger.info("  %s %s: %d candles", symbol, interval, len(klines))
    return out_path


async def download_all_pairs(symbols: list[str], months: int) -> dict[str, dict[str, Path]]:
    """Download 15m + 1h data for all symbols. Returns {symbol: {interval: path}}."""
    result: dict[str, dict[str, Path]] = {}
    total = len(symbols) * 2
    done = 0

    for symbol in symbols:
        paths: dict[str, Path] = {}
        for interval in ("15m", "1h"):
            done += 1
            logger.info("[%d/%d] Downloading %s %s...", done, total, symbol, interval)
            path = await download_pair(symbol, interval, months)
            if path:
                paths[interval] = path
        if "15m" in paths and "1h" in paths:
            result[symbol] = paths

    logger.info("Downloaded data for %d/%d symbols", len(result), len(symbols))
    return result


# ── Indicator computation ─────────────────────────────────────

def prepare_15m(df: pd.DataFrame) -> pd.DataFrame:
    """Add RSI, BB, volume ratio indicators to 15m candles."""
    df = df.copy()
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=RSI_PERIOD).rsi()
    bb = ta.volatility.BollingerBands(df["close"], window=BB_PERIOD, window_dev=BB_STD)
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_upper"] = bb.bollinger_hband()
    df["vol_sma"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_sma"]
    return df


def prepare_1h(df: pd.DataFrame) -> pd.DataFrame:
    """Add EMA50, EMA200, RSI to 1h candles for trend filter."""
    df = df.copy()
    df["ema50"] = ta.trend.EMAIndicator(df["close"], window=EMA_FAST).ema_indicator()
    df["ema200"] = ta.trend.EMAIndicator(df["close"], window=EMA_SLOW).ema_indicator()
    df["rsi_1h"] = ta.momentum.RSIIndicator(df["close"], window=RSI_PERIOD).rsi()
    # EMA50 slope (change over SLOPE_BARS bars, normalized)
    df["ema50_prev"] = df["ema50"].shift(SLOPE_BARS)
    df["slope"] = (df["ema50"] - df["ema50_prev"]) / df["close"] * 100
    return df


def get_trend_at(df_1h: pd.DataFrame, timestamp: pd.Timestamp) -> dict[str, Any]:
    """Get trend state from 1h candles at a given 15m timestamp."""
    # Find the most recent 1h candle at or before this timestamp
    mask = df_1h["open_time"] <= timestamp
    if not mask.any():
        return {"trend": "NEUTRAL", "rsi_1h": None}

    row = df_1h.loc[mask].iloc[-1]
    ema50 = row.get("ema50")
    ema200 = row.get("ema200")
    price = row["close"]
    slope = row.get("slope", 0)
    rsi_1h = row.get("rsi_1h")

    if pd.isna(ema50) or pd.isna(ema200):
        return {"trend": "NEUTRAL", "rsi_1h": rsi_1h if not pd.isna(rsi_1h) else None}

    if ema50 > ema200 and price > ema50 and slope > 0:
        trend = "BULLISH"
    elif ema50 < ema200 and price < ema50 and slope < 0:
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"

    return {"trend": trend, "rsi_1h": float(rsi_1h) if not pd.isna(rsi_1h) else None}


# ── Trailing stop logic ──────────────────────────────────────

def update_trailing(pos: Position, current_high: float) -> None:
    """Update trailing stop levels based on max price seen."""
    if current_high > pos.max_price:
        pos.max_price = current_high

    pnl_pct = (pos.max_price - pos.entry_price) / pos.entry_price * 100

    if pnl_pct >= TRAIL_TIGHT_PCT:
        new_sl = pos.max_price * (1 - TRAIL_TIGHT_DIST_PCT / 100)
        pos.trailing_sl = max(pos.trailing_sl, new_sl)
    elif pnl_pct >= TRAIL_START_PCT:
        new_sl = pos.max_price * (1 - TRAIL_DIST_PCT / 100)
        pos.trailing_sl = max(pos.trailing_sl, new_sl)
    elif pnl_pct >= TRAIL_BREAKEVEN_PCT:
        pos.trailing_sl = max(pos.trailing_sl, pos.entry_price)


# ── Main backtest engine ─────────────────────────────────────

def run_backtest(
    all_15m: dict[str, pd.DataFrame],
    all_1h: dict[str, pd.DataFrame],
    initial_balance: float = INITIAL_BALANCE,
) -> BacktestResult:
    """Run the full Phase 11 multi-pair backtest.

    Processes all 15m candles in chronological order across all symbols.
    Uses 1h candles for trend filter lookups.
    """
    balance = initial_balance
    positions: list[Position] = []
    trades: list[Trade] = []
    equity_curve: list[tuple[int, float]] = []
    cooldowns: dict[str, int] = {}  # symbol -> candle_idx when cooldown expires

    # Merge all 15m candles into one timeline
    frames: list[pd.DataFrame] = []
    for sym, df in all_15m.items():
        df = df.copy()
        df["_symbol"] = sym
        frames.append(df)

    if not frames:
        return BacktestResult(initial_balance, initial_balance)

    merged = pd.concat(frames, ignore_index=True)
    merged = merged.sort_values("open_time").reset_index(drop=True)

    total_candles = len(merged)
    logger.info("Backtest: %d total 15m candles across %d symbols", total_candles, len(all_15m))

    step = max(1, total_candles // 20)  # progress every 5%

    for idx, row in merged.iterrows():
        i = int(idx)
        sym = row["_symbol"]
        price = row["close"]
        high = row["high"]
        low = row["low"]
        ts = row["open_time"]

        if i % step == 0:
            logger.info("  Progress: %d/%d (%.0f%%) — bal=$%.2f, pos=%d, trades=%d",
                        i, total_candles, i / total_candles * 100,
                        balance, len(positions), len(trades))

        # ── 1. Check SL/TP/trailing/time stop on open positions ──
        closed_indices: list[int] = []
        for pi, pos in enumerate(positions):
            if pos.symbol != sym:
                continue

            # Update trailing stop
            update_trailing(pos, high)

            exit_price: float | None = None
            exit_reason = ""

            # Trailing stop hit
            if low <= pos.trailing_sl:
                exit_price = pos.trailing_sl
                exit_reason = "trailing_sl"
            # Original stop loss
            elif low <= pos.stop_loss:
                exit_price = pos.stop_loss
                exit_reason = "sl"
            # Take profit
            elif high >= pos.take_profit:
                exit_price = pos.take_profit
                exit_reason = "tp"
            # Time stop
            else:
                holding = i - pos.entry_candle_idx
                if holding >= TIME_STOP_CANDLES:
                    pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
                    if pnl_pct < TIME_STOP_MIN_PNL_PCT:
                        exit_price = price
                        exit_reason = "time_stop"

            if exit_price is not None:
                exit_price *= (1 - SLIPPAGE_PCT / 100)
                fee = pos.quantity * pos.entry_price * FEE_RATE + pos.quantity * exit_price * FEE_RATE
                pnl = pos.quantity * (exit_price - pos.entry_price) - fee
                balance += pos.quantity * exit_price * (1 - FEE_RATE)

                trades.append(Trade(
                    symbol=sym, entry_price=pos.entry_price, exit_price=exit_price,
                    quantity=pos.quantity, pnl=pnl, fee=fee,
                    entry_idx=pos.entry_candle_idx, exit_idx=i,
                    exit_reason=exit_reason, holding_candles=i - pos.entry_candle_idx,
                ))
                closed_indices.append(pi)

                # Cooldown on loss
                if pnl <= 0:
                    cooldowns[sym] = i + COOLDOWN_CANDLES

        for pi in reversed(closed_indices):
            positions.pop(pi)

        # ── 2. Check exit signal (RSI overbought + BB upper) ──
        rsi_val = row.get("rsi")
        bb_upper = row.get("bb_upper")
        if not pd.isna(rsi_val) and not pd.isna(bb_upper):
            for pi, pos in enumerate(positions):
                if pos.symbol != sym:
                    continue
                if pi in closed_indices:
                    continue
                should_exit = False
                if rsi_val > RSI_OVERBOUGHT and price >= bb_upper * 0.995:
                    should_exit = True

                if should_exit:
                    exit_price = price * (1 - SLIPPAGE_PCT / 100)
                    fee = pos.quantity * pos.entry_price * FEE_RATE + pos.quantity * exit_price * FEE_RATE
                    pnl = pos.quantity * (exit_price - pos.entry_price) - fee
                    balance += pos.quantity * exit_price * (1 - FEE_RATE)
                    trades.append(Trade(
                        symbol=sym, entry_price=pos.entry_price, exit_price=exit_price,
                        quantity=pos.quantity, pnl=pnl, fee=fee,
                        entry_idx=pos.entry_candle_idx, exit_idx=i,
                        exit_reason="signal_exit", holding_candles=i - pos.entry_candle_idx,
                    ))
                    positions.remove(pos)
                    if pnl <= 0:
                        cooldowns[sym] = i + COOLDOWN_CANDLES
                    break

        # ── 3. Check entry signal ──
        if len(positions) < MAX_POSITIONS:
            # Skip if already holding this symbol
            held_syms = {p.symbol for p in positions}
            if sym not in held_syms:
                # Skip if in cooldown
                if cooldowns.get(sym, 0) <= i:
                    can_enter = True

                    # RSI < 30
                    if pd.isna(rsi_val) or rsi_val >= RSI_OVERSOLD:
                        can_enter = False

                    # Price <= BB lower
                    bb_lower = row.get("bb_lower")
                    if can_enter and (pd.isna(bb_lower) or price > bb_lower * 1.005):
                        can_enter = False

                    # Volume ratio >= 1.0
                    vr = row.get("vol_ratio")
                    if can_enter and (pd.isna(vr) or vr < MIN_VOL_RATIO):
                        can_enter = False

                    # Trend filter from 1h
                    if can_enter:
                        df_1h = all_1h.get(sym)
                        if df_1h is not None:
                            trend_info = get_trend_at(df_1h, ts)
                            if trend_info["trend"] != "BULLISH":
                                can_enter = False
                            rsi_1h = trend_info.get("rsi_1h")
                            if rsi_1h is not None and rsi_1h >= RSI_1H_MAX:
                                can_enter = False
                        else:
                            can_enter = False  # no 1h data = skip

                    if can_enter:
                        alloc = balance * (TRADE_SIZE_PCT / 100)
                        entry_price = price * (1 + SLIPPAGE_PCT / 100)
                        qty = alloc / entry_price
                        cost = qty * entry_price * (1 + FEE_RATE)

                        if cost <= balance and qty > 0 and alloc >= 5.5:
                            balance -= cost
                            sl = entry_price * (1 - SL_PCT / 100)
                            tp = entry_price * (1 + TP_PCT / 100)
                            positions.append(Position(
                                symbol=sym, entry_price=entry_price, quantity=qty,
                                stop_loss=sl, take_profit=tp, entry_candle_idx=i,
                            ))

        # ── 4. Equity snapshot (every 96 candles = ~1 day) ──
        if i % 96 == 0:
            mark = 0.0
            for pos in positions:
                bt = all_15m.get(pos.symbol)
                if bt is not None and len(bt) > 0:
                    # Use the latest candle for this symbol up to current time
                    sub = bt[bt["open_time"] <= ts]
                    if len(sub) > 0:
                        mark += pos.quantity * sub.iloc[-1]["close"]
                    else:
                        mark += pos.quantity * pos.entry_price
                else:
                    mark += pos.quantity * pos.entry_price
            equity_curve.append((i, balance + mark))

    # ── Close remaining positions ──
    for pos in positions:
        df_sym = all_15m.get(pos.symbol)
        if df_sym is not None and len(df_sym) > 0:
            last_price = df_sym.iloc[-1]["close"]
            fee = pos.quantity * pos.entry_price * FEE_RATE + pos.quantity * last_price * FEE_RATE
            pnl = pos.quantity * (last_price - pos.entry_price) - fee
            balance += pos.quantity * last_price * (1 - FEE_RATE)
            trades.append(Trade(
                symbol=pos.symbol, entry_price=pos.entry_price, exit_price=last_price,
                quantity=pos.quantity, pnl=pnl, fee=fee,
                entry_idx=pos.entry_candle_idx, exit_idx=total_candles - 1,
                exit_reason="end",
                holding_candles=total_candles - 1 - pos.entry_candle_idx,
            ))

    return BacktestResult(
        initial_balance=initial_balance,
        final_balance=balance,
        trades=trades,
        equity_curve=equity_curve,
    )


# ── Report ────────────────────────────────────────────────────

def print_report(result: BacktestResult) -> None:
    """Print comprehensive backtest report."""
    print("\n" + "=" * 60)
    print("    PHASE 12 BACKTEST — FULL-MARKET MEAN REVERSION")
    print("=" * 60)
    print(f"  Initial balance:       ${result.initial_balance:.2f}")
    print(f"  Final balance:         ${result.final_balance:.2f}")
    print(f"  Total return:          {result.total_return_pct:+.2f}%")
    print(f"  Total P&L:             ${result.total_pnl:+.4f}")
    print(f"  Total trades:          {result.total_trades}")
    print(f"  Win rate:              {result.win_rate * 100:.1f}%")
    print(f"  Profit factor:         {result.profit_factor:.3f}")
    print(f"  Sharpe ratio:          {result.sharpe_ratio:.3f}")
    print(f"  Max drawdown:          {result.max_drawdown_pct:.2f}%")
    print(f"  Total fees:            ${result.total_fees:.4f}")
    print(f"  Avg holding (candles): {result.avg_holding_candles:.1f} (~{result.avg_holding_candles * 15 / 60:.1f}h)")
    print(f"  Max consec. losses:    {result.max_consecutive_losses}")

    # Exit reason breakdown
    reasons: dict[str, int] = {}
    for t in result.trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    print(f"\n  Exit reasons:")
    for reason, count in sorted(reasons.items(), key=lambda x: x[1], reverse=True):
        print(f"    {reason:<16s} {count:>4d}")

    # Top symbols by trade count
    by_sym = result.trades_by_symbol()
    pnl_by_sym = result.pnl_by_symbol()
    print(f"\n  Top 15 symbols by trades:")
    print(f"    {'Symbol':<12s} {'Trades':>7s} {'PnL':>10s}")
    for sym in list(by_sym.keys())[:15]:
        pnl = pnl_by_sym.get(sym, 0)
        print(f"    {sym:<12s} {by_sym[sym]:>7d} ${pnl:>+9.4f}")

    # Profitable vs unprofitable symbols
    profitable = [s for s, p in pnl_by_sym.items() if p > 0]
    unprofitable = [s for s, p in pnl_by_sym.items() if p <= 0]
    print(f"\n  Profitable symbols:   {len(profitable)}")
    print(f"  Unprofitable symbols: {len(unprofitable)}")

    if result.wins:
        avg_win = sum(t.pnl for t in result.wins) / len(result.wins)
        print(f"  Avg win:              ${avg_win:.4f}")
    if result.trades:
        losses = [t for t in result.trades if t.pnl <= 0]
        if losses:
            avg_loss = sum(t.pnl for t in losses) / len(losses)
            print(f"  Avg loss:             ${avg_loss:.4f}")

    print("=" * 60)


def plot_equity(result: BacktestResult, path: Path) -> None:
    """Plot and save equity curve."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not result.equity_curve:
        return

    x = [i for i, _ in result.equity_curve]
    y = [eq for _, eq in result.equity_curve]

    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(x, y, linewidth=0.8, color="#2196F3")
    ax.axhline(y=result.initial_balance, color="gray", linestyle="--", linewidth=0.5)
    ax.set_title(f"Phase 12 Backtest — {result.total_trades} trades, {result.total_return_pct:+.1f}%")
    ax.set_xlabel("Candle Index (15m)")
    ax.set_ylabel("Balance (USDC)")
    ax.grid(True, alpha=0.3)

    text = (
        f"Return: {result.total_return_pct:+.2f}% | "
        f"Trades: {result.total_trades} | "
        f"WR: {result.win_rate * 100:.1f}% | "
        f"MaxDD: {result.max_drawdown_pct:.2f}% | "
        f"PF: {result.profit_factor:.2f}"
    )
    ax.text(0.5, 0.02, text, transform=ax.transAxes, ha="center", fontsize=9,
            bbox=dict(boxstyle="round,pad=0.3", facecolor="wheat", alpha=0.5))

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info("Equity curve saved to %s", path)


# ── Main ──────────────────────────────────────────────────────

async def async_main() -> None:
    parser = argparse.ArgumentParser(description="Phase 12 full-market backtest")
    parser.add_argument("--pairs", type=int, default=50, help="Number of top pairs to test")
    parser.add_argument("--months", type=int, default=6, help="Months of history")
    parser.add_argument("--skip-download", action="store_true", help="Use existing data only")
    parser.add_argument("--balance", type=float, default=INITIAL_BALANCE, help="Initial balance")
    args = parser.parse_args()

    initial_bal = args.balance

    if args.skip_download:
        # Use all existing USDC data files
        symbols: list[str] = []
        if HISTORY_DIR.exists():
            for f in HISTORY_DIR.glob("*USDC_15m.csv"):
                sym = f.stem.replace("_15m", "")
                # Check if 1h also exists
                if (HISTORY_DIR / f"{sym}_1h.csv").exists():
                    symbols.append(sym)
        symbols.sort()
        logger.info("Using existing data for %d symbols: %s", len(symbols), ", ".join(symbols[:10]))
    else:
        # Discover and download
        logger.info("Discovering top %d USDC pairs by volume...", args.pairs)
        symbols = await discover_top_pairs(args.pairs)
        logger.info("Found %d pairs — downloading %d months of data...", len(symbols), args.months)
        await download_all_pairs(symbols, args.months)

    # Load data
    all_15m: dict[str, pd.DataFrame] = {}
    all_1h: dict[str, pd.DataFrame] = {}

    for sym in symbols:
        path_15m = HISTORY_DIR / f"{sym}_15m.csv"
        path_1h = HISTORY_DIR / f"{sym}_1h.csv"
        if path_15m.exists() and path_1h.exists():
            df_15m = pd.read_csv(path_15m)
            df_15m["open_time"] = pd.to_datetime(df_15m["open_time"], unit="ms", utc=True)
            for col in ("open", "high", "low", "close", "volume", "quote_volume"):
                df_15m[col] = df_15m[col].astype(float)

            df_1h = pd.read_csv(path_1h)
            df_1h["open_time"] = pd.to_datetime(df_1h["open_time"], unit="ms", utc=True)
            for col in ("open", "high", "low", "close", "volume", "quote_volume"):
                df_1h[col] = df_1h[col].astype(float)

            # Prepare indicators
            df_15m = prepare_15m(df_15m)
            df_1h = prepare_1h(df_1h)

            all_15m[sym] = df_15m
            all_1h[sym] = df_1h

    logger.info("Loaded %d symbols for backtest", len(all_15m))

    if not all_15m:
        logger.error("No data available — run without --skip-download first")
        return

    # Run backtest
    t0 = time.time()
    result = run_backtest(all_15m, all_1h, initial_balance=initial_bal)
    elapsed = time.time() - t0
    logger.info("Backtest completed in %.1fs", elapsed)

    # Report
    print_report(result)

    # Save equity curve
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    plot_equity(result, OUTPUT_DIR / "phase12_equity.png")

    # Save trade log
    trade_log = OUTPUT_DIR / "phase12_trades.csv"
    with open(trade_log, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "entry_price", "exit_price", "qty", "pnl", "fee", "exit_reason", "holding_candles"])
        for t in result.trades:
            w.writerow([t.symbol, f"{t.entry_price:.6f}", f"{t.exit_price:.6f}",
                        f"{t.quantity:.6f}", f"{t.pnl:.6f}", f"{t.fee:.6f}",
                        t.exit_reason, t.holding_candles])
    logger.info("Trade log saved to %s", trade_log)


def main() -> None:
    setup_logging("INFO")
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
