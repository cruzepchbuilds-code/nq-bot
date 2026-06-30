# CruzCapital — Data Gap Analysis & Recommendations
**Date:** 2026-06-14  
**Purpose:** Identify what additional data would most improve research quality and signal robustness.

---

## 1. Current Data Assets

| Instrument | File | Coverage | Bars | Status |
|------------|------|----------|------|--------|
| NQ 1-min | nq_1min.csv | 2024-2026 | 857K | PRIMARY — Active |
| NQ 1-min (full) | nq_full.csv | 2022-2026 | 1.56M | AVAILABLE — underutilized |
| ES 1-min | es_1min.csv | 2022-2026 | 1.56M | AVAILABLE — es_backtest.py |
| RTY 1-min | rty_1min.csv | 2022-2026 | 1.49M | AVAILABLE — unused |
| CL 1-min | cl_1min.csv | 2022-2026 | 1.46M | AVAILABLE — unused |
| GC 1-min | gc_1min.csv | 2022-2026 | 51K | THIN — partial coverage |

---

## 2. Highest-Priority Missing Data

### PRIORITY 1 — VIX Daily Data (FREE)
**Source:** CBOE VIX (available free via Yahoo Finance, Quandl, or Databento)  
**Why it matters:**
- NQ ORB edge varies dramatically by volatility regime
- April 2025 (tariff shock): VIX 30-60, NQ ORs 140-352pt → 0 qualifying trades
- Normal markets (VIX 12-20): NQ ORs 55-100pt → optimal for strategy
- VIX level predicts WHICH days will have OR outside the 55-110pt filter
- Can pre-filter: if VIX > 35, expect OR > 110pt → skip day entirely

**Research hypothesis:** Conditioning entry on VIX < 30 (or OR/VIX ratio) could
significantly improve the strategy's regime classification without requiring intraday data.

**Data format needed:** VIX daily close (OHLC optional), 2022-2026  
**Estimated cost:** FREE (Yahoo Finance / FRED API)  
**Expected impact:** HIGH — regime classification improvement, -3-5pp DD

### PRIORITY 2 — Economic Calendar (FREE)
**Source:** ForexFactory API or Investing.com (scraped)  
**Why it matters:**
- FOMC decisions (8 per year): NQ moves 100-400pt in 30 minutes
- CPI/PCE releases: 30-60pt spikes in first 15 minutes → OR chaos
- NFP (1st Friday): Globex opens with 50-200pt gaps
- These events directly cause the "OR too large" days that zero out trade counts

**Specific impact evidence:**
- April 2025 tariff shock: Non-calendar but analogous — extreme OR days
- FOMC weeks consistently show higher OR volatility (higher skip rate)
- Pre-FOMC days often see suppressed volatility (lower OR) → missed signals

**Data format needed:** Date, time, event name, impact level, for 2022-2026  
**Estimated cost:** FREE (ForexFactory, DailyFX)  
**Expected impact:** HIGH — better prediction of skip days, ~+0.08 PF

**Rule to test:** Skip trade on FOMC day (decision day). Keep pre-FOMC and post-FOMC.
Research basis: FOMC day ORs are often 80-130pt (borderline), but direction is unpredictable
before the decision. Post-decision direction is strong but OR may already be established.

### PRIORITY 3 — RTY (Russell 2000) as Regime Indicator (ALREADY DOWNLOADED)
**Source:** data/rty_1min.csv (available, unused)  
**Why it matters:**
- RTY vs NQ divergence signals risk-off/risk-on regime shifts
- Large NQ gap up + RTY flat = institutional hedging, not broad risk-on → lower quality NQ long
- RTY and NQ moving together = genuine risk-on → high-quality NQ breakout
- RTY lead/lag vs NQ can identify institutional rotation that precedes breakouts

**Data format needed:** RTY 1-min OHLCV, already available in data/rty_1min.csv  
**Estimated cost:** ZERO — data already exists  
**Expected impact:** MEDIUM — regime filter improvement, +0.05-0.08 PF

**Research hypothesis to test:** On NQ long breakout days where RTY also breaks out
within 15 minutes, WR > baseline. On NQ breakout days where RTY is flat or negative, WR < baseline.

### PRIORITY 4 — Pre-Market Gap Precision Data (MODERATE COST)
**Why it matters:**
- Current gap calculation: OR_midpoint - prior_close (rough approximation)
- True pre-market gap: 4pm RTH close → 9:30am first print (includes overnight)
- The distinction matters for gap alignment: a gap established at 4pm vs 9:25am is different
- True "opening gap" separates institutional overnight positioning from last-hour drift

