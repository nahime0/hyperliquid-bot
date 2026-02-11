"""Backtesting engine for rule-based strategy simulation.

Uses deterministic rules as a proxy for the AI decision engine
(invoking Claude thousands of times isn't feasible for backtesting).

Supported strategies:
  - MeanReversion: RSI oversold + Bollinger Band breakout (LONG only, legacy)
  - HyperliquidMeanRev: Full LONG+SHORT with trend filter, volume, BB (Hyperliquid)
  - ScalpMeanRev: Relaxed mean reversion with softer thresholds
  - GridSim: simulated grid trading on historical candles
  - EMACrossoverADX: Trend following via EMA crossover + ADX strength
  - BBSqueeze: Volatility breakout after Bollinger Band squeeze
  - MACDMomentum: MACD crossover momentum strategy
  - DonchianBreakout: Donchian channel breakout with consolidation
  - KeltnerBreakout: Keltner channel (ATR-based) breakout
  - VWAPReversion: Intraday mean reversion around VWAP
  - RSIDivergence: Bullish/bearish RSI divergence reversal
  - StochasticCross: Stochastic oscillator crossover in extreme zones
  - Combined: Multi-strategy voting system (consensus-based)

Usage:
    from data.backtest import BacktestEngine, HyperliquidMeanRevRule, BacktestConfig
    engine = BacktestEngine(config)
    result = engine.run(df, rule)
    result.print_report()
    result.save_equity_curve("output.csv")
    result.plot_equity_curve("output.png")
"""
from __future__ import annotations

import csv
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import ta

from utils.logger import get_logger

logger = get_logger(__name__)


# ── Configuration ────────────────────────────────────────────


@dataclass
class BacktestConfig:
    """Global backtest parameters."""
    initial_balance: float = 100.0
    fee_rate: float = 0.00045     # 0.045% per trade (Hyperliquid taker)
    max_trade_pct: float = 10.0   # max % of balance per trade
    stop_loss_pct: float = 1.0
    take_profit_pct: float = 1.5
    max_open_positions: int = 5
    slippage_pct: float = 0.01    # 0.01% slippage simulation
    # Trailing stop parameters (match RiskConfig defaults)
    trailing_breakeven_pct: float = 1.0
    trailing_start_pct: float = 1.5
    trailing_distance_pct: float = 1.0
    trailing_tight_pct: float = 2.5
    trailing_tight_distance_pct: float = 0.75


# ── Trade record ─────────────────────────────────────────────


@dataclass
class Trade:
    """One completed round-trip trade."""
    symbol: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    side: str           # "LONG" or "SHORT"
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float          # net after fees
    fee: float
    exit_reason: str    # "tp", "sl", "signal", "end"


# ── Open position tracker ────────────────────────────────────


@dataclass
class _Position:
    symbol: str
    entry_time: pd.Timestamp
    entry_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    direction: str = "LONG"
    max_price_seen: float = 0.0
    min_price_seen: float = float("inf")


# ── Backtest result ──────────────────────────────────────────


@dataclass
class BacktestResult:
    """Complete backtest output with metrics and trades."""
    config: BacktestConfig
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[pd.Timestamp, float]] = field(default_factory=list)

    # ── Metrics ──────────────────────────────────────

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> list[Trade]:
        return [t for t in self.trades if t.pnl > 0]

    @property
    def losses(self) -> list[Trade]:
        return [t for t in self.trades if t.pnl <= 0]

    @property
    def win_rate(self) -> float:
        return len(self.wins) / self.total_trades if self.total_trades else 0.0

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def total_return_pct(self) -> float:
        if self.config.initial_balance <= 0:
            return 0.0
        final = self.config.initial_balance + self.total_pnl
        return ((final / self.config.initial_balance) - 1) * 100

    @property
    def total_fees(self) -> float:
        return sum(t.fee for t in self.trades)

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t.pnl for t in self.wins)
        gross_loss = abs(sum(t.pnl for t in self.losses))
        return gross_profit / gross_loss if gross_loss > 0 else float("inf")

    @property
    def max_drawdown_pct(self) -> float:
        peak = 0.0
        max_dd = 0.0
        for _, equity in self.equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @property
    def sharpe_ratio(self) -> float:
        """Annualized Sharpe ratio (assuming 15min candles → 96 per day)."""
        if len(self.trades) < 2:
            return 0.0
        returns = [t.pnl for t in self.trades]
        avg = sum(returns) / len(returns)
        variance = sum((r - avg) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0
        if std == 0:
            return 0.0
        # Annualize: assume ~2 trades/day average → sqrt(365*2)
        trades_per_year = 365 * 2
        return (avg / std) * math.sqrt(trades_per_year)

    @property
    def avg_win(self) -> float:
        return sum(t.pnl for t in self.wins) / len(self.wins) if self.wins else 0.0

    @property
    def avg_loss(self) -> float:
        return sum(t.pnl for t in self.losses) / len(self.losses) if self.losses else 0.0

    @property
    def max_consecutive_losses(self) -> int:
        max_cl = 0
        current = 0
        for t in self.trades:
            if t.pnl <= 0:
                current += 1
                max_cl = max(max_cl, current)
            else:
                current = 0
        return max_cl

    # ── Output ───────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        final_bal = self.config.initial_balance + self.total_pnl
        return {
            "initial_balance": self.config.initial_balance,
            "final_balance": round(final_bal, 2),
            "total_return_pct": round(self.total_return_pct, 2),
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate * 100, 2),
            "profit_factor": round(self.profit_factor, 3),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "total_fees": round(self.total_fees, 4),
            "avg_win": round(self.avg_win, 4),
            "avg_loss": round(self.avg_loss, 4),
            "max_consecutive_losses": self.max_consecutive_losses,
        }

    def print_report(self) -> None:
        s = self.summary()
        print("\n" + "=" * 50)
        print("        BACKTEST REPORT")
        print("=" * 50)
        print(f"  Initial balance:       ${s['initial_balance']:.2f}")
        print(f"  Final balance:         ${s['final_balance']:.2f}")
        print(f"  Total return:          {s['total_return_pct']:+.2f}%")
        print(f"  Total trades:          {s['total_trades']}")
        print(f"  Win rate:              {s['win_rate']:.1f}%")
        print(f"  Profit factor:         {s['profit_factor']:.3f}")
        print(f"  Sharpe ratio:          {s['sharpe_ratio']:.3f}")
        print(f"  Max drawdown:          {s['max_drawdown_pct']:.2f}%")
        print(f"  Total fees:            ${s['total_fees']:.4f}")
        print(f"  Avg win:               ${s['avg_win']:.4f}")
        print(f"  Avg loss:              ${s['avg_loss']:.4f}")
        print(f"  Max consec. losses:    {s['max_consecutive_losses']}")
        print("=" * 50)

    def save_equity_curve(self, path: str | Path) -> None:
        """Save equity curve to CSV."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "equity"])
            for ts, eq in self.equity_curve:
                writer.writerow([ts.isoformat(), round(eq, 4)])
        logger.info("Equity curve saved to %s", path)

    def plot_equity_curve(self, path: str | Path) -> None:
        """Plot equity curve and save as PNG."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        timestamps = [ts for ts, _ in self.equity_curve]
        equities = [eq for _, eq in self.equity_curve]

        fig, ax = plt.subplots(figsize=(14, 6))
        ax.plot(timestamps, equities, linewidth=0.8, color="#2196F3")
        ax.axhline(y=self.config.initial_balance, color="gray", linestyle="--", linewidth=0.5)
        ax.set_title("Equity Curve")
        ax.set_xlabel("Date")
        ax.set_ylabel("Balance (USDC)")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        fig.autofmt_xdate()
        ax.grid(True, alpha=0.3)

        # Annotate summary
        s = self.summary()
        text = (
            f"Return: {s['total_return_pct']:+.2f}%  |  "
            f"Trades: {s['total_trades']}  |  "
            f"WR: {s['win_rate']:.1f}%  |  "
            f"MaxDD: {s['max_drawdown_pct']:.2f}%  |  "
            f"Sharpe: {s['sharpe_ratio']:.2f}"
        )
        ax.text(
            0.5, 0.02, text,
            transform=ax.transAxes, ha="center", fontsize=9,
            bbox=dict(boxstyle="round,pad=0.3", facecolor="wheat", alpha=0.5),
        )

        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        logger.info("Equity curve plot saved to %s", path)


