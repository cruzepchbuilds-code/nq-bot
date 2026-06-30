# CruzCapital — Full Project Audit
**Conducted by:** Quantitative Research Division  
**Date:** 2026-06-14  
**Scope:** Complete codebase, data pipeline, strategies, validation, risk systems  
**Status:** HEAD OF RESEARCH — AUTHORITATIVE DOCUMENT  

---

## 1. Executive Summary

CruzCapital NQ Bot is a systematic intraday day-trading system targeting NQ futures via an
Opening Range Breakout (ORB) strategy. The system is purpose-built for the Apex prop-firm
evaluation framework ($50k account, trailing DD constraint). As of v7, the primary strategy
shows genuine OOS alpha (PF 2.14, WR 47.2%, 2025-26), but the research infrastructure has
several critical gaps that must be resolved before live capital deployment.

**Verdict on current system:** Edge is real but partially inflated by OOS parameter selection.
The inverted signal strength scorer is a significant live risk. Live execution infrastructure
is entirely absent.

---

## 2. Architecture Overview

```
Config Layer:     config.py (NQ), es_config.py (ES)
Strategy Layer:   strategy_us.py (ORB + 4 disabled), strategy_asia.py, strategy_london.py
Risk Layer:       bankroll.py, regime.py, signal_strength.py
Simulation Layer: backtest.py (bar-by-bar engine)
Validation Layer: walk_forward.py, monte_carlo.py, combined_mc.py
Intelligence:     brain/pattern_engine.py, brain/self_optimizer.py
Data:             data/*.csv (Databento CME 1-min OHLCV)
Live (stubs):     live/execution.py, live/paper_trading.py, live/telegram_alerts.py
```

---

## 3. Strategy Inventory

| Strategy | Status | OOS PF | Alpha Quality |
|----------|--------|--------|---------------|
| NQ ORB Breakout | ACTIVE | 2.14 | GENUINE — core driver |
| Asia Gap Continuation | Disabled (funded) | 1.80 | REAL — structural CME halt edge |
| ES ORB Breakout | Separate system | 1.61 | MODERATE |
| London/NY Overlap 8am | Disabled | 1.14 | BELOW THRESHOLD |
| London pre-market 3am | Never built | 1.17 | BELOW THRESHOLD |
| VWAP Pullback | Disabled | PF 1.46, -$1.4k | REDUNDANT |
| PM VWAP | Disabled | -$11.5k | STRONG NEGATIVE |
| Gap Fill | Disabled | -$4.4k | NEGATIVE |
| Fade | Dead code | N/A | NEVER TESTED |
| Second Breakout | Disabled | N/A | UNTESTED |

---

## 4. Critical Issues Identified

### ISSUE 1 — CRITICAL: Signal Strength Scorer is Inverted
The signal strength scorer (0-100) is inversely correlated with actual trade quality:
- Score 60-69: WR **51.7%**, PF 2.03 (BEST performance)
- Score 70-79: WR **36.5%**, PF 1.09 (WORST performance)
- Score 80-89: WR **35.2%**, PF 1.03

The current system sizes UP on the weakest trade quality. A score of 85 gets 2+ contracts,
but an 85-score trade has WR 35% — worse than the 65-score trade at 52%.

Root cause: scorer was built from in-sample patterns that do not generalize. The component
weights (time window, gap alignment, volume ratio, OR size) may individually have merit,
but their combined scaling produces the wrong rank ordering.

**Immediate action required: Recalibrate scorer on OOS-only data or invert sizing logic.**

### ISSUE 2 — CRITICAL: Monthly-Independent Simulation Bias
The walk_forward.py and monte_carlo.py run each month as an independent simulation, which
means each month starts with prev_close=None. This causes all gaps on the FIRST TRADEABLE
DAY of each month to be classified as "neutral" (gap_dir=0), blocking all entries on day 1.

