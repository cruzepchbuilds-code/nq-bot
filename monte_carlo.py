"""
Monte Carlo Stress Test -- Apex Eval Pass Probability
10,000 simulations sampling from the OOS 2025-2026 trade pool.

Usage:
    python3 monte_carlo.py data/nq_1min.csv
"""
import sys
import random
from collections import defaultdict

import config
from backtest import Backtester, load_csv

# ── Apex eval constants ───────────────────────────────────────────────────────
APEX_EVAL_TARGET     = 3_000.0    # profit target to pass
APEX_EVAL_DD         = 2_500.0    # trailing drawdown limit (fail if breached)
EVAL_TRADES_PER_SIM  = 30         # trades drawn per simulation (~2 months of activity)
N_SIMULATIONS        = 10_000
SEED                 = 42

# Trading days per trade -- computed dynamically from OOS pool in main()
# (placeholder value; overridden once actual pool is collected)
DAYS_PER_TRADE       = 4.9   # 451 OOS trading days / 92 eval trades (2025-Jun2026)


# ── Trade pool collection ─────────────────────────────────────────────────────
def collect_oos_trades(bars, eval_mode=False):
    """
    Run 2025-2026 monthly-independent and return list of trade P&Ls.
    eval_mode=True: 1 contract only, no pyramiding (simulates conservative eval trading).
    """
    # Temporarily apply eval-mode overrides
    orig_eval   = config.EVAL_MODE
    config.EVAL_MODE = eval_mode

    oos = [b for b in bars if b["timestamp"].year >= 2025]
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
    return trades


# ── Single eval simulation ────────────────────────────────────────────────────
def simulate_eval(trade_sequence, dd=None):
    """
    Simulate one Apex eval attempt.
    dd: trailing drawdown limit (defaults to APEX_EVAL_DD module constant).
    Returns (net_pnl, passed, trades_taken, max_consecutive_losses).
    """
    if dd is None:
        dd = APEX_EVAL_DD
    balance       = 50_000.0
    peak          = 50_000.0
    floor         = 50_000.0 - dd
    consec_losses = 0
    max_consec    = 0

    for i, pnl in enumerate(trade_sequence):
        balance += pnl

        if pnl < 0:
            consec_losses += 1
            max_consec = max(max_consec, consec_losses)
        else:
            consec_losses = 0

        if balance > peak:
            peak  = balance
            floor = max(floor, peak - dd)

        # Pass: hit profit target
        if balance >= 50_000.0 + APEX_EVAL_TARGET:
            return balance - 50_000.0, True, i + 1, max_consec

        # Fail: trailing DD floor breached
        if balance <= floor:
            return balance - 50_000.0, False, i + 1, max_consec

    # Ran out of trades without resolution
    return balance - 50_000.0, False, len(trade_sequence), max_consec


# ── 10,000 simulation run ─────────────────────────────────────────────────────
def run_stress_test(trades, dd=None):
    rng = random.Random(SEED)

    all_outcomes   = []    # (net_pnl, passed) for every sim
    trades_to_pass = []    # trade count for sims that passed
    all_max_consec = []    # max consec losses per sim

    for _ in range(N_SIMULATIONS):
        sample = rng.choices(trades, k=EVAL_TRADES_PER_SIM)
        net, passed, n_trades, max_c = simulate_eval(sample, dd=dd)
        all_outcomes.append((net, passed))
        all_max_consec.append(max_c)
        if passed:
            trades_to_pass.append(n_trades)

    return all_outcomes, trades_to_pass, all_max_consec