# ── Trading rules (strategy proxies) ────────────────────────


class TradingRule(ABC):
    """Abstract rule that generates BUY/SHORT/SELL/CLOSE_SHORT signals on each candle."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add indicator columns to the dataframe. Called once before run."""

    @abstractmethod
    def signal(self, row: pd.Series, prev: pd.Series | None) -> str | None:
        """Return "BUY", "SHORT", "SELL", "CLOSE_SHORT", or None for each candle."""


class MeanReversionRule(TradingRule):
    """RSI oversold + Bollinger Band lower breakout.

    BUY:  RSI < rsi_entry AND close < lower Bollinger Band
    SELL: RSI > rsi_exit  OR  take_profit / stop_loss hit
    """

    def __init__(
        self,
        rsi_period: int = 14,
        rsi_entry: float = 28.0,
        rsi_exit: float = 55.0,
        bb_period: int = 20,
        bb_std: float = 2.0,
    ) -> None:
        self.rsi_period = rsi_period
        self.rsi_entry = rsi_entry
        self.rsi_exit = rsi_exit
        self.bb_period = bb_period
        self.bb_std = bb_std

    @property
    def name(self) -> str:
        return f"MeanReversion(rsi={self.rsi_period},entry<{self.rsi_entry},exit>{self.rsi_exit},bb={self.bb_period})"

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=self.rsi_period).rsi()
        bb = ta.volatility.BollingerBands(df["close"], window=self.bb_period, window_dev=self.bb_std)
        df["bb_lower"] = bb.bollinger_lband()
        df["bb_upper"] = bb.bollinger_hband()
        return df

    def signal(self, row: pd.Series, prev: pd.Series | None) -> str | None:
        if pd.isna(row.get("rsi")) or pd.isna(row.get("bb_lower")):
            return None
        if row["rsi"] < self.rsi_entry and row["close"] < row["bb_lower"]:
            return "BUY"
        if row["rsi"] > self.rsi_exit:
            return "SELL"
        return None


class GridSimRule(TradingRule):
    """Simulated grid trading on historical data.

    Buys when price drops by spacing_pct from the last reference price,
    sells when price rises by spacing_pct from entry.  Simulates the
    mechanical buy-low/sell-high cycle of a real grid.
    """

    def __init__(
        self,
        spacing_pct: float = 0.5,
        range_pct: float = 3.0,
    ) -> None:
        self.spacing_pct = spacing_pct
        self.range_pct = range_pct
        self._ref_price: float = 0.0
        self._in_position: bool = False

    @property
    def name(self) -> str:
        return f"GridSim(spacing={self.spacing_pct}%,range={self.range_pct}%)"

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        # No extra indicators needed; grid is purely price-based
        self._ref_price = 0.0
        self._in_position = False
        return df.copy()

    def signal(self, row: pd.Series, prev: pd.Series | None) -> str | None:
        price = row["close"]

        if self._ref_price == 0:
            self._ref_price = price
            return None

        drop = (self._ref_price - price) / self._ref_price * 100
        rise = (price - self._ref_price) / self._ref_price * 100

        if not self._in_position and drop >= self.spacing_pct:
            self._ref_price = price
            self._in_position = True
            return "BUY"

        if self._in_position and rise >= self.spacing_pct:
            self._ref_price = price
            self._in_position = False
            return "SELL"

        # Reset reference if price moves too far (outside range)
        if abs(drop) > self.range_pct or abs(rise) > self.range_pct:
            self._ref_price = price

        return None


class HyperliquidMeanRevRule(TradingRule):
    """Hyperliquid Mean Reversion — LONG + SHORT with trend filter.

    Replicates the live strategy (strategies/mean_reversion.py).
    Requires a separate 1h DataFrame for trend filter and macro RSI.

    LONG entry:  trend BULLISH + RSI<rsi_long_entry + price<=BB_lower*1.005
                 + vol_ratio>=1.2 + RSI_1h<60
    SHORT entry: trend BEARISH + RSI>rsi_short_entry + price>=BB_upper*0.995
                 + vol_ratio>=1.2 + RSI_1h>40
    LONG exit:   RSI>70 or price>=BB_upper
    SHORT exit:  RSI<30 or price<=BB_lower
    """

    def __init__(
        self,
        rsi_period: int = 14,
        rsi_long_entry: float = 25.0,
        rsi_short_entry: float = 75.0,
        bb_period: int = 20,
        bb_std: float = 2.0,
        min_volume_ratio: float = 1.2,
        rsi_1h_max_long: float = 60.0,
        rsi_1h_min_short: float = 40.0,
        ema_fast: int = 50,
        ema_slow: int = 200,
        slope_window: int = 5,
        df_1h: pd.DataFrame | None = None,
    ) -> None:
        self.rsi_period = rsi_period
        self.rsi_long_entry = rsi_long_entry
        self.rsi_short_entry = rsi_short_entry
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.min_volume_ratio = min_volume_ratio
        self.rsi_1h_max_long = rsi_1h_max_long
        self.rsi_1h_min_short = rsi_1h_min_short
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.slope_window = slope_window
        self._df_1h = df_1h
        # Built during prepare()
        self._trend_at: pd.Series | None = None  # trend per 15m timestamp
        self._rsi_1h_at: pd.Series | None = None  # RSI_1h per 15m timestamp

    @property
    def name(self) -> str:
        return (
            f"HLMeanRev(rsi_l<{self.rsi_long_entry},rsi_s>{self.rsi_short_entry},"
            f"bb={self.bb_period})"
        )

    def set_df_1h(self, df_1h: pd.DataFrame) -> None:
        """Set the 1h DataFrame (call before prepare or pass in constructor)."""
        self._df_1h = df_1h

    def _prepare_1h(self, df_15m: pd.DataFrame) -> None:
        """Compute trend filter + RSI on 1h and map to 15m timestamps."""
        if self._df_1h is None or len(self._df_1h) < self.ema_slow + self.slope_window:
            # No 1h data — set neutral trend, no RSI filter
            self._trend_at = pd.Series("NEUTRAL", index=df_15m.index)
            self._rsi_1h_at = pd.Series(50.0, index=df_15m.index)
            return

        h = self._df_1h.copy()
        close_1h = h["close"]

        # EMA trend
        ema_fast_s = ta.trend.EMAIndicator(close_1h, window=self.ema_fast).ema_indicator()
        ema_slow_s = ta.trend.EMAIndicator(close_1h, window=self.ema_slow).ema_indicator()

        # Slope
        slope = ema_fast_s.diff(self.slope_window) / close_1h * 100

        # Classify trend per 1h bar
        trend_1h = pd.Series("NEUTRAL", index=h.index)
        bullish = (ema_fast_s > ema_slow_s) & (close_1h > ema_fast_s) & (slope > 0)
        bearish = (ema_fast_s < ema_slow_s) & (close_1h < ema_fast_s) & (slope < 0)
        trend_1h[bullish] = "BULLISH"
        trend_1h[bearish] = "BEARISH"

        # RSI 1h
        rsi_1h = ta.momentum.RSIIndicator(close_1h, window=self.rsi_period).rsi()

        # Map 1h values to 15m by forward-filling on open_time
        h_mapped = pd.DataFrame({
            "open_time": h["open_time"],
            "trend": trend_1h.values,
            "rsi_1h": rsi_1h.values,
        })

        # merge_asof: for each 15m candle, get the latest 1h bar <= that time
        df_15m_sorted = df_15m[["open_time"]].copy().sort_values("open_time")
        h_mapped = h_mapped.sort_values("open_time")
        mapped = pd.merge_asof(
            df_15m_sorted, h_mapped,
            on="open_time", direction="backward",
        )
        # Align back to df_15m index
        self._trend_at = mapped["trend"].values
        self._rsi_1h_at = mapped["rsi_1h"].values

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        # 15m indicators
        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=self.rsi_period).rsi()
        bb = ta.volatility.BollingerBands(df["close"], window=self.bb_period, window_dev=self.bb_std)
        df["bb_lower"] = bb.bollinger_lband()
        df["bb_upper"] = bb.bollinger_hband()
        # Volume ratio (20-bar SMA)
        vol_sma = df["volume"].rolling(20).mean()
        df["vol_ratio"] = df["volume"] / vol_sma

        # 1h trend + RSI mapped to 15m
        self._prepare_1h(df)
        df["trend"] = self._trend_at
        df["rsi_1h"] = self._rsi_1h_at
        return df

    def signal(self, row: pd.Series, prev: pd.Series | None) -> str | None:
        if pd.isna(row.get("rsi")) or pd.isna(row.get("bb_lower")):
            return None

        rsi = row["rsi"]
        price = row["close"]
        bb_lower = row["bb_lower"]
        bb_upper = row["bb_upper"]
        vol_ratio = row.get("vol_ratio", 0)
        trend = row.get("trend", "NEUTRAL")
        rsi_1h = row.get("rsi_1h", 50.0)

        if pd.isna(vol_ratio):
            vol_ratio = 0
        if pd.isna(rsi_1h):
            rsi_1h = 50.0

        # Entries first: they have stricter conditions and RSI ranges
        # overlap with exits (RSI<25 ⊂ RSI<30, RSI>75 ⊂ RSI>70)

        # ── LONG entry ──
        if (trend == "BULLISH"
                and rsi < self.rsi_long_entry
                and price <= bb_lower * 1.005
                and vol_ratio >= self.min_volume_ratio
                and rsi_1h < self.rsi_1h_max_long):
            return "BUY"

        # ── SHORT entry ──
        if (trend == "BEARISH"
                and rsi > self.rsi_short_entry
                and price >= bb_upper * 0.995
                and vol_ratio >= self.min_volume_ratio
                and rsi_1h > self.rsi_1h_min_short):
            return "SHORT"

        # ── LONG exit ──
        if rsi > 70 or price >= bb_upper:
            return "SELL"

        # ── SHORT exit ──
        if rsi < 30 or price <= bb_lower:
            return "CLOSE_SHORT"

        return None


