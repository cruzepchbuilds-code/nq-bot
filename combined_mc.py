"""
Combined NQ + ES Monte Carlo Stress Test
Simulates trading both instruments on the same $50k account.
Eval mode: 1 contract each, no pyramiding.

Usage: python3 combined_mc.py data/nq_1min.csv data/es_1min.csv
"""
import sys
import random
import math
from collections import defaultdict
from datetime import time

import config          # NQ config (default)
import es_config       # ES config
from backtest import Backtester, load_csv

# ── Constants ─────────────────────────────────────────────────────────────────
N_SIMULATIONS       = 10_000
EVAL_TRADES_PER_SIM = 45      # ~30 NQ + ~15 ES per sim period
APEX_TARGET         = 3_000.0
SEED                = 42
DD_TIERS            = [2_500.0, 3_000.0, 3_500.0]


# ── Collect OOS trades from NQ (2025-2026, eval mode) ────────────────────────
def collect_nq_trades(nq_bars):
    print("  NQ (2025-2026, eval mode)...")
    orig_eval = config.EVAL_MODE
    config.EVAL_MODE = True
    oos = [b for b in nq_bars if b["timestamp"].year >= 2025]
    trading_days = len(set(b["timestamp"].date() for b in oos))

    months = defaultdict(list)
    for b in oos:
        months[b["timestamp"].strftime("%Y-%m")].append(b)

    trades = []
    for ym in sorted(months):
        bt = Backtester()
        bt.run(months[ym], silent=True)
        for t in bt.bank.trade_log:
            trades.append(t["pnl"])

    config.EVAL_MODE = orig_eval
    dpt = trading_days / len(trades) if trades else 5.0
    return trades, dpt


# ── Collect OOS trades from ES (2024-2026, eval mode via es_config) ──────────
def collect_es_trades(es_bars):
    """Monkey-patch config with ES values, collect 2024-2026 eval trades, restore."""
    print("  ES (2024-2026, eval mode)...")
    import backtest as bt_mod

    # Save original config values
    orig = {}
    for attr in dir(es_config):
        if not attr.startswith("_"):
            orig[attr] = getattr(config, attr, None)
            setattr(config, attr, getattr(es_config, attr))
    config.EVAL_MODE = True  # force eval mode

    # Patch backtest module-level constants
    orig_or_end   = bt_mod.OR_END
    orig_le       = bt_mod.LAST_ENTRY
    orig_fl       = bt_mod.FLATTEN
    orig_slip     = bt_mod.SLIP
    bt_mod.OR_END    = time(9, 30 + config.OPENING_RANGE_MINUTES)
    bt_mod.LAST_ENTRY = time(*map(int, config.LAST_ENTRY_TIME.split(":")))
    bt_mod.FLATTEN    = time(*map(int, config.FLATTEN_TIME.split(":")))
    bt_mod.SLIP       = config.SLIPPAGE_TICKS * config.TICK_SIZE

    try:
        oos = [b for b in es_bars if b["timestamp"].year >= 2024]
        trading_days = len(set(b["timestamp"].date() for b in oos))

        months = defaultdict(list)
        for b in oos:
            months[b["timestamp"].strftime("%Y-%m")].append(b)

        trades = []
        for ym in sorted(months):
            bt = Backtester()
            bt.run(months[ym], silent=True)
            for t in bt.bank.trade_log:
                trades.append(t["pnl"])
    finally:
        # Restore NQ config
        for attr, val in orig.items():
            if val is not None:
                setattr(config, attr, val)
        bt_mod.OR_END     = orig_or_end
        bt_mod.LAST_ENTRY = orig_le
        bt_mod.FLATTEN    = orig_fl
        bt_mod.SLIP       = orig_slip

    dpt = trading_days / len(trades) if trades else 5.0
    return trades, dpt


# ── Pool stats ────────────────────────────────────────────────────────────────
def pool_stats(trades):
    wins   = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    gw = sum(wins)
    gl = abs(sum(losses))
    return {
        "n": len(trades), "wr": len(wins)/len(trades),
        "pf": gw/gl if gl else float("inf"),
        "aw": gw/len(wins)   if wins   else 0,
        "al": gl/len(losses) if losses else 0,
    }


# ── Simulate one eval ────────────────────────────────────────────────────────
def simulate_eval(trade_seq, dd):
    balance = 50_000.0
    peak    = 50_000.0
    floor   = 50_000.0 - dd
    for i, pnl in enumerate(trade_seq):
        balance += pnl
        if balance > peak:
            peak  = balance
            floor = max(floor, peak - dd)
        if balance >= 50_000.0 + APEX_TARGET:
            return True, i + 1
        if balance <= floor:
            return False, i + 1
    return False, len(trade_seq)


