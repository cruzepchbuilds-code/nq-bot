# Signal Strength Recalibration Report

**Date:** 2026-06-14

## Critical Finding

The current signal strength scorer is **inversely correlated** with trade quality:

| Score | WR | PF | Current Sizing | Problem |
|-------|----|----|---------------|--------|
| 60-69 | 51.7% | 2.03 | 1 contract | UNDERSIZED — best bucket |
| 70-79 | 36.5% | 1.09 | 2 contracts | OVERSIZED — worst bucket |
| 80-89 | 35.2% | 1.03 | 2 contracts | OVERSIZED — worst bucket |
| 90-100 | 45.5% | 1.58 | 3 contracts | Acceptable |

## Contract Sizing Scheme Comparison

| Scheme | Net P&L | WR | PF |
|--------|---------|----|----||
| Current (60-74=1c, 75+=2c) | $+33,675 | 40.6% | 1.22 |
| Inverted (60-69=2c, 70-89=1c) | $+50,145 | 40.6% | 1.42 |
| Flat (all=1c) | $+27,185 | 40.6% | 1.29 |

## Recommended Action

1. **Short-term fix:** Invert contract sizing in `signal_strength.py`
2. **Medium-term:** Rebuild scorer from scratch using OOS-only component testing
3. **Validation required:** Test any scorer change on 2022-2024 OOS windows

## Root Cause

The scorer was calibrated on the same data used for strategy optimization (2024).
When a scoring function is optimized in-sample, it can learn noise patterns
that don't generalize — this appears to have happened here.