class ScalpMeanRevRule(TradingRule):
    """Relaxed mean reversion — LONG + SHORT, trend filter but softer thresholds.

    Keeps the trend filter (key quality gate) but relaxes RSI, BB, and drops
    volume/1h-RSI requirements for more frequent signals.

    LONG entry:  trend BULLISH/NEUTRAL + RSI < rsi_long_entry + bb_pct < bb_long_zone
    SHORT entry: trend BEARISH/NEUTRAL + RSI > rsi_short_entry + bb_pct > bb_short_zone
    LONG exit:   RSI > rsi_long_exit OR price >= bb_upper * 0.99
    SHORT exit:  RSI < rsi_short_exit OR price <= bb_lower * 1.01

    Uses 1h data for trend filter (same as HyperliquidMeanRevRule).
    """

    def __init__(
        self,
        rsi_period: int = 14,
        rsi_long_entry: float = 35.0,
        rsi_short_entry: float = 65.0,
        rsi_long_exit: float = 60.0,
        rsi_short_exit: float = 40.0,
        bb_period: int = 20,
        bb_std: float = 2.0,
        bb_long_zone: float = 0.25,
        bb_short_zone: float = 0.75,
        ema_fast: int = 50,
        ema_slow: int = 200,
        slope_window: int = 5,
        allow_neutral: bool = True,
        df_1h: pd.DataFrame | None = None,
    ) -> None:
        self.rsi_period = rsi_period
        self.rsi_long_entry = rsi_long_entry
        self.rsi_short_entry = rsi_short_entry
        self.rsi_long_exit = rsi_long_exit
        self.rsi_short_exit = rsi_short_exit
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.bb_long_zone = bb_long_zone
        self.bb_short_zone = bb_short_zone
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.slope_window = slope_window
        self.allow_neutral = allow_neutral
        self._df_1h = df_1h
        self._trend_at: Any = None

    @property
    def name(self) -> str:
        n = "N+" if self.allow_neutral else ""
        return (
            f"Scalp(rsi_l<{self.rsi_long_entry},rsi_s>{self.rsi_short_entry},"
            f"bb={self.bb_long_zone}/{self.bb_short_zone},{n}trend)"
        )

    def set_df_1h(self, df_1h: pd.DataFrame) -> None:
        self._df_1h = df_1h

    def _prepare_1h(self, df_15m: pd.DataFrame) -> None:
        if self._df_1h is None or len(self._df_1h) < self.ema_slow + self.slope_window:
            self._trend_at = pd.Series("NEUTRAL", index=df_15m.index)
            return

        h = self._df_1h.copy()
        close_1h = h["close"]
        ema_fast_s = ta.trend.EMAIndicator(close_1h, window=self.ema_fast).ema_indicator()
        ema_slow_s = ta.trend.EMAIndicator(close_1h, window=self.ema_slow).ema_indicator()
        slope = ema_fast_s.diff(self.slope_window) / close_1h * 100

        trend_1h = pd.Series("NEUTRAL", index=h.index)
        trend_1h[(ema_fast_s > ema_slow_s) & (close_1h > ema_fast_s) & (slope > 0)] = "BULLISH"
        trend_1h[(ema_fast_s < ema_slow_s) & (close_1h < ema_fast_s) & (slope < 0)] = "BEARISH"

        h_mapped = pd.DataFrame({"open_time": h["open_time"], "trend": trend_1h.values})
        df_sorted = df_15m[["open_time"]].copy().sort_values("open_time")
        mapped = pd.merge_asof(df_sorted, h_mapped.sort_values("open_time"),
                               on="open_time", direction="backward")
        self._trend_at = mapped["trend"].values

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=self.rsi_period).rsi()
        bb = ta.volatility.BollingerBands(df["close"], window=self.bb_period, window_dev=self.bb_std)
        df["bb_lower"] = bb.bollinger_lband()
        df["bb_upper"] = bb.bollinger_hband()
        bb_range = df["bb_upper"] - df["bb_lower"]
        df["bb_pct"] = (df["close"] - df["bb_lower"]) / bb_range.replace(0, float("nan"))
        self._prepare_1h(df)
        df["trend"] = self._trend_at
        return df

    def signal(self, row: pd.Series, prev: pd.Series | None) -> str | None:
        if pd.isna(row.get("rsi")) or pd.isna(row.get("bb_pct")):
            return None

        rsi = row["rsi"]
        bb_pct = row["bb_pct"]
        bb_upper = row["bb_upper"]
        bb_lower = row["bb_lower"]
        price = row["close"]
        trend = row.get("trend", "NEUTRAL")

        if pd.isna(bb_pct):
            return None

        # Allowed trends for each direction
        long_ok = trend == "BULLISH" or (self.allow_neutral and trend == "NEUTRAL")
        short_ok = trend == "BEARISH" or (self.allow_neutral and trend == "NEUTRAL")

        # ── LONG entry ──
        if long_ok and rsi < self.rsi_long_entry and bb_pct < self.bb_long_zone:
            return "BUY"

        # ── SHORT entry ──
        if short_ok and rsi > self.rsi_short_entry and bb_pct > self.bb_short_zone:
            return "SHORT"

        # ── LONG exit ──
        if rsi > self.rsi_long_exit or price >= bb_upper * 0.99:
            return "SELL"

        # ── SHORT exit ──
        if rsi < self.rsi_short_exit or price <= bb_lower * 1.01:
            return "CLOSE_SHORT"

        return None


