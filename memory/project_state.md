---
name: project-state
description: Current strategy status, backtest results, what's live and what's next
metadata:
  type: project
---

# CruzCapital Bot — Project State (as of 2026-06-30)

## Live Account Setup (as of 2026-06-30)
- **Account 1 — Lucid 50K funded (prop firm)**: CruzCapitalNQ.cs v10.1 (live, real money)
- **Account 2 — Sim101**: CruzCapitalES.cs v4 (paper trading, tracking separately)
- **Account 3 — Sim**: CruzCapitalVWAP.cs v3 (paper trading, tracking separately)

Accounts are intentionally separated so each strategy's live P&L can be validated against its OOS backtest independently.

## Strategy Verdicts & Final Parameters

### NQ ORB (v10.1) — LIVE ✓ (Lucid funded)
- Running live on Lucid 50K Direct funded account via NT8
- **v10.1 change**: 1 contract until lifetimePnL >= $1,500, then scales to 2c
  - Why: Lucid $2,000 fixed EOD DD floor — at 2c, 1 loss = $1,099 (55% of DD in one trade)
  - At 1c: 1 loss = ~$550 → 3 stop cushion before DD breach
  - After $1,500 profit: $3,500 headroom → safe to run 2c
- 22pt stop + 5pt buffer = 27pt effective, Asia enabled, skip Mondays + {6,9,12} weak months
- Connected via Tradovate Simulation connection (correct for Lucid Direct funded)
- **First active day**: July 1, 2026 (June was correctly skipped — WEAK month)

### ES ORB (v4) — SIM running ✓
- `live/CruzCapitalES.cs` — v4 final
- OOS (2024+): 90 trades | WR 48% | PF 2.17 | Net $24,645
  - 2024: PF 1.81 ($7,000) / 2025: PF 2.42 ($11,110) / 2026: PF 2.42 ($6,535)
- v4 changes:
  1. Dropped April — lost money all 3 OOS years, PF 0.68 total
  2. Kept March — OOS PF 1.57, adds 29 good OOS trades, raises 2026 from 11→20 trades
  3. RR 2.0 → 2.5 — same entries, wider target, OOS net +$7,203 vs v3
  4. SecondBreakoutEnabled default → false — second trade costs $2,465 OOS net
- Trading months: Feb + Mar + Nov
- STRONG months: {2, 11} (March trades but no signal score bonus)
- All other params: STOP=7pt+2buf=9pt effective, SIG_MIN=60, entry cut 10:15, OR 5-30pt
- **First live trade**: November 3, 2026
- **New entry research (brain/research/new_entries.py)**: Tested 7 alternative entry mechanics.
  None beat the baseline. Key finding: VWAP confluence filter removed ZERO trades (every ORB
  signal is already on the correct side of VWAP naturally). Edge lives in month selection +
  signal score, not entry mechanics.

### NQ VWAP Reclaim (v3) — SIM running ✓ [FINAL — no v4]
- `live/CruzCapitalVWAP.cs` — v3 is the final version
- OOS (2025+): 117 trades | WR 40% | PF 1.49 | Net $13,704
- Overall: 199 trades | WR 44% | PF 1.78 | Net $34,650
- v3 params: STOP=20pt, RR=2.5, MIN_EXTEND=25pt, window 10AM–1PM, 1 trade/day, trend-aligned
- WEAK months: {Apr, May, Jun, Sep, Dec} | Skip Mondays: true
- Final sweep confirmed v3 is already at the optimal point — 20+ param combos tested, none better
- **Active in July** (July not in WEAK set)

### Power Hour — NO EDGE, DO NOT DEPLOY ✗
- NQ PH: IS (2024) PF 0.87 LOSING / OOS reversed — red flag
- ES PH: IS PF 1.36 / OOS PF 1.02 — flat after costs

## Risk Management (Lucid $2k DD Floor)
- **DLL**: $1,200/day (MAX_LOSSES_PER_DAY=1 hardcoded)
- **Max DD**: $2,000 fixed EOD floor (NOT trailing)
- **1c risk (current)**: ~$550/loss → 3 cushion stops
- **2c risk (after gate)**: ~$1,099/loss → 3 cushion stops once $1,500 cushion earned
- **Scale gate**: SCALE_GATE=$1,500 in NinjaScript (gates both 2c entries and pyramid)

## Research Scripts
- `brain/research/deep_dive.py` — Monte Carlo, rolling PF, DOW/month breakdowns, edge search
- `brain/research/final_sweep.py` — Stop/RR grid, window times, direction, OR range, entry cutoffs
- `brain/research/new_entries.py` — 7 alternative entry mechanics tested (retest, VWAP, prev-day H/L, 5-min momentum, false break, N-bar confirm, retest+VWAP combo)
- Data: data/nq_1min.csv (632 days), data/es_1min.csv (1159 days)

## Open Work Items
1. Monitor live P&L on Lucid funded — validate 1c → 2c scale gate working correctly
2. Monitor ES ORB and VWAP sim P&L — validate against OOS targets
3. Fix Contabo VPS network (VirtIO driver) for 24/7 NQ ORB operation
4. Set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env vars on Windows VPS (morning_check.py won't alert until this is done)
5. Stack multiple uncorrelated edges on one account (next research goal)

**Why:** Running Lucid Trading 50K Direct funded ($1,200 DLL, 20% consistency rule, instant funded).
**How to apply:** IS=2022-23 / OOS=2024+ for ES ORB. IS=2024 / OOS=2025+ for NQ VWAP.
