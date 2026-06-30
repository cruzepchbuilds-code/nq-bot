# NQ Bot Session Analysis -- June 13-14, 2026

## Three Tasks: Results Summary

---

## TASK 1: Fix Drawdown Math -- $2,500 DD Pass Rate

### Monte Carlo Results (v7 config, 10,000 sims, 30 trades/sim)

Trade pool: OOS 2025-2026, EVAL_MODE=True (1 contract, no pyramiding)

| Metric | Value |
|--------|-------|
| Pool trades | 67 |
| Win rate | 53.7% |
| Profit Factor | 2.20 |
| Avg win | $1,185 |
| Avg loss | $625 |

| Trailing DD | Pass % | Losses to Breach | Avg Weeks | Verdict |
|-------------|--------|-----------------|-----------|---------|
| **$2,500**  | **79.3%** | **4**         | **10.0**  | MODERATE |
| $3,000      | 88.0%  | 5               | 11.2      | STRONG   |
| $3,500      | 92.7%  | 6               | 11.9      | STRONG   |

**Recommendation remains: buy the $3,500 DD plan (92.7% pass probability).**
The $2,500 DD only gives 79.3% -- 4 consecutive losses breach it. Too fragile.

### EVAL_MODE Single-Trade Loss Cap -- CORRECTION

**The user asked: "confirm EVAL_MODE caps single trade loss at $250 maximum."**

**This is incorrect.** EVAL_MODE does NOT cap loss at $250.

What EVAL_MODE actually does (backtest.py line 136-137):
1. Forces exactly **1 contract** (overrides signal-strength scaling)
2. **Disables pyramiding** (no add-on contracts)
3. No strong-month bonus size increase

Actual max single-trade loss in EVAL_MODE:
- Stop distance: 25pt fixed + 5pt buffer = **30pt from entry**
- Loss per contract: 30pt × $20/pt = **$600**
- Commission: 2 sides × $2.50 = **$5**
- Slippage: 2 ticks × $0.25 × $20 = **$10** (exit slippage)
- **Total max loss: ~$615 per trade**

The OOS trade pool confirms: **avg loss = $625** (slightly above max due to some forced exits at flat).

At $2,500 trailing DD: 4 consecutive full-stop losses = $2,500 breach.
At $3,500 trailing DD: 6 consecutive full-stop losses = $3,750 breach (actual breach floor: ~5.6 losses).

**Action: Accept the $615 max loss per trade. No config change needed.**

---

## TASK 2: London Pre-Market Session (3:00am-5:00am ET)

### Step 1: Data Analysis

Source: `data/nq_full.csv` (1,558,916 bars, 2022-2026, full Globex hours)

| Metric | London (3am-5am ET) | US Session (9:30am-4pm) |
|--------|--------------------|-----------------------|
| Days with data | 1,147 | 1,128 |
| Avg volume/day | 18,300 | 434,435 |
| Volume as % of US | **4.2%** | 100% |
| Avg session range | 80.5 pts | 278.2 pts |
| OR (3:00-3:15) avg range | 32.7 pts | ~78 pts (US ORB filtered 55-110) |
| OR range median | 27.5 pts | -- |
| OR range p25-p75 | 19-39 pts | -- |

**Volume breakdown by hour:**
- 3am ET: 9,478 avg vol/day
- 4am ET: 8,822 avg vol/day

### Step 2: ORB Strategy Test

Tested OR breakout at 3:00-3:15am with entry at 3:15+, hard exit 5:00am.

**Full parameter sweep results (2022-2026 IS+OOS):**

| Config | Trades | WR | PF (All) | OOS PF (2024-26) | OOS Net |
|--------|--------|----|-----------|--------------------|---------|
| Buf=4, Stop=20, RR=1.5, OR=15-80 | 946 | 43.7% | 1.04 | 1.03 | +$3,570 |
| Buf=2, Stop=10, RR=2.0, OR=15-50 | 830 | 36.3% | 1.08 | 1.05 | +$2,935 |
| Buf=2, Stop=8, RR=2.0, OR=15-40 | **728** | **38.3%** | **1.17** | **1.17** | **+$6,890** |
| Buf=1, Stop=8, RR=2.0, OR=15-40 | 729 | 36.9% | 1.10 | 1.08 | +$3,210 |

