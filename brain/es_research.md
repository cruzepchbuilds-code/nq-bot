# ES Research — Full Strategy Build

Generated: 2026-06-14 | Data: 2022-01-03 to 2026-06-11
IS: 2022-2023 | OOS: 2024-2026 | Viability gate: PF ≥ 1.4, n ≥ 30

**Baseline (es_config.py):** stop=6, buf=2.25, or=6-35, rr=2.0, no Mon
OOS 2024-2026: 163 trades, WR 47%, PF 1.61, Net +$23,380


## Strategy Results Summary

| Strategy | Config | n_all | WR | PF_all | n_oos | WR_oos | PF_oos | Net_oos | V |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S5 | es_confirm=15min | 146 | 45.2% | 1.55 | 81 | 51.9% | 2.02 | $+24,975 | ✓ |
| S2 | months=JFON dows=wed_thu_fri | 152 | 42.1% | 1.12 | 84 | 50.0% | 1.54 | $+7,980 | ✓ |
| S10 | tighter | 225 | 41.8% | 1.10 | 116 | 49.1% | 1.49 | $+10,245 | ✓ |
| S6 | pyramid@1R strong_months | 257 | 42.0% | 1.14 | 137 | 47.4% | 1.46 | $+16,215 | ✓ |
| S1 | stop=6 buf=3.0 or=8-25 rr=2.5 | 489 | 33.9% | 1.00 | 262 | 39.7% | 1.29 | $+16,202 | ✗ |
| S9 | rr=2.5 | 618 | 32.7% | 0.95 | 335 | 36.4% | 1.13 | $+9,638 | ✗ |
| S4 | or=8-20 | 399 | 37.3% | 0.92 | 212 | 42.0% | 1.12 | $+5,228 | ✗ |
| S8 | or>35 stop=15 rr=1.5 | 44 | 47.7% | 1.21 | 33 | 45.5% | 1.11 | $+1,560 | ✗ |
| S3 | last_entry=10:00 | 387 | 37.2% | 0.91 | 214 | 39.7% | 1.02 | $+992 | ✗ |
| S7 | gap>15 stop=8 rr=1.0 | 579 | 31.3% | 0.86 | 321 | 32.1% | 0.93 | $-6,718 | ✗ |

## S1 ORB Sweep — Top 10

| Config | n_oos | WR_oos | PF_oos | Net_oos |
| --- | --- | --- | --- | --- |
| stop=6 buf=3.0 or=8-25 rr=2.5 | 262 | 39.7% | 1.29 | $+16,202 |
| stop=6 buf=3.0 or=8-30 rr=2.5 | 291 | 39.5% | 1.28 | $+17,458 |
| stop=6 buf=3.0 or=8-35 rr=2.5 | 305 | 39.3% | 1.27 | $+17,738 |
| stop=6 buf=2.0 or=8-30 rr=2.5 | 297 | 38.7% | 1.24 | $+15,702 |
| stop=6 buf=3.0 or=8-40 rr=2.5 | 312 | 38.8% | 1.24 | $+16,302 |
| stop=6 buf=2.25 or=8-30 rr=2.5 | 297 | 38.4% | 1.23 | $+14,652 |
| stop=6 buf=2.0 or=8-25 rr=2.5 | 267 | 38.2% | 1.22 | $+12,702 |
| stop=6 buf=3.0 or=6-25 rr=2.5 | 286 | 38.1% | 1.21 | $+13,270 |
| stop=6 buf=3.0 or=6-30 rr=2.5 | 315 | 38.1% | 1.21 | $+14,525 |
| stop=6 buf=3.0 or=8-50 rr=2.5 | 320 | 38.1% | 1.21 | $+14,512 |

## Best Strategy Year-by-Year
**S5** — es_confirm=15min

| Year | n | WR | PF | Net |
| --- | --- | --- | --- | --- |
| 2022 | 39 | 41.0% | 1.31 | $+4,425 |
| 2023 | 26 | 30.8% | 0.84 | $-1,850 |
| 2024 | 31 | 54.8% | 2.28 | $+11,225 |
| 2025 | 27 | 51.9% | 2.02 | $+8,325 |
| 2026 | 23 | 47.8% | 1.72 | $+5,425 |

## Viability Verdict

**4 strategies passed PF ≥ 1.4:**

- **S5** — es_confirm=15min: OOS PF 2.02, n=81
- **S2** — months=JFON dows=wed_thu_fri: OOS PF 1.54, n=84
- **S10** — tighter: OOS PF 1.49, n=116
- **S6** — pyramid@1R strong_months: OOS PF 1.46, n=137

## Recommendations

Best: **S5** — `es_confirm=15min`  OOS PF 2.02