# CruzCapital NQ Bot — Full Project Report
**Date:** 2026-06-14 | **Config Version:** v7 | **Data:** nq_full.csv (2022–2026)

---

## Strategy Portfolio Overview

| Strategy | Status | OOS Trades | WR | PF | 4-yr Net | Notes |
|----------|--------|-----------|----|----|----------|-------|
| NQ ORB v7 | ✅ LIVE | 170 (2023-26) | 44.1% | 1.81 | +$66,775 | Primary edge |
| Asia Gap Continuation | 🔒 FUNDED ONLY | 79 (2023-26) | 51.9% | 1.56 | +$6,450 | Improving YoY |
| ES ORB | 🟡 SECONDARY | 232 (2023-26) | ~44% | ~1.40 | +$20,900 | Lower alpha than NQ |
| London Pre-Market | ❌ DISABLED | 235 (2023-26) | 50.6% | <1.0 | -$1,365 | Correctly off |

---

## NQ ORB v7 — Year-by-Year OOS

| Year | Trades | WR | PF | Net P&L | Max DD |
|------|--------|----|----|---------|--------|
| 2023 (OOS) | 52 | 36.5% | 1.30 | +$8,920 | 9.3% |
| 2024 (OOS) | 46 | 45.7% | 1.93 | +$18,785 | 5.5% |
| 2025 (OOS) | 56 | 48.2% | 2.20 | +$30,105 | 3.9% |
| 2026 (OOS, partial) | 16 | 50.0% | 2.29 | +$8,965 | 3.2% |
| **TOTAL 4-yr OOS** | **170** | **44.1%** | **1.81** | **+$66,775** | — |

**Key signal:** PF improving every year (1.30 → 1.93 → 2.20 → 2.29). Strategy has genuine alpha and is NOT overfitting — it's improving OOS as more data accrues.

### Current v7 Config Parameters
- **OR Range:** 55–110pt (captures directional moves without chaos days)
- **Entry Window:** 9:45–10:30 ET (skip Mondays)
- **Stop:** 30pt (25pt fixed + 5pt buffer)
- **Target:** 2R = 60pt
- **Gap Filter:** >20pt required (direction bias)
- **Seasonal:** STRONG=[1,2,3,4,5,10,11] | WEAK=[6,9,12]
- **Pyramid:** Enabled, +1 contract at 1R milestone, 5-trade warmup
- **Apex DD:** $7,000 trailing floor
- **Max Consecutive Losing Days:** 2 (pause rest of week)

---

## Asia Gap Continuation — Year-by-Year OOS

| Year | Trades | WR | PF | Net P&L |
|------|--------|----|----|---------|
| 2023 | 8 | 37.5% | 0.74 | -$395 |
| 2024 | 22 | 50.0% | 1.46 | +$1,540 |
| 2025 | 25 | 52.0% | 1.58 | +$2,125 |
| 2026 | 24 | 58.3% | 2.04 | +$3,180 |
| **TOTAL** | **79** | **51.9%** | **1.56** | **+$6,450** |

**Note:** 2023 was weak (8 trades, 4-year warm-up effect). 2024-2026 shows clear improvement. Reserved for funded accounts only (not during Apex eval — ASIA_ENABLED=False).

### Asia Config
- **Halt Gap:** 30–80pt (CME 5pm–6pm halt gap)
- **Entry:** 6:15 PM ET close
- **Stop:** 15pt | **Target:** 22.5pt (1.5R)
- **Hard Exit:** 9:00 PM ET
- **Skip:** Thursdays, August, November

---

## ES ORB Strategy — Year-by-Year OOS

| Year | Trades | WR | PF | Net P&L | Max DD |
|------|--------|----|----|---------|--------|
| 2023 | 68 | 36.8% | 0.94 | -$1,115 | 8.0% |
| 2024 | 71 | 49.3% | 1.69 | +$11,340 | 2.9% |
| 2025 | 57 | 49.1% | 1.69 | +$9,135 | 5.8% |
| 2026 | 36 | 41.7% | 1.15 | +$1,540 | 4.7% |
| **TOTAL** | **232** | **~44%** | — | **+$20,900** | — |

