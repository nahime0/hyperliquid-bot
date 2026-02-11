"""Run backtests on historical data and generate reports.

Usage:
    # Mean reversion on all downloaded pairs
    .venv/bin/python -m scripts.backtest_runner

    # Grid strategy on specific pair
    .venv/bin/python -m scripts.backtest_runner --strategy grid --symbols ETHUSDC

    # Custom parameters
    .venv/bin/python -m scripts.backtest_runner --strategy mean_reversion \
        --rsi-entry 25 --rsi-exit 60 --stop-loss 1.5 --take-profit 2.0

    # Parameter sweep
    .venv/bin/python -m scripts.backtest_runner --sweep
"""
from __future__ import annotations

import argparse
from pathlib import Path

from data.backtest import (
    BacktestConfig,
    BacktestEngine,
    GridSimRule,
    MeanReversionRule,
    TradingRule,
    load_candles,
)
from utils.logger import setup_logging, get_logger

logger = get_logger(__name__)

HISTORY_DIR = Path(__file__).resolve().parent.parent / "data" / "history"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "backtest_results"


def find_data_files(symbols: list[str] | None, interval: str) -> dict[str, Path]:
    """Find CSV files in history dir matching the requested symbols."""
    if not HISTORY_DIR.exists():
        logger.error("No history dir at %s — run download_history.py first", HISTORY_DIR)
        return {}

    files: dict[str, Path] = {}
    for csv_file in sorted(HISTORY_DIR.glob(f"*_{interval}.csv")):
        sym = csv_file.stem.replace(f"_{interval}", "")
        if symbols is None or sym in symbols:
            files[sym] = csv_file

    if not files:
        logger.error("No matching CSV files found in %s", HISTORY_DIR)
    return files


def run_single(
    symbols: list[str] | None,
    interval: str,
    rule: TradingRule,
    config: BacktestConfig,
) -> None:
    """Run backtest (single or multi-symbol) and print report."""
    engine = BacktestEngine(config)
    files = find_data_files(symbols, interval)
    if not files:
        return

    if len(files) == 1:
        sym, path = next(iter(files.items()))
        df = load_candles(path)
        logger.info("Running %s on %s (%d candles)", rule.name, sym, len(df))
        result = engine.run(df, rule, symbol=sym)
    else:
        datasets = {}
        for sym, path in files.items():
            df = load_candles(path)
            datasets[sym] = df
            logger.info("  Loaded %s: %d candles", sym, len(df))
        logger.info("Running %s on %d symbols", rule.name, len(datasets))
        result = engine.run_multi(datasets, rule)

    result.print_report()

    # Save outputs
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = rule.name.split("(")[0].lower()
    syms = "_".join(files.keys()) if len(files) <= 3 else f"{len(files)}pairs"
    base = f"{tag}_{syms}"

    result.save_equity_curve(OUTPUT_DIR / f"{base}_equity.csv")
    result.plot_equity_curve(OUTPUT_DIR / f"{base}_equity.png")
    print(f"\nResults saved to {OUTPUT_DIR / base}_*")


def run_sweep(
    symbols: list[str] | None,
    interval: str,
    config: BacktestConfig,
) -> None:
    """Run parameter sweep across multiple configurations."""
    files = find_data_files(symbols, interval)
    if not files:
        return

    # Load data once
    datasets = {}
    for sym, path in files.items():
        datasets[sym] = load_candles(path)

    print("\n" + "=" * 90)
    print("  PARAMETER SWEEP")
    print("=" * 90)
    print(f"  {'Strategy':<45s} {'Return':>8s} {'WR':>6s} {'PF':>7s} {'Sharpe':>7s} {'MaxDD':>7s} {'Trades':>7s}")
    print("-" * 90)

    engine = BacktestEngine(config)

    # Mean reversion sweep
    for rsi_entry in (25, 28, 32):
        for rsi_exit in (50, 55, 60):
            rule = MeanReversionRule(rsi_entry=rsi_entry, rsi_exit=rsi_exit)
            if len(datasets) == 1:
                sym, df = next(iter(datasets.items()))
                r = engine.run(df, rule, symbol=sym)
            else:
                r = engine.run_multi(datasets, rule)

            s = r.summary()
            print(
                f"  {rule.name:<45s} {s['total_return_pct']:>+7.2f}% "
                f"{s['win_rate']:>5.1f}% {s['profit_factor']:>6.3f} "
                f"{s['sharpe_ratio']:>6.3f} {s['max_drawdown_pct']:>6.2f}% "
                f"{s['total_trades']:>6d}"
            )

    # Grid sweep
    for spacing in (0.3, 0.5, 0.8, 1.0):
        for range_pct in (2.0, 3.0, 5.0):
            rule = GridSimRule(spacing_pct=spacing, range_pct=range_pct)
            if len(datasets) == 1:
                sym, df = next(iter(datasets.items()))
                r = engine.run(df, rule, symbol=sym)
            else:
                r = engine.run_multi(datasets, rule)

            s = r.summary()
            print(
                f"  {rule.name:<45s} {s['total_return_pct']:>+7.2f}% "
                f"{s['win_rate']:>5.1f}% {s['profit_factor']:>6.3f} "
                f"{s['sharpe_ratio']:>6.3f} {s['max_drawdown_pct']:>6.2f}% "
                f"{s['total_trades']:>6d}"
            )

    print("=" * 90)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run backtests on historical data")
    parser.add_argument("--symbols", nargs="+", default=None, help="Symbols to test (default: all downloaded)")
    parser.add_argument("--interval", default="15m", help="Candle interval (must match downloaded data)")
    parser.add_argument("--strategy", choices=["mean_reversion", "grid"], default="mean_reversion")
    parser.add_argument("--balance", type=float, default=100.0, help="Initial USDC balance")
    parser.add_argument("--stop-loss", type=float, default=1.0, help="Stop loss %%")
    parser.add_argument("--take-profit", type=float, default=1.5, help="Take profit %%")
    parser.add_argument("--max-trade-pct", type=float, default=10.0, help="Max trade size %%")

    # Mean reversion params
    parser.add_argument("--rsi-entry", type=float, default=28.0, help="RSI entry threshold")
    parser.add_argument("--rsi-exit", type=float, default=55.0, help="RSI exit threshold")
    parser.add_argument("--rsi-period", type=int, default=14, help="RSI period")

    # Grid params
    parser.add_argument("--spacing", type=float, default=0.5, help="Grid spacing %%")
    parser.add_argument("--range", type=float, default=3.0, dest="grid_range", help="Grid range %%")

    # Sweep mode
    parser.add_argument("--sweep", action="store_true", help="Run parameter sweep")

    args = parser.parse_args()
    setup_logging("INFO")

    config = BacktestConfig(
        initial_balance=args.balance,
        stop_loss_pct=args.stop_loss,
        take_profit_pct=args.take_profit,
        max_trade_pct=args.max_trade_pct,
    )

    if args.sweep:
        run_sweep(args.symbols, args.interval, config)
        return

    rule: TradingRule
    if args.strategy == "mean_reversion":
        rule = MeanReversionRule(
            rsi_period=args.rsi_period,
            rsi_entry=args.rsi_entry,
            rsi_exit=args.rsi_exit,
        )
    else:
        rule = GridSimRule(
            spacing_pct=args.spacing,
            range_pct=args.grid_range,
        )

    run_single(args.symbols, args.interval, rule, config)


if __name__ == "__main__":
    main()
