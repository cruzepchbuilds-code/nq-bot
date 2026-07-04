# CruzCapital Research Log

**Last Updated:** 2026-06-14 22:51

---

## Confirmed Edges

### NQ ORB Breakout v7

- **Type:** Opening Range Breakout — trend-following intraday
- **OOS PF:** 2.14 | WR: 47.2% | Trades: 72
- **Evidence:** OOS 2025-26: PF 2.14, WR 47.2%, 72 trades. Improving YoY. MC pass 93.2%.
- **Key Parameters:** {"or_range": "55-110pt", "breakout_buffer": "4pt", "gap_filter": "20pt", "stop": "30pt", "target": "60pt (2R)", "entry_window": "9:45-10:30 ET", "skip_mondays": true, "weak_months": [6, 9, 12], "strong_months": [1, 2, 3, 4, 5, 10, 11]}

### Asia Gap Continuation (CME halt)

- **Type:** Gap continuation — overnight institutional positioning
- **OOS PF:** 1.8 | WR: 56.0% | Trades: 77
- **Evidence:** OOS 2024-26: PF 1.80, WR 56%, 77 trades. Improving YoY: 1.42→1.82→2.31.
- **Key Parameters:** {"halt_gap_range": "30-80pt", "entry_time": "18:15 ET", "stop": "15pt", "target": "22.5pt (1.5R)", "hard_exit": "21:00 ET", "skip_thursdays": true, "weak_months": [8, 11]}

---

## Failed Strategies (Do Not Revisit Without New Evidence)

| Strategy | Config | Results | Why Failed |
|----------|--------|---------|------------|
| PM VWAP Continuation | 12:00-14:30 ET, VWAP touch + direction b | {'oos_net': -11500, 'oos_pf':  | Strong negative OOS P&L, cascades halts |
| Gap Fill Strategy | Large gap days, fade toward prior close | {'oos_net': -4400, 'oos_pf': 0 | Consistent OOS drag across all years |
| CL 9:00 ORB (native sweep) | 1008 cfgs: OR 5/10/15m, stop 0.10-0.50, 1.5-3R, cut 10:30/11:00, flat 11:30-14:00 | 0/1008 cfgs IS PF>1.0 (max 0.95); frictionless IS max 1.07; best cfg net -$15.7k | No gross edge — OR breaks 99% of days (zero selectivity). Wed/EIA sign-flips IS→OOS (1.04→0.80). cl_orb.py 2026-07-03. Ledger item CLOSED |
| VWAP Pullback (AM) | Second trade after ORB, VWAP touch | {'oos_net': -1400, 'oos_pf': 1 | Redundant to ORB, insufficient independent edge |
| London/NY Overlap 8am-9:25am | Range classification, entry at 9:05 | {'oos_net': -2900, 'oos_pf': 1 | Below PF threshold, unstable YoY |
| London Pre-Market 3am-5am | ORB at 3:00-3:15 ET, exit 5am | {'best_oos_pf': 1.17, 'volume_ | Max OOS PF 1.17 across all configs. Volume 4.2% of |

---

## Key Research Discoveries

### Signal Strength Scorer is Inverted

**Finding:** The signal strength scorer (0-100) is inversely correlated with actual trade quality. Score 60-69: WR 51.7%, PF 2.03. Score 70-79: WR 36.5%, PF 1.09. The system currently sizes UP on exactly the wrong trades.

**Evidence:** trade_memory.csv (286 trades, 2022-2026), insights.md analysis

**Action:** Recalibrate scorer. Short-term: invert sizing (60-69=2c, 70-89=1c). Long-term: rebuild scorer from OOS-only component testing.

### Monthly-Independent Simulation Bias

**Finding:** The walk_forward.py and monte_carlo.py monthly-independent mode starts each month with prev_close=None, blocking all entries on day 1 of each month (neutral gap). April 2025: 0 trades in monthly mode vs 1 trade in continuous mode. April 2025 was the Trump tariff shock month (ORs 140-352pt on most days; only 4 days tradeable).

**Evidence:** Direct comparison: Backtester() on April 2025 alone = 0 trades; continuous from Jan 2025 = 1 trade on April 1.

**Action:** Fix monthly-independent simulation by seeding prev_close from prior month's last bar.

### April 2025 Tariff Shock — Zero Trading Month

**Finding:** April 2025 had 25 trading days but 0 qualifying ORB setups in monthly-independent mode. Root cause: Trump 'Liberation Day' tariffs (April 2) caused NQ OR of 140-352pt on 17/21 non-Monday days. OR_MAX filter of 110pt correctly excluded these chaos days. Only 4 days (Apr 1, 16, 25, 29) had OR in 55-110pt range.

**Evidence:** bar count analysis: 28,977 bars, 25 trading days. OR analysis per day in April 2025.

**Action:** No action needed — OR_MAX filter working correctly. Document as expected behavior during high-volatility events.

### Friday Day-of-Week is Weakest Non-Monday

**Finding:** Friday WR 35.8% (n=67), PF 1.06 — the weakest non-Monday day. Monday is skipped. Friday is borderline: PF 1.06 barely above 1.0. Skipping Fridays would remove ~20% of trades but improve average quality.

**Evidence:** insights.md Day of Week table (286 trades, 2022-2026)

**Action:** Test Friday skip hypothesis. Expected: +0.08 PF improvement, -20% trade count.

### Gap Dead Zone 40-60pt is Unstable Across Years

**Finding:** Gap 40-60pt has WR 32.5% (n=40) in aggregate, but excluding these gaps was tested and rejected: 2025 OOS improved (PF 2.22) but 2026 OOS collapsed (PF 1.17). The dead zone pattern is real but not consistent across years.

**Evidence:** improvement_results.md Improvement #4. Two-year OOS instability confirmed.

**Action:** Do not apply hard exclusion. Consider single-contract sizing for gap 40-60pt days.

### Consecutive Losing Days Rule is Optimal at 2

**Finding:** Testing MAX_CONSECUTIVE_LOSING_DAYS at 3 (PF 1.63) and 999 (PF 1.68) both performed worse than the current setting of 2 (PF 1.73). The bankroll protection of sitting out after 2 losing days outweighs the marginal WR improvement from trading.

**Evidence:** improvement_results.md Improvement #5

**Action:** Keep MAX_CONSECUTIVE_LOSING_DAYS=2. Do not change.

### Volume Ratio Gate: Floor Works, Ceiling Doesn't

**Finding:** Vol ratio floor at 0.8x: flat PF (1.73). Vol ratio floor at 1.0x: worse (-0.02). BUT: vol ratio 1.5x+ WR 27.3% (n=11). A CEILING (exclude high vol) may work even though a floor didn't. High-volume OR bars are often news/reversal events.

**Evidence:** improvement_results.md Improvement #7, trade_memory.csv volume analysis

**Action:** Test: exclude trades where vol_ratio > 1.5x. Hypothesis H01 in pipeline.

### Regime Detector Fade Mode is Dead Code

**Finding:** REGIME_BREAKOUT_THRESHOLD = REGIME_FADE_THRESHOLD = 0.18. Equal thresholds mean 'fade' is never returned by classify(). The fade strategy code path is permanently inaccessible in current configuration.

**Evidence:** regime.py classify() function analysis

**Action:** Fix threshold asymmetry: breakout >= 0.25, fade < 0.15, skip < 0.08. Then test fade strategy OOS before enabling.

---

## Tested Hypotheses

_No hypotheses tested yet. Run hypothesis_pipeline.py._