# ── Run MC on combined pool ───────────────────────────────────────────────────
def run_mc(combined_pool, days_per_trade, dd):
    rng = random.Random(SEED)
    t4  = 4 * 5 / days_per_trade
    t6  = 6 * 5 / days_per_trade
    n_pass = 0
    ttp = []
    p4wk = p6wk = 0

    for _ in range(N_SIMULATIONS):
        sample = rng.choices(combined_pool, k=EVAL_TRADES_PER_SIM)
        passed, n_trades = simulate_eval(sample, dd)
        if passed:
            n_pass += 1
            ttp.append(n_trades)
            if n_trades <= t4:
                p4wk += 1
            if n_trades <= t6:
                p6wk += 1

    pass_prob = n_pass / N_SIMULATIONS
    avg_ttp   = sum(ttp) / len(ttp) if ttp else 0
    avg_weeks = avg_ttp * days_per_trade / 5
    return {
        "pass_prob":  pass_prob,
        "avg_weeks":  avg_weeks,
        "avg_ttp":    avg_ttp,
        "pct4wk":     p4wk / n_pass if n_pass else 0,
        "pct6wk":     p6wk / n_pass if n_pass else 0,
        "n_pass":     n_pass,
        "trades_per_week": 5 / days_per_trade,
    }


def pct(lst, p):
    s = sorted(lst)
    idx = max(0, min(int(len(s) * p / 100), len(s) - 1))
    return s[idx]


def verdict(p):
    if p >= 0.85: return "STRONG -- buy the eval"
    if p >= 0.80: return "SOLID -- excellent odds"
    if p >= 0.65: return "MODERATE -- reasonable odds"
    if p >= 0.50: return "BORDERLINE"
    return "POOR"


