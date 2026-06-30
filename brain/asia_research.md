# Asia Session Deep Research (6:00pm – 9:00pm ET)

Generated: 2026-06-14 | Data: 2022-01-03 to 2026-06-11
IS: 2022-2023 | OOS: 2024-2026 | Viability: OOS PF ≥ 1.3, n ≥ 30


## Session Overview

| Metric | Value |
| --- | --- |
| Window | 6:00pm – 9:00pm ET |
| Gap definition | Same-day 4pm close → 6pm open |
| Trade cost | $25/rt |
| Point value | $20/pt |


## All Strategy Results (best config per strategy)

| Strategy | Config | n_all | WR | PF_all | n_oos | WR_oos | PF_oos | Net_oos | Viable |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S11 | prox=10 stop=20 rr=1.5 | 4 | 75.0% | 6.51 | 3 | 100.0% | 9.99 | $+1,725 | FAIL |
| S20 | trend+seasonal stop=15 rr=1.5 | 16 | 75.0% | 3.92 | 12 | 83.3% | 6.54 | $+3,600 | FAIL |
| S1 | gap=50-80 +15m stop=25 rr=1.5 thu=True | 49 | 65.3% | 2.83 | 35 | 71.4% | 3.54 | $+12,270 | ✓ PASS |
| S18 | dir=short stop=20 rr=2.0 | 48 | 52.1% | 1.97 | 34 | 61.8% | 3.21 | $+10,750 | ✓ PASS |
| S12 | trend_align stop=15 rr=1.5 | 46 | 58.7% | 1.86 | 35 | 62.9% | 2.21 | $+5,125 | ✓ PASS |
| S9 | gap=30-80 stop=25 rr=2.5 | 160 | 46.9% | 1.63 | 112 | 50.9% | 1.95 | $+25,755 | ✓ PASS |
| S15 | stop=15 rr=2.0 | 126 | 42.9% | 1.32 | 84 | 51.2% | 1.88 | $+11,190 | ✓ PASS |
| S17 | months=Jan/Feb/Mar | 41 | 51.2% | 1.37 | 29 | 58.6% | 1.85 | $+3,325 | FAIL |
| S19 | months=strong dows=tue_wed_fri stop=15 rr=1.5 | 27 | 55.6% | 1.63 | 19 | 57.9% | 1.80 | $+2,075 | FAIL |
| S10 | atr>=1.0x stop=20 rr=1.5 | 52 | 50.0% | 1.33 | 36 | 55.6% | 1.73 | $+4,475 | ✓ PASS |
| S6 | stop=12 rr=2.0 +0m | 911 | 38.7% | 1.02 | 499 | 41.3% | 1.14 | $+10,630 | FAIL |
| S4 | thr=30 stop=20 rr=2.0 | 388 | 38.9% | 0.94 | 280 | 40.4% | 1.04 | $+2,545 | FAIL |
| S13 | gap>80 stop=25 rr=1.5 | 65 | 49.2% | 1.19 | 43 | 44.2% | 1.03 | $+315 | FAIL |
| S3 | stop=25 rr=2.0 | 895 | 43.6% | 0.97 | 494 | 40.9% | 1.02 | $+2,215 | FAIL |
| S8 | thr=40 stop=15 rr=1.5 | 121 | 40.5% | 0.88 | 94 | 42.6% | 0.97 | $-550 | FAIL |
| S2 | stop=20 rr=2.0 | 768 | 40.9% | 0.91 | 394 | 39.6% | 0.96 | $-3,570 | FAIL |
| S5 | stop=20 rr=2.0 | 911 | 43.9% | 0.96 | 499 | 42.7% | 0.95 | $-4,540 | FAIL |
| S16 | comp<0.7x stop=20 rr=2.0 | 305 | 40.7% | 0.87 | 152 | 38.2% | 0.93 | $-2,140 | FAIL |
| S7 | thr=20 stop=15 rr=2.0 | 683 | 38.8% | 0.85 | 426 | 39.2% | 0.89 | $-8,505 | FAIL |
| S14 | ema=3/10 stop=20 rr=2.0 | 854 | 34.2% | 0.58 | 468 | 31.0% | 0.54 | $-56,415 | FAIL |

## S1 — Top 25 Configurations

