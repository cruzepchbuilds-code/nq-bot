# CruzCapital NQ Bot — v7

NQ (and ES) futures 1-minute ORB breakout system with walk-forward validation,
Monte Carlo stress testing, and Apex prop firm eval support.

---

## Folder Structure

```
nq_bot_final/
│
├── data/                      # Historical OHLCV data (CSV, 1-minute bars)
│   ├── nq_1min.csv            # NQ 2024-01-01 to 2026-06-11 (856k bars)
│   ├── nq_full.csv            # NQ 2022-01-03 to 2026-06-11 (1.56M bars)
│   └── es_1min.csv            # ES 2022-01-03 to 2026-06-11 (1.56M bars)
│
├── strategies/                # Strategy modules (one file per session/instrument)
│   ├── strategy_us.py         # US session ORB — breakout, fade, VWAP pull (MAIN)
│   ├── strategy_asia.py       # Asia gap continuation 6pm-9pm ET (funded phase)
│   └── strategy_london.py     # London overlap 8am-9:25am ET (disabled, PF 1.14)
│
├── brain/                     # Pattern analysis, insights, session research
│   ├── trade_memory.csv       # 286 NQ trades with full metadata
│   ├── pattern_engine.py      # 9-category pattern analysis → insights.md
│   ├── self_optimizer.py      # Config suggestions based on pattern data (read-only)
│   ├── insights.md            # Auto-generated pattern report
│   ├── trade_dissection.md    # Top/bottom 20 trade deep analysis
│   ├── session_log.md         # Live trading session template
│   ├── session_analysis.md    # London/Asia research + DD math (June 2026)
│   ├── london_research.md     # London session deep research (NOT VIABLE)
│   └── asia_research.md       # Asia gap continuation research (VIABLE, funded)
│
├── dashboard/                 # Live trading dashboard (placeholder)
│   ├── index.html             # Dashboard UI (not yet implemented)
│   └── server.py              # Flask/aiohttp server for live stats (not yet implemented)
│
├── live/                      # Live and paper trading execution
│   ├── telegram_alerts.py     # Telegram trade notifications
│   ├── paper_trading.py       # Paper trading runner (placeholder)
│   ├── morning_check.py       # Pre-market go/no-go checklist (placeholder)
│   └── execution.py           # Broker order execution (placeholder)
│
├── results/                   # Backtesting outputs, configs, and research reports
│   ├── results_summary.html   # Full strategy report with charts (open in browser)
│   ├── best_config_final.py   # v7 final config snapshot (reference copy)
│   ├── improvement_results.md # 10-improvement test results (5 winners, 5 rejected)
│   ├── stress_test_results.md # MC stress test scenarios
│   ├── monthly_returns.csv    # Monthly P&L breakdown
│   └── optimization_log.md    # Combined MC + ES + research findings
│
├── backtest.py                # Core backtest engine (run this)
├── bankroll.py                # Bankroll and position sizing manager
├── config.py                  # v7 config — ALL strategy parameters here
├── regime.py                  # Regime detector (ATR-based trend/chop classification)
├── signal_strength.py         # Signal scoring (gap size, OR size, volume)
├── monte_carlo.py             # Apex eval Monte Carlo stress test
├── walk_forward.py            # Year-by-year walk-forward validation
├── es_backtest.py             # ES-specific walk-forward runner
├── es_config.py               # ES calibrated parameters
├── combined_mc.py             # NQ + ES combined Monte Carlo
├── download_data.py           # Data fetcher (Databento CME)
└── README.md                  # This file
```

---

## How to Run

### 1. Backtest (NQ)

```bash
python3 backtest.py data/nq_1min.csv
```

Runs 2024-2026 NQ ORB strategy with v7 config. Output: trades, PF, net P&L, max DD.

### 2. Walk-Forward Validation (NQ)

```bash
python3 walk_forward.py data/nq_full.csv
```

Runs year-by-year IS/OOS validation (2022-2023 IS, 2024-2026 OOS). Primary quality metric.

### 3. Monte Carlo — Apex Eval Stress Test

```bash
python3 monte_carlo.py data/nq_1min.csv
```

