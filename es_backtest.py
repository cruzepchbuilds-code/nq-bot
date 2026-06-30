"""
ES Walk-Forward Backtest
Runs the existing ORB strategy engine on ES (E-mini S&P 500) futures.

Approach: monkey-patch the config module with ES parameters before any
other module imports happen, so all sub-modules (bankroll, strategy_orb,
regime, signal_strength) pick up ES values automatically.

Usage:
    python3 es_backtest.py data/es_1min.csv
"""

import sys

# Step 1: Load ES config and patch the config module in-place
# This must happen BEFORE importing any other project module
import es_config
import config

for attr in dir(es_config):
    if not attr.startswith("_"):
        setattr(config, attr, getattr(es_config, attr))

# Step 2: Now import the rest -- they will all see ES config values
import backtest as bt_module
from backtest import Backtester, load_csv
from datetime import time, datetime
from bankroll import BankrollManager

# Step 3: Patch backtest module-level constants (computed at import time from config)
bt_module.OR_END = (
    time(9, 30 + config.OPENING_RANGE_MINUTES)
    if config.OPENING_RANGE_MINUTES < 30
    else time(9 + (30 + config.OPENING_RANGE_MINUTES) // 60,
              (30 + config.OPENING_RANGE_MINUTES) % 60)
)
bt_module.LAST_ENTRY  = time(*map(int, config.LAST_ENTRY_TIME.split(":")))
bt_module.FLATTEN     = time(*map(int, config.FLATTEN_TIME.split(":")))
bt_module.GAP_FILL_LAST = time(*map(int, config.GAP_FILL_LAST_ENTRY.split(":")))
bt_module.PM_VWAP_START = time(*map(int, config.PM_VWAP_START.split(":")))
bt_module.PM_VWAP_LAST  = time(*map(int, config.PM_VWAP_LAST_ENTRY.split(":")))
bt_module.SECOND_BREAKOUT_AFTER = time(*map(int, config.SECOND_BREAKOUT_MIN_TIME.split(":")))
bt_module.SLIP = config.SLIPPAGE_TICKS * config.TICK_SIZE


def run_year(bars, year):
    """Run a single year backtest starting at $50k balance."""
    year_bars = [b for b in bars if b["timestamp"].year == year]
    if not year_bars:
        return None

    # Reset config to fresh $50k starting balance for this year
    config.STARTING_BALANCE = 50000.0

    bt = Backtester()
    bt.run(year_bars, silent=True)

    log = bt.bank.trade_log
    if not log:
        return {
            "year": year, "trades": 0, "wins": 0, "losses": 0,
            "wr": 0.0, "pf": 0.0, "net_pnl": 0.0, "max_dd": 0.0,
            "end_balance": 50000.0, "halted": False, "halt_reason": "",
        }

    wins    = [t for t in log if t["pnl"] > 0]
    losses  = [t for t in log if t["pnl"] <= 0]
    gw      = sum(t["pnl"] for t in wins)
    gl      = abs(sum(t["pnl"] for t in losses))
    pf      = gw / gl if gl else float("inf")
    net_pnl = sum(t["pnl"] for t in log)
    wr      = len(wins) / len(log) if log else 0.0

    # Compute max drawdown from balance curve
    peak    = 50000.0
    max_dd  = 0.0
    for t in log:
        peak   = max(peak, t["balance"])
        max_dd = max(max_dd, (peak - t["balance"]) / 50000.0)

    return {
        "year": year,
        "trades": len(log),
        "wins": len(wins),
        "losses": len(losses),
        "wr": wr,
        "pf": pf,
        "net_pnl": net_pnl,
        "max_dd": max_dd,
        "end_balance": bt.bank.s.balance,
        "halted": bt.bank.s.halted_permanently,
        "halt_reason": bt.bank.s.halt_reason,
    }


def walk_forward(bars):
    """Run year-by-year walk-forward: 2022-2023 IS, 2024-2026 OOS."""
    years = [2022, 2023, 2024, 2025, 2026]
    is_years  = {2022, 2023}
    oos_years = {2024, 2025, 2026}

    results = []
    for year in years:
        print(f"  Running {year}...", end=" ", flush=True)
        res = run_year(bars, year)
        if res is None:
            print(f"No data for {year}, skipping.")
            continue
        results.append(res)
        status = "IS" if year in is_years else "OOS"
        halted_flag = " HALT" if res["halted"] else ""
        print(f"Done. {res['trades']} trades, PF={res['pf']:.2f}, "
              f"Net=${res['net_pnl']:+,.0f}{halted_flag}")

    return results