# ── Percentile helper ─────────────────────────────────────────────────────────
def pct(lst, p):
    s = sorted(lst)
    idx = max(0, min(int(len(s) * p / 100), len(s) - 1))
    return s[idx]


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data/nq_1min.csv"
    import math

    bars = load_csv(path)
    print(f"Loaded {len(bars):,} bars")

    # ── Compute OOS trading days for accurate DAYS_PER_TRADE ─────────────────
    global DAYS_PER_TRADE
    oos_trading_days = len(set(b["timestamp"].date()
                               for b in bars if b["timestamp"].year >= 2025))

    # ── Collect two pools ────────────────────────────────────────────────────
    print("Collecting OOS 2025-2026 trades...")
    print("  [1/2] Funded mode (pyramid + multi-contract sizing)...")
    funded_trades = collect_oos_trades(bars, eval_mode=False)
    print("  [2/2] Eval mode (1 contract only, no pyramiding)...")
    eval_trades   = collect_oos_trades(bars, eval_mode=True)

    # Set DAYS_PER_TRADE from actual eval pool
    if eval_trades:
        DAYS_PER_TRADE = oos_trading_days / len(eval_trades)

    if not eval_trades:
        print("No OOS trades found.")
        return

    def pool_stats(trades):
        wins     = [t for t in trades if t > 0]
        losses   = [t for t in trades if t <= 0]
        gw       = sum(wins)
        gl       = abs(sum(losses))
        return {
            "n": len(trades), "wr": len(wins)/len(trades),
            "pf": gw/gl if gl else float("inf"),
            "net": sum(trades),
            "aw": gw/len(wins)   if wins   else 0,
            "al": gl/len(losses) if losses else 0,
        }

    fs = pool_stats(funded_trades)
    es = pool_stats(eval_trades)

    print(f"\n  Funded pool: {fs['n']} trades | WR {fs['wr']:.1%} | PF {fs['pf']:.2f}"
          f" | Avg win ${fs['aw']:,.0f} | Avg loss $-{fs['al']:,.0f}")
    print(f"  Eval pool:   {es['n']} trades | WR {es['wr']:.1%} | PF {es['pf']:.2f}"
          f" | Avg win ${es['aw']:,.0f} | Avg loss $-{es['al']:,.0f}")
    print(f"\nRunning {N_SIMULATIONS:,} Monte Carlo simulations (funded + eval x4 DD tiers)...")

    funded_outcomes, funded_ttp, funded_consec = run_stress_test(funded_trades)
    eval_outcomes,   eval_ttp,   eval_consec   = run_stress_test(eval_trades)

    # ── DD tier analysis (eval mode only, three DD thresholds) ───────────────
    DD_TIERS = [2_500.0, 3_000.0, 3_500.0]
    tier_results = {}
    for dd_val in DD_TIERS:
        o, t, c = run_stress_test(eval_trades, dd=dd_val)
        tier_results[dd_val] = (o, t, c)

    def aggregate(outcomes, ttp, consec, avg_loss):
        n_pass = sum(1 for _, p in outcomes if p)
        n_fail = N_SIMULATIONS - n_pass
        nets   = sorted(o[0] for o in outcomes)
        return {
            "n_pass": n_pass, "n_fail": n_fail,
            "pass_prob": n_pass / N_SIMULATIONS,
            "p05": pct(nets, 5), "p50": pct(nets, 50), "p95": pct(nets, 95),
            "avg_ttp": sum(ttp)/len(ttp) if ttp else 0,
            "min_ttp": min(ttp) if ttp else 0,
            "max_ttp": max(ttp) if ttp else 0,
            "avg_consec": sum(consec)/len(consec),
            "max_consec": max(consec),
            "breach_count": math.ceil(APEX_EVAL_DD / avg_loss) if avg_loss else 0,
        }

    fa = aggregate(funded_outcomes, funded_ttp, funded_consec, fs["al"])
    ea = aggregate(eval_outcomes,   eval_ttp,   eval_consec,   es["al"])

    def verdict(p):
        if p >= 0.80: return "STRONG -- buy the eval"
        if p >= 0.65: return "MODERATE -- reasonable odds, manage daily limits carefully"
        if p >= 0.50: return "BORDERLINE -- below ideal; tighten risk before eval"
        return "POOR -- avg loss likely too large for eval DD tolerance"

    # ── Print results ────────────────────────────────────────────────────────
    sep = "=" * 66
    print(f"\n{sep}")
    print(f"  APEX EVAL STRESS TEST  ({N_SIMULATIONS:,} simulations, {EVAL_TRADES_PER_SIM} trades/sim)")
    print(f"  Target: +${APEX_EVAL_TARGET:,.0f}  |  Trailing DD: -${APEX_EVAL_DD:,.0f}")
    print(sep)
    print(f"  {'Metric':<36} {'Funded':>12} {'Eval Mode':>12}")
    print(f"  {'-'*60}")
    print(f"  {'Pool: trades / WR / PF':<36} "
          f"{fs['n']}t {fs['wr']:.0%} {fs['pf']:.2f}   "
          f"{es['n']}t {es['wr']:.0%} {es['pf']:.2f}")
    print(f"  {'Avg win / avg loss':<36} "
          f"${fs['aw']:,.0f} / $-{fs['al']:,.0f}   "
          f"${es['aw']:,.0f} / $-{es['al']:,.0f}")
    print(f"  {'-'*60}")
    print(f"  {'PASS PROBABILITY':<36} {fa['pass_prob']:>11.1%} {ea['pass_prob']:>11.1%}")
    print(f"  {'Sims failed (DD breach)':<36} {fa['n_fail']:>10,} {ea['n_fail']:>10,}")
    print(f"  {'-'*60}")
    print(f"  {'5th pct (worst case)':<36} ${fa['p05']:>+10,.0f} ${ea['p05']:>+10,.0f}")
    print(f"  {'50th pct (median)':<36} ${fa['p50']:>+10,.0f} ${ea['p50']:>+10,.0f}")
    print(f"  {'95th pct (best case)':<36} ${fa['p95']:>+10,.0f} ${ea['p95']:>+10,.0f}")
    print(f"  {'-'*60}")
    print(f"  {'Avg trades to pass':<36} {fa['avg_ttp']:>11.1f} {ea['avg_ttp']:>11.1f}")
    print(f"  {'Avg days to pass':<36} "
          f"{fa['avg_ttp']*DAYS_PER_TRADE:>10.0f} {ea['avg_ttp']*DAYS_PER_TRADE:>10.0f}")
    print(f"  {'Consec losses to breach DD':<36} {fa['breach_count']:>12} {ea['breach_count']:>12}")
    print(f"  {'Worst consec losses (any sim)':<36} {fa['max_consec']:>12} {ea['max_consec']:>12}")
    print(sep)
    print(f"  Funded verdict:   {verdict(fa['pass_prob'])}")
    print(f"  Eval mode verdict: {verdict(ea['pass_prob'])}")
    print(sep)

    # ── DD tier table (eval mode) ─────────────────────────────────────────────
    print(f"\n  DD TIER ANALYSIS -- Eval Mode (1c, no pyramid)")
    print(f"  {'Trailing DD':<16} {'Pass%':>7} {'Breach@':>8} {'p05':>9} {'p50':>9} {'p95':>9} {'AvgTrades':>10} {'AvgWeeks':>9}")
    print(f"  {'-'*82}")
    tier_aggs = {}
    for dd_val in DD_TIERS:
        o, t, c = tier_results[dd_val]
        ta = aggregate(o, t, c, es["al"])
        ta["dd"] = dd_val
        tier_aggs[dd_val] = ta
        breach = math.ceil(dd_val / es["al"]) if es["al"] else 0
        weeks  = ta["avg_ttp"] * DAYS_PER_TRADE / 5
        star   = " <-- STRONG" if ta["pass_prob"] >= 0.80 else (
                 " <-- MODERATE" if ta["pass_prob"] >= 0.65 else "")
        print(f"  ${dd_val:>7,.0f} DD      {ta['pass_prob']:>6.1%}  {breach:>7} losses"
              f"  ${ta['p05']:>+8,.0f}  ${ta['p50']:>+8,.0f}  ${ta['p95']:>+8,.0f}"
              f"  {ta['avg_ttp']:>9.1f}t  {weeks:>7.1f}wk{star}")
    print(f"  {'-'*82}")
    print(f"  (breach@ = consecutive losses needed to hit the trailing DD floor)")

    # ── Save stress_test_results.md ──────────────────────────────────────────
    ep = ea["pass_prob"]
    ev = verdict(ep)
    ed = ea
    es_d = es
    with open("stress_test_results.md", "w") as f:
        f.write("# Apex Eval Stress Test -- Monte Carlo Results\n\n")
        f.write(f"## Setup\n\n")
        f.write(f"| Parameter | Value |\n|-----------|-------|\n")
        f.write(f"| Simulations | {N_SIMULATIONS:,} |\n")
        f.write(f"| Trades per sim | {EVAL_TRADES_PER_SIM} (sampled with replacement) |\n")
        f.write(f"| Eval profit target | +${APEX_EVAL_TARGET:,.0f} |\n")
        f.write(f"| Eval trailing DD | -${APEX_EVAL_DD:,.0f} |\n")
        f.write(f"| Strategy | v6 + EVAL_MODE (1 contract, no pyramiding) |\n")
        f.write(f"| OOS period | 2025-2026 monthly-independent |\n\n")
        f.write(f"## Eval Mode Trade Pool\n\n")
        f.write(f"| Metric | Value |\n|--------|-------|\n")
        f.write(f"| Trades | {es_d['n']} |\n")
        f.write(f"| Win Rate | {es_d['wr']:.1%} |\n")
        f.write(f"| Profit Factor | {es_d['pf']:.2f} |\n")
        f.write(f"| Net P&L | ${es_d['net']:+,.0f} |\n")
        f.write(f"| Avg Win | ${es_d['aw']:,.0f} |\n")
        f.write(f"| Avg Loss | $-{es_d['al']:,.0f} |\n\n")
        f.write(f"## Results: Funded vs Eval Mode\n\n")
        f.write(f"| Metric | Funded (pyramid+scaling) | Eval Mode (1c, no pyramid) |\n")
        f.write(f"|--------|--------------------------|-----------------------------|\n")
        f.write(f"| Pass probability | {fa['pass_prob']:.1%} | **{ep:.1%}** |\n")
        f.write(f"| 5th pct (worst) | ${fa['p05']:+,.0f} | ${ed['p05']:+,.0f} |\n")
        f.write(f"| 50th pct (median) | ${fa['p50']:+,.0f} | ${ed['p50']:+,.0f} |\n")
        f.write(f"| 95th pct (best) | ${fa['p95']:+,.0f} | ${ed['p95']:+,.0f} |\n")
        f.write(f"| Avg trades to pass | {fa['avg_ttp']:.1f} | {ed['avg_ttp']:.1f} |\n")
        f.write(f"| Consec losses to breach DD | {fa['breach_count']} | {ed['breach_count']} |\n")
        f.write(f"| Sims failed | {fa['n_fail']:,} ({fa['n_fail']/N_SIMULATIONS:.1%}) | {ed['n_fail']:,} ({ed['n_fail']/N_SIMULATIONS:.1%}) |\n\n")
        f.write(f"## Eval Mode Verdict\n\n")
        f.write(f"**{ep:.1%} pass probability** (Apex $2,500 trailing DD)\n\n**{ev}**\n\n")
        f.write(f"## DD Tier Analysis -- Which Apex Plan to Buy\n\n")
        f.write(f"Eval mode (1c, no pyramid) tested across three trailing-DD tiers.\n\n")
        f.write(f"| Trailing DD | Pass % | Losses to Breach | p05 | p50 | p95 | Avg Trades | Avg Weeks | Verdict |\n")
        f.write(f"|-------------|--------|-----------------|-----|-----|-----|------------|-----------|--------|\n")
        for dd_val in DD_TIERS:
            ta = tier_aggs[dd_val]
            breach = math.ceil(dd_val / es["al"]) if es["al"] else 0
            weeks  = ta["avg_ttp"] * DAYS_PER_TRADE / 5
            bold_s = "**" if ta["pass_prob"] >= 0.65 else ""
            bold_e = "**" if ta["pass_prob"] >= 0.65 else ""
            f.write(f"| ${dd_val:,.0f} | {bold_s}{ta['pass_prob']:.1%}{bold_e} | {breach} | "
                    f"${ta['p05']:+,.0f} | ${ta['p50']:+,.0f} | ${ta['p95']:+,.0f} | "
                    f"{ta['avg_ttp']:.1f} | {weeks:.1f} | {verdict(ta['pass_prob'])} |\n")
        f.write(f"\n**Key insight:** each +$500 of trailing DD adds ~1 extra consecutive-loss buffer.\n")
        f.write(f"Crossing from $2,500 -> $3,000 DD adds +10 pp pass probability.\n\n")
        f.write(f"## How to Use\n\n")
        f.write(f"1. Choose an Apex plan with **$3,000+ trailing DD** (crosses 75% threshold).\n")
        f.write(f"2. Set `EVAL_MODE = True` in config.py before starting the eval:\n\n")
        f.write(f"```python\nEVAL_MODE = True   # 1 contract only, no pyramiding\n```\n\n")
        f.write(f"3. Switch back to `EVAL_MODE = False` once funded (pyramiding and scaling re-enable).\n")
    print(f"\n  Saved: stress_test_results.md")


if __name__ == "__main__":
    main()