Confirmed impact:
- April 2025: 0 trades in monthly-independent mode vs 1 trade in continuous mode
- The "0 trades" is not a data gap — data exists (28,977 bars, 25 trading days)
- April 2025 was the Trump tariff shock month: OR ranged 140-352pt on most days
- The 4 tradeable days (OR 55-110pt) produced 0 monthly-independent trades

Implication: The OOS trade pool of 67-72 trades systematically underestimates actual
activity. Months starting with strong directional gaps are underrepresented.

**Action required: Fix by seeding monthly simulations with prior month's last close.**

### ISSUE 3 — HIGH: Single In-Sample Year
The walk-forward uses 2024 as in-sample and 2025-2026 as OOS. This is ONE training window
tested on TWO OOS years. The strategy has never been tested on the 2022-2023 period, which
is available in data/nq_full.csv. Three additional OOS windows are available and unused:
- IS=2022, OOS=2023
- IS=2022-23, OOS=2024
- IS=2022-24, OOS=2025

**Action required: Expand to anchored rolling walk-forward using nq_full.csv.**

### ISSUE 4 — HIGH: OOS Parameter Selection
Key parameters were explicitly selected by optimizing on OOS data:
- LAST_ENTRY_TIME = 10:30 → selected by sweep of 7 values on 2025-26 OOS
- STRONG_MONTHS/WEAK_MONTHS → April/May moved based on 2025-26 OOS performance
- ORB_MAX_RANGE_POINTS = 110 → selected from sensitivity test on OOS

The OOS PF of 2.14 is partially inflated by these selections. True forward performance
will likely be lower. Conservative estimate: adjust OOS PF by ~0.15-0.25 for selection bias.

### ISSUE 5 — HIGH: Live Execution Infrastructure is 100% Missing
All live trading code is placeholder stubs:
- live/execution.py: no broker API integration
- live/paper_trading.py: no real-time bar aggregation
- live/morning_check.py: checklist stub only
- dashboard/server.py: placeholder

The slippage assumption (2 ticks = 0.5pt per side) has never been validated in live trading.
Real breakout slippage on fast NQ moves can be 4-10 ticks. At 6-tick slippage, a 30pt stop
trade loses 1.5pt of edge per trade (5% of P&L at current win/loss ratios).

### ISSUE 6 — MEDIUM: Fade Strategy is Structurally Dead Code
The RegimeDetector has REGIME_BREAKOUT_THRESHOLD = REGIME_FADE_THRESHOLD = 0.18.
Since the comparison is >= for breakout and <= for fade, and they're equal, fade can
NEVER be returned. The fade code path in strategy_us.py and backtest.py is dead code.

### ISSUE 7 — MEDIUM: OR_MIN Fragility
Sensitivity test shows ORB_MIN_RANGE_POINTS is "FRAGILE+": raising it 15% (55→63.25pt)
drops OOS PF by 0.33, beyond the fragility threshold. This means the 55pt floor is at the
edge of what works — days with OR 55-65pt are marginal quality.

### ISSUE 8 — LOW: Missing November Reclassification
November WR cited as 52.6% "strong" in one table but the pattern engine shows November
only qualifies marginally. Monthly sample sizes are 20-25 trades — not statistically
meaningful enough to justify strong/weak classification with confidence.

---

## 5. Data Assets

| File | Coverage | Bars | Quality |
|------|----------|------|---------|
| data/nq_1min.csv | 2024-2026 | 857K | Good |
| data/nq_full.csv | 2022-2026 | 1.56M | Good — underutilized |
| data/es_1min.csv | 2022-2026 | 1.56M | Good |
| data/rty_1min.csv | 2022-2026 | 1.49M | Available — unused |
| data/cl_1min.csv | 2022-2026 | 1.46M | Available — unused |
| data/gc_1min.csv | 2022-2026 | 51K | Thin coverage |

Critical missing:
- VIX daily data (regime context)
- Economic calendar (FOMC, CPI, NFP — high-impact event filter)
- Pre-market gap data (prior 4pm RTH close)
- Volume profile / DOM data

---

