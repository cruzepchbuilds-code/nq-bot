"""
Three untested PnL growth candidates — OOS 2024-2026.

Idea 1: Skip Fridays
  OOS data: Friday WR 35.8%, PF 1.06 (n=67). Essentially breakeven drag.

Idea 2: 3R target (22pt stop, never tested — H12 only tested 20pt stop)
  Current: 22pt + 5pt buffer = 27pt eff stop, 2R target = 54pt.
  Test: same stop, 3R target = 81pt.

Idea 3: OR 65-75pt exclusion
  Edge discovery: OR 65-75pt bucket WR 37.8%, PF 1.15 (worst sub-bucket, n=45).
  Small (55-65) and large (75-110) both outperform. Carve out the dead middle.

Run: python3 brain/research/three_ideas_test.py data/nq_full.csv
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from datetime import date
import config
import strategies.strategy_us as su
from backtest import Backtester, load_csv

DATA_FILE = sys.argv[1] if len(sys.argv) > 1 else "data/nq_full.csv"
OOS_START = date(2024, 1, 1)
OOS_END   = date(2027, 1, 1)

# -- Monkey-patch run_silent / summary from walk_forward -----------------------
from walk_forward import _run_silent, _summary
Backtester.run_silent = _run_silent
Backtester.summary    = _summary


def run_oos(bars):
    """Run OOS window with current config/patches. Returns summary dict."""
    subset = [b for b in bars if OOS_START <= b["timestamp"].date() < OOS_END]
    bt = Backtester()
    bt.run_silent(subset)
    return bt.summary("test")


def collect_stats(s):
    return s["trades"], s["win_rate"], s["pf"] or 0.0, s["net"]


# -- Test harness --------------------------------------------------------------
print("Loading data ...", end=" ", flush=True)
all_bars = load_csv(DATA_FILE)
print(f"{len(all_bars):,} bars")

print("=" * 68)
print(f"  OOS GROWTH CANDIDATES — 2024-2026")
print("=" * 68)

# Baseline
print("Baseline ...            ", end=" ", flush=True)
bt_s  = run_oos(all_bars)
bt_t, bt_wr, bt_pf, bt_net = collect_stats(bt_s)
print(f"T={bt_t}  WR={bt_wr:.1%}  PF={bt_pf:.2f}  Net=${bt_net:+,.0f}")

# ── Idea 1: Skip Fridays ──────────────────────────────────────────────────────
orig_finalize = su.ORBStrategy.finalize_range

def _friday_skip(self):
    if self.day_of_week == 4:
        return False
    return orig_finalize(self)

print("Idea 1: Skip Fridays   ", end=" ", flush=True)
su.ORBStrategy.finalize_range = _friday_skip
i1_s = run_oos(all_bars)
su.ORBStrategy.finalize_range = orig_finalize
i1_t, i1_wr, i1_pf, i1_net = collect_stats(i1_s)
print(f"T={i1_t}  WR={i1_wr:.1%}  PF={i1_pf:.2f}  Net=${i1_net:+,.0f}")

# ── Idea 2: 3R Target ─────────────────────────────────────────────────────────
orig_rr = config.ORB_BREAKOUT_RR_TARGET
config.ORB_BREAKOUT_RR_TARGET = 3.0
print("Idea 2: 3R Target      ", end=" ", flush=True)
i2_s = run_oos(all_bars)
config.ORB_BREAKOUT_RR_TARGET = orig_rr
i2_t, i2_wr, i2_pf, i2_net = collect_stats(i2_s)
print(f"T={i2_t}  WR={i2_wr:.1%}  PF={i2_pf:.2f}  Net=${i2_net:+,.0f}")

# ── Idea 3: OR 65-75pt exclusion ─────────────────────────────────────────────
def _or_excl(self):
    result = orig_finalize(self)
    if result:
        size = self.or_high - self.or_low
        if 65.0 <= size < 75.0:
            return False
    return result

print("Idea 3: OR 65-75 excl  ", end=" ", flush=True)
su.ORBStrategy.finalize_range = _or_excl
i3_s = run_oos(all_bars)
su.ORBStrategy.finalize_range = orig_finalize
i3_t, i3_wr, i3_pf, i3_net = collect_stats(i3_s)
print(f"T={i3_t}  WR={i3_wr:.1%}  PF={i3_pf:.2f}  Net=${i3_net:+,.0f}")

# ── Summary table ─────────────────────────────────────────────────────────────
print()
print("=" * 68)
hdr = f"  {'':26}  {'T':>4}  {'WR':>6}  {'PF':>5}  {'Net':>10}  {'ΔNet':>10}  {'ΔPF':>6}"
print(hdr)
print("  " + "-" * 66)

rows = [
    ("Baseline (v8)",        bt_t, bt_wr, bt_pf, bt_net),
    ("1. Skip Fridays",      i1_t, i1_wr, i1_pf, i1_net),
    ("2. 3R Target (81pt)",  i2_t, i2_wr, i2_pf, i2_net),
    ("3. OR 65-75 Exclude",  i3_t, i3_wr, i3_pf, i3_net),
]

for name, t, wr, pf, net in rows:
    is_base = name.startswith("Baseline")
    d_net   = net - bt_net if not is_base else 0
    d_pf    = pf  - bt_pf  if not is_base else 0.0
    dn_str  = f"${d_net:+,.0f}" if not is_base else "—"
    dp_str  = f"{d_pf:+.3f}"   if not is_base else "—"
    print(f"  {name:26}  {t:>4}  {wr:>5.1%}  {pf:>5.2f}  ${net:>+9,.0f}  {dn_str:>10}  {dp_str:>6}")

print("=" * 68)
print("\nVerdicts:")
for name, t, wr, pf, net in rows[1:]:
    d_net = net - bt_net
    d_pf  = pf  - bt_pf
    if d_net > 1000 and d_pf > 0:
        verdict = "✅ KEEP"
    elif d_net > 0 and d_pf > 0:
        verdict = "~ MARGINAL"
    elif d_net > 0 and d_pf <= 0:
        verdict = "~ REVIEW (net+ but PF-)"
    else:
        verdict = "❌ REJECT"
    print(f"  {name}: {verdict}  (ΔNet ${d_net:+,.0f}, ΔPF {d_pf:+.3f})")