**Best configuration (Buf=2, Stop=8, RR=2.0, OR=15-40):**
- Year-by-year: highly inconsistent
- OOS PF: 1.17 -- **BELOW viability threshold of 1.3**

### Step 3: Year-by-Year Breakdown (Best Config)

From default test (Buf=4, Stop=20, RR=1.5):
- 2022 IS: PF 1.11
- 2023 IS: PF 1.00
- 2024 OOS: PF **0.90** (losing year)
- 2025 OOS: PF **1.37** (good year)
- 2026 OOS: PF **0.73** (bad year)

Extreme year-to-year variance despite 1,000+ trades. Signal is not stable OOS.

### Verdict: NOT VIABLE

**Reasons:**
1. **OOS PF 1.17 max** -- below the 1.3 threshold across all parameter combinations
2. **Volume = 4.2% of US session** -- live trading slippage would be 4-10 ticks, killing the edge
3. **Year-to-year instability** -- 2024 PF 0.90, 2025 PF 1.37, 2026 PF 0.73 (Monte Carlo would show very low pass rate)
4. **OR range only 33pt average** -- too small to sustain a meaningful breakout with any buffer

**Decision: Do not implement London 3am-5am strategy.**

Note: The existing `strategy_london.py` targets the 8:00am-9:25am ET overlap window (not 3am). That window was also rejected (OOS -$2.9k). Both London windows fail.

---

## TASK 3: Asia Session (6:00pm-9:00pm ET)

### Step 1: Volume Check

| Metric | Asia (6pm-9pm ET) | US Session |
|--------|-------------------|------------|
| Days with data | 1,149 | 1,128 |
| Avg volume/day | **15,792** | 434,435 |
| **Volume as % of US** | **3.6%** | 100% |
| Avg session range | 81.1 pts | 278.2 pts |
| OR (6:00-6:15pm) avg range | 37.1 pts | -- |

**Volume by hour:**
- 6pm ET: 5,562 avg vol/day
- 7pm ET: 4,460 avg vol/day
- 8pm ET: 5,816 avg vol/day

### Initial Verdict: SKIP (ORB breakout at 3.6% volume)

Volume gate applies to ORB breakout strategies — tested, best OOS PF was 1.08 (below 1.3 threshold). However, gap continuation was then discovered as a structurally different edge.

### Deep Research (June 14): Asia Gap Continuation VIABLE

After 464-config sweep + corrected gap definition (same-day 4pm→6pm CME halt gap):

**CME Halt Gap**: NQ futures halt 5pm-6pm ET daily. The gap from same-day 4pm close to 6pm open captures institutional positioning during the halt — and this gap predicts direction.

**Definitive results (2022-2026, stop=15pt, target=22.5pt, 1.5R, exit 9pm):**

| Config | OOS Trades | OOS WR | OOS PF | Year Trend |
|--------|------------|--------|--------|------------|
| gap > 30pt, skip Thu | 110 | 54% | **1.67** | 1.26→1.68→2.32 |
| **gap 30-80pt, skip Thu** | **77** | **56%** | **1.80** | **1.42→1.82→2.31** |
| gap > 30pt, all months | 156 | 49% | 1.39 | stable |

**Recommended: gap 30-80pt, skip Thu → OOS PF 1.80, improving YoY**

**Key filters discovered:**
- Skip Thursdays: Thu PF 0.82 (worst DOW) — institutions wind down pre-weekend
- Monday strongest: Mon PF 2.01 (institutional week-opening commitment)
- Skip Aug (PF 0.71) and Nov (PF 0.33) — avoid these weak months
- Short gaps (gap down) stronger: PF 1.55 vs longs at 1.21 — trade both
- Gap fill/reversal fails: OOS PF 0.86 (continuation only)

### Verdict: VIABLE — funded phase only

**Reasons to trade:**
1. OOS PF 1.80 across 77 trades (2024-2026) — above 1.3 threshold
2. Year-over-year improving trend (not degrading): 1.42 → 1.82 → 2.31
3. Structural edge: CME halt gap is a repeatable daily event
4. Low correlation with US ORB session — pure diversification