# ── Trend filter helper (shared by all new rules) ───────────


class _TrendFilterMixin:
    """Mixin that provides 1h trend filter logic for TradingRule subclasses.

    All new strategies use the same 1h EMA50/EMA200 trend classification.
    Call set_df_1h() before prepare(), then _prepare_trend() inside prepare().
    """

    _df_1h: pd.DataFrame | None
    _trend_at: Any

    def set_df_1h(self, df_1h: pd.DataFrame) -> None:
        self._df_1h = df_1h

    def _prepare_trend(
        self,
        df: pd.DataFrame,
        ema_fast: int = 50,
        ema_slow: int = 200,
        slope_window: int = 5,
    ) -> pd.DataFrame:
        """Compute 1h trend and map to df's timeframe. Adds 'trend' column."""
        if self._df_1h is None or len(self._df_1h) < ema_slow + slope_window:
            df["trend"] = "NEUTRAL"
            return df

        h = self._df_1h.copy()
        close_1h = h["close"]
        ema_f = ta.trend.EMAIndicator(close_1h, window=ema_fast).ema_indicator()
        ema_s = ta.trend.EMAIndicator(close_1h, window=ema_slow).ema_indicator()
        slope = ema_f.diff(slope_window) / close_1h * 100

        trend_1h = pd.Series("NEUTRAL", index=h.index)
        trend_1h[(ema_f > ema_s) & (close_1h > ema_f) & (slope > 0)] = "BULLISH"
        trend_1h[(ema_f < ema_s) & (close_1h < ema_f) & (slope < 0)] = "BEARISH"

        h_mapped = pd.DataFrame({
            "open_time": h["open_time"],
            "trend": trend_1h.values,
        }).sort_values("open_time")

        df_sorted = df[["open_time"]].copy().sort_values("open_time")
        mapped = pd.merge_asof(df_sorted, h_mapped, on="open_time", direction="backward")
        df["trend"] = mapped["trend"].values
        return df


# ── 2.1 EMA Crossover + ADX ────────────────────────────────


class EMACrossoverADXRule(_TrendFilterMixin, TradingRule):
    """Trend following: EMA12/EMA26 crossover confirmed by ADX strength.

    LONG:  trend BULLISH + EMA12 crosses above EMA26 + ADX > 20
    SHORT: trend BEARISH + EMA12 crosses below EMA26 + ADX > 20
    Exit:  opposite EMA cross OR ADX < 15
    """

    def __init__(
        self,
        ema_fast: int = 12,
        ema_slow: int = 26,
        adx_period: int = 14,
        adx_entry: float = 20.0,
        adx_exit: float = 15.0,
        df_1h: pd.DataFrame | None = None,
    ) -> None:
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.adx_period = adx_period
        self.adx_entry = adx_entry
        self.adx_exit = adx_exit
        self._df_1h = df_1h
        self._trend_at: Any = None

    @property
    def name(self) -> str:
        return f"EMACrossADX(ema={self.ema_fast}/{self.ema_slow},adx>{self.adx_entry})"

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["ema_fast"] = ta.trend.EMAIndicator(df["close"], window=self.ema_fast).ema_indicator()
        df["ema_slow"] = ta.trend.EMAIndicator(df["close"], window=self.ema_slow).ema_indicator()
        adx_ind = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=self.adx_period)
        df["adx"] = adx_ind.adx()
        df = self._prepare_trend(df)
        return df

    def signal(self, row: pd.Series, prev: pd.Series | None) -> str | None:
        if prev is None:
            return None
        for col in ("ema_fast", "ema_slow", "adx", "trend"):
            if pd.isna(row.get(col)) or pd.isna(prev.get(col)):
                return None

        ema_f = row["ema_fast"]
        ema_s = row["ema_slow"]
        prev_ema_f = prev["ema_fast"]
        prev_ema_s = prev["ema_slow"]
        adx = row["adx"]
        trend = row["trend"]

        cross_up = prev_ema_f <= prev_ema_s and ema_f > ema_s
        cross_down = prev_ema_f >= prev_ema_s and ema_f < ema_s

        # Entries
        if trend == "BULLISH" and cross_up and adx > self.adx_entry:
            return "BUY"
        if trend == "BEARISH" and cross_down and adx > self.adx_entry:
            return "SHORT"

        # Exits
        if cross_down or adx < self.adx_exit:
            return "SELL"
        if cross_up or adx < self.adx_exit:
            return "CLOSE_SHORT"

        return None


# ── 2.2 Bollinger Band Squeeze ──────────────────────────────


class BBSqueezeRule(_TrendFilterMixin, TradingRule):
    """Volatility breakout after BB squeeze (low bandwidth).

    Squeeze: BB bandwidth < squeeze_pct for squeeze_bars+ candles
    LONG:  trend BULLISH/NEUTRAL + squeeze + price breaks BB upper + volume > 1.5x
    SHORT: trend BEARISH/NEUTRAL + squeeze + price breaks BB lower + volume > 1.5x
    Exit:  price returns to BB mid OR bandwidth > exit_bw_pct
    """

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        squeeze_pct: float = 0.02,
        squeeze_bars: int = 3,
        exit_bw_pct: float = 0.03,
        min_volume_ratio: float = 1.5,
        df_1h: pd.DataFrame | None = None,
    ) -> None:
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.squeeze_pct = squeeze_pct
        self.squeeze_bars = squeeze_bars
        self.exit_bw_pct = exit_bw_pct
        self.min_volume_ratio = min_volume_ratio
        self._df_1h = df_1h
        self._trend_at: Any = None

    @property
    def name(self) -> str:
        return f"BBSqueeze(sq<{self.squeeze_pct},bars={self.squeeze_bars})"

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        bb = ta.volatility.BollingerBands(df["close"], window=self.bb_period, window_dev=self.bb_std)
        df["bb_upper"] = bb.bollinger_hband()
        df["bb_lower"] = bb.bollinger_lband()
        df["bb_mid"] = bb.bollinger_mavg()
        df["bb_wband"] = bb.bollinger_wband()
        # Squeeze: bandwidth < threshold for N consecutive bars
        is_narrow = (df["bb_wband"] < self.squeeze_pct).astype(int)
        df["squeeze_count"] = is_narrow.rolling(self.squeeze_bars, min_periods=self.squeeze_bars).sum()
        # Volume ratio
        vol_sma = df["volume"].rolling(20).mean()
        df["vol_ratio"] = df["volume"] / vol_sma
        df = self._prepare_trend(df)
        return df

    def signal(self, row: pd.Series, prev: pd.Series | None) -> str | None:
        for col in ("bb_upper", "bb_lower", "bb_mid", "bb_wband", "squeeze_count", "vol_ratio", "trend"):
            if pd.isna(row.get(col)):
                return None

        price = row["close"]
        squeeze = row["squeeze_count"] >= self.squeeze_bars
        vol_ok = row["vol_ratio"] >= self.min_volume_ratio
        trend = row["trend"]
        bw = row["bb_wband"]

        # Entries (squeeze breakout)
        if squeeze and vol_ok:
            if trend in ("BULLISH", "NEUTRAL") and price > row["bb_upper"]:
                return "BUY"
            if trend in ("BEARISH", "NEUTRAL") and price < row["bb_lower"]:
                return "SHORT"

        # Exits
        if price <= row["bb_mid"] or bw > self.exit_bw_pct:
            return "SELL"
        if price >= row["bb_mid"] or bw > self.exit_bw_pct:
            return "CLOSE_SHORT"

        return None


# ── 2.3 MACD Momentum ──────────────────────────────────────


