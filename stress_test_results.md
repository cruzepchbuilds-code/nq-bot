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
| Win Rate | 58.2% |
| Profit Factor | 2.63 |
| Net P&L | $+25,715 |
| Avg Win | $1,065 |
| Avg Loss | $-565 |

## Results: Funded vs Eval Mode

| Metric | Funded (pyramid+scaling) | Eval Mode (1c, no pyramid) |
|--------|--------------------------|-----------------------------|
| Pass probability | 86.0% | **93.3%** |
| 5th pct (worst) | $-2,585 | $-760 |
| 50th pct (median) | $+3,580 | $+3,195 |
| 95th pct (best) | $+5,685 | $+3,695 |
| Avg trades to pass | 5.8 | 7.6 |
| Consec losses to breach DD | 5 | 5 |
| Sims failed | 1,396 (14.0%) | 671 (6.7%) |

## Eval Mode Verdict

**93.3% pass probability** (Apex $2,500 trailing DD)

**STRONG -- buy the eval**

## DD Tier Analysis -- Which Apex Plan to Buy

Eval mode (1c, no pyramid) tested across three trailing-DD tiers.

| Trailing DD | Pass % | Losses to Breach | p05 | p50 | p95 | Avg Trades | Avg Weeks | Verdict |
|-------------|--------|-----------------|-----|-----|-----|------------|-----------|--------|
| $2,500 | **93.3%** | 5 | $-760 | $+3,195 | $+3,695 | 7.6 | 10.2 | STRONG -- buy the eval |
| $3,000 | **96.3%** | 6 | $+3,000 | $+3,195 | $+3,695 | 7.9 | 10.7 | STRONG -- buy the eval |
| $3,500 | **97.6%** | 7 | $+3,000 | $+3,195 | $+3,695 | 8.1 | 10.9 | STRONG -- buy the eval |

**Key insight:** each +$500 of trailing DD adds ~1 extra consecutive-loss buffer.
Crossing from $2,500 -> $3,000 DD adds +10 pp pass probability.

## How to Use

1. Choose an Apex plan with **$3,000+ trailing DD** (crosses 75% threshold).
2. Set `EVAL_MODE = True` in config.py before starting the eval:

```python
EVAL_MODE = True   # 1 contract only, no pyramiding
```

3. Switch back to `EVAL_MODE = False` once funded (pyramiding and scaling re-enable).
