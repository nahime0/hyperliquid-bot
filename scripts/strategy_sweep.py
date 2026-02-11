"""Strategy sweep — vectorized, tests 7 strategies × multiple params on 50 pairs.

PYTHONPATH=. .venv/bin/python scripts/strategy_sweep.py
"""
from __future__ import annotations
import time, math, csv
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
import pandas as pd
import ta
from utils.logger import setup_logging, get_logger

logger = get_logger(__name__)
HIST = Path(__file__).resolve().parent.parent / "data" / "history"
OUT = Path(__file__).resolve().parent.parent / "data" / "backtest_results"
BAL0 = 112.0; FEE = 0.00075; SLIP = 0.0002; MAX_POS = 5; COOL = 2

# ── Prepare indicators (vectorized) ──────────────────────────

def prep_15m(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy(); c = d["close"]; v = d["volume"]
    d["rsi"] = ta.momentum.RSIIndicator(c, 14).rsi()
    bb = ta.volatility.BollingerBands(c, 20, 2)
    d["bb_lo"] = bb.bollinger_lband(); d["bb_hi"] = bb.bollinger_hband()
    d["bb_mid"] = bb.bollinger_mavg()
    d["bb_w"] = (d["bb_hi"] - d["bb_lo"]) / d["bb_mid"]
    d["bb_wpct"] = d["bb_w"].rolling(120).rank(pct=True)
    d["ema9"] = ta.trend.EMAIndicator(c, 9).ema_indicator()
    d["ema20"] = ta.trend.EMAIndicator(c, 20).ema_indicator()
    d["ema50"] = ta.trend.EMAIndicator(c, 50).ema_indicator()
    d["vsma"] = v.rolling(20).mean(); d["vr"] = v / d["vsma"]
    d["atr"] = ta.volatility.AverageTrueRange(d["high"], d["low"], c, 14).average_true_range()
    d["pctc"] = c.pct_change()
    m = ta.trend.MACD(c, 26, 12, 9)
    d["macd_h"] = m.macd_diff()
    return d

def prep_1h(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy(); c = d["close"]
    d["e50"] = ta.trend.EMAIndicator(c, 50).ema_indicator()
    d["e200"] = ta.trend.EMAIndicator(c, 200).ema_indicator()
    d["rsi1h"] = ta.momentum.RSIIndicator(c, 14).rsi()
    d["slope"] = (d["e50"] - d["e50"].shift(5)) / c * 100
    return d

# ── 1h trend lookup (vectorized per-symbol) ──────────────────

def build_1h_trend_array(df_1h: pd.DataFrame, timestamps_15m: np.ndarray) -> np.ndarray:
    """Return array of trend codes: 1=BULLISH, -1=BEARISH, 0=NEUTRAL for each 15m ts."""
    ts_1h = df_1h["open_time"].values
    e50 = df_1h["e50"].values; e200 = df_1h["e200"].values
    price = df_1h["close"].values; slope = df_1h["slope"].values
    rsi1h = df_1h["rsi1h"].values

    idx = np.searchsorted(ts_1h, timestamps_15m, side="right") - 1
    idx = np.clip(idx, 0, len(ts_1h) - 1)

    e50_v = e50[idx]; e200_v = e200[idx]; p_v = price[idx]; sl_v = slope[idx]
    rsi1h_v = rsi1h[idx]

    bull = (e50_v > e200_v) & (p_v > e50_v) & (sl_v > 0) & ~np.isnan(e50_v) & ~np.isnan(e200_v)
    trend = np.zeros(len(timestamps_15m), dtype=np.int8)
    trend[bull] = 1
    return trend, rsi1h_v

# ── Generic backtest engine (numpy-based) ─────────────────────

def run_bt(
    sym_data: dict[str, dict[str, np.ndarray]],
    entry_mask_fn,  # fn(arrays, trend, rsi1h) -> bool array
    exit_check_fn,  # fn(arrays, i) -> bool (for signal exit on position)
    sl_pct: float, tp_pct: float, size_pct: float,
    trail: bool, tr_start: float, tr_dist: float, time_stop: int,
) -> dict:
    """Run backtest across all symbols with shared balance."""
    # Build unified timeline
    all_entries = []  # (timestamp_ns, sym_idx, candle_idx_in_sym)
    sym_list = list(sym_data.keys())
    sym_arrays = {}  # sym -> dict of numpy arrays

    for si, sym in enumerate(sym_list):
        d = sym_data[sym]
        n = len(d["close"])
        ts = d["ts"]
        for j in range(n):
            all_entries.append((ts[j], si, j))
        sym_arrays[si] = d

    # Sort by timestamp
    all_entries.sort(key=lambda x: x[0])

    bal = BAL0
    positions = []  # (sym_idx, entry_price, qty, sl, tp, entry_step, max_price)
    trades = 0; wins = 0; fees_total = 0.0
    cooldowns = {}  # sym_idx -> step when cooldown expires
    exit_reasons = {}
    eq_curve = []
    peak = BAL0; max_dd = 0.0
    step = 0

    for ts_val, si, ci in all_entries:
        d = sym_arrays[si]
        price = d["close"][ci]
        high = d["high"][ci]
        low = d["low"][ci]

        # ── Check positions for this symbol ──
        new_positions = []
        for pos in positions:
            p_si, ep, qty, sl, tp, e_step, mx = pos
            if p_si != si:
                new_positions.append(pos)
                continue

            mx = max(mx, high)
            ex_price = None; reason = ""

            # Trailing
            if trail and mx > ep:
                pnl_pct = (mx - ep) / ep * 100
                if pnl_pct >= tr_start:
                    tsl = mx * (1 - tr_dist / 100)
                    if low <= tsl:
                        ex_price = tsl; reason = "trail"

            if ex_price is None and low <= sl:
                ex_price = sl; reason = "sl"
            if ex_price is None and high >= tp:
                ex_price = tp; reason = "tp"
            if ex_price is None and time_stop > 0 and (step - e_step) >= time_stop:
                cpnl = (price - ep) / ep * 100
                if cpnl < 0.5:
                    ex_price = price; reason = "time"

            # Signal exit
            if ex_price is None and exit_check_fn(d, ci):
                ex_price = price; reason = "signal"

            if ex_price is not None:
                ex_price *= (1 - SLIP)
                fee = qty * ep * FEE + qty * ex_price * FEE
                pnl = qty * (ex_price - ep) - fee
                bal += qty * ex_price * (1 - FEE)
                trades += 1; fees_total += fee
                if pnl > 0: wins += 1
                exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
                if pnl <= 0: cooldowns[si] = step + COOL
            else:
                new_positions.append((p_si, ep, qty, sl, tp, e_step, mx))

        positions = new_positions

        # ── Entry check ──
        if len(positions) < MAX_POS:
            held = {p[0] for p in positions}
            if si not in held and cooldowns.get(si, 0) <= step:
                if d["entry_mask"][ci]:
                    alloc = bal * (size_pct / 100)
                    ep = price * (1 + SLIP)
                    qty = alloc / ep
                    cost = qty * ep * (1 + FEE)
                    if cost <= bal and alloc >= 5.5:
                        bal -= cost
                        positions.append((
                            si, ep, qty,
                            ep * (1 - sl_pct / 100),
                            ep * (1 + tp_pct / 100),
                            step, ep
                        ))

        # Equity every ~1 day
        if step % 500 == 0:
            mark = sum(p[2] * price for p in positions if p[0] == si)
            other = sum(p[2] * p[1] for p in positions if p[0] != si)
            eq = bal + mark + other
            eq_curve.append(eq)
            if eq > peak: peak = eq
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            if dd > max_dd: max_dd = dd

        step += 1

    # Close remaining
    for pos in positions:
        si, ep, qty, sl, tp, es, mx = pos
        d = sym_arrays[si]
        lp = d["close"][-1]
        fee = qty * ep * FEE + qty * lp * FEE
        pnl = qty * (lp - ep) - fee
        bal += qty * lp * (1 - FEE)
        trades += 1; fees_total += fee
        if pnl > 0: wins += 1

    return {
        "bal": bal, "trades": trades, "wins": wins, "fees": fees_total,
        "dd": max_dd, "exits": exit_reasons, "eq": eq_curve,
    }

# ── Strategy signal generators (vectorized) ──────────────────

def signals_momentum(d: dict, trend: np.ndarray, rsi1h: np.ndarray) -> tuple[np.ndarray, callable]:
    c, rsi, e20, e50, vr, mh = d["close"], d["rsi"], d["ema20"], d["ema50"], d["vr"], d["macd_h"]
    entry = (c > e20) & (e20 > e50) & (rsi > 50) & (rsi < 70) & (vr >= 1.5) & (mh > 0)
    entry &= ~np.isnan(rsi) & ~np.isnan(e20) & ~np.isnan(e50)
    def exit_fn(d, i):
        return (not np.isnan(d["rsi"][i]) and not np.isnan(d["ema20"][i]) and
                (d["close"][i] < d["ema20"][i] or d["rsi"][i] > 80))
    return entry, exit_fn

def signals_mom_trend(d: dict, trend: np.ndarray, rsi1h: np.ndarray) -> tuple[np.ndarray, callable]:
    entry, exit_fn = signals_momentum(d, trend, rsi1h)
    entry = entry & (trend == 1)
    return entry, exit_fn

def signals_squeeze(d: dict, trend: np.ndarray, rsi1h: np.ndarray) -> tuple[np.ndarray, callable]:
    c, bh, bwp, vr, rsi = d["close"], d["bb_hi"], d["bb_wpct"], d["vr"], d["rsi"]
    entry = (bwp < 0.3) & (c > bh) & (vr >= 1.3) & (rsi > 50)
    entry &= ~np.isnan(bwp) & ~np.isnan(bh) & ~np.isnan(rsi)
    def exit_fn(d, i):
        return not np.isnan(d["bb_mid"][i]) and d["close"][i] < d["bb_mid"][i]
    return entry, exit_fn

def signals_pullback(d: dict, trend: np.ndarray, rsi1h: np.ndarray) -> tuple[np.ndarray, callable]:
    c, e20, e50, rsi, vr = d["close"], d["ema20"], d["ema50"], d["rsi"], d["vr"]
    dist = np.abs(c - e20) / np.where(e20 > 0, e20, 1)
    entry = (dist < 0.005) & (c > e50) & (rsi > 35) & (rsi < 55) & (vr >= 0.8) & (trend == 1)
    entry &= ~np.isnan(rsi) & ~np.isnan(e20) & ~np.isnan(e50)
    def exit_fn(d, i):
        return not np.isnan(d["ema50"][i]) and d["close"][i] < d["ema50"][i]
    return entry, exit_fn

def signals_strict_mr(d: dict, trend: np.ndarray, rsi1h: np.ndarray) -> tuple[np.ndarray, callable]:
    c, rsi, bl, vr = d["close"], d["rsi"], d["bb_lo"], d["vr"]
    entry = (rsi < 25) & (c < bl) & (vr >= 1.2) & (trend == 1)
    entry &= ~np.isnan(rsi) & ~np.isnan(bl)
    rsi1h_ok = np.isnan(rsi1h) | (rsi1h < 60)
    entry &= rsi1h_ok
    def exit_fn(d, i):
        return not np.isnan(d["rsi"][i]) and d["rsi"][i] > 60
    return entry, exit_fn

def signals_volspike(d: dict, trend: np.ndarray, rsi1h: np.ndarray) -> tuple[np.ndarray, callable]:
    vr, pc, rsi = d["vr"], d["pctc"], d["rsi"]
    entry = (vr >= 3.0) & (pc > 0.005) & (rsi < 70) & (rsi > 40)
    entry &= ~np.isnan(vr) & ~np.isnan(pc) & ~np.isnan(rsi)
    def exit_fn(d, i):
        return not np.isnan(d["vr"][i]) and d["vr"][i] < 0.5
    return entry, exit_fn

def signals_ema_cross(d: dict, trend: np.ndarray, rsi1h: np.ndarray) -> tuple[np.ndarray, callable]:
    e9, e20, rsi, mh = d["ema9"], d["ema20"], d["rsi"], d["macd_h"]
    diff = (e9 - e20) / np.where(e20 > 0, e20, 1) * 100
    entry = (diff > 0) & (diff < 0.15) & (rsi > 45) & (mh > 0) & (trend == 1)
    entry &= ~np.isnan(e9) & ~np.isnan(e20) & ~np.isnan(rsi)
    def exit_fn(d, i):
        return (not np.isnan(d["ema9"][i]) and not np.isnan(d["ema20"][i]) and
                d["ema9"][i] < d["ema20"][i])
    return entry, exit_fn

# ── Load + prepare ────────────────────────────────────────────

def load_all() -> dict[str, dict[str, np.ndarray]]:
    result = {}
    if not HIST.exists():
        return result
    for f in sorted(HIST.glob("*USDC_15m.csv")):
        sym = f.stem.replace("_15m", "")
        p1h = HIST / f"{sym}_1h.csv"
        if not p1h.exists(): continue

        df15 = pd.read_csv(f)
        df15["open_time"] = pd.to_datetime(df15["open_time"], unit="ms", utc=True)
        for c in ("open","high","low","close","volume","quote_volume"): df15[c] = df15[c].astype(float)
        df15 = prep_15m(df15)

        df1h = pd.read_csv(p1h)
        df1h["open_time"] = pd.to_datetime(df1h["open_time"], unit="ms", utc=True)
        for c in ("open","high","low","close","volume","quote_volume"): df1h[c] = df1h[c].astype(float)
        df1h = prep_1h(df1h)

        # Build 1h trend array aligned to 15m timestamps
        ts15 = df15["open_time"].values
        trend, rsi1h = build_1h_trend_array(df1h, ts15)

        # Convert to numpy dict
        arrays = {
            "ts": ts15, "close": df15["close"].values, "high": df15["high"].values,
            "low": df15["low"].values, "rsi": df15["rsi"].values,
            "bb_lo": df15["bb_lo"].values, "bb_hi": df15["bb_hi"].values,
            "bb_mid": df15["bb_mid"].values, "bb_wpct": df15["bb_wpct"].values,
            "ema9": df15["ema9"].values, "ema20": df15["ema20"].values,
            "ema50": df15["ema50"].values, "vr": df15["vr"].values,
            "pctc": df15["pctc"].values, "macd_h": df15["macd_h"].values,
            "atr": df15["atr"].values, "trend": trend, "rsi1h": rsi1h,
        }
        result[sym] = arrays
    return result

# ── Main ──────────────────────────────────────────────────────

def main():
    setup_logging("INFO")
    logger.info("Loading data...")
    data = load_all()
    logger.info("Loaded %d symbols", len(data))
    if not data: return

    STRATS = [
        # (name, signal_fn, [(sl, tp, size, trail, tstart, tdist, tstop), ...])
        ("Momentum", signals_momentum, [
            (1.5, 3.0, 5, True, 1.0, 0.8, 0),
            (1.0, 2.0, 5, True, 0.8, 0.6, 0),
            (2.0, 4.0, 5, True, 1.5, 1.0, 0),
            (1.5, 3.0, 10, True, 1.0, 0.8, 0),
            (2.0, 5.0, 10, True, 2.0, 1.0, 0),
        ]),
        ("Mom+Trend", signals_mom_trend, [
            (1.5, 3.0, 5, True, 1.0, 0.8, 0),
            (1.0, 2.0, 5, True, 0.8, 0.6, 0),
            (2.0, 5.0, 10, True, 1.5, 1.0, 0),
        ]),
        ("Squeeze", signals_squeeze, [
            (1.5, 3.0, 5, True, 1.0, 0.8, 0),
            (2.0, 4.0, 5, True, 1.5, 1.0, 0),
            (1.0, 2.0, 10, True, 0.8, 0.6, 0),
            (2.0, 5.0, 10, True, 2.0, 1.0, 0),
        ]),
        ("Pullback", signals_pullback, [
            (1.0, 2.0, 5, True, 0.8, 0.6, 0),
            (1.5, 3.0, 5, True, 1.0, 0.8, 0),
            (1.0, 1.5, 10, True, 0.6, 0.5, 0),
            (2.0, 4.0, 10, True, 1.5, 1.0, 0),
        ]),
        ("StrictMR", signals_strict_mr, [
            (1.5, 2.0, 5, True, 0.8, 0.6, 0),
            (2.0, 3.0, 5, True, 1.0, 0.8, 0),
            (1.0, 1.5, 10, True, 0.6, 0.5, 0),
            (2.0, 3.0, 10, True, 1.0, 0.8, 16),
        ]),
        ("VolSpike", signals_volspike, [
            (1.0, 2.0, 5, True, 0.8, 0.6, 0),
            (1.5, 3.0, 5, True, 1.0, 0.8, 0),
            (1.0, 2.0, 10, True, 0.8, 0.6, 0),
            (2.0, 5.0, 10, True, 1.5, 1.0, 0),
        ]),
        ("EMACross", signals_ema_cross, [
            (1.5, 3.0, 5, True, 1.0, 0.8, 0),
            (1.0, 2.0, 5, True, 0.8, 0.6, 0),
            (2.0, 4.0, 10, True, 1.5, 1.0, 0),
            (2.0, 5.0, 10, True, 2.0, 1.0, 0),
        ]),
    ]

    print("\n" + "=" * 115)
    print("  STRATEGY SWEEP — 50 USDC pairs × 6 months × $112")
    print("=" * 115)
    print(f"  {'#':>2} {'Strategy':<11} {'SL':>4} {'TP':>4} {'Sz':>3} {'Return':>8} {'$PnL':>8} "
          f"{'Trades':>6} {'WR':>6} {'MaxDD':>6} {'Fees':>6} {'TopExit':>14}")
    print("-" * 115)

    results = []
    num = 0

    for strat_name, sig_fn, param_list in STRATS:
        # Pre-compute entry masks for this strategy (once per strategy)
        for sym, d in data.items():
            entry_mask, exit_fn = sig_fn(d, d["trend"], d["rsi1h"])
            d["entry_mask"] = entry_mask
            d["_exit_fn"] = exit_fn

        for sl, tp, sz, tr, trs, trd, ts in param_list:
            num += 1
            t0 = time.time()

            # Create a unified exit function wrapper
            def make_exit(sym_data):
                fns = {sym: d["_exit_fn"] for sym, d in sym_data.items()}
                return fns

            exit_fns = make_exit(data)

            # Run the backtest
            r = run_bt(
                data,
                entry_mask_fn=None,  # already set in d["entry_mask"]
                exit_check_fn=lambda d, i: d["_exit_fn"](d, i),
                sl_pct=sl, tp_pct=tp, size_pct=sz,
                trail=tr, tr_start=trs, tr_dist=trd, time_stop=ts,
            )

            elapsed = time.time() - t0
            ret = (r["bal"] / BAL0 - 1) * 100
            pnl = r["bal"] - BAL0
            wr = r["wins"] / r["trades"] * 100 if r["trades"] else 0

            top_ex = max(r["exits"].items(), key=lambda x: x[1]) if r["exits"] else ("-", 0)
            top_pct = top_ex[1] / r["trades"] * 100 if r["trades"] else 0

            tag = "**" if ret > 1 else " +" if ret > 0 else "  "
            params = f"SL={sl} TP={tp} sz={sz}%"

            print(f"{tag}{num:>2} {strat_name:<11} {sl:>4.1f} {tp:>4.1f} {sz:>2}% "
                  f"{ret:>+7.2f}% ${pnl:>+7.2f} {r['trades']:>6} {wr:>5.1f}% "
                  f"{r['dd']:>5.2f}% ${r['fees']:>5.2f} "
                  f"{top_ex[0]:>8}({top_pct:.0f}%)  [{elapsed:.1f}s]")

            results.append((strat_name, params, ret, r["trades"], wr, r["dd"], pnl, r["eq"]))

    print("=" * 115)

    # Top 5
    results.sort(key=lambda x: x[2], reverse=True)
    print("\n  TOP 5:")
    for i, (name, params, ret, trades, wr, dd, pnl, _) in enumerate(results[:5]):
        print(f"    {i+1}. {name} {params} → {ret:+.2f}% (${pnl:+.2f}) [{trades} trades, WR={wr:.1f}%, DD={dd:.1f}%]")

    print("\n  WORST 3:")
    for name, params, ret, trades, wr, dd, pnl, _ in results[-3:]:
        print(f"    {name} {params} → {ret:+.2f}% (${pnl:+.2f}) [{trades} trades]")

    # Save best equity curve
    best = results[0]
    if best[7]:
        OUT.mkdir(parents=True, exist_ok=True)
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(14, 6))
        ax.plot(range(len(best[7])), best[7], lw=0.8, color="#2196F3")
        ax.axhline(BAL0, color="gray", ls="--", lw=0.5)
        ax.set_title(f"Best: {best[0]} ({best[1]}) → {best[2]:+.2f}%")
        ax.set_ylabel("USDC"); ax.grid(True, alpha=0.3)
        fig.savefig(OUT / "sweep_best.png", dpi=150, bbox_inches="tight"); plt.close(fig)
        logger.info("Saved best equity curve")

if __name__ == "__main__":
    main()