def print_summary(results):
    """Print the formatted walk-forward summary table."""
    is_years  = {2022, 2023}
    oos_years = {2024, 2025, 2026}

    sep = "=" * 80
    print()
    print(sep)
    print("  ES WALK-FORWARD RESULTS")
    print(sep)
    print(f"  {'Year':<6} | {'Trades':>6} | {'WR':>5} | {'PF':>5} | "
          f"{'Net P&L':>10} | {'MaxDD':>7} | {'Status'}")
    print(f"  {'-'*6}-+-{'-'*6}-+-{'-'*5}-+-{'-'*5}-+-{'-'*10}-+-{'-'*7}-+-{'-'*10}")

    for r in results:
        year   = r["year"]
        status = ("IS" if year in is_years else "OOS")
        if r["halted"]:
            status += " HALT"
        halted_dd = r["max_dd"]
        print(f"  {year:<6} | {r['trades']:>6} | {r['wr']:>4.0%} | {r['pf']:>5.2f} | "
              f"  {r['net_pnl']:>+9,.0f} | {halted_dd:>6.1%} | {status}")

    print()

    # IS combined
    is_results  = [r for r in results if r["year"] in is_years]
    oos_results = [r for r in results if r["year"] in oos_years]

    def combined_stats(res_list):
        if not res_list:
            return None
        all_wins   = sum(r["wins"]   for r in res_list)
        all_losses = sum(r["losses"] for r in res_list)
        all_trades = sum(r["trades"] for r in res_list)
        all_net    = sum(r["net_pnl"] for r in res_list)
        # Reconstruct gross wins / gross losses from PF and net for each year
        total_gw = 0.0
        total_gl = 0.0
        for r in res_list:
            wins_pnl   = [0] * r["wins"]    # placeholder -- we need to recompute
            # Use PF and net to back-compute gross win/loss
            # net = gw - gl, pf = gw/gl => gw = pf*gl, net = pf*gl - gl = gl*(pf-1)
            if r["pf"] == float("inf"):
                total_gw += r["net_pnl"]
            elif r["pf"] == 0:
                total_gl += abs(r["net_pnl"])
            else:
                # gl = net / (pf - 1), gw = pf * gl
                # Handle edge: pf very close to 1
                if abs(r["pf"] - 1.0) < 1e-6:
                    gl_yr = abs(r["net_pnl"]) / 2.0
                else:
                    gl_yr = r["net_pnl"] / (r["pf"] - 1.0)
                    if gl_yr < 0:
                        gl_yr = -gl_yr
                gw_yr = r["pf"] * gl_yr
                total_gw += gw_yr
                total_gl += gl_yr
        pf = total_gw / total_gl if total_gl else float("inf")
        return {
            "trades": all_trades,
            "wins": all_wins,
            "losses": all_losses,
            "net": all_net,
            "pf": pf,
            "gw": total_gw,
            "gl": total_gl,
        }

    is_comb  = combined_stats(is_results)
    oos_comb = combined_stats(oos_results)

    if is_comb:
        print(f"  IS Combined  (2022-2023):  PF {is_comb['pf']:.2f} | "
              f"Net ${is_comb['net']:>+10,.0f}")
    if oos_comb:
        print(f"  OOS Combined (2024-2026):  PF {oos_comb['pf']:.2f} | "
              f"Net ${oos_comb['net']:>+10,.0f}")

    print()

    # Gate check
    if oos_comb:
        if oos_comb["pf"] >= 1.4:
            print(f"  ES OOS gate (PF >= 1.40): PASS  (OOS PF = {oos_comb['pf']:.2f})")
        else:
            print(f"  ES OOS gate (PF >= 1.40): FAIL  (OOS PF = {oos_comb['pf']:.2f})")
            if oos_comb["pf"] >= 1.0:
                print("    Strategy is profitable OOS but below target PF threshold.")
            else:
                print("    Strategy is unprofitable OOS -- edge may not transfer to ES.")

    print(sep)
    return is_comb, oos_comb


