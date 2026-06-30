# Apex Eval Stress Test -- Monte Carlo Results

## Setup

| Parameter | Value |
|-----------|-------|
| Simulations | 10,000 |
| Trades per sim | 30 (sampled with replacement) |
| Eval profit target | +$3,000 |
| Eval trailing DD | -$2,500 |
| Strategy | v6 + EVAL_MODE (1 contract, no pyramiding) |
| OOS period | 2025-2026 monthly-independent |

## Eval Mode Trade Pool

| Metric | Value |
|--------|-------|
| Trades | 67 |
| Win Rate | 53.7% |
| Profit Factor | 2.20 |
| Net P&L | $+23,285 |
| Avg Win | $1,185 |
| Avg Loss | $-625 |

## Results: Funded vs Eval Mode

| Metric | Funded (pyramid+scaling) | Eval Mode (1c, no pyramid) |
|--------|--------------------------|-----------------------------|
| Pass probability | 70.9% | **79.3%** |
| 5th pct (worst) | $-2,500 | $-2,500 |
| 50th pct (median) | $+3,555 | $+3,490 |
| 95th pct (best) | $+5,170 | $+4,115 |
| Avg trades to pass | 5.0 | 7.4 |
| Consec losses to breach DD | 4 | 4 |
| Sims failed | 2,915 (29.1%) | 2,069 (20.7%) |

## Eval Mode Verdict

**79.3% pass probability** (Apex $2,500 trailing DD)

**MODERATE -- reasonable odds, manage daily limits carefully**

## DD Tier Analysis -- Which Apex Plan to Buy

Eval mode (1c, no pyramid) tested across three trailing-DD tiers.

| Trailing DD | Pass % | Losses to Breach | p05 | p50 | p95 | Avg Trades | Avg Weeks | Verdict |
|-------------|--------|-----------------|-----|-----|-----|------------|-----------|--------|
| $2,500 | **79.3%** | 4 | $-2,500 | $+3,490 | $+4,115 | 7.4 | 10.0 | MODERATE -- reasonable odds, manage daily limits carefully |
| $3,000 | **88.0%** | 5 | $-2,005 | $+3,490 | $+4,115 | 8.3 | 11.2 | STRONG -- buy the eval |
| $3,500 | **92.7%** | 6 | $-1,445 | $+3,490 | $+4,115 | 8.8 | 11.9 | STRONG -- buy the eval |

**Key insight:** each +$500 of trailing DD adds ~1 extra consecutive-loss buffer.
Crossing from $2,500 -> $3,000 DD adds +10 pp pass probability.

## How to Use

1. Choose an Apex plan with **$3,000+ trailing DD** (crosses 75% threshold).
2. Set `EVAL_MODE = True` in config.py before starting the eval:

```python
EVAL_MODE = True   # 1 contract only, no pyramiding
```

3. Switch back to `EVAL_MODE = False` once funded (pyramiding and scaling re-enable).
