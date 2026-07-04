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

## 4. Fleet lifecycle — SUPERSEDED 2026-07-03

**Tradeify: officially SKIP** (rank 77/108, 12× ROI vs ETF Static's 82× — firmcard.py).
The old business sim here (p50 $133k) was **debunked** by fleet_audit.py (it modeled the
wrong rules). Current doctrine (buffer_policy.py + capital_plan.py, see LEDGER.md):

1. Lucid: withdraw to **$55k, never below** (the $5k cushion is free — consistency
   rule builds it anyway — and makes accounts ~5× longer-lived with MORE extracted).
2. Reinvest above-cushion cash: Lucid to 5, then ETF 50K Static (buy eval with
   DEFAULT settings, ~70% pass, ~$332 all-in per funded).
3. Death = same-day replacement, no exceptions (user commitment 07-03).
4. ≈ Month 8 (fleet built): STOP buying props; payouts fund the personal live
   account (1 NQ per $25k, compound to ~$150k working capital, then it's income).

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

Frontier **closed** across 6 markets (07-03 five-desk sweep: CL 0/1008 configs, GC
5 families, crypto 5 structures, index fades — all killed; full receipts in
`brain/research/LEDGER.md`, the machine's permanent memory). Two survivors BENCHED
behind month-3 gates: NQ close-hour leg (~$5.5k/yr, zero collision) · ES magnet fade
(~$3.3k/yr, needs account design). Open on inputs: live-fill cost calibration after
month 1 · tick pilot ($62, awaiting go) · ZN/6E/SI pilots priced <$17 total.

## 10. Forward-test protocol (predefined 2026-07-03 — BEFORE first live trade)

**Sample:** first **30 resolved trades** (target/stop exits; time-exits excluded) or
Oct 3 2026, whichever first. NO parameter or config changes during the sample —
safety disables only. Day-15 checkpoint (~Jul 20) is a health check, not a change gate.

**PASS gates (tools decide, feelings don't):**
1. `journal.py` avg drift better than **−$37/trade** (25% of expectancy)
2. `sentinel.py`: no component below its own p05 band
3. Losing streak ≤ 15 · no day worse than −$909 (engine bound)
4. **ASIA execution:** entries must print ≤18:16 — two fills at 18:17+ = disable the
   Asia leg (edge decays within minutes of the reopen; param_stability 07-03; the
   nightly digest warns automatically)
5. **ASIA statistical watch** (weakest leg — reality_check deflated p≈1.0): first 8
   resolved Asia trades net negative → disable pending month-3 review

**Diagnosis order on failure:** (1) fill LATENCY first — a 60s delay costs 48% of the
edge and REJ/PM are the fragile legs (noise_mc); (2) slippage second — break-even is
25.7 ticks/trade vs 1–3 realistic, so slippage alone is almost never the cause;
(3) parameters LAST, only via law §5.

**PASS →** Phase-2 scaling + month-3 bench review (close-hour leg · ratchet verdict ·
OR-max 125 consideration). **FAIL →** halt purchases, investigate fills, touch nothing.

**Statistical honesty (reality_check 07-03):** no look-ahead found (edge dies on
shuffled worlds, as it should). Morning ORB survives full selection-bias deflation
(p=0.024). REJ/PM/ASIA are plausible but not statistically separable from search
luck on backtest evidence alone — **live trades are their exam.** RampMode already
sequences trust correctly: the proven leg trades first; the marginal legs unlock
only after +$800 of real profit.