def save_results(results, is_comb, oos_comb, path="es_results.md"):
    """Save walk-forward results to a Markdown file."""
    is_years  = {2022, 2023}
    oos_years = {2024, 2025, 2026}

    lines = [
        "# ES Walk-Forward Backtest Results",
        "",
        "## Configuration",
        "",
        "| Parameter | Value |",
        "|-----------|-------|",
        f"| Symbol | {config.SYMBOL} |",
        f"| Point Value | ${config.POINT_VALUE:.0f}/pt |",
        f"| Starting Balance | ${config.STARTING_BALANCE:,.0f} |",
        f"| ORB_MIN_RANGE_POINTS | {config.ORB_MIN_RANGE_POINTS} |",
        f"| ORB_MAX_RANGE_POINTS | {config.ORB_MAX_RANGE_POINTS} |",
        f"| ORB_FIXED_STOP_POINTS | {config.ORB_FIXED_STOP_POINTS} |",
        f"| ORB_STOP_BUFFER_POINTS | {config.ORB_STOP_BUFFER_POINTS} |",
        f"| Effective Stop | {config.ORB_FIXED_STOP_POINTS + config.ORB_STOP_BUFFER_POINTS} pts |",
        f"| ORB_BREAKOUT_BUFFER_POINTS | {config.ORB_BREAKOUT_BUFFER_POINTS} |",
        f"| ORB_BREAKOUT_RR_TARGET | {config.ORB_BREAKOUT_RR_TARGET} |",
        f"| APEX_TRAILING_DD | ${config.APEX_TRAILING_DD:,.0f} |",
        f"| MAX_TOTAL_DRAWDOWN_PCT | {config.MAX_TOTAL_DRAWDOWN_PCT:.0%} |",
        "",
        "## Year-by-Year Walk-Forward Results",
        "(Each year starts fresh at $50,000 balance)",
        "",
        "| Year | Trades | WR | PF | Net P&L | MaxDD | Status |",
        "|------|--------|----|----|---------|-------|--------|",
    ]

    for r in results:
        year   = r["year"]
        status = "IS" if year in is_years else "OOS"
        if r["halted"]:
            status += " HALT"
        lines.append(
            f"| {year} | {r['trades']} | {r['wr']:.0%} | {r['pf']:.2f} | "
            f"${r['net_pnl']:+,.0f} | {r['max_dd']:.1%} | {status} |"
        )

    lines.extend([
        "",
        "## Combined Results",
        "",
    ])

    if is_comb:
        lines.append(
            f"**IS Combined (2022-2023):**  PF {is_comb['pf']:.2f} | "
            f"Net ${is_comb['net']:+,.0f}"
        )
    if oos_comb:
        lines.append(
            f"**OOS Combined (2024-2026):**  PF {oos_comb['pf']:.2f} | "
            f"Net ${oos_comb['net']:+,.0f}"
        )

    lines.extend([
        "",
        "## OOS Gate Check",
        "",
    ])

    if oos_comb:
        gate = "PASS" if oos_comb["pf"] >= 1.4 else "FAIL"
        lines.append(f"**ES OOS gate (PF >= 1.40): {gate}**  (OOS PF = {oos_comb['pf']:.2f})")

    lines.extend([
        "",
        "## ES Statistics Summary",
        "",
        "Calibrated from 2022-2026 ES 1-minute data (1,128 RTH trading days):",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        "| Mean OR size (15 min) | 16.61 pts |",
        "| Median OR size | 14.00 pts |",
        "| 25th pct OR | 10.00 pts |",
        "| 75th pct OR | 21.25 pts |",
        "| 90th pct OR | 28.25 pts |",
        "| Mean daily range | 60.13 pts |",
        "| Median daily range | 51.00 pts |",
        "",
        "## Notes",
        "",
        "- Breakout-only mode (VWAP pull, London, PM VWAP all disabled)",
        "- Pyramiding disabled for clean baseline test",
        "- Signal strength filter uses 1-contract baseline for below-threshold signals",
        "  (NQ OR size thresholds in scorer don't apply to ES, but trades still execute at 1 contract)",
        "- ES effective stop = 8 pts (6 fixed + 2 buffer) at $50/pt = $400/loss + commission",
        "- Apex floor at $50k - $7k = $43k (allows ~17 consecutive max losses)",
    ])

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n  Results saved to {path}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 es_backtest.py data/es_1min.csv")
        sys.exit(1)

    data_path = sys.argv[1]
    print(f"Loading {data_path}...")
    bars = load_csv(data_path)
    print(f"Loaded {len(bars):,} bars")

    years_available = sorted(set(b["timestamp"].year for b in bars))
    print(f"Years in data: {years_available}")
    print()

    print("Running walk-forward (each year = fresh $50k account)...")
    print()

    results = walk_forward(bars)

    is_comb, oos_comb = print_summary(results)
    save_results(results, is_comb, oos_comb)


if __name__ == "__main__":
    main()