class MACDMomentumRule(_TrendFilterMixin, TradingRule):
    """MACD crossover momentum with histogram confirmation.

    LONG:  trend BULLISH + MACD > 0 + MACD crosses above signal + histogram growing
    SHORT: trend BEARISH + MACD < 0 + MACD crosses below signal + histogram shrinking
    Exit:  opposite MACD cross OR zero-line cross
    """

    def __init__(
        self,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
        df_1h: pd.DataFrame | None = None,
    ) -> None:
        self.fast = fast
        self.slow = slow
        self._signal = signal
        self._df_1h = df_1h
        self._trend_at: Any = None

    @property
    def name(self) -> str:
        return f"MACDMomentum({self.fast}/{self.slow}/{self._signal})"

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        macd_ind = ta.trend.MACD(df["close"], window_slow=self.slow, window_fast=self.fast, window_sign=self._signal)
        df["macd"] = macd_ind.macd()
        df["macd_signal"] = macd_ind.macd_signal()
        df["macd_hist"] = macd_ind.macd_diff()
        df = self._prepare_trend(df)
        return df

    def signal(self, row: pd.Series, prev: pd.Series | None) -> str | None:
        if prev is None:
            return None
        for col in ("macd", "macd_signal", "macd_hist", "trend"):
            if pd.isna(row.get(col)) or pd.isna(prev.get(col)):
                return None

        macd = row["macd"]
        sig = row["macd_signal"]
        hist = row["macd_hist"]
        prev_macd = prev["macd"]
        prev_sig = prev["macd_signal"]
        prev_hist = prev["macd_hist"]
        trend = row["trend"]

        cross_up = prev_macd <= prev_sig and macd > sig
        cross_down = prev_macd >= prev_sig and macd < sig
        hist_growing = hist > prev_hist
        hist_shrinking = hist < prev_hist

        # Entries
        if trend == "BULLISH" and macd > 0 and cross_up and hist_growing:
            return "BUY"
        if trend == "BEARISH" and macd < 0 and cross_down and hist_shrinking:
            return "SHORT"

        # Exits
        if cross_down or (prev_macd > 0 and macd <= 0):
            return "SELL"
        if cross_up or (prev_macd < 0 and macd >= 0):
            return "CLOSE_SHORT"

        return None


# ── 2.4 Donchian Channel Breakout ──────────────────────────


class DonchianBreakoutRule(_TrendFilterMixin, TradingRule):
    """Channel breakout: price breaks 20-bar high/low with volume and consolidation.

    LONG:  trend BULLISH + price > 20-bar high + volume > 1.3x + 5+ bar consolidation
    SHORT: trend BEARISH + price < 20-bar low + volume > 1.3x + 5+ bar consolidation
    Exit:  price returns below Donchian mid
    """

    def __init__(
        self,
        period: int = 20,
        consolidation_bars: int = 5,
        consolidation_pct: float = 1.5,
        min_volume_ratio: float = 1.3,
        df_1h: pd.DataFrame | None = None,
    ) -> None:
        self.period = period
        self.consolidation_bars = consolidation_bars
        self.consolidation_pct = consolidation_pct
        self.min_volume_ratio = min_volume_ratio
        self._df_1h = df_1h
        self._trend_at: Any = None

    @property
    def name(self) -> str:
        return f"DonchianBreakout(p={self.period},consol={self.consolidation_bars})"

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        dc = ta.volatility.DonchianChannel(df["high"], df["low"], df["close"], window=self.period)
        df["dc_high"] = dc.donchian_channel_hband()
        df["dc_low"] = dc.donchian_channel_lband()
        df["dc_mid"] = dc.donchian_channel_mband()
        # Consolidation: range of last N bars as % of close
        rolling_high = df["high"].rolling(self.consolidation_bars).max()
        rolling_low = df["low"].rolling(self.consolidation_bars).min()
        df["consol_range"] = (rolling_high - rolling_low) / df["close"] * 100
        # Volume ratio
        vol_sma = df["volume"].rolling(20).mean()
        df["vol_ratio"] = df["volume"] / vol_sma
        df = self._prepare_trend(df)
        return df

    def signal(self, row: pd.Series, prev: pd.Series | None) -> str | None:
        if prev is None:
            return None
        for col in ("dc_high", "dc_low", "dc_mid", "consol_range", "vol_ratio", "trend"):
            if pd.isna(row.get(col)) or pd.isna(prev.get(col)):
                return None

        price = row["close"]
        prev_price = prev["close"]
        trend = row["trend"]
        vol_ok = row["vol_ratio"] >= self.min_volume_ratio
        consolidated = row["consol_range"] < self.consolidation_pct

        # Entries: breakout from consolidation
        if trend == "BULLISH" and vol_ok and consolidated:
            if prev_price <= prev["dc_high"] and price > row["dc_high"]:
                return "BUY"
        if trend == "BEARISH" and vol_ok and consolidated:
            if prev_price >= prev["dc_low"] and price < row["dc_low"]:
                return "SHORT"

        # Exits: return to mid
        if price < row["dc_mid"]:
            return "SELL"
        if price > row["dc_mid"]:
            return "CLOSE_SHORT"

        return None


# ── 2.5 Keltner Channel Breakout ───────────────────────────


class KeltnerBreakoutRule(_TrendFilterMixin, TradingRule):
    """ATR-based breakout: price breaks Keltner channel with volume.

    LONG:  trend BULLISH + price > Keltner upper (EMA20 + 2*ATR) + volume > 1.2x
    SHORT: trend BEARISH + price < Keltner lower + volume > 1.2x
    Exit:  price returns below Keltner mid
    """

    def __init__(
        self,
        period: int = 20,
        atr_mult: float = 2.0,
        min_volume_ratio: float = 1.2,
        df_1h: pd.DataFrame | None = None,
    ) -> None:
        self.period = period
        self.atr_mult = atr_mult
        self.min_volume_ratio = min_volume_ratio
        self._df_1h = df_1h
        self._trend_at: Any = None

    @property
    def name(self) -> str:
        return f"KeltnerBreakout(p={self.period},atr_m={self.atr_mult})"

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        kc = ta.volatility.KeltnerChannel(
            df["high"], df["low"], df["close"],
            window=self.period, window_atr=self.period,
            multiplier=self.atr_mult,
        )
        df["kc_upper"] = kc.keltner_channel_hband()
        df["kc_lower"] = kc.keltner_channel_lband()
        df["kc_mid"] = kc.keltner_channel_mband()
        vol_sma = df["volume"].rolling(20).mean()
        df["vol_ratio"] = df["volume"] / vol_sma
        df = self._prepare_trend(df)
        return df

    def signal(self, row: pd.Series, prev: pd.Series | None) -> str | None:
        if prev is None:
            return None
        for col in ("kc_upper", "kc_lower", "kc_mid", "vol_ratio", "trend"):
            if pd.isna(row.get(col)) or pd.isna(prev.get(col)):
                return None

        price = row["close"]
        prev_price = prev["close"]
        trend = row["trend"]
        vol_ok = row["vol_ratio"] >= self.min_volume_ratio

        # Entries
        if trend == "BULLISH" and vol_ok and prev_price <= prev["kc_upper"] and price > row["kc_upper"]:
            return "BUY"
        if trend == "BEARISH" and vol_ok and prev_price >= prev["kc_lower"] and price < row["kc_lower"]:
            return "SHORT"

        # Exits
        if price < row["kc_mid"]:
            return "SELL"
        if price > row["kc_mid"]:
            return "CLOSE_SHORT"

        return None


# ── 2.6 VWAP Reversion ────────────────────────────────────


