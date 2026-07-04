"""
live/morning_check.py

Pre-market go/no-go checklist for NQ/ES live trading.
Run before 9:30 AM ET each trading day.

Checks:
    1. Day of week (skip Mondays)
    2. Month filter (SKIP_MONTHS, WEAK_MONTHS, STRONG_MONTHS)
    3. EVAL_MODE — full eval dashboard if True (progress, floor, ETA)
    4. DD floor status (Apex trailing or Lucid fixed floor)
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
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live.discord_alerts as da
import live.telegram_alerts as tg

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


def _load_trade_log(log_path: str = "live/paper_trades.csv",
                    since: date | None = None) -> tuple[float, int, set]:
    """
    Return (total_net_pnl, trade_count, trading_days) from paper trade log.
    If `since` is given, only include trades on or after that date.
    """
    if not os.path.exists(log_path):
        return 0.0, 0, set()
    total  = 0.0
    count  = 0
    days: set[date] = set()
    cutoff = since or date(date.today().year, 1, 1)
    with open(log_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                d = date.fromisoformat(row["date"])
                if d >= cutoff:
                    total += float(row.get("net_pnl", 0))
                    count += 1
                    days.add(d)
            except (ValueError, KeyError):
                continue
    return round(total, 2), count, days


def _session_pnl_from_log(log_path: str = "live/paper_trades.csv") -> tuple[float, int]:
    """Backward-compat wrapper — return (pnl, count) for current year."""
    pnl, count, _ = _load_trade_log(log_path)
    return pnl, count


def _apex_status(cfg, pnl_cumulative: float) -> dict:
    start    = cfg.STARTING_BALANCE
    current  = start + pnl_cumulative
    peak     = current
    dd_floor = peak - cfg.APEX_TRAILING_DD
    headroom = current - dd_floor
    return {
        "start": start, "current": current, "peak": peak,
        "dd_limit": cfg.APEX_TRAILING_DD,
        "dd_floor": dd_floor, "headroom": headroom,
    }


def _eval_status(cfg, pnl_cumulative: float, trading_days: set) -> dict:
    """
    Compute full eval progress for morning display.
    Returns a dict with all metrics needed for display and Telegram.
    """
    start       = cfg.STARTING_BALANCE
    target      = getattr(cfg, "EVAL_PROFIT_TARGET", 3000.0)
    max_loss    = getattr(cfg, "EVAL_MAX_LOSS",      2000.0)
    start_str   = getattr(cfg, "EVAL_START_DATE",    "")

    current     = start + pnl_cumulative
    remaining   = max(0.0, target - pnl_cumulative)
    pct         = min(pnl_cumulative / target * 100, 100.0) if target else 0
    floor       = start - max_loss
    headroom    = current - floor

    # Trading day count: use log or EVAL_START_DATE
    if start_str:
        try:
            start_date = date.fromisoformat(start_str)
            n_days = len([d for d in trading_days if d >= start_date])
        except ValueError:
            n_days = len(trading_days)
    else:
        n_days = len(trading_days)

    avg_per_day = pnl_cumulative / n_days if n_days > 0 else 0.0
    est_days    = int(remaining / avg_per_day) + 1 if avg_per_day > 0 else 999

    passed  = pnl_cumulative >= target
    failed  = current <= floor

    return {
        "current": current, "profit": pnl_cumulative, "target": target,
        "remaining": remaining, "pct": pct,
        "floor": floor, "headroom": headroom,
        "n_days": n_days, "avg_per_day": avg_per_day, "est_days": est_days,
        "passed": passed, "failed": failed,
        "max_loss": max_loss,
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

    W = 62
    print()
    print("=" * W)
    print(f"  CruzCapital Morning Check — {today}  [{args.symbol}]")
    print(f"  {now_et.strftime('%H:%M')} ET  |  {DOW_NAMES[dow_idx]}")
    print("=" * W)

    issues   = []
    warnings = []
    info     = []

    # ── 1. Day of week ──────────────────────────────────────────────
    skip_day = (cfg.SKIP_MONDAYS and dow_idx == 0) or (getattr(cfg, "SKIP_FRIDAYS", False) and dow_idx == 4)
    if skip_day:
        day_name = DOW_NAMES[dow_idx]
        issues.append(f"SKIP: {day_name} (SKIP_{day_name.upper()}S=True)")
    else:
        info.append(f"DOW    : {DOW_NAMES[dow_idx]} ✓")

    # ── 2. Month filter ─────────────────────────────────────────────
    skip_months   = getattr(cfg, "SKIP_MONTHS",   [])
    weak_months   = getattr(cfg, "WEAK_MONTHS",   [])
    strong_months = getattr(cfg, "STRONG_MONTHS", [])
    month_name    = MONTH_NAMES[month]

    if month in skip_months:
        issues.append(f"SKIP: {month_name} is in SKIP_MONTHS — no trading today")
    elif month in weak_months:
        warnings.append(f"WEAK MONTH: {month_name} — 1 contract only in eval, reduce risk")
        info.append(f"Month  : {month_name} (WEAK)")
    elif month in strong_months:
        info.append(f"Month  : {month_name} (STRONG) ✓")
    else:
        info.append(f"Month  : {month_name} (neutral)")

    # ── 3. Eval mode ────────────────────────────────────────────────
    eval_mode = getattr(cfg, "EVAL_MODE", False)

    log_path = f"live/paper_trades_{args.symbol}.csv"
    pnl_sum, trade_count, trading_days = _load_trade_log(log_path)
    current_balance = cfg.STARTING_BALANCE + pnl_sum

    if eval_mode:
        ev = _eval_status(cfg, pnl_sum, trading_days)

        bar_n = int(ev["pct"] / 10)
        bar   = "█" * bar_n + "░" * (10 - bar_n)
        profit_sign = "+" if ev["profit"] >= 0 else ""

        print()
        print(f"  ┌{'─'*(W-2)}┐")
        print(f"  │{'  EVAL MODE — LUCID 50K PRO':^{W-2}}│")
        print(f"  ├{'─'*(W-2)}┤")
        print(f"  │  [{bar}] {ev['pct']:>5.1f}%{'':<{W-21}}│")
        print(f"  │  Profit  {profit_sign}${ev['profit']:>8,.0f}  /  ${ev['target']:,.0f} target{'':<{W-43}}│")
        print(f"  │  Remain  ${ev['remaining']:>8,.0f}{'':<{W-22}}│")
        print(f"  │  Balance ${ev['current']:>8,.0f}  ({trade_count} trades, {ev['n_days']} trading days){'':<{max(0,W-54)}}│")

        if ev["n_days"] > 0:
            avg_s = f"${ev['avg_per_day']:>+,.0f}/day avg"
            eta_s = (f"~{ev['est_days']} more days to target"
                     if ev["avg_per_day"] > 0 else "on pace unknown (no avg yet)")
            print(f"  │  Pace    {avg_s}  →  {eta_s}{'':<{max(0,W-len(avg_s)-len(eta_s)-18)}}│")

        headroom_warn = "  ⚠️ LOW" if ev["headroom"] < 1000 else ""
        print(f"  │  Floor   ${ev['floor']:>8,.0f}  (headroom: ${ev['headroom']:,.0f}){headroom_warn}{'':<{max(0,W-46-len(headroom_warn))}}│")
        print(f"  └{'─'*(W-2)}┘")

        if ev["passed"]:
            print()
            print(f"  🏆  EVAL TARGET REACHED — STOP TRADING")
            print(f"      Notify Lucid Trading to activate funded account.")
            issues.append("EVAL PASSED — do not trade, contact Lucid Trading")
        elif ev["failed"]:
            print()
            print(f"  🚨  EVAL FAILED — balance hit max loss floor")
            issues.append("EVAL FAILED — account at/below max loss floor, halt trading")
        else:
            if ev["headroom"] < 1100:
                warnings.append(f"FLOOR RISK: only ${ev['headroom']:,.0f} headroom — trade 1c, no second trade")
                tg.send_eval_at_risk(args.symbol, ev["current"], ev["floor"], ev["headroom"])
            elif ev["headroom"] < 1500:
                warnings.append(f"LOW FLOOR HEADROOM: ${ev['headroom']:,.0f} — stay conservative")

            info.append(f"EVAL   : ON — 1 contract, no pyramiding, no Asia")

        # Telegram morning eval update (only if not passed/failed)
        if not ev["passed"] and not ev["failed"] and ev["n_days"] > 0:
            tg.send_eval_morning(
                args.symbol, ev["n_days"] + 1, ev["profit"],
                ev["target"], ev["avg_per_day"], ev["est_days"], ev["headroom"],
            )

    else:
        info.append("EVAL   : OFF — funded mode (2c, pyramiding, Asia active)")

        # ── Apex DD status (funded mode only) ───────────────────────
        enforce_apex = getattr(cfg, "ENFORCE_APEX_RULES", True)
        if enforce_apex:
            apex = _apex_status(cfg, pnl_sum)
            info.append(f"Apex   : floor=${apex['dd_floor']:,.0f}  headroom=${apex['headroom']:,.0f}")
            info.append(f"Balance: ≈${apex['current']:,.0f}  ({trade_count} trades logged)")

            risk_per_trade = (cfg.ORB_FIXED_STOP_POINTS + cfg.ORB_STOP_BUFFER_POINTS) * cfg.POINT_VALUE
            max_losses_before_halt = int(apex["headroom"] / risk_per_trade)
            info.append(f"Risk   : ${risk_per_trade:.0f}/trade → {max_losses_before_halt} losses before DD halt")

            if apex["headroom"] < risk_per_trade * 2:
                issues.append(f"CRITICAL: Apex headroom ${apex['headroom']:,.0f} < 2 trades — DO NOT TRADE")
            elif apex["headroom"] < risk_per_trade * 4:
                warnings.append(f"LOW headroom ${apex['headroom']:,.0f} — trade 1c only")

            da.post_account_status(args.symbol, apex["current"], apex["peak"],
                                   apex["dd_floor"], apex["headroom"], eval_mode, trade_count)

    # ── 4. Key config summary ────────────────────────────────────────
    rr = cfg.ORB_BREAKOUT_RR_TARGET if eval_mode else cfg.ORB_FUNDED_RR_TARGET
    contracts_display = "1 (eval)" if eval_mode else str(cfg.MAX_CONTRACTS)
    print()
    print("  Key Parameters:")
    print(f"    Symbol       : {cfg.SYMBOL}  (${cfg.POINT_VALUE}/pt, tick={cfg.TICK_SIZE})")
    print(f"    Stop         : {cfg.ORB_FIXED_STOP_POINTS}pt + {cfg.ORB_STOP_BUFFER_POINTS}pt buffer "
          f"= {cfg.ORB_FIXED_STOP_POINTS + cfg.ORB_STOP_BUFFER_POINTS}pt effective")
    print(f"    RR Target    : {rr:.1f}x  |  Contracts: {contracts_display}")
    print(f"    OR Range     : {cfg.ORB_MIN_RANGE_POINTS}-{cfg.ORB_MAX_RANGE_POINTS}pt")
    print(f"    Entry Window : 9:45-{cfg.LAST_ENTRY_TIME} ET  |  Flatten: {cfg.FLATTEN_TIME} ET")
    print(f"    Daily Loss   : ${cfg.STARTING_BALANCE * cfg.DAILY_LOSS_LIMIT_PCT:,.0f}  "
          f"(max losses/day: {cfg.MAX_LOSSES_PER_DAY})")
    if eval_mode:
        target = getattr(cfg, "EVAL_PROFIT_TARGET", 3000.0)
        floor  = cfg.STARTING_BALANCE - getattr(cfg, "EVAL_MAX_LOSS", 2000.0)
        print(f"    Eval Target  : +${target:,.0f}  |  Fail Floor: ${floor:,.0f}")

    # ── 5. Status items ─────────────────────────────────────────────
    print()
    print("  Status:")
    for item in info:
        print(f"    {item}")

    # ── 6. Verdict ──────────────────────────────────────────────────
    print()
    if issues:
        print("  ISSUES:")
        for i in issues:
            print(f"    ✗  {i}")

    if warnings:
        print("  WARNINGS:")
        for w in warnings:
            print(f"    !  {w}")

    print()
    if issues:
        verdict = "NO-GO — DO NOT TRADE TODAY"
        print("  " + "═" * (W - 2))
        print(f"  {'  ' + verdict:^{W-2}}")
        print("  " + "═" * (W - 2))
    elif warnings:
        verdict = "GO WITH CAUTION — see warnings above"
        print("  " + "─" * (W - 2))
        print(f"  {verdict}")
        print("  " + "─" * (W - 2))
    else:
        verdict = "GO — TRADE TODAY"
        print("  " + "═" * (W - 2))
        print(f"  {'  ✅  ' + verdict:^{W-2}}")
        print("  " + "═" * (W - 2))

    print()
    print(f"  Run: python live/paper_trading.py --symbol {args.symbol}")
    print("=" * W)
    print()

    da.post_premarket(args.symbol, verdict, info, warnings, issues)

    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
