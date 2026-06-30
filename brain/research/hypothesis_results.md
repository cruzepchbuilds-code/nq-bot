# Hypothesis Pipeline Results

**Date:** 2026-06-14
**Baseline:** PF 1.81 | WR 44.1% | Net $+66,775 | Trades 170

## Results

| ID | Name | Base PF | Test PF | ΔPF | ΔNet | ΔTrades | Verdict |
|----|----|---------|---------|-----|------|---------|--------|
| H12 | Tighter Stop 20pt (25pt fixed - 5pt buffer) with 3R target | 1.812 | 1.905 | +0.093 | $+4,970 | -1% | KEEP |
| H01 | High-Volume Exclusion Gate | 1.812 | 1.812 | +0.000 | $+0 | +0% | REJECT |
| H11 | Extended Pyramid Target (2.5R when pyramid fires) | 1.812 | 1.730 | -0.082 | $-12,260 | -21% | REJECT |
| H05 | Gap Dead Zone 45-55pt (tighter precision) | 1.812 | 1.680 | -0.132 | $-26,220 | -29% | REJECT |
| H04 | Volume Ratio Floor at 0.7x | 1.812 | 1.654 | -0.158 | $-25,285 | -26% | REJECT |
| H09 | Add July to Strong Months | 1.812 | 1.799 | -0.013 | $+495 | +0% | REJECT |
| H03 | OR Minimum 60pt (remove borderline 55-60pt) | 1.812 | 1.699 | -0.113 | $-11,500 | -8% | REJECT |
| H13 | Second Breakout Re-Entry (after target hit) | 1.812 | 1.722 | -0.090 | $-140 | +11% | REJECT |
| H07 | ATR-Adaptive OR Max Filter | 1.812 | 1.816 | +0.004 | $-4,285 | -9% | MARGINAL |
| H02 | Entry Cutoff at 10:15 ET | 1.812 | 1.812 | +0.000 | $+0 | +0% | REJECT |

## Untested (Require Code Changes)

- **H08**: Signal Score Inversion (60-69 = 2c, 70-89 = 1c) — Need to modify signal_strength.py contracts_for_score() or build scorer_v2.py.
- **H14**: Large OR + Small Gap Filter (counter-trend setups) — Need to add OR+gap interaction filter to strategy_us.py finalize_range().

## Post-Pipeline Empirical Tests (4-yr OOS, fresh per-year bankrolls)

### H08 — Signal Score Inversion (60-69=2c, 70-89=1c)
**TESTED AND REJECTED.**
- 4-yr OOS: T=170, Net=+$52,000 vs baseline $66,775 → **ΔNet=-$14,775**
- Root cause: giving 2c to score-60-69 trades blocks the pyramid add-on
  (`PYRAMID_MAX_CONTRACTS=2`). Converts a 1c+pyramid setup (half-stop, full win)
  into a 2c-no-pyramid setup (full-stop, same win). The asymmetric risk/reward
  is destroyed. Static trade_memory analysis didn't model this interaction.
- Correct fix requires rebuilding scorer COMPONENTS (not flipping allocation):
  vol reward bracket needs to shift from 1.2-1.8x → 0.7-0.9x range.

### H01 — High-Volume Ceiling (1.5x)  
**TESTED AND REJECTED.**  
- 4-yr OOS: T=167 (-3 trades), Net=+$63,880 → **ΔNet=-$2,895**
- The 3 excluded spike-vol trades happened to be profitable (+$965/trade avg).
  Small sample (n=3) means noise dominates. Default remains 0.0 (disabled).

### H02b — LAST_ENTRY_TIME = '10:29' (surgical 10:30 exclusion)
**MARGINAL / REJECT for absolute returns.**
- 4-yr OOS: T=125 (-45 trades), PF=1.863 (+0.051), Net=+$50,980 → **ΔNet=-$15,795**
- Removes far more than the 12 bad 10:30-bar trades. Most removed trades are
  legitimate 10:29 entries that are profitable. Eval-mode only consideration.
- LAST_ENTRY_TIME is now a runtime config (not pre-computed at import), so this
  can be switched live without restarting Python.

### Infrastructure Change: LAST_ENTRY Runtime Fix (IMPLEMENTED)
- Moved `LAST_ENTRY` usage in backtest.py lines 264 and 395 from the
  module-level constant to `time(*map(int, config.LAST_ENTRY_TIME.split(":")))`.
- The module-level `LAST_ENTRY` constant is kept for external importers.
- hypothesis_pipeline.py can now test LAST_ENTRY_TIME overrides correctly.

### Infrastructure Change: BREAKOUT_MAX_OR_VOLUME_RATIO (ADDED, DISABLED)
- Added to config.py: `BREAKOUT_MAX_OR_VOLUME_RATIO = 0.0` (disabled).
- Gate logic added in backtest.py `try_enter()` after the existing floor gate.
- Empirically tested at 1.5x: REJECT (-$2,895 net). Remains 0.0.