class VWAPReversionRule(_TrendFilterMixin, TradingRule):
    """Intraday mean reversion around VWAP.

    LONG:  trend BULLISH/NEUTRAL + price < VWAP - 2*ATR + RSI < 35
    SHORT: trend BEARISH/NEUTRAL + price > VWAP + 2*ATR + RSI > 65
    Exit:  price returns to VWAP OR RSI normalizes
    """

    def __init__(
        self,
        atr_period: int = 14,
        atr_mult: float = 2.0,
        rsi_period: int = 14,
        rsi_long_entry: float = 35.0,
        rsi_short_entry: float = 65.0,
        rsi_long_exit: float = 50.0,
        rsi_short_exit: float = 50.0,
        vwap_session_bars: int = 96,
        df_1h: pd.DataFrame | None = None,
    ) -> None:
        self.atr_period = atr_period
        self.atr_mult = atr_mult
        self.rsi_period = rsi_period
        self.rsi_long_entry = rsi_long_entry
        self.rsi_short_entry = rsi_short_entry
        self.rsi_long_exit = rsi_long_exit
        self.rsi_short_exit = rsi_short_exit
        self.vwap_session_bars = vwap_session_bars
        self._df_1h = df_1h
        self._trend_at: Any = None

    @property
    def name(self) -> str:
        return f"VWAPRev(atr_m={self.atr_mult},rsi_l<{self.rsi_long_entry})"

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        # VWAP: rolling cumulative (session = vwap_session_bars)
        hlc3 = (df["high"] + df["low"] + df["close"]) / 3
        vwap_num = (hlc3 * df["volume"]).rolling(self.vwap_session_bars, min_periods=1).sum()
        vwap_den = df["volume"].rolling(self.vwap_session_bars, min_periods=1).sum()
        df["vwap"] = vwap_num / vwap_den.replace(0, np.nan)
        # ATR
        df["atr"] = ta.volatility.AverageTrueRange(
            df["high"], df["low"], df["close"], window=self.atr_period
        ).average_true_range()
        # RSI
        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=self.rsi_period).rsi()
        df = self._prepare_trend(df)
        return df

    def signal(self, row: pd.Series, prev: pd.Series | None) -> str | None:
        for col in ("vwap", "atr", "rsi", "trend"):
            if pd.isna(row.get(col)):
                return None

        price = row["close"]
        vwap = row["vwap"]
        atr = row["atr"]
        rsi = row["rsi"]
        trend = row["trend"]

        vwap_lower = vwap - self.atr_mult * atr
        vwap_upper = vwap + self.atr_mult * atr

        # Entries
        if trend in ("BULLISH", "NEUTRAL") and price < vwap_lower and rsi < self.rsi_long_entry:
            return "BUY"
        if trend in ("BEARISH", "NEUTRAL") and price > vwap_upper and rsi > self.rsi_short_entry:
            return "SHORT"

        # Exits
        if price >= vwap or rsi > self.rsi_long_exit:
            return "SELL"
        if price <= vwap or rsi < self.rsi_short_exit:
            return "CLOSE_SHORT"

        return None


# ── 2.7 RSI Divergence ────────────────────────────────────


class RSIDivergenceRule(_TrendFilterMixin, TradingRule):
    """RSI divergence reversal — price makes new extreme, RSI doesn't.

    LONG:  trend BULLISH/NEUTRAL + price lower low + RSI higher low (bullish divergence)
    SHORT: trend BEARISH/NEUTRAL + price higher high + RSI lower high (bearish divergence)
    Exit:  RSI normalizes OR target reached
    """

    def __init__(
        self,
        rsi_period: int = 14,
        swing_window: int = 5,
        rsi_long_exit: float = 60.0,
        rsi_short_exit: float = 40.0,
        df_1h: pd.DataFrame | None = None,
    ) -> None:
        self.rsi_period = rsi_period
        self.swing_window = swing_window
        self.rsi_long_exit = rsi_long_exit
        self.rsi_short_exit = rsi_short_exit
        self._df_1h = df_1h
        self._trend_at: Any = None

    @property
    def name(self) -> str:
        return f"RSIDiv(rsi={self.rsi_period},swing={self.swing_window})"

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=self.rsi_period).rsi()
        w = self.swing_window
        # Swing lows: low is the min of surrounding window
        df["swing_low"] = df["low"].rolling(2 * w + 1, center=True).min()
        df["is_swing_low"] = df["low"] == df["swing_low"]
        # Swing highs: high is the max of surrounding window
        df["swing_high"] = df["high"].rolling(2 * w + 1, center=True).max()
        df["is_swing_high"] = df["high"] == df["swing_high"]
        # Track previous swing low/high prices and RSI values
        df["prev_swing_low_price"] = np.nan
        df["prev_swing_low_rsi"] = np.nan
        df["prev_swing_high_price"] = np.nan
        df["prev_swing_high_rsi"] = np.nan

        last_sl_price = np.nan
        last_sl_rsi = np.nan
        last_sh_price = np.nan
        last_sh_rsi = np.nan

        for i in range(len(df)):
            df.iloc[i, df.columns.get_loc("prev_swing_low_price")] = last_sl_price
            df.iloc[i, df.columns.get_loc("prev_swing_low_rsi")] = last_sl_rsi
            df.iloc[i, df.columns.get_loc("prev_swing_high_price")] = last_sh_price
            df.iloc[i, df.columns.get_loc("prev_swing_high_rsi")] = last_sh_rsi

            if df.iloc[i]["is_swing_low"]:
                last_sl_price = df.iloc[i]["low"]
                last_sl_rsi = df.iloc[i]["rsi"]
            if df.iloc[i]["is_swing_high"]:
                last_sh_price = df.iloc[i]["high"]
                last_sh_rsi = df.iloc[i]["rsi"]

        df = self._prepare_trend(df)
        return df

    def signal(self, row: pd.Series, prev: pd.Series | None) -> str | None:
        for col in ("rsi", "trend"):
            if pd.isna(row.get(col)):
                return None

        rsi = row["rsi"]
        trend = row["trend"]

        # Bullish divergence: price lower low + RSI higher low
        if (row.get("is_swing_low")
                and not pd.isna(row.get("prev_swing_low_price"))
                and not pd.isna(row.get("prev_swing_low_rsi"))):
            if (row["low"] < row["prev_swing_low_price"]
                    and rsi > row["prev_swing_low_rsi"]
                    and trend in ("BULLISH", "NEUTRAL")):
                return "BUY"

        # Bearish divergence: price higher high + RSI lower high
        if (row.get("is_swing_high")
                and not pd.isna(row.get("prev_swing_high_price"))
                and not pd.isna(row.get("prev_swing_high_rsi"))):
            if (row["high"] > row["prev_swing_high_price"]
                    and rsi < row["prev_swing_high_rsi"]
                    and trend in ("BEARISH", "NEUTRAL")):
                return "SHORT"

        # Exits
        if rsi > self.rsi_long_exit:
            return "SELL"
        if rsi < self.rsi_short_exit:
            return "CLOSE_SHORT"

        return None


# ── 2.8 Stochastic Oscillator Cross ───────────────────────


class StochasticCrossRule(_TrendFilterMixin, TradingRule):
    """Stochastic oscillator crossover in extreme zones.

    LONG:  trend BULLISH + %K and %D < 20 + %K crosses above %D
    SHORT: trend BEARISH + %K and %D > 80 + %K crosses below %D
    Exit:  opposite cross OR opposite zone reached
    """

    def __init__(
        self,
        k_period: int = 14,
        d_period: int = 3,
        oversold: float = 20.0,
        overbought: float = 80.0,
        df_1h: pd.DataFrame | None = None,
    ) -> None:
        self.k_period = k_period
        self.d_period = d_period
        self.oversold = oversold
        self.overbought = overbought
        self._df_1h = df_1h
        self._trend_at: Any = None

    @property
    def name(self) -> str:
        return f"StochCross(k={self.k_period},d={self.d_period})"

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        stoch = ta.momentum.StochasticOscillator(
            df["high"], df["low"], df["close"],
            window=self.k_period, smooth_window=self.d_period,
        )
        df["stoch_k"] = stoch.stoch()
        df["stoch_d"] = stoch.stoch_signal()
        df = self._prepare_trend(df)
        return df

    def signal(self, row: pd.Series, prev: pd.Series | None) -> str | None:
        if prev is None:
            return None
        for col in ("stoch_k", "stoch_d", "trend"):
            if pd.isna(row.get(col)) or pd.isna(prev.get(col)):
                return None

        k = row["stoch_k"]
        d = row["stoch_d"]
        prev_k = prev["stoch_k"]
        prev_d = prev["stoch_d"]
        trend = row["trend"]

        cross_up = prev_k <= prev_d and k > d
        cross_down = prev_k >= prev_d and k < d

        # Entries
        if trend == "BULLISH" and k < self.oversold and d < self.oversold and cross_up:
            return "BUY"
        if trend == "BEARISH" and k > self.overbought and d > self.overbought and cross_down:
            return "SHORT"

        # Exits
        if cross_down or k > self.overbought:
            return "SELL"
        if cross_up or k < self.oversold:
            return "CLOSE_SHORT"

        return None


