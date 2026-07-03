"""
brain/research/portfolio_policy.py

Portfolio-level risk-policy optimization for v11 (single account, single strategy):
  morning ORB (9:46-10:30) + VWAP rejection (11:00-13:00) + PM ORB (13:15-14:00)
  + Asia gap (18:15)

Generates real trade lists (entry/exit times + P&L) for each sub-strategy over
2022-2026, then simulates the COMBINED account day by day under candidate risk
policies. A trade only enters if no other position is open (v11 = one NT8
strategy instance) and the policy allows it.

Policies swept:
  DLL        ∈ {none, 900, 700, 500}   — halt day when dailyPnL ≤ -DLL
  maxLosses  ∈ {99, 2, 1}              — halt day after N losing trades

Reported per policy: net, $/wk, worst day, EOD max drawdown, losing streaks,
plus marginal per-strategy contribution and rejection-after-morning-loss slice.

Morning config: SKIP_FRIDAYS=False (user preference), funded 3R, pyramid OFF, 1c.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import csv
import config
config.SKIP_FRIDAYS        = False   # v11 default: morning trades Fridays
config.PYRAMIDING_ENABLED  = False   # clean 1c policy study
config.PARTIAL_EXIT_ENABLED = False
config.EVAL_MODE           = False   # funded 3R targets

from backtest import Backtester, load_csv
from datetime import datetime, time, date
from collections import defaultdict
from itertools import product

DATA   = "data/nq_full.csv"
NQ_PT  = 20.0
COST   = 14.50
YEARS  = [2022, 2023, 2024, 2025, 2026]


# ── Morning trades via engine (patched to record exit time) ──────────────────

class TimedBacktester(Backtester):
    """Records exit bar time into each trade dict."""
    def check_exit(self, bar, ts, force=False):
        n_before = len(self.bank.trade_log)
        super().check_exit(bar, ts, force)
        for t in self.bank.trade_log[n_before:]:
            t["exit_time"] = ts.time().strftime("%H:%M")


def run_year_morning(bars, year):
    ystart, yend = date(year, 1, 1), date(year + 1, 1, 1)
    prior  = [b for b in bars if b["timestamp"].date() < ystart]
    subset = [b for b in bars if ystart <= b["timestamp"].date() < yend]
    if not subset:
        return []
    warmup = TimedBacktester(); warmup.run(prior, silent=True)
    bt = TimedBacktester()
    bt._last_close         = warmup._last_close
    bt.regime.daily_ranges = list(warmup.regime.daily_ranges)
    bt.or_volume_history   = list(warmup.or_volume_history)
    bt.prev_day_mode       = warmup.prev_day_mode
    bt.run(subset, silent=True)
    return [t for t in bt.bank.trade_log if t.get("mode") == "breakout"]


# ── Standalone day sims (rejection / PM / Asia) with entry+exit times ─────────

def load_days(path, h_lo, h_hi):
    days = defaultdict(list)
    with open(path) as f:
        for row in csv.DictReader(f):
            s = row["timestamp"][:19]
            try:
                ts = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if not (h_lo <= ts.hour < h_hi):
                continue
            days[ts.date()].append({
                "t": ts.time(), "h": float(row["high"]), "l": float(row["low"]),
                "c": float(row["close"]), "v": float(row["volume"]),
            })
    return days


def rejection_day(bars):
    """stop 20 / 3R / ext 25 / arm ≥11:00 / flat 13:00. -> (entry_t, exit_t, pnl) | None"""
    sum_pv = sum_vol = 0.0
    vwap = None
    was_ext = saw = False
    rec_up = prev_above = None
    entry = sl = tp = e_t = None
    is_long = None
    for b in bars:
        t = b["t"]
        if t < time(9, 30) or t >= time(16, 0):
            continue
        sum_pv += (b["h"] + b["l"] + b["c"]) / 3 * b["v"]
        sum_vol += b["v"]
        vwap = sum_pv / sum_vol if sum_vol else None
        if vwap is None:
            continue
        close = b["c"]; above = close > vwap
        if entry is not None:
            if is_long:
                if b["l"] <= sl: return (e_t, t, (sl - entry) * NQ_PT - COST)
                if b["h"] >= tp: return (e_t, t, (tp - entry) * NQ_PT - COST)
            else:
                if b["h"] >= sl: return (e_t, t, (entry - sl) * NQ_PT - COST)
                if b["l"] <= tp: return (e_t, t, (entry - tp) * NQ_PT - COST)
            if t >= time(13, 0):
                pts = (close - entry) if is_long else (entry - close)
                return (e_t, t, pts * NQ_PT - COST)
            prev_above = above
            continue
        if t < time(10, 0):
            prev_above = above
            continue
        if not was_ext and abs(close - vwap) > 25.0:
            was_ext = True
        if was_ext and prev_above is not None and time(11, 0) <= t < time(13, 0):
            cu = (not prev_above) and above
            cd = prev_above and (not above)
            if not saw:
                if cu:   saw, rec_up = True, True
                elif cd: saw, rec_up = True, False
            else:
                if rec_up and cd:
                    entry, is_long, e_t = close, False, t
                    sl, tp = close + 20.0, close - 60.0
                elif (not rec_up) and cu:
                    entry, is_long, e_t = close, True, t
                    sl, tp = close - 20.0, close + 60.0
        prev_above = above
    if entry is not None:
        pts = (bars[-1]["c"] - entry) if is_long else (entry - bars[-1]["c"])
        return (e_t, bars[-1]["t"], pts * NQ_PT - COST)
    return None


def pm_day(bars):
    """PM ORB: OR 13:00-13:14 (15-60pt), entry 13:15-14:00, stop 22 / 2.5R, flat 15:55."""
    or_hi = or_lo = None
    or_done = False
    entry = sl = tp = e_t = None
    is_long = None
    for b in bars:
        t = b["t"]
        if t < time(13, 0):
            continue
        if t >= time(15, 55):
            break
        if t < time(13, 15):
            or_hi = b["h"] if or_hi is None else max(or_hi, b["h"])
            or_lo = b["l"] if or_lo is None else min(or_lo, b["l"])
            continue
        if not or_done:
            or_done = True
            if or_hi is None or not (15 <= or_hi - or_lo <= 60):
                return None
        if entry is not None:
            if is_long:
                if b["l"] <= sl: return (e_t, t, (sl - entry) * NQ_PT - COST)
                if b["h"] >= tp: return (e_t, t, (tp - entry) * NQ_PT - COST)
            else:
                if b["h"] >= sl: return (e_t, t, (entry - sl) * NQ_PT - COST)
                if b["l"] <= tp: return (e_t, t, (entry - tp) * NQ_PT - COST)
            continue
        if t > time(14, 0):
            continue
        if b["c"] > or_hi + 2:
            entry, is_long, e_t = b["c"], True, t
            sl, tp = entry - 22.0, entry + 55.0
        elif b["c"] < or_lo - 2:
            entry, is_long, e_t = b["c"], False, t
            sl, tp = entry + 22.0, entry - 55.0
    if entry is not None:
        pts = (bars[-1]["c"] - entry) if is_long else (entry - bars[-1]["c"])
        return (e_t, time(15, 55), pts * NQ_PT - COST)
    return None


def asia_day(bars):
    """Asia gap: ref close pre-17:00, 18:15 bar, gap 30-80, stop 25 / 3R, flat 21:00."""
    cme = None
    entry = sl = tp = e_t = None
    is_long = None
    for b in bars:
        t = b["t"]
        if t < time(17, 0):
            cme = b["c"]
            continue
        if t < time(18, 15):
            continue
        if entry is None:
            if t >= time(18, 16) or cme is None:
                return None
            gap = b["c"] - cme
            if not (30 <= abs(gap) <= 80):
                return None
            entry, is_long, e_t = b["c"], gap > 0, t
            sl = entry - 25 if is_long else entry + 25
            tp = entry + 75 if is_long else entry - 75
            continue
        if t >= time(21, 0):
            pts = (b["c"] - entry) if is_long else (entry - b["c"])
            return (e_t, t, pts * NQ_PT - COST)
        if is_long:
            if b["l"] <= sl: return (e_t, t, (sl - entry) * NQ_PT - COST)
            if b["h"] >= tp: return (e_t, t, (tp - entry) * NQ_PT - COST)
        else:
            if b["h"] >= sl: return (e_t, t, (entry - sl) * NQ_PT - COST)
            if b["l"] <= tp: return (e_t, t, (entry - tp) * NQ_PT - COST)
    if entry is not None:
        pts = (bars[-1]["c"] - entry) if is_long else (entry - bars[-1]["c"])
        return (e_t, bars[-1]["t"], pts * NQ_PT - COST)
    return None


# ── Combined policy simulation ────────────────────────────────────────────────

def simulate(day_trades, dll, max_losses, enabled):
    """
    day_trades: {date: [(strat, entry_t, exit_t, pnl), ...]} sorted by entry_t.
    One open position at a time; entry allowed only if dailyPnL > -dll,
    losses_today < max_losses, and no open position at entry time.
    P&L lands at exit time. Returns daily pnl dict + trade tally.
    """
    daily = {}
    taken = defaultdict(int)
    for d in sorted(day_trades):
        pnl_day = 0.0
        losses = 0
        open_until = None
        for strat, e_t, x_t, pnl in day_trades[d]:
            if strat not in enabled:
                continue
            if open_until is not None and e_t < open_until:
                continue                      # position still open → signal skipped
            if pnl_day <= -dll:
                continue
            if losses >= max_losses:
                continue
            pnl_day += pnl
            open_until = x_t
            taken[strat] += 1
            if pnl < 0:
                losses += 1
        daily[d] = pnl_day
    return daily, taken


def metrics(daily):
    days = sorted(daily)
    pnls = [daily[d] for d in days]
    net = sum(pnls)
    n_days = len([p for p in pnls if p != 0])
    worst = min(pnls) if pnls else 0
    # EOD equity max drawdown
    eq = peak = dd = 0.0
    streak = max_streak = 0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        streak = streak + 1 if p < 0 else 0
        max_streak = max(max_streak, streak)
    weeks = max(1, (days[-1] - days[0]).days / 7) if days else 1
    return {"net": round(net), "wk": round(net / weeks), "worst": round(worst),
            "dd": round(dd), "streak": max_streak, "active": n_days}


if __name__ == "__main__":
    print("Loading full bars for morning engine...", flush=True)
    bars = load_csv(DATA)
    print("Generating morning ORB trades (gap filter on, Fridays on, 1c/3R)...", flush=True)
    morning = []
    for y in YEARS:
        morning.extend(run_year_morning(bars, y))
    del bars
    print(f"  {len(morning)} morning trades")

    print("Generating rejection / PM / Asia trades...", flush=True)
    rth  = load_days(DATA, 9, 16)
    eve  = load_days(DATA, 16, 21)

    day_trades = defaultdict(list)
    for t in morning:
        d = date.fromisoformat(t["date"])
        e = datetime.strptime(t.get("entry_time") or "09:46", "%H:%M").time()
        x = datetime.strptime(t.get("exit_time") or "15:55", "%H:%M").time()
        day_trades[d].append(("ORB", e, x, t["pnl"]))

    n_rej = n_pm = n_asia = 0
    for d in sorted(rth):
        wd, mo = d.weekday(), d.month
        if wd != 0 and mo not in (4, 5, 6, 9, 12):          # rejection: skip Mon+weak
            r = rejection_day(rth[d])
            if r:
                day_trades[d].append(("REJ", *r)); n_rej += 1
        if wd not in (0, 4):                                  # PM: skip Mon+Fri
            r = pm_day(rth[d])
            if r:
                day_trades[d].append(("PM", *r)); n_pm += 1
    for d in sorted(eve):
        if d.weekday() != 3 and d.month not in (8, 11):      # Asia: skip Thu/Aug/Nov
            r = asia_day(eve[d])
            if r:
                day_trades[d].append(("ASIA", *r)); n_asia += 1
    for d in day_trades:
        day_trades[d].sort(key=lambda x: x[1])
    print(f"  {n_rej} rejection, {n_pm} PM, {n_asia} Asia signal-days")

    ALL = {"ORB", "REJ", "PM", "ASIA"}

    print(f"\n{'═'*108}")
    print(f"  POLICY GRID — combined account, 1c, 2022-2026 ({len(day_trades)} trading days)")
    print(f"  (worst = worst single day; DD = max EOD equity drawdown; Lucid limits: day $1,200 / total $2,000)")
    print(f"{'═'*108}")
    print(f"  {'Policy':<26} {'Net $':>10} {'$/wk':>7} {'WorstDay':>9} {'MaxDD':>8} "
          f"{'LoseStreak':>10}   trades taken")
    print(f"  {'─'*104}")

    grid = []
    for dll, ml in product([99999, 900, 700, 500], [99, 2, 1]):
        daily, taken = simulate(day_trades, dll, ml, ALL)
        m = metrics(daily)
        tag = f"DLL={'none' if dll > 9000 else dll} maxL={'∞' if ml == 99 else ml}"
        grid.append((tag, m, dict(taken)))
        print(f"  {tag:<26} {m['net']:>+10,} {m['wk']:>+7,} {m['worst']:>+9,} "
              f"{-m['dd']:>+8,} {m['streak']:>10}   {dict(taken)}")

    # Marginal strategy contribution under the leading policies
    print(f"\n  ── Marginal contribution (drop one strategy), policy DLL=700 maxL=2 ──")
    base_daily, _ = simulate(day_trades, 700, 2, ALL)
    base_m = metrics(base_daily)
    for drop in ["ORB", "REJ", "PM", "ASIA"]:
        daily, _ = simulate(day_trades, 700, 2, ALL - {drop})
        m = metrics(daily)
        print(f"  without {drop:<5}  net {m['net']:>+10,} (Δ{m['net']-base_m['net']:>+8,})   "
              f"worst {m['worst']:>+7,}   DD {-m['dd']:>+8,}")

    # Rejection performance conditioned on morning outcome (same day)
    print(f"\n  ── Rejection edge vs morning outcome (no policy, raw pairing) ──")
    rej_after = {"morning_loss": [], "morning_win": [], "no_morning": []}
    for d, lst in day_trades.items():
        orb = [x for x in lst if x[0] == "ORB"]
        rej = [x for x in lst if x[0] == "REJ"]
        if not rej:
            continue
        if not orb:
            rej_after["no_morning"].append(rej[0][3])
        elif sum(x[3] for x in orb) < 0:
            rej_after["morning_loss"].append(rej[0][3])
        else:
            rej_after["morning_win"].append(rej[0][3])
    for k, v in rej_after.items():
        if not v:
            continue
        wins = [p for p in v if p > 0]
        gl = abs(sum(p for p in v if p <= 0))
        pf = round(sum(wins) / gl, 2) if gl else 99
        print(f"  {k:<14} N={len(v):>3}  PF={pf:>5}  net=${sum(v):>+8,.0f}  avg=${sum(v)/len(v):>+6,.0f}")

    # OOS-only view of the best policies
    print(f"\n  ── OOS 2025-2026 only ──")
    oos_trades = {d: v for d, v in day_trades.items() if d.year >= 2025}
    for dll, ml in [(99999, 99), (900, 99), (900, 2), (700, 2), (500, 1)]:
        daily, _ = simulate(oos_trades, dll, ml, ALL)
        m = metrics(daily)
        tag = f"DLL={'none' if dll > 9000 else dll} maxL={'∞' if ml == 99 else ml}"
        print(f"  {tag:<26} net {m['net']:>+10,}  $/wk {m['wk']:>+6,}  worst {m['worst']:>+7,}  "
              f"DD {-m['dd']:>+8,}  streak {m['streak']}")

    print(f"\n{'═'*108}\n  portfolio_policy done.\n{'═'*108}")