10,000 simulations × 30 trades. Shows pass probability for each trailing DD tier.
- $2,500 DD: 79.3% pass (avoid)
- $3,000 DD: 88.0% pass
- **$3,500 DD: 92.7% pass ← buy this**

### 4. ES Backtest

```bash
python3 es_backtest.py data/es_1min.csv
```

ES walk-forward with calibrated config (es_config.py). OOS PF 1.61. Funded phase only.

### 5. Combined NQ + ES Monte Carlo

```bash
python3 combined_mc.py data/nq_1min.csv data/es_1min.csv
```

Combined portfolio MC for funded phase sizing decisions.

### 6. Pattern Analysis

```bash
python3 brain/pattern_engine.py brain/trade_memory.csv
```

Analyzes trade_memory.csv → writes brain/insights.md with 9-category breakdown.

---

## v7 Strategy (Current Best)

**NQ US Session ORB Breakout** — `strategies/strategy_us.py`

| Parameter | Value |
|-----------|-------|
| Session | 9:30 AM - 10:30 AM ET |
| OR window | 9:30-9:45 (15 min) |
| OR range filter | 55-110 pts |
| Gap filter | > 20 pt directional |
| Stop | 30 pt (25 fixed + 5 buffer) |
| Target | 60 pt (2.0R) |
| Skip | Mondays, June/Sep/Dec |
| Strong months | Jan-May, Oct-Nov |

**OOS 2025-2026 results**: PF 2.14, WR 47.2%, Net +$36,675, MaxDD 3.6%

**Apex eval MC** (10,000 sims): **92.7% pass at $3,500 trailing DD**

---

## Funded Phase (After Eval Pass)

Switch in `config.py`:
```python
EVAL_MODE = False       # re-enable pyramiding and scaling
ASIA_ENABLED = True     # add Asia gap continuation
```

Enable ES: `python3 es_backtest.py data/es_1min.csv`

### Asia Gap Continuation — `strategies/strategy_asia.py`

CME halts NQ 5pm-6pm ET daily. The halt gap (same-day 4pm→6pm) signals institutional positioning.

| Parameter | Value |
|-----------|-------|
| Session | 6:00 PM - 9:00 PM ET |
| Gap filter | 30-80 pt (absolute) |
| Entry | 6:15 PM close, in gap direction |
| Stop | 15 pt |
| Target | 22.5 pt (1.5R) |
| Skip | Thursdays, August, November |

**OOS 2024-2026**: PF 1.80, WR 56%, 77 trades — improving YoY (1.42→1.82→2.31)

---

## Eval Setup Checklist

1. Set `EVAL_MODE = True` in `config.py`
2. Buy Apex $50k account with **$3,500 trailing DD**
3. Trade NQ only — 9:30-10:30 AM ET (US session)
4. Skip Mondays, skip June/Sep/Dec
5. Max loss per trade: **~$615** (1 contract × 30pt stop + commissions)
6. Daily loss limit: $750 | Weekly: $2,500
7. Target: $3,000 profit (~11.9 weeks avg)

---

## Key Files to Know

| File | Purpose |
|------|---------|
| `config.py` | Single source of truth for ALL parameters |
| `results/results_summary.html` | Full strategy report — open in browser |
| `brain/session_analysis.md` | London/Asia research + Monday trading checklist |
| `brain/insights.md` | Pattern engine output (286 trade analysis) |
| `brain/asia_research.md` | Asia gap continuation deep research |

---

## Data Notes

- All data: CME NQ/ES futures, 1-minute OHLCV bars, US/Eastern timestamps
- Source: Databento (full Globex hours including overnight)
- `nq_full.csv`: Full Globex hours needed for Asia strategy (18:00-21:00 ET bars)
- `nq_1min.csv`: Regular trading hours (8:00-16:00 ET), sufficient for US ORB

---

## Strategy Version History

| Version | Key Change | OOS PF |
|---------|-----------|--------|
| v4 | Breakout-only, Apex DD rules | 1.33 |
| v5 | Signal scoring, partial exits | 1.38 |
| v6 | OR range 55-130, gap fix | 1.63 |
| **v7** | **10:30 cutoff, OR max 110, Apr/May→strong, warmup=5** | **2.14** |

v7 vs v6: +0.41 PF, MaxDD 3.6% vs 6.0%, WR 47% vs 41%
