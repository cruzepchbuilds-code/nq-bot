"""
live/morning_check.py

Pre-market go/no-go checklist for NQ/ES live trading.
Run before 9:30 AM ET each trading day.

Checks:
    1. Day of week (skip Mondays)
    2. Month filter (SKIP_MONTHS, WEAK_MONTHS, STRONG_MONTHS)
    3. EVAL_MODE setting
    4. Apex trailing DD floor (estimated from session P&L CSV)
    5. Key config values printed for review
    6. Final GO / NO-GO verdict

Usage:
    python live/morning_check.py
    python live/morning_check.py --symbol ES
"""

import sys
import os
import csv
import argparse
from datetime import datetime, date, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live.discord_alerts as da

MONTH_NAMES = {
    1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
    7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec",
}
DOW_NAMES = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]


def _load_config(symbol: str):
    import config as cfg
    if symbol == "ES":
        try:
            import es_config as ec
            for attr in dir(ec):
                if not attr.startswith("_"):
                    setattr(cfg, attr, getattr(ec, attr))
            print(f"  Config   : es_config.py loaded")
        except ImportError:
            print(f"  Config   : WARNING — es_config.py not found, using config.py")
    else:
        print(f"  Config   : config.py (NQ)")
    return cfg


def _session_pnl_from_log(log_path: str = "live/paper_trades.csv") -> tuple[float, int]:
    """Return (total_net_pnl, trade_count) from paper trade log, current year."""
    if not os.path.exists(log_path):
        return 0.0, 0
    total = 0.0
    count = 0
    today_yr = date.today().year
    with open(log_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                d = date.fromisoformat(row["date"])
                if d.year == today_yr:
                    total += float(row.get("net_pnl", 0))
                    count += 1
            except (ValueError, KeyError):
                continue
    return round(total, 2), count


def _apex_status(cfg, pnl_cumulative: float) -> dict:
    """
    Estimate Apex trailing DD floor.
    Peak balance = STARTING_BALANCE + max cumulative pnl reached.
    DD floor     = peak_balance - APEX_TRAILING_DD
    Headroom     = current_balance - dd_floor
    """
    start   = cfg.STARTING_BALANCE
    current = start + pnl_cumulative
    peak    = current  # conservative: assume we've never been higher than current
    dd_floor = peak - cfg.APEX_TRAILING_DD
    headroom = current - dd_floor
    return {
        "start":     start,
        "current":   current,
        "peak":      peak,
        "dd_limit":  cfg.APEX_TRAILING_DD,
        "dd_floor":  dd_floor,
        "headroom":  headroom,
    }


def main():
    parser = argparse.ArgumentParser(description="Morning pre-market check")
    parser.add_argument("--symbol", default="NQ", choices=["NQ", "ES", "RTY"])
    args = parser.parse_args()

    now_et  = datetime.now()
    today   = now_et.date()
    dow_idx = today.weekday()
    month   = today.month

    cfg = _load_config(args.symbol)

    print()
    print("=" * 58)
    print(f"  CruzCapital Morning Check — {today}  [{args.symbol}]")
    print(f"  {now_et.strftime('%H:%M')} ET  |  {DOW_NAMES[dow_idx]}")
    print("=" * 58)

    issues  = []
    warnings = []
    info    = []

    # ── 1. Day of week ──────────────────────────────────────────────
    skip_day = cfg.SKIP_MONDAYS and dow_idx == 0
    if skip_day:
        issues.append(f"SKIP: Monday (SKIP_MONDAYS=True)")
    else:
        info.append(f"DOW   : {DOW_NAMES[dow_idx]} ✓")

    # ── 2. Month filter ─────────────────────────────────────────────
    skip_months   = getattr(cfg, "SKIP_MONTHS",   [])
    weak_months   = getattr(cfg, "WEAK_MONTHS",   [])
    strong_months = getattr(cfg, "STRONG_MONTHS", [])

    month_name = MONTH_NAMES[month]
    if month in skip_months:
        issues.append(f"SKIP: {month_name} is in SKIP_MONTHS — no trading today")
    elif month in weak_months:
        warnings.append(f"WEAK MONTH: {month_name} — max 1 contract, reduce size")
        info.append(f"Month : {month_name} (WEAK) — max_c=1")
    elif month in strong_months:
        info.append(f"Month : {month_name} (STRONG) — max_c up to {min(3, cfg.MAX_CONTRACTS+1)}")
    else:
        info.append(f"Month : {month_name} (neutral) — max_c={cfg.MAX_CONTRACTS}")

    # ── 3. EVAL_MODE ────────────────────────────────────────────────
    eval_mode = getattr(cfg, "EVAL_MODE", False)
    if eval_mode:
        info.append("EVAL  : ON — 1 contract only, no pyramiding")
        warnings.append("EVAL_MODE=True — treat each trade like your qualification")
    else:
        info.append("EVAL  : OFF — funded mode (pyramiding/sizing active)")

    # ── 4. Apex DD status ───────────────────────────────────────────
    enforce_apex = getattr(cfg, "ENFORCE_APEX_RULES", True)
    if enforce_apex:
        pnl_sum, trade_count = _session_pnl_from_log()
        apex = _apex_status(cfg, pnl_sum)
        info.append(f"Apex  : DD limit=${apex['dd_limit']:,.0f}  "
                    f"floor=${apex['dd_floor']:,.0f}  "
                    f"headroom=${apex['headroom']:,.0f}")
        info.append(f"Acct  : balance≈${apex['current']:,.0f}  "
                    f"({trade_count} trades logged this year)")

        risk_per_trade = (cfg.ORB_FIXED_STOP_POINTS + cfg.ORB_STOP_BUFFER_POINTS) * cfg.POINT_VALUE
        max_losses_before_halt = int(apex["headroom"] / risk_per_trade)
        info.append(f"Risk  : ${risk_per_trade:.0f}/trade  "
                    f"→ {max_losses_before_halt} consecutive losses before DD halt")

        if apex["headroom"] < risk_per_trade * 2:
            issues.append(f"CRITICAL: Apex headroom ${apex['headroom']:,.0f} < 2 trades — "
                          f"DO NOT TRADE until reset")
        elif apex["headroom"] < risk_per_trade * 4:
            warnings.append(f"LOW headroom ${apex['headroom']:,.0f} — trade conservatively (1c only)")

        da.post_account_status(args.symbol, apex["current"], apex["peak"],
                               apex["dd_floor"], apex["headroom"], eval_mode, trade_count)

    # ── 5. Key config summary ───────────────────────────────────────
    print()
    print("  Key Parameters:")
    print(f"    Symbol       : {cfg.SYMBOL}  (${cfg.POINT_VALUE}/pt, tick={cfg.TICK_SIZE})")
    print(f"    Stop         : {cfg.ORB_FIXED_STOP_POINTS}pt fixed + "
          f"{cfg.ORB_STOP_BUFFER_POINTS}pt buffer = "
          f"{cfg.ORB_FIXED_STOP_POINTS + cfg.ORB_STOP_BUFFER_POINTS}pt eff.")
    print(f"    RR Target    : {cfg.ORB_BREAKOUT_RR_TARGET:.1f}x")
    print(f"    OR Range     : {cfg.ORB_MIN_RANGE_POINTS}-{cfg.ORB_MAX_RANGE_POINTS}pt")
    print(f"    Entry Window : 9:30-{cfg.LAST_ENTRY_TIME} ET")
    print(f"    Flatten      : {cfg.FLATTEN_TIME} ET")
    print(f"    Breakout Buf : {cfg.ORB_BREAKOUT_BUFFER_POINTS}pt above OR edge")
    print(f"    Min Score    : {cfg.SIGNAL_STRENGTH_MIN_SCORE}")
    print(f"    Max Trades   : {cfg.MAX_TRADES_PER_DAY}/day | "
          f"Max Losses: {cfg.MAX_LOSSES_PER_DAY}/day")
    print(f"    Daily Loss   : {cfg.DAILY_LOSS_LIMIT_PCT*100:.1f}% = "
          f"${cfg.STARTING_BALANCE * cfg.DAILY_LOSS_LIMIT_PCT:,.0f}")

    # ── 6. Status items ─────────────────────────────────────────────
    print()
    print("  Status:")
    for item in info:
        print(f"    {item}")

    # ── 7. Verdict ──────────────────────────────────────────────────
    print()
    if issues:
        print("  ⛔  ISSUES:")
        for i in issues:
            print(f"    ✗  {i}")

    if warnings:
        print("  ⚠️  WARNINGS:")
        for w in warnings:
            print(f"    !  {w}")

    print()
    if issues:
        verdict = "NO-GO — DO NOT TRADE TODAY"
        print("  ════════════════════════════════")
        print(f"       {verdict}")
        print("  ════════════════════════════════")
    elif warnings:
        verdict = "GO WITH CAUTION — see warnings"
        print("  ────────────────────────────────")
        print(f"   {verdict}")
        print("  ────────────────────────────────")
    else:
        verdict = "GO — TRADE TODAY"
        print("  ════════════════════════════════")
        print(f"         ✅  {verdict}")
        print("  ════════════════════════════════")

    print()
    print(f"  Run: python live/paper_trading.py --symbol {args.symbol}")
    print("=" * 58)
    print()

    da.post_premarket(args.symbol, verdict, info, warnings, issues)

    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
