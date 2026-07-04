"""
brain/research/exit_management.py

Exit-management lab for the morning NQ ORB (strongest edge, OOS PF 5.42).
Entries are IDENTICAL across variants (real Backtester); only exits differ.
Pyramiding disabled everywhere to isolate the exit effect.

Variants (funded base = fixed 3R target, 27pt eff stop):
  base_3R      — as-is fixed 3R
  tgt_4R/5R    — wider fixed targets
  be_1R        — stop -> breakeven after price touches +1R (target 3R)
  be_1R_5R     — BE at 1R + 5R target (protected runner)
  trail_2R     — after +2R, stop trails 1R behind the extreme (no fixed cap: 8R)
  be+trail     — BE at 1R, then trail 1R behind extreme after +2R (8R cap)

Stop/trail updates computed from a bar are applied AFTER that bar is processed
(no intrabar look-ahead).

IS: 2022-2024  |  OOS: 2025-Jun 2026
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config
from backtest import Backtester, load_csv
from datetime import date

DATA      = "data/nq_full.csv"
IS_YEARS  = [2022, 2023, 2024]
OOS_YEARS = [2025, 2026]


class ExitLab(Backtester):
    MODE   = "base"    # be1r | trail | be_trail  (any combo via flags below)
    BE_1R  = False
    TRAIL  = False     # after +2R, trail 1R behind extreme
    TGT_RR = None      # override target multiple (None = signal's own)

    def check_exit(self, bar, ts, force=False):
        p = self.open_position
        if p is not None and self.TGT_RR and not p.get("_tgt_adj"):
            sd = p["orig_stop_dist"]
            p["target"] = (p["entry"] + sd * self.TGT_RR if p["dir"] == "long"
                           else p["entry"] - sd * self.TGT_RR)
            p["_tgt_adj"] = True

        super().check_exit(bar, ts, force)

        # Post-bar stop maintenance (affects NEXT bar only)
        p = self.open_position
        if p is None:
            return
        is_long = p["dir"] == "long"
        entry, sd = p["entry"], p["orig_stop_dist"]

        if self.BE_1R and not p.get("_be_done"):
            hit_1r = bar["high"] >= entry + sd if is_long else bar["low"] <= entry - sd
            if hit_1r:
                p["stop"] = entry
                p["_be_done"] = True

        if self.TRAIL:
            if is_long:
                p["_ext"] = max(p.get("_ext", entry), bar["high"])
                if p["_ext"] >= entry + 2 * sd:
                    p["stop"] = max(p["stop"], p["_ext"] - sd)
            else:
                p["_ext"] = min(p.get("_ext", entry), bar["low"])
                if p["_ext"] <= entry - 2 * sd:
                    p["stop"] = min(p["stop"], p["_ext"] + sd)


def run_year(bars, year, **flags):
    ystart, yend = date(year, 1, 1), date(year + 1, 1, 1)
    prior  = [b for b in bars if b["timestamp"].date() < ystart]
    subset = [b for b in bars if ystart <= b["timestamp"].date() < yend]
    if not subset:
        return []
    warmup = ExitLab()
    for k, v in flags.items():
        setattr(warmup, k, v)
    warmup.run(prior, silent=True)
    bt = ExitLab()
    for k, v in flags.items():
        setattr(bt, k, v)
    bt._last_close         = warmup._last_close
    bt.regime.daily_ranges = list(warmup.regime.daily_ranges)
    bt.or_volume_history   = list(warmup.or_volume_history)
    bt.prev_day_mode       = warmup.prev_day_mode
    bt.run(subset, silent=True)
    return bt.bank.trade_log


def run_years(bars, years, **flags):
    out = []
    for y in years:
        out.extend(run_year(bars, y, **flags))
    return out


def stats(trades):
    t = [x for x in trades if x.get("mode") == "breakout"]
    if not t:
        return {"n": 0, "wr": 0, "pf": 0, "net": 0, "avg": 0}
    w  = [x for x in t if x["pnl"] > 0]
    gl = abs(sum(x["pnl"] for x in t if x["pnl"] <= 0))
    net = sum(x["pnl"] for x in t)
    return {"n": len(t), "wr": len(w) / len(t),
            "pf": round(sum(x["pnl"] for x in w) / gl, 3) if gl else 99.0,
            "net": round(net), "avg": round(net / len(t))}


def line(tag, s_is, s_oos, ref_oos):
    d = s_oos["net"] - ref_oos["net"]
    print(f"  {tag:<22} IS: N={s_is['n']:>3} WR={s_is['wr']:>4.0%} PF={s_is['pf']:>6.3f} "
          f"${s_is['net']:>+8,.0f}  | OOS: N={s_oos['n']:>3} WR={s_oos['wr']:>4.0%} "
          f"PF={s_oos['pf']:>6.3f} ${s_oos['net']:>+8,.0f}  ΔOOS={d:>+7,.0f}")


if __name__ == "__main__":
    # Isolate exits: no pyramid anywhere (it moves stops on its own)
    config.PYRAMIDING_ENABLED   = False
    config.PARTIAL_EXIT_ENABLED = False

    print(f"\n{'═'*104}")
    print(f"  MORNING ORB EXIT MANAGEMENT  (identical entries, pyramiding OFF, funded 3R base)")
    print(f"  IS: 2022-2024  |  OOS: 2025-Jun 2026")
    print(f"{'═'*104}\n")

    print("  Loading bars...", flush=True)
    bars = load_csv(DATA)

    variants = [
        ("base_3R",   dict()),
        ("tgt_4R",    dict(TGT_RR=4.0)),
        ("tgt_5R",    dict(TGT_RR=5.0)),
        ("be_1R",     dict(BE_1R=True)),
        ("be_1R_5R",  dict(BE_1R=True, TGT_RR=5.0)),
        ("trail_2R",  dict(TRAIL=True, TGT_RR=8.0)),
        ("be+trail",  dict(BE_1R=True, TRAIL=True, TGT_RR=8.0)),
    ]

    ref_oos = None
    for tag, flags in variants:
        s_is  = stats(run_years(bars, IS_YEARS,  **flags))
        s_oos = stats(run_years(bars, OOS_YEARS, **flags))
        if ref_oos is None:
            ref_oos = s_oos
        line(tag, s_is, s_oos, ref_oos)

    print(f"\n{'═'*104}\n  exit_management done.\n{'═'*104}")