| Config | n_oos | WR_oos | PF_oos | Net_oos | V |
| --- | --- | --- | --- | --- | --- |
| gap=50-80 +15m stop=25 rr=1.5 thu=True | 35 | 71.4% | 3.54 | $+12,270 | ✓ |
| gap=40-80 +15m stop=25 rr=1.5 thu=True | 63 | 68.3% | 2.91 | $+18,680 | ✓ |
| gap=40-70 +15m stop=25 rr=1.5 thu=True | 53 | 67.9% | 2.85 | $+15,180 | ✓ |
| gap=50-80 +30m stop=12 rr=3.0 thu=False | 24 | 50.0% | 2.81 | $+5,375 | ✗ |
| gap=50-80 +15m stop=25 rr=1.5 thu=False | 24 | 66.7% | 2.80 | $+6,795 | ✗ |
| gap=50-80 +15m stop=30 rr=1.5 thu=True | 35 | 65.7% | 2.72 | $+11,090 | ✓ |
| gap=50-80 +30m stop=12 rr=2.5 thu=False | 24 | 54.2% | 2.56 | $+4,560 | ✗ |
| gap=50-80 +30m stop=12 rr=1.5 thu=False | 24 | 66.7% | 2.53 | $+3,240 | ✗ |
| gap=40-80 +15m stop=30 rr=1.5 thu=True | 63 | 65.1% | 2.49 | $+17,995 | ✓ |
| gap=40-80 +15m stop=20 rr=2.0 thu=True | 63 | 58.7% | 2.45 | $+15,105 | ✓ |
| gap=40-70 +15m stop=25 rr=1.5 thu=False | 43 | 65.1% | 2.45 | $+10,430 | ✓ |
| gap=50-80 +30m stop=15 rr=3.0 thu=False | 24 | 45.8% | 2.44 | $+5,675 | ✗ |
| gap=40-70 +15m stop=20 rr=2.0 thu=True | 53 | 58.5% | 2.43 | $+12,395 | ✓ |
| gap=40-80 +15m stop=25 rr=1.5 thu=False | 48 | 64.6% | 2.40 | $+11,555 | ✓ |
| gap=50-80 +30m stop=12 rr=2.0 thu=False | 24 | 58.3% | 2.40 | $+3,720 | ✗ |
| gap=50-80 +30m stop=15 rr=2.5 thu=False | 24 | 50.0% | 2.40 | $+5,075 | ✗ |
| gap=40-70 +15m stop=30 rr=1.5 thu=True | 53 | 64.2% | 2.38 | $+14,085 | ✓ |
| gap=40-70 +15m stop=25 rr=2.5 thu=True | 53 | 56.6% | 2.35 | $+14,510 | ✓ |
| gap=40-80 +15m stop=25 rr=2.5 thu=True | 63 | 55.6% | 2.32 | $+17,225 | ✓ |
| gap=40-80 +30m stop=25 rr=3.0 thu=True | 63 | 54.0% | 2.31 | $+17,735 | ✓ |
| gap=40-80 +15m stop=25 rr=2.0 thu=True | 63 | 58.7% | 2.28 | $+15,745 | ✓ |
| gap=40-80 +15m stop=30 rr=2.0 thu=True | 63 | 58.7% | 2.27 | $+17,740 | ✓ |
| gap=30-70 +15m stop=25 rr=1.5 thu=False | 72 | 62.5% | 2.26 | $+15,640 | ✓ |
| gap=30-80 +15m stop=25 rr=1.5 thu=False | 77 | 62.3% | 2.24 | $+16,765 | ✓ |
| gap=50-80 +15m stop=30 rr=2.0 thu=True | 35 | 57.1% | 2.23 | $+9,420 | ✓ |

## Best Strategy Deep Dive
**S11** — prox=10 stop=20 rr=1.5

| Period | n | WR | PF | Net |
| --- | --- | --- | --- | --- |
| All | 4 | 75.0% | 6.51 | $+1,460 |
| IS (2022-23) | 1 | 0.0% | 0.00 | $-265 |
| OOS (2024-26) | 3 | 100.0% | 9.99 | $+1,725 |


### DOW
| DOW | n | WR | PF | Net |
| --- | --- | --- | --- | --- |
| Mon | 2 | 100.0% | 9.99 | $+1,150 |
| Wed | 2 | 50.0% | 2.17 | $+310 |

### Month
| Month | n | WR | PF | Net |
| --- | --- | --- | --- | --- |
| Mar | 1 | 100.0% | 9.99 | $+575 |
| Apr | 1 | 100.0% | 9.99 | $+575 |
| Nov | 1 | 0.0% | 0.00 | $-265 |
| Dec | 1 | 100.0% | 9.99 | $+575 |

### Direction
| Dir | n | WR | PF | Net |
| --- | --- | --- | --- | --- |
| long | 1 | 0.0% | 0.00 | $-265 |
| short | 3 | 100.0% | 9.99 | $+1,725 |

### Year
| Year | n | WR | PF | Net |
| --- | --- | --- | --- | --- |
| 2022 | 1 | 0.0% | 0.00 | $-265 |
| 2025 | 2 | 100.0% | 9.99 | $+1,150 |
| 2026 | 1 | 100.0% | 9.99 | $+575 |

## Viability Verdict

**6 strategies VIABLE (OOS PF ≥ 1.3):**

- **S1** — gap=50-80 +15m stop=25 rr=1.5 thu=True: OOS PF 3.54, n=35
- **S18** — dir=short stop=20 rr=2.0: OOS PF 3.21, n=34
- **S12** — trend_align stop=15 rr=1.5: OOS PF 2.21, n=35
- **S9** — gap=30-80 stop=25 rr=2.5: OOS PF 1.95, n=112
- **S15** — stop=15 rr=2.0: OOS PF 1.88, n=84
- **S10** — atr>=1.0x stop=20 rr=1.5: OOS PF 1.73, n=36

## Recommendations

Best: **S1** — `gap=50-80 +15m stop=25 rr=1.5 thu=True`  OOS PF 3.54