# ── 3. Combined Rule (consensus voting) ───────────────────


class CombinedRule(TradingRule):
    """Multi-strategy voting system.

    Entry:  N+ strategies vote BUY -> BUY, N+ vote SHORT -> SHORT
    Exit:   N+ strategies vote SELL/CLOSE_SHORT (same threshold as entry)

    Entries have priority over exits: if enough strategies agree on a
    direction, we enter regardless of exit signals from other strategies.
    """

    def __init__(
        self,
        rules: list[TradingRule] | None = None,
        entry_threshold: int = 2,
        exit_threshold: int | None = None,
        df_1h: pd.DataFrame | None = None,
    ) -> None:
        self.rules = rules or self._default_rules()
        self.entry_threshold = entry_threshold
        # Exit threshold defaults to entry_threshold (conservative exits
        # with threshold=1 fire too often since most strategies have
        # loose exit conditions like "price below channel mid")
        self.exit_threshold = exit_threshold if exit_threshold is not None else entry_threshold
        self._df_1h = df_1h

    @staticmethod
    def _default_rules() -> list[TradingRule]:
        return [
            EMACrossoverADXRule(),
            BBSqueezeRule(),
            MACDMomentumRule(),
            DonchianBreakoutRule(),
            KeltnerBreakoutRule(),
            VWAPReversionRule(),
            RSIDivergenceRule(),
            StochasticCrossRule(),
            HyperliquidMeanRevRule(),
        ]

    @property
    def name(self) -> str:
        return f"Combined({len(self.rules)}rules,thresh={self.entry_threshold})"

    def set_df_1h(self, df_1h: pd.DataFrame) -> None:
        self._df_1h = df_1h
        for rule in self.rules:
            if hasattr(rule, "set_df_1h"):
                rule.set_df_1h(df_1h)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        # Store prepared DataFrames for signal computation
        self._prepared_dfs = []
        for rule in self.rules:
            self._prepared_dfs.append(rule.prepare(df))
        return result

    def signal(self, row: pd.Series, prev: pd.Series | None) -> str | None:
        buy_votes = 0
        short_votes = 0
        sell_votes = 0
        close_short_votes = 0

        for i, rule in enumerate(self.rules):
            prep_df = self._prepared_dfs[i]
            idx = row.name
            if idx >= len(prep_df):
                continue
            rule_row = prep_df.iloc[idx]
            rule_prev = prep_df.iloc[idx - 1] if prev is not None and idx > 0 else None

            sig = rule.signal(rule_row, rule_prev)

            if sig == "BUY":
                buy_votes += 1
            elif sig == "SHORT":
                short_votes += 1
            elif sig == "SELL":
                sell_votes += 1
            elif sig == "CLOSE_SHORT":
                close_short_votes += 1

        # Entries have priority over exits
        if buy_votes >= self.entry_threshold:
            return "BUY"
        if short_votes >= self.entry_threshold:
            return "SHORT"

        # Exits require threshold agreement too
        if sell_votes >= self.exit_threshold:
            return "SELL"
        if close_short_votes >= self.exit_threshold:
            return "CLOSE_SHORT"

        return None


# ── Backtest engine ──────────────────────────────────────────


def load_candles(path: str | Path) -> pd.DataFrame:
    """Load a CSV file produced by download_history.py into a DataFrame."""
    df = pd.read_csv(path)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume", "quote_volume"):
        df[col] = df[col].astype(float)
    df = df.sort_values("open_time").reset_index(drop=True)
    return df


