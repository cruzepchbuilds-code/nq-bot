# ES Walk-Forward Backtest Results

## Configuration

| Parameter | Value |
|-----------|-------|
| Symbol | ES |
| Point Value | $50/pt |
| Starting Balance | $50,000 |
| ORB_MIN_RANGE_POINTS | 5.0 |
| ORB_MAX_RANGE_POINTS | 30.0 |
| ORB_FIXED_STOP_POINTS | 7.0 |
| ORB_STOP_BUFFER_POINTS | 2.0 |
| Effective Stop | 9.0 pts |
| ORB_BREAKOUT_BUFFER_POINTS | 1.0 |
| ORB_BREAKOUT_RR_TARGET | 2.0 |
| APEX_TRAILING_DD | $7,000 |
| MAX_TOTAL_DRAWDOWN_PCT | 12% |

## Year-by-Year Walk-Forward Results
(Each year starts fresh at $50,000 balance)

| Year | Trades | WR | PF | Net P&L | MaxDD | Status |
|------|--------|----|----|---------|-------|--------|
| 2022 | 33 | 36% | 0.98 | $-165 | 7.7% | IS |
| 2023 | 29 | 34% | 0.77 | $-2,300 | 8.4% | IS |
| 2024 | 32 | 59% | 2.45 | $+9,502 | 3.0% | OOS |
| 2025 | 20 | 55% | 2.11 | $+5,025 | 2.0% | OOS |
| 2026 | 13 | 54% | 2.01 | $+3,060 | 2.0% | OOS |

## Combined Results

**IS Combined (2022-2023):**  PF 0.88 | Net $-2,465
**OOS Combined (2024-2026):**  PF 2.24 | Net $+17,588

## OOS Gate Check

**ES OOS gate (PF >= 1.40): PASS**  (OOS PF = 2.24)

## ES Statistics Summary

Calibrated from 2022-2026 ES 1-minute data (1,128 RTH trading days):

| Metric | Value |
|--------|-------|
| Mean OR size (15 min) | 16.61 pts |
| Median OR size | 14.00 pts |
| 25th pct OR | 10.00 pts |
| 75th pct OR | 21.25 pts |
| 90th pct OR | 28.25 pts |
| Mean daily range | 60.13 pts |
| Median daily range | 51.00 pts |

## Notes

- Breakout-only mode (VWAP pull, London, PM VWAP all disabled)
- Pyramiding disabled for clean baseline test
- Signal strength filter uses 1-contract baseline for below-threshold signals
  (NQ OR size thresholds in scorer don't apply to ES, but trades still execute at 1 contract)
- ES effective stop = 8 pts (6 fixed + 2 buffer) at $50/pt = $400/loss + commission
- Apex floor at $50k - $7k = $43k (allows ~17 consecutive max losses)
