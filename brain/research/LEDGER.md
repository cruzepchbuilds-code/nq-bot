# RESEARCH LEDGER — every edge ever tested, one line each

**The machine's memory.** Every study ends by adding ONE row here (via the main
session — agents report, the ledger is appended centrally to avoid write races).
House law: IS = 2022-2024, OOS = 2025-2026; an edge must be coherent in BOTH.

**Verdicts:** DEPLOYED (live) · BENCH (validated, waiting for a slot) ·
SHELVED (validated but blocked — receipts kept) · KILL (do not resurrect
without new data) · IN-AUDIT (desk running) · INCONCLUSIVE (bad data/N).

| Date | Market | Concept | IS PF | OOS PF | Verdict | Notes / receipt |
|---|---|---|---|---|---|---|
| 2026-07 | NQ | Morning ORB (v12 core) | 1.4-1.6 | 3.2-5.4 | DEPLOYED | gap>20 aligned, score≥3, regime gate |
| 2026-07 | NQ | VWAP Rejection 11-13h | 1.21 | 1.58 | DEPLOYED | only on no-morning days |
| 2026-07 | NQ | PM ORB 13:15-14:00 | 1.24 | 1.36 | DEPLOYED | 22pt stop / 2.5R |
| 2026-07 | NQ | Asia Gap 18:15 | 1.24 | 1.64 | DEPLOYED | 25pt/3R, funded only |
| 2026-07 | NQ | VWAP Reclaim v10 | 1.33 | 1.45 | BENCH | account #5 diversification slot |
| 2026-07 | NQ | Europe 3AM ORB | ≤1.01 | ≤1.01 | KILL | 24 configs, all dead |
| 2026-07 | NQ | PDH/PDL sweep-reject | — | — | KILL | mirage: single-month profit concentration |
| 2026-07 | NQ | Exit trails / breakeven / partials | — | — | KILL | 3 independent confirmations: fixed 3R wins |
| 2026-07 | NQ | Auto kill-switch (rolling-PF gating) | — | — | KILL | -$8.7k to -$13.7k vs human law |
| 2026-07 | ES | Opening-range breakout | — | 0.92 | KILL | 85% NQ correlation, drags book |
| 2026-07 | ES | VWAP reclaim port | neg | — | KILL | ES costs eat it |
| 2026-07 | ES | v4 monthly system | 0.63 | fit | KILL | months were fit on OOS — audit classic |
| 2026-07 | ES | ES-confirmation filter for NQ | — | — | KILL* | *INVERTED: ES-divergent NQ breakouts best (PF 2.18) |
| 2026-07 | RTY | Opening-range breakout | — | 0.72 | KILL | loses every year |
| 2026-07 | GC | ORB (old sparse file) | — | — | INCONCLUSIVE | bad data; reopened on gc_1min_v2.csv |
| 2026-07 | BTC | London-open breakout (spot) | 1.16* | 1.83-2.02 | SHELVED | *house-law IS weak: edge born 2024; venue-banned at Lucid/Tradeify; $4.11 MBT pilot priced |
| 2026-07 | BTC | MBT futures port | — | — | KILL | banned at both firms + ~$90/mo scale |
| 2026-07 | BTC | Weekend structure | — | — | KILL | dead flat |
| 2026-07-03 | CL | 9:00 ET pit-open ORB | 0.95 max | 0.97 | KILL | definitive: 0/1008 configs ≥ PF 1.0 on IS; OR breaks 99% of days (no information); frictionless best 1.07→0.87 OOS; EIA-Wed rule = era noise. Instrument tally closed: NQ works, ES/RTY/GC*/CL rejected (*GC re-audit running) |
| 2026-07-03 | GC | Session-structure sweep (5 families, ~730 configs) | — | — | KILL | all 5 family winners failed era test. Near-miss: 8:30 impulse-continuation (IS 1.19/OOS 1.51, corr +0.02, zero collision) KILLED in stress battery — 2× spread → IS 1.03, 2023 negative, MGC version dead, threshold meaning-drift. OOS-strong configs = 2025-26 gold-bull beta, not structure. gc_structures.py |
| 2026-07-03 | ES/NQ/RTY | Gap fade (all 3) | 1.27 | 1.27* | KILL | *fill artifact — edge lives in the 9:30 auction print you can't get; 9:31 entry → PF 1.02 |
| 2026-07-03 | ES/NQ/RTY | Lunch reversion (all 3) | ≤1.07 | ≤0.74 | KILL | indices don't fade the morning extreme at lunch |
| 2026-07-03 | ES/NQ/RTY | Close-hour MOC/continuation (all 3) | 0.59-0.90 | — | KILL | continuation OOS 1.47 was 100% 2022-carried = regime artifact; the collision-free 15:00 window stays empty |
| 2026-07-03 | ES/NQ/RTY | First-hour sweep-fail (all 3) | 0.71-0.91 | — | KILL | uniformly negative, fails like the daily PDH/PDL version |
| 2026-07-03 | NQ/RTY | Prior-close magnet | — | — | KILL | NQ = 2022-long-carried; RTY IS 1.06 thin |
| **2026-07-03** | **ES** | **Prior-close magnet fade (1/day, 9:31-10:59, tgt prev close, flat 12:00)** | **1.22** | **1.40** | **BENCH** | FIRST non-NQ survivor. Positive every year 22-26 (1.20/1.26/1.23/1.41/1.39), both directions both eras, survived 2×slip + next-bar-entry + bootstrap (P(OOS≤1)=0.09). Modest: ~$3.3k/yr @1c, $55/tr. Corr vs v12 −0.04/−0.10 = diversifying. Worst day −$1,030 → CANNOT share a $1,200-DLL account with v12 without a stacking design (NQ worst −$870 same-day risk). Next gates: deployment design study → NT8 port → Analyzer → paper. index_structures.py |
| 2026-07-03 | NQ | 8:30 release impulse | 1.24 | 0.60 | KILL | OOS collapse; fade side OOS-only = regime flip |
| 2026-07-03 | NQ | 15:30 mini-ORB | 0.91 | 1.02 | KILL | 3/3 IS years negative |
| 2026-07-03 | NQ | Monday gap fade | 1.18 | 1.58 | KILL | 2024-carried; deployable stops → IS 0.51-0.67; worst day -$6,444 |
| 2026-07-03 | NQ | Sunday 18:00 reopen drift | 1.07 | 0.60 | KILL | incoherent (note: Sunday is v12-dark — clean slot for future tests) |
| 2026-07-03 | NQ | Midnight Globex 00:00-02:00 | ≤0.99 | — | KILL | nothing IS-positive |
| 2026-07-03 | NQ | Post-14:00 PM continuation | 1.06 | 0.84 | KILL | co-fires 100% with live PM leg — would double exposure |
| **2026-07-03** | **NQ** | **Close-hour trend continuation (15:00-15:58: day range ≥1.5× 20d med + price in end-quartile → enter day direction, 25pt stop, flat 15:58)** | **1.62** | **2.10** | **BENCH** | All 5 years positive (2.90/1.33/1.27/2.20/1.83), ~$5.5k/yr @1c (~20 tr/yr), $514.50/stop fits NQ risk band, worst day -$514, ZERO collision (corr vs v12 -0.003; 15:00 start vs 14:00 last v12 entry). Survived +$10 slip, quartile shift, 15:05 entry, 8-config family OOS 1.30-2.10. CAVEATS: tail-harvester (PF ex-top-5 = 1.00 — profit lives in ~5 real macro days across 3 yrs; slow bleed between), shorts carry (longs OOS 0.28 — post-hoc, deploy both sides), on $900-guard accounts gate to dayP&L ≥ -$385 (stacked worst -$1,014 fits $1,200 only). Gates: NT8 port → Analyzer → month-3 review. nq_hidden_windows.py |
| 2026-07-03 | CRYPTO | US-open spillover (8:00 fade) | 1.19 | 0.99 | KILL | best of grid; died at IS/OOS gate |
| 2026-07-03 | CRYPTO | Asia-open breakout (00:00 UTC) | 1.05 | 0.81 | KILL | grid median 0.87 — net loser both eras |
| 2026-07-03 | CRYPTO | UTC-midnight drift/reversal | 1.00 | 0.81 | KILL | 0/28 configs cleared IS 1.0 |
| 2026-07-03 | CRYPTO | Vol-regime overlay (mid-band) | 1.37 | 0.95 | KILL | classic band-fitting — IS improves, OOS refutes |
| 2026-07-03 | CRYPTO | ETH/BTC ratio range-break | 1.20 | 0.82 | KILL | OOS decisive (N=79); no cross-coin generalization reached |
| 2026-07-03 | ZN/6E/SI | Data pilots PRICED (not bought) | — | — | — | ZN $5.48 · 6E $5.72 · SI $5.53 (all three < $17 total, awaiting go) |
| 2026-07-03 | META | Firm-side actuary (B2B asset) | — | — | BUILT | firm_actuary.py: breakeven skilled-share ETF 2.37% < Lucid 2.68% < Tradeify 9.20%; 1 shark ≈ 36 chum of revenue; trailing floor = only efficient firm lever (+$10.2k/shark, invisible to chum); consistency rules BACKFIRE on firms (−$12.9k/shark — the clause that makes our $5k cushion free); win-win frontier EMPTY at $364 pricing (needs ~$1,004); HONEYPOT WATCH: fixed $2,500 floor + 20% consistency = $32.5k/yr extraction — instant max-buy if any firm ever ships it |
| 2026-07-03 | NQ | Parameter stability audit (24 params, 400+ runs) | — | — | **PASS** | 22/24 PLATEAU, 0 SPIKES (no narrow sweet spot — overfit fear answered). 2 EDGEs: Morning OR-max 110 = shelf edge (cliff at 95, shelf 110-139, raising to ~125 defensible — never tighten); Asia 18:15 = EXECUTION requirement (18:17 entry → PF 1.63→1.30, IS collapses — late fills kill the leg; digest now warns). Also: morning stop must stay ≥23pt; REJ arm-time pocket 10:44-11:08 only; PM edge thin in absolute terms (PF~1.3) but parameter-stable; vol≥200 + gap filters inert (can't overfit). Side-find: portfolio_policy.py ATR unbounded-list bug (chip filed) — FIXED 2026-07-03 (uncommitted): pp + 13 copied scripts → bounded deque, reality_check chained replica aligned, v12 stream rebuilt (710→713 td, full net −0.08%, OOS net +2.4%, Lucid extraction $15,095→$15,392); pp grid ordering + eval_boost baseline (48%/10td) UNCHANGED — no decision flips; deltas in script headers. param_stability.py |
| 2026-07-03 | NQ | Noise-injection MC (10k paths/scenario) | — | — | **PASS** | break-even at 25.7 extra ticks/trade ($128!) vs 1-3 realistic; survives 12 ticks at PF≥1.2; combined harsh scenario (60s latency + slippage + 5% missed) still P(year>0)=98%. LATENCY is the dominant noise (60s delay = −48% of edge; REJ −76% of its leg); fragility/tick: PM 5.6% > REJ 4.6% > ASIA 4.1% > ORB 1.7%. Fill speed > commission tier. noise_mc.py |
| 2026-07-03 | NQ | Reality check: synthetic + bootstrap + deflation | — | — | **PASS w/ flags** | NO look-ahead (real PF 1.46 vs shuffled median 1.00 — system exploits real structure); stack OOS p=1e-4, Sharpe 3.35; ORB SURVIVES max-of-family deflation (p=0.024, and is rank 9/27 in own family = anti-cherry-pick); REJ/PM/ASIA MARGINAL after search-width correction (can't separate from selection on backtest alone — live = their exam); protocol defect documented: some knobs historically tuned on OOS. Asia weakest (raw p=0.025, 464-config history). Forward-test protocol → RUNBOOK §10. reality_check.py |
| 2026-07-03 | META | Buffer/ratchet policy studies | — | — | BUILT | buffer_policy.py: KEEP=$5k free lunch (consistency rule builds it anyway; ~5× lifespan, MORE extracted; house policy = withdraw to $55k); C+cushion+graduate ≈ $1.09M m24 hot-regime p50 vs $305k fortress. **ratchet_lock.py: REJECT (insurance-only at best)** — user's literal cell (arm $10k extracted / lock lifetime+$2k) is a PROVABLE NO-OP (with $5k cushion, death point already above the lock by arm time — 0% fire rate, identical output); intent cell (lock at balance-above-start ≤$2k) = least-bad variant but costs −$5.5k/m24, −$8.7k/m12 fleet (≈ the kill-switch's exact historical loss, same mechanism: amputates routine dips baseline recovers from; baseline m24 deaths = ZERO with cushion); cold-regime insurance payoff only +$234/acct-yr (29% ever arm). THE CUSHION + Lucid's locked floor already IS the lockdown: every withdrawal = profit ratcheted forever, firm floor caps giveback at the cushion. Do not deploy. |