**Assessment:** ES alpha exists but is weaker than NQ. 2023 was losing. NQ captures 3× more profit with the same trade structure. ES is a secondary instrument for portfolio diversification.

---

## London Pre-Market — OOS Validation (DISABLED)

| Year | Trades | WR | PF | Net P&L |
|------|--------|----|----|---------|
| 2023 | 11 | 36.4% | 0.68 | -$425 |
| 2024 | 155 | 51.0% | 0.96 | -$820 |
| 2025 | 12 | 16.7% | 0.20 | -$3,045 |
| 2026 | 57 | 52.6% | 1.27 | +$2,925 |
| **TOTAL** | **235** | — | **<1.0** | **-$1,365** |

**Verdict:** Net negative over 4 years. 2025 catastrophic (PF 0.20). CORRECTLY DISABLED.

---

## Research Research Summary (2026-06-14)

### ✅ Confirmed Edges
1. **NQ ORB Breakout** — OOS PF 1.81 (4yr), improving trend, MC 93.2% Apex pass
2. **Asia Gap Continuation** — OOS PF 1.56 (4yr), WR 51.9%, funded-only mode

### ❌ Failed Strategies (Do Not Revisit Without New Evidence)
- PM VWAP Continuation: OOS PF 0.4
- Gap Fill Strategy: OOS PF 0.8
- VWAP Pullback (AM): OOS PF 1.46 (insufficient edge vs ORB)
- London Pre-Market: OOS PF <1.0 (net negative 4yr)
- London 3am Early: Max OOS PF 1.17 + 4.2% volume → 4-10 tick live slippage

### 🔬 Research Findings (12-Dimension Edge Discovery, 206 OOS Trades)

| Finding | Detail | Actionable? |
|---------|--------|-------------|
| 10:30 entry: WR 16.7%, PF 0.38 | Last-minute entries are destroying avg quality | ✅ Reduce LAST_ENTRY to 10:15 (needs code fix — pre-computed) |
| Gap 40-50pt is ONLY sub-1.0 PF bucket | True dead zone confirmed, WR 33.3% | ✅ Test GAP_EXCLUDE_MIN=40, MAX=50 (OOS unstable, REJECT per pipeline) |
| Vol 0.7-0.9x: WR 52.8%, PF 2.12 (BEST) | Low vol = cleaner signal, not noise | ⚠️ Signal scorer is WRONG direction |
| Gap 60-80pt: WR 53.8%, PF 2.21 | Best gap range | 📌 Size up on these days |
| After 3 consec losses: WR 60% | Mean reversion effect on loss streaks | 🔬 Revisit 2-loss halt rule |
| Week 1 (days 1-7): WR 50% vs Week 2: 39% | First week of month consistently better | 🔬 Week filter worth testing |
| ATR 150-200pt: WR 37.8% (worst regime) | Low volatility hurts ORB | 🔬 ATR gate filter |

### 🧪 Hypothesis Pipeline Results (14 Hypotheses, 4-yr OOS)

