"""Run backtests on historical data and generate reports.

Usage:
    # Hyperliquid Mean Reversion (LONG+SHORT) on all downloaded coins
    .venv/bin/python -m scripts.backtest_runner

    # Specific coins
    .venv/bin/python -m scripts.backtest_runner --symbols ETH BTC SOL

    # New strategies
    .venv/bin/python -m scripts.backtest_runner --strategy ema_adx
    .venv/bin/python -m scripts.backtest_runner --strategy combined --combined-threshold 3

    # 5m interval
    .venv/bin/python -m scripts.backtest_runner --interval 5m --strategy macd

    # Parameter sweep (all strategies)
    .venv/bin/python -m scripts.backtest_runner --sweep

    # Combined sweep (threshold 2/3/4)
    .venv/bin/python -m scripts.backtest_runner --sweep --strategy combined
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from data.backtest import (
    BacktestConfig,
    BacktestEngine,
    BacktestResult,
    BBSqueezeRule,
    CombinedRule,
    DonchianBreakoutRule,
    EMACrossoverADXRule,
    GridSimRule,
    HyperliquidMeanRevRule,
    KeltnerBreakoutRule,
    MACDMomentumRule,
    MeanReversionRule,
    RSIDivergenceRule,
    ScalpMeanRevRule,
    StochasticCrossRule,
    TradingRule,
    VWAPReversionRule,
    load_candles,
)
from utils.logger import setup_logging, get_logger

logger = get_logger(__name__)

HISTORY_DIR = Path(__file__).resolve().parent.parent / "data" / "history"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "backtest_results"

STRATEGY_CHOICES = [
    "hyperliquid_mr", "scalp", "mean_reversion", "grid",
    "ema_adx", "bb_squeeze", "macd", "donchian", "keltner",
    "vwap_rev", "rsi_div", "stoch", "combined",
]

# Rules that need 1h data for trend filter
_RULES_WITH_1H = (
    HyperliquidMeanRevRule, ScalpMeanRevRule,
    EMACrossoverADXRule, BBSqueezeRule, MACDMomentumRule,
    DonchianBreakoutRule, KeltnerBreakoutRule, VWAPReversionRule,
    RSIDivergenceRule, StochasticCrossRule, CombinedRule,
)


def find_data_files(symbols: list[str] | None, interval: str) -> dict[str, Path]:
    """Find CSV files in history dir matching the requested symbols."""
    if not HISTORY_DIR.exists():
        logger.error("No history dir at %s -- run download_history.py first", HISTORY_DIR)
        return {}

    files: dict[str, Path] = {}
    for csv_file in sorted(HISTORY_DIR.glob(f"*_{interval}.csv")):
        sym = csv_file.stem.replace(f"_{interval}", "")
        if symbols is None or sym in symbols:
            files[sym] = csv_file

    if not files:
        logger.error("No matching CSV files found in %s", HISTORY_DIR)
    return files


def _load_1h_for_symbol(sym: str) -> pd.DataFrame | None:
    """Try to load the 1h CSV for a symbol."""
    path = HISTORY_DIR / f"{sym}_1h.csv"
    if path.exists():
        return load_candles(path)
    return None


def _set_1h_on_rule(rule: TradingRule, df_1h: pd.DataFrame | None) -> None:
    """Set 1h data on a rule if it supports it."""
    if hasattr(rule, "set_df_1h") and df_1h is not None:
        rule.set_df_1h(df_1h)


def _make_rule(strategy: str, args: argparse.Namespace) -> TradingRule:
    """Create a TradingRule from strategy name and CLI args."""
    if strategy == "hyperliquid_mr":
        return HyperliquidMeanRevRule(
            rsi_period=args.rsi_period,
            rsi_long_entry=args.rsi_long_entry,
            rsi_short_entry=args.rsi_short_entry,
        )
    elif strategy == "scalp":
        return ScalpMeanRevRule(
            rsi_period=args.rsi_period,
            rsi_long_entry=args.rsi_long_entry,
            rsi_short_entry=args.rsi_short_entry,
        )
    elif strategy == "mean_reversion":
        return MeanReversionRule(
            rsi_period=args.rsi_period,
            rsi_entry=args.rsi_entry,
            rsi_exit=args.rsi_exit,
        )
    elif strategy == "grid":
        return GridSimRule(
            spacing_pct=args.spacing,
            range_pct=args.grid_range,
        )
    elif strategy == "ema_adx":
        return EMACrossoverADXRule()
    elif strategy == "bb_squeeze":
        return BBSqueezeRule()
    elif strategy == "macd":
        return MACDMomentumRule()
    elif strategy == "donchian":
        return DonchianBreakoutRule()
    elif strategy == "keltner":
        return KeltnerBreakoutRule()
    elif strategy == "vwap_rev":
        return VWAPReversionRule()
    elif strategy == "rsi_div":
        return RSIDivergenceRule(
            rsi_period=args.rsi_period,
            swing_window=args.swing_window,
            rsi_long_exit=args.rsi_long_exit,
            rsi_short_exit=args.rsi_short_exit,
        )
    elif strategy == "stoch":
        return StochasticCrossRule()
    elif strategy == "combined":
        return CombinedRule(entry_threshold=args.combined_threshold)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")


def run_single(
    symbols: list[str] | None,
    interval: str,
    rule: TradingRule,
    config: BacktestConfig,
) -> BacktestResult | None:
    """Run backtest (single or multi-symbol) and print report."""
    engine = BacktestEngine(config)
    files = find_data_files(symbols, interval)
    if not files:
        return None

    if len(files) == 1:
        sym, path = next(iter(files.items()))
        df = load_candles(path)
        if isinstance(rule, _RULES_WITH_1H):
            df_1h = _load_1h_for_symbol(sym)
            _set_1h_on_rule(rule, df_1h)
            if df_1h is not None:
                logger.info("  Loaded 1h data for %s: %d candles", sym, len(df_1h))
        logger.info("Running %s on %s (%d candles)", rule.name, sym, len(df))
        result = engine.run(df, rule, symbol=sym)
    else:
        datasets = {}
        for sym, path in files.items():
            df = load_candles(path)
            datasets[sym] = df
            logger.info("  Loaded %s: %d candles", sym, len(df))

        if isinstance(rule, _RULES_WITH_1H):
            result = _run_multi_with_1h(engine, datasets, rule, config)
        else:
            logger.info("Running %s on %d symbols", rule.name, len(datasets))
            result = engine.run_multi(datasets, rule)

    result.print_report()

    # Save outputs
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = rule.name.split("(")[0].lower()
    syms = "_".join(files.keys()) if len(files) <= 3 else f"{len(files)}coins"
    base = f"{tag}_{syms}"

    result.save_equity_curve(OUTPUT_DIR / f"{base}_equity.csv")
    result.plot_equity_curve(OUTPUT_DIR / f"{base}_equity.png")
    print(f"\nResults saved to {OUTPUT_DIR / base}_*")
    return result


def _clone_rule_fresh(rule_template: TradingRule, df_1h: pd.DataFrame | None) -> TradingRule:
    """Create a fresh copy of a rule with its own 1h data.

    Uses the rule's __init__ params stored as attributes.
    """
    import copy
    rule = copy.deepcopy(rule_template)
    _set_1h_on_rule(rule, df_1h)
    return rule


def _run_multi_with_1h(
    engine: BacktestEngine,
    datasets: dict[str, pd.DataFrame],
    rule_template: TradingRule,
    config: BacktestConfig,
) -> BacktestResult:
    """Run a rule per-symbol with 1h data, aggregate results."""
    combined = BacktestResult(config=config)
    balance = config.initial_balance

    logger.info("Running %s on %d symbols (per-symbol with 1h data)", rule_template.name, len(datasets))

    for sym, df in datasets.items():
        df_1h = _load_1h_for_symbol(sym)
        rule = _clone_rule_fresh(rule_template, df_1h)

        per_config = BacktestConfig(
            initial_balance=balance,
            fee_rate=config.fee_rate,
            max_trade_pct=config.max_trade_pct,
            stop_loss_pct=config.stop_loss_pct,
            take_profit_pct=config.take_profit_pct,
            max_open_positions=config.max_open_positions,
            slippage_pct=config.slippage_pct,
            trailing_breakeven_pct=config.trailing_breakeven_pct,
            trailing_start_pct=config.trailing_start_pct,
            trailing_distance_pct=config.trailing_distance_pct,
            trailing_tight_pct=config.trailing_tight_pct,
            trailing_tight_distance_pct=config.trailing_tight_distance_pct,
        )
        eng = BacktestEngine(per_config)
        r = eng.run(df, rule, symbol=sym)

        combined.trades.extend(r.trades)
        combined.equity_curve.extend(r.equity_curve)
        balance = per_config.initial_balance + r.total_pnl

    combined.equity_curve.sort(key=lambda x: x[0])
    return combined


def run_sweep(
    symbols: list[str] | None,
    interval: str,
    config: BacktestConfig,
    strategy: str | None = None,
) -> None:
    """Run parameter sweep across multiple configurations."""
    files = find_data_files(symbols, interval)
    if not files:
        return

    # Load data once
    datasets: dict[str, pd.DataFrame] = {}
    datasets_1h: dict[str, pd.DataFrame] = {}
    for sym, path in files.items():
        datasets[sym] = load_candles(path)
        df_1h = _load_1h_for_symbol(sym)
        if df_1h is not None:
            datasets_1h[sym] = df_1h

    header = f"  {'Strategy':<55s} {'Return':>8s} {'WR':>6s} {'PF':>7s} {'Sharpe':>7s} {'MaxDD':>7s} {'Trades':>7s}"
    sep = "-" * 105

    def _run_sweep_rule(rule: TradingRule, cfg: BacktestConfig) -> BacktestResult:
        engine = BacktestEngine(cfg)
        combined = BacktestResult(config=cfg)
        for sym, df in datasets.items():
            import copy
            r_copy = copy.deepcopy(rule)
            _set_1h_on_rule(r_copy, datasets_1h.get(sym))
            r = engine.run(df, r_copy, symbol=sym)
            combined.trades.extend(r.trades)
            combined.equity_curve.extend(r.equity_curve)
        combined.equity_curve.sort(key=lambda x: x[0])
        return combined

    def _print_result(label: str, s: dict) -> None:
        print(
            f"  {label:<55s} {s['total_return_pct']:>+7.2f}% "
            f"{s['win_rate']:>5.1f}% {s['profit_factor']:>6.3f} "
            f"{s['sharpe_ratio']:>6.3f} {s['max_drawdown_pct']:>6.2f}% "
            f"{s['total_trades']:>6d}"
        )

    # RSI Divergence dedicated sweep
    if strategy == "rsi_div":
        print("\n" + "=" * 105)
        print("  PARAMETER SWEEP -- RSI Divergence")
        print("=" * 105)
        print(header)
        print(sep)

        sweep_results: list[tuple[str, dict]] = []
        for swing_w in (3, 4, 5, 6, 7):
            for rsi_p in (10, 12, 14, 18):
                for rsi_le in (55, 60, 65):
                    for rsi_se in (35, 40, 45):
                        for sl in (0.5, 1.0, 1.5):
                            for tp in (1.0, 1.5, 2.5):
                                sweep_cfg = BacktestConfig(
                                    initial_balance=config.initial_balance,
                                    fee_rate=config.fee_rate,
                                    max_trade_pct=config.max_trade_pct,
                                    stop_loss_pct=sl,
                                    take_profit_pct=tp,
                                    max_open_positions=config.max_open_positions,
                                    slippage_pct=config.slippage_pct,
                                )
                                rule = RSIDivergenceRule(
                                    rsi_period=rsi_p,
                                    swing_window=swing_w,
                                    rsi_long_exit=rsi_le,
                                    rsi_short_exit=rsi_se,
                                )
                                combined = _run_sweep_rule(rule, sweep_cfg)
                                s = combined.summary()
                                label = (
                                    f"RSIDiv(sw={swing_w},rsi={rsi_p},"
                                    f"le={rsi_le},se={rsi_se},sl={sl},tp={tp})"
                                )
                                _print_result(label, s)
                                sweep_results.append((label, s))

        # Print top 10 by total return
        print(sep)
        print("  TOP 10 BY RETURN")
        print(sep)
        sweep_results.sort(key=lambda x: x[1]["total_return_pct"], reverse=True)
        for label, s in sweep_results[:10]:
            _print_result(label, s)
        print("=" * 105)
        return

    # If strategy is 'combined', sweep threshold values
    if strategy == "combined":
        print("\n" + "=" * 105)
        print("  PARAMETER SWEEP -- Combined Strategy")
        print("=" * 105)
        print(header)
        print(sep)
        for threshold in (2, 3, 4):
            rule = CombinedRule(entry_threshold=threshold)
            for sl in (0.8, 1.0, 1.5):
                for tp in (1.0, 1.5, 2.0):
                    sweep_cfg = BacktestConfig(
                        initial_balance=config.initial_balance,
                        fee_rate=config.fee_rate,
                        max_trade_pct=config.max_trade_pct,
                        stop_loss_pct=sl,
                        take_profit_pct=tp,
                        max_open_positions=config.max_open_positions,
                        slippage_pct=config.slippage_pct,
                    )
                    combined = _run_sweep_rule(CombinedRule(entry_threshold=threshold), sweep_cfg)
                    s = combined.summary()
                    label = f"Combined(thresh={threshold},sl={sl},tp={tp})"
                    _print_result(label, s)
        print("=" * 105)
        return

    # Full sweep: all strategies
    print("\n" + "=" * 105)
    print("  PARAMETER SWEEP -- All Strategies")
    print("=" * 105)
    print(header)
    print(sep)

    # 1. Hyperliquid Mean Reversion sweep
    for rsi_long in (22, 25, 28):
        for sl in (0.8, 1.0, 1.5):
            for tp in (1.0, 1.5, 2.0):
                sweep_cfg = BacktestConfig(
                    initial_balance=config.initial_balance,
                    fee_rate=config.fee_rate,
                    max_trade_pct=config.max_trade_pct,
                    stop_loss_pct=sl,
                    take_profit_pct=tp,
                    max_open_positions=config.max_open_positions,
                    slippage_pct=config.slippage_pct,
                    trailing_breakeven_pct=config.trailing_breakeven_pct,
                    trailing_start_pct=config.trailing_start_pct,
                    trailing_distance_pct=config.trailing_distance_pct,
                    trailing_tight_pct=config.trailing_tight_pct,
                    trailing_tight_distance_pct=config.trailing_tight_distance_pct,
                )
                rule = HyperliquidMeanRevRule(
                    rsi_long_entry=rsi_long,
                    rsi_short_entry=100 - rsi_long,
                )
                combined = _run_sweep_rule(rule, sweep_cfg)
                s = combined.summary()
                label = f"HLMeanRev(rsi_l<{rsi_long},rsi_s>{100-rsi_long},sl={sl},tp={tp})"
                _print_result(label, s)

    print(sep)
    print("  SCALP VARIANTS")
    print(sep)

    # 2. Scalp sweep
    for rsi_l in (30, 35, 40):
        rsi_s = 100 - rsi_l
        for sl in (0.3, 0.5, 0.8):
            for tp in (0.3, 0.5, 0.8):
                sweep_cfg = BacktestConfig(
                    initial_balance=config.initial_balance,
                    fee_rate=config.fee_rate,
                    max_trade_pct=config.max_trade_pct,
                    stop_loss_pct=sl,
                    take_profit_pct=tp,
                    max_open_positions=config.max_open_positions,
                    slippage_pct=config.slippage_pct,
                )
                rule = ScalpMeanRevRule(rsi_long_entry=rsi_l, rsi_short_entry=rsi_s)
                combined = _run_sweep_rule(rule, sweep_cfg)
                s = combined.summary()
                label = f"Scalp(rsi_l<{rsi_l},rsi_s>{rsi_s},sl={sl},tp={tp})"
                _print_result(label, s)

    print(sep)
    print("  NEW STRATEGIES (default params)")
    print(sep)

    # 3. Each new strategy with default params + SL/TP sweep
    new_strats: list[tuple[str, type]] = [
        ("ema_adx", EMACrossoverADXRule),
        ("bb_squeeze", BBSqueezeRule),
        ("macd", MACDMomentumRule),
        ("donchian", DonchianBreakoutRule),
        ("keltner", KeltnerBreakoutRule),
        ("vwap_rev", VWAPReversionRule),
        ("rsi_div", RSIDivergenceRule),
        ("stoch", StochasticCrossRule),
    ]
    for strat_name, strat_cls in new_strats:
        for sl in (0.8, 1.0, 1.5):
            for tp in (1.0, 1.5, 2.0):
                sweep_cfg = BacktestConfig(
                    initial_balance=config.initial_balance,
                    fee_rate=config.fee_rate,
                    max_trade_pct=config.max_trade_pct,
                    stop_loss_pct=sl,
                    take_profit_pct=tp,
                    max_open_positions=config.max_open_positions,
                    slippage_pct=config.slippage_pct,
                )
                rule = strat_cls()
                combined = _run_sweep_rule(rule, sweep_cfg)
                s = combined.summary()
                label = f"{rule.name}(sl={sl},tp={tp})"
                _print_result(label, s)

    print(sep)
    print("  COMBINED STRATEGY")
    print(sep)

    # 4. Combined sweep
    for threshold in (2, 3, 4):
        for sl in (0.8, 1.0, 1.5):
            for tp in (1.0, 1.5, 2.0):
                sweep_cfg = BacktestConfig(
                    initial_balance=config.initial_balance,
                    fee_rate=config.fee_rate,
                    max_trade_pct=config.max_trade_pct,
                    stop_loss_pct=sl,
                    take_profit_pct=tp,
                    max_open_positions=config.max_open_positions,
                    slippage_pct=config.slippage_pct,
                )
                rule = CombinedRule(entry_threshold=threshold)
                combined = _run_sweep_rule(rule, sweep_cfg)
                s = combined.summary()
                label = f"Combined(thresh={threshold},sl={sl},tp={tp})"
                _print_result(label, s)

    print("=" * 105)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run backtests on historical data")
    parser.add_argument("--symbols", nargs="+", default=None, help="Symbols to test (default: all downloaded)")
    parser.add_argument("--interval", default="15m", help="Candle interval (must match downloaded data)")
    parser.add_argument(
        "--strategy",
        choices=STRATEGY_CHOICES,
        default="hyperliquid_mr",
    )
    parser.add_argument("--balance", type=float, default=100.0, help="Initial USDC balance")
    parser.add_argument("--stop-loss", type=float, default=1.0, help="Stop loss %%")
    parser.add_argument("--take-profit", type=float, default=1.5, help="Take profit %%")
    parser.add_argument("--max-trade-pct", type=float, default=10.0, help="Max trade size %%")

    # Hyperliquid MR / Scalp params
    parser.add_argument("--rsi-long-entry", type=float, default=25.0, help="RSI LONG entry threshold")
    parser.add_argument("--rsi-short-entry", type=float, default=75.0, help="RSI SHORT entry threshold")

    # Legacy mean reversion params
    parser.add_argument("--rsi-entry", type=float, default=28.0, help="RSI entry threshold (legacy)")
    parser.add_argument("--rsi-exit", type=float, default=55.0, help="RSI exit threshold (legacy)")
    parser.add_argument("--rsi-period", type=int, default=14, help="RSI period")

    # Grid params
    parser.add_argument("--spacing", type=float, default=0.5, help="Grid spacing %%")
    parser.add_argument("--range", type=float, default=3.0, dest="grid_range", help="Grid range %%")

    # RSI Divergence params
    parser.add_argument("--swing-window", type=int, default=5, help="RSI Div swing detection window")
    parser.add_argument("--rsi-long-exit", type=float, default=60.0, help="RSI Div LONG exit threshold")
    parser.add_argument("--rsi-short-exit", type=float, default=40.0, help="RSI Div SHORT exit threshold")

    # Combined params
    parser.add_argument("--combined-threshold", type=int, default=2, help="Min votes for combined entry (default: 2)")

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
        run_sweep(args.symbols, args.interval, config, strategy=args.strategy)
        return

    rule = _make_rule(args.strategy, args)
    run_single(args.symbols, args.interval, rule, config)


if __name__ == "__main__":
    main()