def main():
    nq_path = sys.argv[1] if len(sys.argv) > 1 else "data/nq_1min.csv"
    es_path = sys.argv[2] if len(sys.argv) > 2 else "data/es_1min.csv"

    print("Loading bars...")
    nq_bars = load_csv(nq_path)
    es_bars = load_csv(es_path)
    print(f"  NQ: {len(nq_bars):,} bars | ES: {len(es_bars):,} bars")

    print("\nCollecting OOS trade pools...")
    nq_trades, nq_dpt = collect_nq_trades(nq_bars)
    es_trades, es_dpt = collect_es_trades(es_bars)

    nq_ps = pool_stats(nq_trades)
    es_ps = pool_stats(es_trades)

    # Combined pool and blended days-per-trade
    combined = nq_trades + es_trades
    # Total OOS trading days (2025-2026 is ~451 days)
    # NQ: 451 days / 92 trades = 4.9 dpt → 1.02 t/wk
    # ES 2024-2026: ~630 days / 163 trades = 3.9 dpt → 1.28 t/wk
    # Combined: ~900 OOS person-days / 255 total trades → 3.5 dpt → 1.43 t/wk
    combined_total_oos_days = 451 + 630   # NQ 2025-26 + ES 2024-26 (approx)
    combined_dpt = combined_total_oos_days / len(combined) if combined else 5.0
    combined_ps  = pool_stats(combined)
    combined_tpw = 5 / combined_dpt

    print(f"\n  NQ pool: {nq_ps['n']}t | WR {nq_ps['wr']:.1%} | PF {nq_ps['pf']:.2f} | "
          f"avg win ${nq_ps['aw']:,.0f} | avg loss ${nq_ps['al']:,.0f} | {5/nq_dpt:.2f} t/wk")
    print(f"  ES pool: {es_ps['n']}t | WR {es_ps['wr']:.1%} | PF {es_ps['pf']:.2f} | "
          f"avg win ${es_ps['aw']:,.0f} | avg loss ${es_ps['al']:,.0f} | {5/es_dpt:.2f} t/wk")
    print(f"  Combined: {combined_ps['n']}t | WR {combined_ps['wr']:.1%} | "
          f"PF {combined_ps['pf']:.2f} | {combined_tpw:.2f} t/wk")

    print(f"\nRunning {N_SIMULATIONS:,} MC simulations across {len(DD_TIERS)} DD tiers...")

    sep = "=" * 80
    print(f"\n{sep}")
    print(f"  COMBINED NQ+ES EVAL STRESS TEST  ({N_SIMULATIONS:,} sims | {EVAL_TRADES_PER_SIM} trades/sim)")
    print(f"  Target: +${APEX_TARGET:,.0f}  |  NQ eval mode (1c) + ES eval mode (1c)")
    print(sep)
    print(f"  {'Trailing DD':<14} {'Pass%':>6} {'Avg Wk':>7} {'<4wk':>6} {'<6wk':>6} {'t/wk':>5}  Verdict")
    print(f"  {'-'*72}")

    tier_data = {}
    for dd in DD_TIERS:
        mc = run_mc(combined, combined_dpt, dd)
        tier_data[dd] = mc
        breach = math.ceil(dd / combined_ps["al"]) if combined_ps["al"] else 0
        print(f"  ${dd:>7,.0f} DD     {mc['pass_prob']:>5.1%} {mc['avg_weeks']:>7.1f} "
              f"{mc['pct4wk']:>5.1%} {mc['pct6wk']:>5.1%} {mc['trades_per_week']:>5.2f}  "
              f"{verdict(mc['pass_prob'])} ({breach} losses to breach)")
    print(sep)

    # Best DD tier
    best_dd = max(DD_TIERS, key=lambda d: tier_data[d]["pass_prob"])
    best_mc = tier_data[best_dd]
    print(f"\n  Best: ${best_dd:,.0f} DD plan -> {best_mc['pass_prob']:.1%} pass rate "
          f"| {best_mc['avg_weeks']:.1f}wk avg | {best_mc['trades_per_week']:.2f} t/wk")

    # Compare to NQ-only baseline ($3,500 DD)
    nq_only_mc = run_mc(nq_trades, nq_dpt, 3_500.0)
    print(f"\n  Comparison:")
    print(f"    NQ-only  ($3,500 DD): {nq_only_mc['pass_prob']:.1%} pass | "
          f"{nq_only_mc['avg_weeks']:.1f}wk avg | {5/nq_dpt:.2f} t/wk")
    print(f"    NQ+ES    (${best_dd:,.0f} DD): {best_mc['pass_prob']:.1%} pass | "
          f"{best_mc['avg_weeks']:.1f}wk avg | {combined_tpw:.2f} t/wk")
    delta_pass = (best_mc['pass_prob'] - nq_only_mc['pass_prob']) * 100
    delta_wk   = best_mc['avg_weeks'] - nq_only_mc['avg_weeks']
    print(f"    Delta:   {delta_pass:+.1f}pp pass rate | {delta_wk:+.1f} weeks")

    # ── Save results ─────────────────────────────────────────────────────────
    out = "combined_mc_results.md"
    with open(out, "w") as f:
        f.write("# Combined NQ + ES Monte Carlo Results\n\n")
        f.write("## Trade Pools (Eval Mode)\n\n")
        f.write(f"| Instrument | Trades | WR | PF | Avg Win | Avg Loss | t/wk |\n")
        f.write(f"|------------|--------|----|----|---------|----------|------|\n")
        f.write(f"| NQ (2025-2026) | {nq_ps['n']} | {nq_ps['wr']:.1%} | {nq_ps['pf']:.2f} | "
                f"${nq_ps['aw']:,.0f} | ${nq_ps['al']:,.0f} | {5/nq_dpt:.2f} |\n")
        f.write(f"| ES (2024-2026) | {es_ps['n']} | {es_ps['wr']:.1%} | {es_ps['pf']:.2f} | "
                f"${es_ps['aw']:,.0f} | ${es_ps['al']:,.0f} | {5/es_dpt:.2f} |\n")
        f.write(f"| **Combined** | **{combined_ps['n']}** | **{combined_ps['wr']:.1%}** | "
                f"**{combined_ps['pf']:.2f}** | **${combined_ps['aw']:,.0f}** | "
                f"**${combined_ps['al']:,.0f}** | **{combined_tpw:.2f}** |\n\n")
        f.write("## DD Tier Results\n\n")
        f.write("| Trailing DD | Pass% | Avg Weeks | <4wk | <6wk | t/wk | Verdict |\n")
        f.write("|-------------|-------|-----------|------|------|------|--------|\n")
        for dd, mc in tier_data.items():
            f.write(f"| ${dd:,.0f} | **{mc['pass_prob']:.1%}** | {mc['avg_weeks']:.1f} | "
                    f"{mc['pct4wk']:.1%} | {mc['pct6wk']:.1%} | {mc['trades_per_week']:.2f} | "
                    f"{verdict(mc['pass_prob'])} |\n")
        f.write("\n## Comparison: NQ-Only vs NQ+ES\n\n")
        f.write(f"| Portfolio | DD Plan | Pass% | Avg Weeks | t/wk |\n")
        f.write(f"|-----------|---------|-------|-----------|------|\n")
        f.write(f"| NQ only   | $3,500  | {nq_only_mc['pass_prob']:.1%} | "
                f"{nq_only_mc['avg_weeks']:.1f} | {5/nq_dpt:.2f} |\n")
        f.write(f"| NQ + ES   | ${best_dd:,.0f}  | **{best_mc['pass_prob']:.1%}** | "
                f"**{best_mc['avg_weeks']:.1f}** | **{combined_tpw:.2f}** |\n\n")
        f.write(f"Delta: {delta_pass:+.1f}pp pass rate, {delta_wk:+.1f} weeks avg  \n\n")
        f.write(f"_10,000 simulations | eval mode | $3,000 profit target_\n")

    print(f"\n  Saved: {out}")


if __name__ == "__main__":
    main()
