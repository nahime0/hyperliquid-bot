"""Backtesting engine for rule-based strategy simulation.

Uses deterministic rules as a proxy for the AI decision engine
(invoking Claude thousands of times isn't feasible for backtesting).

Supported strategies:
  - MeanReversion: RSI oversold + Bollinger Band breakout
  - GridSim: simulated grid trading on historical candles

Usage:
    from data.backtest import BacktestEngine, MeanReversionRule, GridSimRule, BacktestConfig
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

import pandas as pd
import ta

from utils.logger import get_logger

logger = get_logger(__name__)


# ── Configuration ────────────────────────────────────────────


@dataclass
class BacktestConfig:
    """Global backtest parameters."""
    initial_balance: float = 100.0
    fee_rate: float = 0.00075     # 0.075% per trade (BNB discount)
    max_trade_pct: float = 10.0   # max % of balance per trade
    stop_loss_pct: float = 1.0
    take_profit_pct: float = 1.5
    max_open_positions: int = 5
    slippage_pct: float = 0.01    # 0.01% slippage simulation


# ── Trade record ─────────────────────────────────────────────


@dataclass
class Trade:
    """One completed round-trip trade."""
    symbol: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    side: str           # "LONG"
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
    """Abstract rule that generates BUY/SELL signals on each candle."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add indicator columns to the dataframe. Called once before run."""

    @abstractmethod
    def signal(self, row: pd.Series, prev: pd.Series | None) -> str | None:
        """Return "BUY", "SELL", or None for each candle."""


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

    Handles position sizing, stop loss, take profit, fees, and slippage.
    """

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()

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

        # Prepare indicators
        df = rule.prepare(df)

        prev_row: pd.Series | None = None

        for i, row in df.iterrows():
            ts = row["open_time"]
            price = row["close"]
            high = row["high"]
            low = row["low"]

            # ── Check stop loss / take profit on open positions ──
            closed_indices: list[int] = []
            for pi, pos in enumerate(positions):
                exit_price: float | None = None
                exit_reason = ""

                # Stop loss hit (using candle low)
                if low <= pos.stop_loss:
                    exit_price = pos.stop_loss
                    exit_reason = "sl"
                # Take profit hit (using candle high)
                elif high >= pos.take_profit:
                    exit_price = pos.take_profit
                    exit_reason = "tp"

                if exit_price is not None:
                    exit_price *= (1 - cfg.slippage_pct / 100)  # slippage on exit
                    fee = pos.quantity * pos.entry_price * cfg.fee_rate + pos.quantity * exit_price * cfg.fee_rate
                    pnl = pos.quantity * (exit_price - pos.entry_price) - fee
                    balance += pos.quantity * exit_price - pos.quantity * exit_price * cfg.fee_rate
                    result.trades.append(Trade(
                        symbol=symbol,
                        entry_time=pos.entry_time,
                        exit_time=ts,
                        side="LONG",
                        entry_price=pos.entry_price,
                        exit_price=exit_price,
                        quantity=pos.quantity,
                        pnl=pnl,
                        fee=fee,
                        exit_reason=exit_reason,
                    ))
                    closed_indices.append(pi)

            for pi in reversed(closed_indices):
                positions.pop(pi)

            # ── Check signal ──
            sig = rule.signal(row, prev_row)

            if sig == "BUY" and len(positions) < cfg.max_open_positions:
                # Position sizing: fixed % of current balance
                alloc = balance * (cfg.max_trade_pct / 100)
                entry_price = price * (1 + cfg.slippage_pct / 100)  # slippage on entry
                qty = alloc / entry_price
                cost = qty * entry_price * (1 + cfg.fee_rate)

                if cost <= balance and qty > 0:
                    balance -= cost
                    sl = entry_price * (1 - cfg.stop_loss_pct / 100)
                    tp = entry_price * (1 + cfg.take_profit_pct / 100)
                    positions.append(_Position(
                        symbol=symbol,
                        entry_time=ts,
                        entry_price=entry_price,
                        quantity=qty,
                        stop_loss=sl,
                        take_profit=tp,
                    ))

            elif sig == "SELL" and positions:
                # Close oldest position on SELL signal
                pos = positions.pop(0)
                exit_price = price * (1 - cfg.slippage_pct / 100)
                fee = pos.quantity * pos.entry_price * cfg.fee_rate + pos.quantity * exit_price * cfg.fee_rate
                pnl = pos.quantity * (exit_price - pos.entry_price) - fee
                balance += pos.quantity * exit_price - pos.quantity * exit_price * cfg.fee_rate
                result.trades.append(Trade(
                    symbol=symbol,
                    entry_time=pos.entry_time,
                    exit_time=ts,
                    side="LONG",
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    quantity=pos.quantity,
                    pnl=pnl,
                    fee=fee,
                    exit_reason="signal",
                ))

            # ── Equity snapshot ──
            # balance + market value of open positions
            mark_to_market = sum(p.quantity * price for p in positions)
            result.equity_curve.append((ts, balance + mark_to_market))

            prev_row = row

        # ── Close remaining positions at last price ──
        if positions and len(df) > 0:
            last_row = df.iloc[-1]
            last_price = last_row["close"]
            last_ts = last_row["open_time"]
            for pos in positions:
                fee = pos.quantity * pos.entry_price * cfg.fee_rate + pos.quantity * last_price * cfg.fee_rate
                pnl = pos.quantity * (last_price - pos.entry_price) - fee
                result.trades.append(Trade(
                    symbol=symbol,
                    entry_time=pos.entry_time,
                    exit_time=last_ts,
                    side="LONG",
                    entry_price=pos.entry_price,
                    exit_price=last_price,
                    quantity=pos.quantity,
                    pnl=pnl,
                    fee=fee,
                    exit_reason="end",
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

        # Merge all dataframes with symbol column
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

            # ── Check SL/TP for this symbol ──
            closed_indices: list[int] = []
            for pi, pos in enumerate(positions):
                if pos.symbol != sym:
                    continue
                exit_price: float | None = None
                exit_reason = ""
                if low <= pos.stop_loss:
                    exit_price = pos.stop_loss
                    exit_reason = "sl"
                elif high >= pos.take_profit:
                    exit_price = pos.take_profit
                    exit_reason = "tp"

                if exit_price is not None:
                    exit_price *= (1 - cfg.slippage_pct / 100)
                    fee = pos.quantity * pos.entry_price * cfg.fee_rate + pos.quantity * exit_price * cfg.fee_rate
                    pnl = pos.quantity * (exit_price - pos.entry_price) - fee
                    balance += pos.quantity * exit_price - pos.quantity * exit_price * cfg.fee_rate
                    result.trades.append(Trade(
                        symbol=sym, entry_time=pos.entry_time, exit_time=ts,
                        side="LONG", entry_price=pos.entry_price, exit_price=exit_price,
                        quantity=pos.quantity, pnl=pnl, fee=fee, exit_reason=exit_reason,
                    ))
                    closed_indices.append(pi)

            for pi in reversed(closed_indices):
                positions.pop(pi)

            # ── Signal ──
            prev = prev_by_sym.get(sym)
            sig = rule.signal(row, prev)
            prev_by_sym[sym] = row

            if sig == "BUY" and len(positions) < cfg.max_open_positions:
                alloc = balance * (cfg.max_trade_pct / 100)
                entry_price = price * (1 + cfg.slippage_pct / 100)
                qty = alloc / entry_price
                cost = qty * entry_price * (1 + cfg.fee_rate)
                if cost <= balance and qty > 0:
                    balance -= cost
                    positions.append(_Position(
                        symbol=sym, entry_time=ts, entry_price=entry_price,
                        quantity=qty,
                        stop_loss=entry_price * (1 - cfg.stop_loss_pct / 100),
                        take_profit=entry_price * (1 + cfg.take_profit_pct / 100),
                    ))

            elif sig == "SELL":
                # Close oldest position for this symbol
                for pi, pos in enumerate(positions):
                    if pos.symbol == sym:
                        exit_price = price * (1 - cfg.slippage_pct / 100)
                        fee = pos.quantity * pos.entry_price * cfg.fee_rate + pos.quantity * exit_price * cfg.fee_rate
                        pnl = pos.quantity * (exit_price - pos.entry_price) - fee
                        balance += pos.quantity * exit_price - pos.quantity * exit_price * cfg.fee_rate
                        result.trades.append(Trade(
                            symbol=sym, entry_time=pos.entry_time, exit_time=ts,
                            side="LONG", entry_price=pos.entry_price, exit_price=exit_price,
                            quantity=pos.quantity, pnl=pnl, fee=fee, exit_reason="signal",
                        ))
                        positions.pop(pi)
                        break

            # ── Equity snapshot ──
            mark = sum(p.quantity * price for p in positions if p.symbol == sym)
            other_mark = sum(p.quantity * p.entry_price for p in positions if p.symbol != sym)
            result.equity_curve.append((ts, balance + mark + other_mark))

        # Close remaining
        for pos in positions:
            last_df = prepared.get(pos.symbol)
            if last_df is not None and len(last_df) > 0:
                last_price = last_df.iloc[-1]["close"]
                last_ts = last_df.iloc[-1]["open_time"]
            else:
                continue
            fee = pos.quantity * pos.entry_price * cfg.fee_rate + pos.quantity * last_price * cfg.fee_rate
            pnl = pos.quantity * (last_price - pos.entry_price) - fee
            result.trades.append(Trade(
                symbol=pos.symbol, entry_time=pos.entry_time, exit_time=last_ts,
                side="LONG", entry_price=pos.entry_price, exit_price=last_price,
                quantity=pos.quantity, pnl=pnl, fee=fee, exit_reason="end",
            ))

        return result