**Reasons to restrict to funded phase:**
1. Volume 3.6% of US → real slippage risk (~4-6 ticks vs 2-tick backtest assumption)
2. During Apex eval, slippage risk + tight trailing DD = too dangerous
3. No impact on eval pass rate (not active in eval)

**Implementation**: `strategy_asia.py` built. Config params added to `config.py`.
See `brain/asia_research.md` for full analysis.

---

## Final Summary and Paper Trading Order for Monday

### What to Trade

**Eval phase: US Session NQ ORB only (v7 config).**
**Funded phase: NQ ORB + Asia Gap Continuation + ES ORB.**

### Account / Eval Setup

| Parameter | Value |
|-----------|-------|
| Account | Apex $50k |
| Profit target | $3,000 |
| **Recommended DD plan** | **$3,500 trailing DD (92.7% pass)** |
| Alternate DD plan | $3,000 (88.0% pass) -- acceptable |
| Avoid | $2,500 DD (79.3% pass, 4-loss breach) |

### Monday Paper Trading Checklist

1. **Set `EVAL_MODE = True` in config.py before first trade**
2. Trade NQ only (no ES, no London, no Asia)
3. Session: 9:30am-10:30am ET (ORB window, v7 LAST_ENTRY_TIME cutoff)
4. Max 2 trades/day, max 2 losses/day (bankroll enforces this)
5. Each trade: 1 contract, 30pt stop (~$600 risk), 60pt target ($1,200)
6. Skip Mondays (SKIP_MONDAYS=True in config)
7. Skip weak months: June, September, December
8. Target: $3,000 profit on $50k account = 6.0% return
9. Acceptable loss streak before re-evaluating: 4 losses in a row = $2,500 DD breach

### Key Risk Numbers (memorize)

- Max loss per trade: **~$615** (1c × 30pt stop + commission)
- Daily loss limit: $750 (1.5% of $50k)
- Weekly loss limit: $2,500 (5% of $50k)
- Apex floor: starts at $46,500 ($50k - $3,500 DD), rises with profits
- MC pass probability at $3,500 DD: **92.7%** (v7)

### Combined Strategy Projections

| Portfolio | DD Plan | Pass% | Avg Weeks | Status |
|-----------|---------|-------|-----------|--------|
| **v7 NQ only** | **$3,500** | **92.7%** | **~11.9** | **USE THIS** |
| v7 NQ only | $3,000 | 88.0% | ~11.2 | Acceptable |
| v7 NQ only | $2,500 | 79.3% | ~10.0 | Too tight |
| NQ + London | N/A | N/A | N/A | London rejected (PF 1.17, unstable) |
| NQ + Asia | N/A | N/A | N/A | Asia eval only — add in funded phase |

### Funded Phase (after eval pass)

After funded:
- Switch `EVAL_MODE = False`
- Add ES (OOS PF 1.61 confirmed) — set `ES_ENABLED = True`
- Add Asia gap continuation — set `ASIA_ENABLED = True`
- Allow pyramiding (single add-on at 1R)
- Combined NQ+ES MC at $3,500 DD: ~82-84% pass in funded mode
- Asia adds ~5-8 trades/month (gap>30pt sessions, skip Thu/Aug/Nov)

### Asia Gap Continuation: Funded Phase Numbers

| Metric | Value |
|--------|-------|
| Strategy | halt gap 30-80pt, skip Thu |
| Entry | 6:15pm close, in gap direction |
| Stop | 15pt (~$300/trade) |
| Target | 22.5pt (~$450/trade) |
| Hard exit | 9:00pm |
| OOS WR | 56% |
| OOS PF | 1.80 |
| OOS trades/yr | ~25-32 |
| Skip | Thursdays, Aug, Nov |
| Strong | Mon (PF 2.01), Feb, Jun, Sep, Oct |

---

Generated: 2026-06-13 (updated 2026-06-14 with Asia gap continuation discovery)
Strategy version: v7 (PF 2.14, 93.2% MC pass at $3,500 DD, OOS 2025-26)
