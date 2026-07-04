# CruzCapital Operations Runbook — v12 (2026-07-03)

Single reference for running the system. Backtest basis: 2022–Jun 2026, 1-min NQ,
all numbers strict 1c unless noted, costs $14.50/trade included.

---

## 1. The files

| File | What | Status |
|---|---|---|
| `CruzCapitalNQ_v12.cs` | THE deploy file: morning ORB + rejection + PM ORB + Asia, risk engine, telemetry | **Deploy this** |
| `CruzCapitalVWAP.cs` (v10) | VWAP reclaim — SEPARATE account only (conflicts with v12's rejection) | Optional acct #4-5 |
| v11.x / v10.x / CruzCapitalREJ.cs | Superseded — never run alongside v12 | Retire |

## 2. Settings per platform

| Property | Lucid 50K | Tradeify EVAL | Tradeify FUNDED |
|---|---|---|---|
| MaxContracts | **2** (auto: 2c only REJ/PM) | 1 | 1 |
| DayLossGuard | **1150** | 900 | **900** |
| EvalMode | false | **false** (3R passes more) | false |
| RampMode | **ON** (fixed floor) | **OFF** | **OFF** |
| PmOrbEnabled | true | **false** | true |
| AsiaEnabled | true | **false** | true |
| RejectionEnabled | true | true | true |
| SkipMondays / PmSkipFridays | true / true | true / true | true / true |
| SkipFridays (morning) | false | false | false |
| Telemetry | true | true | true |
| Chart | NQ ##-## continuous, 1-min, **Days to load ≥ 60** | same | same |

## 3. Expected numbers (live journal benchmarks)

| Metric | Lucid (2c REJ/PM) | Tradeify funded | Tradeify eval |
|---|---|---|---|
| Monthly | ~$3,700 (median $4,200) | ~$900–1,050 extracted | — |
| Trades/week | ~4 | ~4 | ~1.8 (lean config) |
| Worst day (hard cap) | −$909 | −$870 | −$565/−$870 |
| Worst single trade | −$830 (2c REJ) | −$626 | −$626 |
| Max losing streak seen | 9–15 trades | same | same |
| Account fate | 72% pay (ramp ON); 10% die w/ $0; avg extract ~$13k; 1st payout ~2.5-3 mo (20% consistency) | dies by design ~4-5 mo, extracts ~$4,600 first | 64% pass, median 10 td |

**Red flags (call for review, don't improvise):** any single day worse than −$1,150 ·
any single trade worse than −$700 at 1c · trade count drifting outside 2–6/wk over a
full month · fills averaging >2pt worse than chart closes.

## 4. Tradeify lifecycle (the extraction business)

1. Eval ($99): lean config → 64% pass, median 2 weeks. Reset and retry on failure (~$155 expected/funded).
2. Funded: flip to funded settings same file. **Daily path.** Sweep everything above $50k every day.
3. Death at trailing floor = COMPLETED CYCLE, not failure (~$4,600 extracted avg; each death ≈ +$1,740 net transfer from firm). Replace, repeat. Keep one eval always cooking.
4. Business sim (1 Lucid + 2 Tradeify slots, 333 worlds): 12-mo net p10 $58k / p50 $133k / p90 $241k. **Plan on p10.**
5. VERIFY with Tradeify before scaling: DLL-breach consequence, trailing lock behavior, withdrawal-vs-HWM, min payout, max accounts, copier/churn policy.

## 5. Manual kill-switch rules (write-once, follow always)

- Sub-strategy rolling **3-month** PF < 1.0 → pause it, review with full-data rerun. (Auto-gating was tested and LOSES — humans review, machines don't bench components.)
- Live slippage > 2× the $10/trade budget over 20 trades → halve size, investigate.
- Account equity −$1,200 from start (Lucid) → stop trading, review before resuming.
- A losing streak alone is NEVER a reason to change anything: 9–15 straight losses is in-sample normal at 38% WR.
- Parameter changes require: full-data rerun (2022–now), IS/OOS split, all-years table. No window-picking — windows lie (documented twice: ramp windows, sweep-reject mirage).

## 6. Expectation calendar

- **Holiday weeks (Jul 4th, Thanksgiving, Christmas):** 0–6 trades historically; zero-trade weeks have precedent (2024). Not broken.
- Weak months (auto-handled): morning caps 1c in Jun/Sep/Dec · rejection skips Apr/May/Jun/Sep/Dec · Asia skips Aug/Nov · VWAP-reclaim skips May.
- ~3-month flat stretches happened in backtest (max-time-to-recover 84 days). The monthly averages include them.

## 7. Everything tested and rejected (do NOT revisit without new data)

Strategy concepts (~40 lifetime, 5 survived): ES/CL/RTY/GC ports · Europe/London/overnight
sessions · micro-ORs · PDH/PDL & Globex breakouts · 10:30 momentum · opening drive ·
OR retest/failed-BO patterns · gap drift · MOC drive (died 2024) · PDH/PDL sweep-reject
(April-2025 crash mirage: top-5 trades = 121% of the "edge") · prior-close rejection ·
failed-gap fade · frequency combos.

Structure/policy: trailing/BE stops (3× catastrophic) · partial exits · re-entries
(PM, VWAP) · percent-scaled stops · trend-strength floors · extension recency ·
weekly loss brake · day-profit stops · auto kill-switch (−$8.7k to −$13.7k, benches
components right before recovery) · split-stack account pairs · VWAP-reclaim merge
into v12 (creates −$1,124 day = 94% of Lucid DLL) · RampMode on trailing accounts.

## 8. Telemetry → journal

v12 prints to NT8 Output window: `ENTRY <sig> <dir> <qty>c @ <px> stop <px> tgt <px>`,
`EXIT <sig> <P&L> | day | life`, `EOD <date> day P&L`. Copy daily into the fill journal;
compare fills vs printed prices — that delta IS the live-vs-backtest tracking number.

## 9. Research status

NQ intraday frontier: **closed** (3 systematic sweeps + v12 lab, last 12 concepts all
rejected). Open items requiring new inputs: GC data via Databento (only untested
uncorrelated instrument) · event-day playbook (sweep-rejects work in crash vol) ·
live-fill-driven cost calibration after 1 month of statements. Repo still needs `git push`.