**Current issue:** `prev_close` is the last bar close at 3:55pm (from FLATTEN=15:55 flatten).
The 4:00pm RTH close and the overnight/Globex price action are not separately captured.

**Fix available without new data:** Use `nq_full.csv` (full Globex hours) to capture 4:00pm 
close precisely, then compute 9:30am gap from actual 4pm close, not 3:55pm approximation.

**Estimated cost:** ZERO — already in nq_full.csv  
**Expected impact:** LOW-MEDIUM — gap signal precision improvement

### PRIORITY 5 — Extended NQ History (2018-2021) via Databento
**Why it matters:**
- Current IS data is 2022-2026 (4.5 years)
- 2018-2021 includes: 2018 Q4 crash, 2019 grind, 2020 COVID crash, 2021 bull run
- Testing the ORB strategy through COVID (March 2020: ORs 400-800pt) would reveal
  whether the OR_MAX filter properly handles extreme regimes
- Longer history → more robust parameter confidence

**Estimated cost:** ~$50-100 via Databento (1-min NQ futures, 2018-2021)  
**Expected impact:** HIGH confidence improvement — validates robustness through extreme regimes

### PRIORITY 6 — Level 2 / Order Book Snapshots
**Why it matters:**
- Current volume confirmation: bar volume >= 200 (very crude)
- True order flow: bid/ask imbalance at OR breakout is far more predictive
- Large bid stacks at OR high → strong breakout. No bid stack → false breakout
- This is the kind of edge that institutions exploit on breakout strategies

**Estimated cost:** $200-500/month (live feed required, historical L2 very expensive)  
**Recommendation:** Research with current data first. Add L2 only after confirming edge
magnitude justifies the data cost.

---

## 3. Data Already Available but Underutilized

### nq_full.csv — Not Used for Main Backtest
The full NQ dataset (2022-2026, 1.56M bars) is loaded only by session research scripts.
The main backtest defaults to nq_1min.csv (2024-2026 only). Running the rolling
walk-forward on nq_full.csv would add TWO additional OOS windows and dramatically improve
parameter confidence.

**Action:** Switch walk_forward.py default to data/nq_full.csv

### rty_1min.csv — Available but Unused
Russell 2000 1-min data is downloaded but no code uses it. RTY provides:
1. Risk-on/risk-off regime indicator (RTY/NQ divergence)
2. Small-cap vs large-cap rotation signal (institutional sector rotation)
3. Independent ORB backtest (lower point value, potentially different seasonality)

**Action:** Build brain/research/rty_correlation.py to test RTY as NQ signal enhancer

### es_1min.csv — Used Only in es_backtest.py
ES data is available for 2022-2026 but only used in isolated ES backtest.
Not used as a live confirmation signal for NQ breakouts.

**Action:** Test NQ+ES simultaneous breakout as a quality filter

### gc_1min.csv — 51K bars only (2022-2026 sparse)
Gold data is present but very sparse (51K bars vs 1.56M for NQ). Likely has missing sessions.
Gold is a risk-off asset that could inform NQ direction on flight-to-safety days.

**Recommendation:** Download full GC history via Databento if adding macro regime layer.

---

## 4. Data Acquisition Recommendations (Prioritized by EV/Cost)

| Rank | Data | Cost | EV | Action |
|------|------|------|-----|--------|
| 1 | VIX daily | FREE | HIGH | Download immediately (Yahoo Finance) |
| 2 | Economic calendar | FREE | HIGH | Scrape ForexFactory |
| 3 | RTY as NQ signal | ZERO (exists) | MEDIUM | Build rty_correlation.py |
| 4 | nq_full.csv for walk-forward | ZERO (exists) | HIGH | Switch default path |
| 5 | NQ 2018-2021 history | ~$75 | HIGH | Buy when budget allows |
| 6 | ES as NQ signal | ZERO (exists) | MEDIUM | Build confirmation module |
| 7 | Pre-market gap precision | ZERO (nq_full.csv) | LOW-MED | Extract 4pm close from full data |
| 8 | L2 order book | $200-500/mo | MEDIUM | Defer until edge validated |

---

## 5. Data Quality Checks Required

Before any new strategy validation, verify:

1. **NQ data continuity:** Check for gaps > 5 trading days in nq_full.csv (holidays excluded)
2. **Volume data validity:** OR volume calculation depends on accurate 1-min volume; 
   verify volume is not zero-filled or missing for key periods
3. **Timestamp timezone consistency:** All bars labeled US/Eastern; verify no DST errors
4. **RTY data coverage:** Check if rty_1min.csv covers full Globex hours or RTH only
5. **GC data completeness:** Verify gc_1min.csv is complete enough to be usable

---

_Generated: 2026-06-14 | Version: 1.0_