class BacktestEngine:
    """Run a backtest of a TradingRule on historical candles.

    Handles position sizing, stop loss, take profit, trailing stop,
    fees, slippage, and LONG/SHORT directions.
    """

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()

    # ── Helpers ───────────────────────────────────────────

    def _update_trailing(self, pos: _Position, high: float, low: float) -> None:
        """Update trailing stop for a position based on candle high/low."""
        cfg = self.config
        entry = pos.entry_price

        if pos.direction == "LONG":
            pos.max_price_seen = max(pos.max_price_seen, high)
            gain_pct = (pos.max_price_seen - entry) / entry * 100 if entry > 0 else 0
            old_sl = pos.stop_loss

            if gain_pct >= cfg.trailing_tight_pct:
                new_sl = pos.max_price_seen * (1 - cfg.trailing_tight_distance_pct / 100)
            elif gain_pct >= cfg.trailing_start_pct:
                new_sl = pos.max_price_seen * (1 - cfg.trailing_distance_pct / 100)
            elif gain_pct >= cfg.trailing_breakeven_pct:
                new_sl = entry
            else:
                return
            pos.stop_loss = max(pos.stop_loss, new_sl)  # SL only moves up
        else:  # SHORT
            pos.min_price_seen = min(pos.min_price_seen, low)
            gain_pct = (entry - pos.min_price_seen) / entry * 100 if entry > 0 else 0
            old_sl = pos.stop_loss

            if gain_pct >= cfg.trailing_tight_pct:
                new_sl = pos.min_price_seen * (1 + cfg.trailing_tight_distance_pct / 100)
            elif gain_pct >= cfg.trailing_start_pct:
                new_sl = pos.min_price_seen * (1 + cfg.trailing_distance_pct / 100)
            elif gain_pct >= cfg.trailing_breakeven_pct:
                new_sl = entry
            else:
                return
            pos.stop_loss = min(pos.stop_loss, new_sl)  # SL only moves down

    def _check_sl_tp(self, pos: _Position, high: float, low: float) -> tuple[float | None, str]:
        """Check SL/TP hit. Returns (exit_price, reason) or (None, "")."""
        if pos.direction == "LONG":
            if low <= pos.stop_loss:
                return pos.stop_loss, "sl"
            if high >= pos.take_profit:
                return pos.take_profit, "tp"
        else:  # SHORT
            if high >= pos.stop_loss:
                return pos.stop_loss, "sl"
            if low <= pos.take_profit:
                return pos.take_profit, "tp"
        return None, ""

    def _calc_pnl(self, pos: _Position, exit_price: float) -> tuple[float, float]:
        """Calculate (pnl, fee) for closing a position."""
        cfg = self.config
        fee = pos.quantity * pos.entry_price * cfg.fee_rate + pos.quantity * exit_price * cfg.fee_rate
        if pos.direction == "LONG":
            pnl = pos.quantity * (exit_price - pos.entry_price) - fee
        else:
            pnl = pos.quantity * (pos.entry_price - exit_price) - fee
        return pnl, fee

    def _close_pos(
        self, pos: _Position, exit_price: float, reason: str,
        ts: pd.Timestamp, sym: str, balance: float, result: BacktestResult,
    ) -> float:
        """Record a trade close and return updated balance."""
        cfg = self.config
        exit_price *= (1 - cfg.slippage_pct / 100)
        pnl, fee = self._calc_pnl(pos, exit_price)
        # Return collateral + PnL (simplified: notional back minus exit fee)
        balance += pos.quantity * exit_price - pos.quantity * exit_price * cfg.fee_rate
        result.trades.append(Trade(
            symbol=sym, entry_time=pos.entry_time, exit_time=ts,
            side=pos.direction, entry_price=pos.entry_price,
            exit_price=exit_price, quantity=pos.quantity,
            pnl=pnl, fee=fee, exit_reason=reason,
        ))
        return balance

    def _open_pos(
        self, sym: str, ts: pd.Timestamp, price: float,
        direction: str, balance: float, positions: list[_Position],
    ) -> float:
        """Open a new position and return updated balance."""
        cfg = self.config
        alloc = balance * (cfg.max_trade_pct / 100)
        entry_price = price * (1 + cfg.slippage_pct / 100)
        qty = alloc / entry_price
        cost = qty * entry_price * (1 + cfg.fee_rate)

        if cost > balance or qty <= 0:
            return balance

        balance -= cost
        if direction == "LONG":
            sl = entry_price * (1 - cfg.stop_loss_pct / 100)
            tp = entry_price * (1 + cfg.take_profit_pct / 100)
        else:
            sl = entry_price * (1 + cfg.stop_loss_pct / 100)
            tp = entry_price * (1 - cfg.take_profit_pct / 100)

        positions.append(_Position(
            symbol=sym, entry_time=ts, entry_price=entry_price,
            quantity=qty, stop_loss=sl, take_profit=tp,
            direction=direction,
            max_price_seen=entry_price,
            min_price_seen=entry_price,
        ))
        return balance

    def _mark_to_market(self, pos: _Position, price: float) -> float:
        """Return unrealized value of a position at current price."""
        if pos.direction == "LONG":
            return pos.quantity * price
        else:
            # SHORT: collateral + unrealized PnL
            return pos.quantity * pos.entry_price + pos.quantity * (pos.entry_price - price)

    # ── Single-symbol run ────────────────────────────────

    def run(
        self,
        df: pd.DataFrame,
        rule: TradingRule,
        symbol: str = "UNKNOWN",
    ) -> BacktestResult:
        """Run the backtest and return the result."""
        cfg = self.config
        balance = cfg.initial_balance
        positions: list[_Position] = []
        result = BacktestResult(config=cfg)

        df = rule.prepare(df)
        prev_row: pd.Series | None = None

        for i, row in df.iterrows():
            ts = row["open_time"]
            price = row["close"]
            high = row["high"]
            low = row["low"]

            # ── Update trailing stops ──
            for pos in positions:
                self._update_trailing(pos, high, low)

            # ── Check SL/TP ──
            closed_indices: list[int] = []
            for pi, pos in enumerate(positions):
                exit_price, exit_reason = self._check_sl_tp(pos, high, low)
                if exit_price is not None:
                    balance = self._close_pos(pos, exit_price, exit_reason, ts, symbol, balance, result)
                    closed_indices.append(pi)
            for pi in reversed(closed_indices):
                positions.pop(pi)

            # ── Check signal ──
            sig = rule.signal(row, prev_row)

            if sig == "BUY" and len(positions) < cfg.max_open_positions:
                balance = self._open_pos(symbol, ts, price, "LONG", balance, positions)
            elif sig == "SHORT" and len(positions) < cfg.max_open_positions:
                balance = self._open_pos(symbol, ts, price, "SHORT", balance, positions)
            elif sig == "SELL":
                # Close oldest LONG
                for pi, pos in enumerate(positions):
                    if pos.direction == "LONG":
                        balance = self._close_pos(pos, price, "signal", ts, symbol, balance, result)
                        positions.pop(pi)
                        break
            elif sig == "CLOSE_SHORT":
                # Close oldest SHORT
                for pi, pos in enumerate(positions):
                    if pos.direction == "SHORT":
                        balance = self._close_pos(pos, price, "signal", ts, symbol, balance, result)
                        positions.pop(pi)
                        break

            # ── Equity snapshot ──
            mtm = sum(self._mark_to_market(p, price) for p in positions)
            result.equity_curve.append((ts, balance + mtm))
            prev_row = row

        # ── Close remaining ──
        if positions and len(df) > 0:
            last = df.iloc[-1]
            for pos in positions:
                pnl, fee = self._calc_pnl(pos, last["close"])
                result.trades.append(Trade(
                    symbol=symbol, entry_time=pos.entry_time,
                    exit_time=last["open_time"], side=pos.direction,
                    entry_price=pos.entry_price, exit_price=last["close"],
                    quantity=pos.quantity, pnl=pnl, fee=fee, exit_reason="end",
                ))

        return result

    def run_multi(
        self,
        datasets: dict[str, pd.DataFrame],
        rule: TradingRule,
    ) -> BacktestResult:
        """Run backtest across multiple symbols with shared balance.

        Interleaves candles by timestamp and processes them in order.
        """
        cfg = self.config
        balance = cfg.initial_balance
        positions: list[_Position] = []
        result = BacktestResult(config=cfg)

        frames: list[pd.DataFrame] = []
        prepared: dict[str, pd.DataFrame] = {}
        for sym, df in datasets.items():
            prepped = rule.prepare(df)
            prepped["_symbol"] = sym
            prepared[sym] = prepped
            frames.append(prepped)

        if not frames:
            return result

        merged = pd.concat(frames, ignore_index=True).sort_values("open_time").reset_index(drop=True)
        prev_by_sym: dict[str, pd.Series] = {}

        for _, row in merged.iterrows():
            ts = row["open_time"]
            price = row["close"]
            high = row["high"]
            low = row["low"]
            sym = row["_symbol"]

            # ── Update trailing + check SL/TP for this symbol ──
            closed_indices: list[int] = []
            for pi, pos in enumerate(positions):
                if pos.symbol != sym:
                    continue
                self._update_trailing(pos, high, low)
                exit_price, exit_reason = self._check_sl_tp(pos, high, low)
                if exit_price is not None:
                    balance = self._close_pos(pos, exit_price, exit_reason, ts, sym, balance, result)
                    closed_indices.append(pi)
            for pi in reversed(closed_indices):
                positions.pop(pi)

            # ── Signal ──
            prev = prev_by_sym.get(sym)
            sig = rule.signal(row, prev)
            prev_by_sym[sym] = row

            if sig == "BUY" and len(positions) < cfg.max_open_positions:
                balance = self._open_pos(sym, ts, price, "LONG", balance, positions)
            elif sig == "SHORT" and len(positions) < cfg.max_open_positions:
                balance = self._open_pos(sym, ts, price, "SHORT", balance, positions)
            elif sig == "SELL":
                for pi, pos in enumerate(positions):
                    if pos.symbol == sym and pos.direction == "LONG":
                        balance = self._close_pos(pos, price, "signal", ts, sym, balance, result)
                        positions.pop(pi)
                        break
            elif sig == "CLOSE_SHORT":
                for pi, pos in enumerate(positions):
                    if pos.symbol == sym and pos.direction == "SHORT":
                        balance = self._close_pos(pos, price, "signal", ts, sym, balance, result)
                        positions.pop(pi)
                        break

            # ── Equity snapshot ──
            mark = sum(self._mark_to_market(p, price) for p in positions if p.symbol == sym)
            other_mark = sum(self._mark_to_market(p, p.entry_price) for p in positions if p.symbol != sym)
            result.equity_curve.append((ts, balance + mark + other_mark))

        # Close remaining
        for pos in positions:
            last_df = prepared.get(pos.symbol)
            if last_df is None or len(last_df) == 0:
                continue
            last_price = last_df.iloc[-1]["close"]
            last_ts = last_df.iloc[-1]["open_time"]
            pnl, fee = self._calc_pnl(pos, last_price)
            result.trades.append(Trade(
                symbol=pos.symbol, entry_time=pos.entry_time, exit_time=last_ts,
                side=pos.direction, entry_price=pos.entry_price, exit_price=last_price,
                quantity=pos.quantity, pnl=pnl, fee=fee, exit_reason="end",
            ))

        return result