## 6. Validation Quality Assessment

| Method | Current State | Quality |
|--------|--------------|---------|
| Walk-forward | Single IS/OOS split (2024/2025-26) | INADEQUATE |
| Monte Carlo | 10,000 sims on OOS trade pool | GOOD methodology, biased pool |
| Parameter stability | ±15% sweep on key params | PARTIAL |
| Multi-instrument | NQ+ES combined MC | GOOD |
| Rolling windows | NOT IMPLEMENTED | MISSING |
| Slippage stress test | Fixed 2-tick assumption | NEEDS VALIDATION |
| Trade pool integrity | Affected by monthly-independent bias | NEEDS FIX |

---

## 7. Risk System Assessment

| Control | Status | Quality |
|---------|--------|---------|
| Daily loss limit (1.5%) | Active | Good |
| Daily profit lock (3%) | Active | Good |
| Max trades/day (2) | Active | Good |
| Max losses/day (2) | Active | Good |
| Consecutive losing days (2) | Active | Good — tested and confirmed optimal |
| Weekly loss limit (5%) | Active | Good |
| Apex trailing DD | Active | Good |
| Recovery mode (50% size at -5% DD) | Active | Good |
| Max total DD (12%) | Active | Good |
| Signal strength gate | Active | INVERTED — see Issue 1 |
| Regime calendar | Active | FRAGILE statistical base |

---

## 8. Brain / Self-Improvement System

| Component | Status | Quality |
|-----------|--------|---------|
| pattern_engine.py | Functional | Good — 9 categories, Wilson CI |
| self_optimizer.py | Functional | Good — suggests changes, never auto-applies |
| trade_memory.csv | 286 trades | Good — complete metadata |
| insights.md | Auto-generated | Good |
| trade_dissection.md | Manual | Good |
| asia_research.md | Complete | Good |
| session_analysis.md | Complete | Good |
| improvement_results.md | Complete | Good |

Missing:
- Research memory for failed strategies (what was tried and failed)
- Rolling hypothesis tracking
- Automated OOS validation pipeline

---

## 9. Prioritized Action Plan

| Priority | Action | Expected Impact |
|----------|--------|----------------|
| 1 | Fix signal strength scorer inversion | +0.15-0.25 PF, -5pp DD |
| 2 | Fix monthly-independent simulation bias | Accurate MC probabilities |
| 3 | Expand to rolling walk-forward (2022-2026) | Higher statistical confidence |
| 4 | Fix regime detector threshold asymmetry | Enable fade testing |
| 5 | Add economic calendar filter (FOMC/CPI) | Reduce chaotic-day losses |
| 6 | High-volume exclusion gate (vol_ratio > 1.5x) | -2pp DD, +0.05 PF |
| 7 | Implement live execution (broker API) | Critical for deployment |
| 8 | Add trend bias filter (20-day EMA direction) | +0.05-0.12 PF |
| 9 | Multi-instrument confirmation (NQ+ES) | +0.08-0.15 PF |
| 10 | Remove dead code (5 disabled strategies) | Maintenance clarity |

---

## 10. Honest Assessment

The system has a genuine edge. The NQ ORB strategy with v7 parameters produces real
positive expectancy: OOS PF 2.14 across 72 trades (2025-26). The Asia gap continuation
adds uncorrelated alpha (OOS PF 1.80, improving YoY).

However:
- The OOS PF is inflated ~10-15% by parameter selection on OOS data
- The signal scorer actively misdirects position sizing
- The validation framework uses only 2 years of OOS data
- Zero live execution infrastructure exists

Adjusted forward expectation: OOS PF ~1.7-1.9 in true forward trading (before slippage
validation). With 2-4 tick additional real slippage, forward PF ~1.5-1.7.

This is still tradeable and worth deploying on Apex eval. But the research improvements
above are expected to add meaningful, durable alpha before live capital scales.

---

_Generated: 2026-06-14 | Version: 1.0 | Status: AUTHORITATIVE_