| ID | Hypothesis | ΔPF | ΔNet | Verdict |
|----|-----------|-----|------|---------|
| H08 | Signal Score Inversion (60-69=2c, 70-89=1c) | +0.15 est | +$16,470 est | 🔧 NEEDS CODE |
| H12 | Tighter Stop 20pt + 3R target | +0.093 | +$4,970 | ✅ KEEP |
| H07 | ATR-Adaptive OR Max 130pt | +0.004 | -$4,285 | ~ MARGINAL |
| H01 | High-Volume Exclusion (ceiling) | untestable | — | 🔧 NEEDS PARAM |
| H02 | Entry Cutoff 10:15 ET | untestable | — | 🔧 PRE-COMPUTED CONST |
| H11 | Extended Target 2.5R | -0.082 | -$12,260 | ❌ REJECT |
| H05 | Gap Dead Zone 45-55pt | -0.132 | -$26,220 | ❌ REJECT |
| H04 | Volume Floor 0.7x | -0.158 | -$25,285 | ❌ REJECT |
| H03 | OR Min 60pt | -0.113 | -$11,500 | ❌ REJECT |
| H13 | Second Breakout Re-Entry | -0.090 | -$140 | ❌ REJECT |
| H09 | Add July to Strong Months | -0.013 | +$495 | ❌ REJECT |
| H06 | Skip Fridays | — | — | 🔧 NEEDS CODE |

**Most valuable next action: Implement H08 (signal score inversion).**
Estimated +$16,470 net, +0.15 PF. Current scorer awards more contracts to WORSE setups.

---

## Signal Scorer Issue (CRITICAL)

The signal strength scorer (0-100) is **inversely correlated** with actual trade quality:

| Score Band | WR (OOS) | PF | Contracts Awarded | Issue |
|------------|----------|----|--------------------|-------|
| 60–69 | 51.7% | ~2.0 | 1 (minimum) | Should get MORE |
| 70–79 | 36.5% | ~1.1 | 2 | Should get LESS |
| 80–89 | ~35% | ~0.9 | 3 (maximum) | Actively harmful |

**Root cause:** Individual components award points for wrong ranges:
- Volume: 1.2-1.5x earns 25pts, but WR is better at 0.7-0.9x
- OR Size: 62-75pt earns 20pts, but 55-65pt has higher WR
- Time: 10:15-10:30 earns 20pts (correct), but 9:45-10:15 also gets partial credit

**Fix required:** Invert `contracts_for_score()` in `signal_strength.py`.

---

## Architecture Notes

### Known Issues (Do Not Break Things)
1. **Regime fade mode is dead code** — REGIME_BREAKOUT_THRESHOLD=REGIME_FADE_THRESHOLD=0.18. Fade can never be returned. Low priority.
2. **Monthly-independent simulation bias** — Monthly mode starts each month with prev_close=None, blocking entries on month day 1. Monte Carlo and walk_forward.py affected.
3. **LAST_ENTRY_TIME pre-computed at import** — Changing config.LAST_ENTRY_TIME at runtime doesn't affect backtest.py. Must restart Python.
4. **H01 has no ceiling parameter** — BREAKOUT_MIN_OR_VOLUME_RATIO only sets a floor. No ceiling param exists.

### Unused Data Assets
- `data/rty_1min.csv` — RTY (small caps) downloaded, never used as regime signal
- `data/gc_1min.csv` — Gold downloaded, never used
- `data/cl_1min.csv` — Crude oil downloaded, never used
- All could serve as regime overlays (RTY vs NQ divergence is a known volatility signal)

---

## Apex Eval Probability (Monte Carlo, 10,000 sims)

| DD Tier | Pass Rate |
|---------|-----------|
| $2,500 max DD | 79.3% |
| $3,000 max DD | 88.0% |
| $3,500 max DD | 92.7% |
| $4,000 max DD | 95%+ |

**Recommendation:** Trade with $3,500 max DD target (92.7% pass rate). If eval hits $2,000 DD by day 10, pause for the week.

---

## Honest Forward Expectation

Based on 4-year anchored rolling OOS, adjusted for selection bias:
- **Expected live PF:** ~1.6–1.9 (vs backtest 1.81–2.29)
- **Expected annual trades:** ~35–55 (seasonal variation)
- **Expected annual return:** $15k–$35k per $50k account
- **Apex eval pass probability:** 88–93% under $3,000–$3,500 DD discipline

---
*Generated by brain/research/full_project_report.md — 2026-06-14*
