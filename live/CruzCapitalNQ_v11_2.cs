// CruzCapitalNQ_v11_2.cs — NinjaTrader 8 Strategy
// CruzCapital NQ — Lucid Trading 50K  |  4 sub-strategies, 1 risk engine
//
// HOW TO INSTALL:
//   1. Copy to Documents\NinjaTrader 8\bin\Custom\Strategies\CruzCapitalNQ_v11_2.cs
//   2. NT8: NinjaScript Editor → Compile All (F5)
//   3. Add "CruzCapitalNQ v11.2" to NQ Continuous (#F) 1-min chart
//   4. REMOVE any v10.x instance AND any separate CruzCapitalREJ instance first —
//      v11 contains the rejection strategy; running both double-trades it.
//
// ── WHAT'S IN v11 ────────────────────────────────────────────────────────────
//
//  MORNING ORB   9:30-9:44 range | 9:46-10:30 entry          (unchanged v10.4)
//    OR 55-110pt + regime ≥18% of 14d range + gap >20pt aligned + vol ≥200
//    + confidence ≥3.  Stop 27pt | 2R eval / 3R funded.  One re-entry after
//    target.  Skip Mon (Fri optional).
//
//  VWAP REJECTION  11:00-13:00  ← NEW in v11 (was separate CruzCapitalREJ.cs)
//    Extension ≥25pt from VWAP → first cross ≥11:00 (reclaim attempt) →
//    opposite re-cross = failed reclaim → enter with the failure.
//    Stop 20pt | 3R (60pt) | force-flat 13:00 | skip Mon + Apr/May/Jun/Sep/Dec.
//    ONLY FIRES IF MORNING DIDN'T TRADE: portfolio sim shows rejection on
//    morning-trade days is a LOSER (PF 0.46-0.97); on no-morning days PF 1.45
//    (+$27k / 247 trades). Conditioning adds +$3.1k and removes all overlap.
//
//  PM ORB        13:00-13:14 range | 13:15-14:00 entry       (unchanged v10.4)
//    OR 15-60pt.  Stop 22pt | 2.5R (55pt).  Skip Mon+Fri.
//
//  ASIA GAP      18:15 bar | gap 30-80pt | stop 25pt | 3R    (unchanged v10.4)
//    Skip Thu/Aug/Nov.  Funded only.
//
// ── RISK ENGINE v2 (the v11 core change) ─────────────────────────────────────
//  Policy grid over 710 trading days 2022-2026 (brain/research/portfolio_policy.py):
//
//    DLL=$500 internal halt = MAX NET of all policies tested ($137.9k vs
//    $134.3k at the old $900) — trades taken after a -$500 day are net losers.
//    Halting earlier makes MORE money and keeps the day far from Lucid's $1,200.
//
//    RISK-ROOM CONTRACT GATE: every entry sizes as
//        qty = min(qty, floor((1150 + dailyPnL) / riskPerContract))
//    so a 2c morning trade (risk $1,130) is only allowed with a clean slate,
//    auto-degrades to 1c as the day reddens, and NO fill sequence can push the
//    day past -$1,150 (Lucid DLL $1,200 with $50 slippage buffer).
//
//    RAMP MODE (default ON): rejection/PM/Asia unlock at lifetime P&L ≥ +$800.
//    A fresh account trades the highest-PF edge (morning) only, until there's
//    cushion under the $2,000 account floor. Turn off to run everything day 1.
//
//  Worst-case day by construction:
//    1c: ORB stop -$565 → day halted (DLL 500)   |  REJ -$415 → PM -$455 = -$870
//    2c: ORB stop -$1,130 → day halted            |  never past -$1,150 guard
//
//  Combined OOS 2025-26 (1c, all four): ~$920/wk, worst day -$1,130 (2c sized),
//  max EOD equity DD -$4,891 over 18mo → run RampMode on fresh accounts.
//
// ── CHANGELOG ────────────────────────────────────────────────────────────────
//  v11.2 (2026-07-03): PM gate — skip PM when morning ORB traded AND lost
//        (that cohort: PF 0.81, -$3,374/53 trades; morning-WIN days keep PM,
//        PF 1.58). Also caps every morning-loss day at a single stop (-$565).
//        Final polish pass also TESTED AND REJECTED: PM re-entry (IS/OOS
//        disagree), weekly loss brake (costs net, doesn't move survival),
//        rejection Friday skip (Fri PF 1.48 — fine), VWAP-reclaim ORB-day
//        gate (still positive, keep).
//  v11.1 (2026-07-03): MaxContracts property (default 1) after Analyzer showed a
//        2c stop with gap-through printing -$1,240 — over the 50K's $1,200 DLL
//        on its own. 50K accounts: leave at 1 (scale via account count).
//        Set 2 only on tiers with DLL ≥ $2,400. Pyramiding requires it ≥ 2.
//        Analyzer validation (Jan 2025-Jul 2026, ramp ON): $66,295 net,
//        285 trades, PF 1.62 — matches portfolio sim within 10%.
//  v11   (2026-07-02): rejection integrated (no-morning-day gate), DLL 900→500,
//        risk-room contract gate, RampMode, Asia 25pt/3R, PM 2.5R
//        (both sweeps landed in v10.4 same day), single shared dailyPnL.
//  v10.4 (2026-07-02): Python-parity audit — RTH-only prev-day context, manual
//        prevClose, single ORB2 re-entry armed by target-exit only, pyramid
//        morning-only, gap filter, regime gate, volume floor, weak-month 1c cap
//  v10.3 (2026-07-01): PM OR 60pt / 14:00 cutoff, PM fires on morning-skip days
//
#region Using declarations
using System;
using System.Collections.Generic;
using System.Windows.Media;
using NinjaTrader.Cbi;
using NinjaTrader.Gui;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    public class CruzCapitalNQ_v11_2 : Strategy
    {
        // ── Morning ORB parameters ─────────────────────────────────────────
        private const double STOP_PT     = 22.0;
        private const double STOP_BUF    = 5.0;    // eff stop 27pt
        private const double BRK_BUF     = 4.0;
        private const double MIN_OR      = 55.0;
        private const double MAX_OR      = 110.0;
        private const double FUNDED_RR   = 3.0;
        private const double EVAL_RR     = 2.0;
        private const double GAP_FILTER  = 20.0;
        private const double MIN_BRK_VOL = 200.0;
        private const double REGIME_MIN  = 0.18;
        private const int    REGIME_LEN  = 14;

        // ── Rejection parameters ───────────────────────────────────────────
        private const double REJ_STOP   = 20.0;
        private const double REJ_RR     = 3.0;    // 60pt target
        private const double REJ_EXTEND = 25.0;
        private static readonly HashSet<int> REJ_WEAK = new HashSet<int> { 4, 5, 6, 9, 12 };

        // ── PM ORB parameters ──────────────────────────────────────────────
        private const double PM_STOP    = 22.0;
        private const double PM_MIN_OR  = 15.0;
        private const double PM_MAX_OR  = 60.0;
        private const double PM_RR      = 2.5;
        private const double PM_BRK_BUF = 2.0;

        // ── Asia parameters ────────────────────────────────────────────────
        private const double ASIA_GAP_LO = 30.0;
        private const double ASIA_GAP_HI = 80.0;
        private const double ASIA_STOP   = 25.0;
        private const double ASIA_RR     = 3.0;
        private static readonly HashSet<int> ASIA_WK = new HashSet<int> { 8, 11 };

        // ── Risk engine v2 ─────────────────────────────────────────────────
        private const double DLL         = 500.0;   // internal daily halt (policy-sim optimum)
        private const double LUCID_GUARD = 1150.0;  // hard day floor ($1,200 Lucid - $50 slip buffer)
        private const double RISK_ORB    = 565.0;   // per-contract worst loss incl. costs
        private const double RISK_REJ    = 415.0;
        private const double RISK_PM     = 455.0;
        private const double RISK_ASIA   = 515.0;
        private const double SCALE_GATE  = 1500.0;  // lifetime P&L before 2c
        private const double RAMP_GATE   = 800.0;   // lifetime P&L before REJ/PM/Asia unlock
        private const int    MAX_CON     = 2;
        private const int    PYR_WARMUP  = 5;
        private static readonly HashSet<int> WEAK_MONTHS = new HashSet<int> { 6, 9, 12 };

        // ── NT8 properties ─────────────────────────────────────────────────
        // MaxContracts=1 for 50K accounts ($1,200 DLL): a 2c morning stop with
        // gap-through printed -$1,240 in the Analyzer — over the DLL on its own.
        // Set 2 ONLY on account tiers with DLL ≥ $2,400.
        [NinjaScriptProperty] public int  MaxContracts          { get; set; }
        [NinjaScriptProperty] public bool EvalMode              { get; set; }
        [NinjaScriptProperty] public bool RampMode              { get; set; }  // morning-only until +$800 lifetime
        [NinjaScriptProperty] public bool RejectionEnabled      { get; set; }
        [NinjaScriptProperty] public bool AsiaEnabled           { get; set; }
        [NinjaScriptProperty] public bool PyramidEnabled        { get; set; }
        [NinjaScriptProperty] public bool SecondBreakoutEnabled { get; set; }
        [NinjaScriptProperty] public bool SkipMondays           { get; set; }
        [NinjaScriptProperty] public bool SkipFridays           { get; set; }  // morning only; Fri OOS PF 1.21 → default OFF
        [NinjaScriptProperty] public bool PmOrbEnabled          { get; set; }
        [NinjaScriptProperty] public bool PmSkipFridays         { get; set; }

        // ── Morning OR state ───────────────────────────────────────────────
        private double orHi, orLo;
        private bool   orBuilt;

        // ── Rejection state ────────────────────────────────────────────────
        private double rejSumPV, rejSumVol, rejVwap;   // typical-price VWAP
        private bool   rejExtended, rejSawCross, rejCrossUp;
        private bool   rejPrevAbove, rejPrevSet;
        private bool   rejTraded;

        // ── PM OR state ────────────────────────────────────────────────────
        private double pmOrHi, pmOrLo;
        private bool   pmOrBuilt;
        private bool   pmTraded;

        // ── Daily session state ────────────────────────────────────────────
        private bool   tradedToday;      // morning ORB traded
        private bool   secondReady;
        private bool   pyramidDone;
        private bool   inPos;
        private bool   posLong;
        private double entryPx;
        private string activeSig;
        private double dailyPnL;
        private double morningPnL;       // closed P&L of ORB1/ORB2/PYR today (PM gate)

        // ── Asia state ─────────────────────────────────────────────────────
        private double cmeClose;
        private bool   asiaTraded;

        // ── Lifetime tracking ──────────────────────────────────────────────
        private int      totalTrades;
        private double   lifetimePnL;
        private DateTime lastDay;

        // ── Prev-day context (RTH 9:30-16:00 only) ─────────────────────────
        private double prevClose;
        private double prevDayHigh, prevDayLow, prevDayVwap;
        private double dayHi, dayLo, dayVwapPV, dayVwapV;
        private double rthClose;
        private double vwapAt935, vwapAt944;
        private double orClosePx;
        private Queue<double> dailyRanges = new Queue<double>();

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "CruzCapital NQ v11.2 — ORB + rejection + PM + Asia, risk engine v2";
                Name        = "CruzCapitalNQ v11";
                Calculate   = Calculate.OnBarClose;
                EntriesPerDirection          = 2;
                EntryHandling                = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds    = 300;
                BarsRequiredToTrade          = 20;

                MaxContracts          = 1;      // 50K prefunded: ALWAYS 1 (see property note)
                EvalMode              = false;
                RampMode              = true;   // fresh account: morning-only until +$800
                RejectionEnabled      = true;
                AsiaEnabled           = true;
                PyramidEnabled        = true;
                SecondBreakoutEnabled = true;
                SkipMondays           = true;
                SkipFridays           = false;
                PmOrbEnabled          = true;
                PmSkipFridays         = true;
            }
            else if (State == State.DataLoaded)
            {
                ResetDay();
                totalTrades = 0;
                lifetimePnL = 0;
                lastDay     = DateTime.MinValue;
            }
        }

        // Risk-room gate: contracts allowed so the day can never breach LUCID_GUARD
        private int RoomQty(int wantQty, double riskPerContract)
        {
            int room = (int)Math.Floor((LUCID_GUARD + dailyPnL) / riskPerContract);
            return Math.Min(wantQty, Math.Max(0, room));
        }

        private bool RampUnlocked()
        {
            return !RampMode || lifetimePnL >= RAMP_GATE;
        }

        protected override void OnBarUpdate()
        {
            if (BarsInProgress != 0) return;
            if (CurrentBars[0] < BarsRequiredToTrade) return;

            DateTime now  = Time[0];
            TimeSpan ts   = now.TimeOfDay;
            int month     = now.Month;
            int dow       = (int)now.DayOfWeek;   // 0=Sun 1=Mon ... 5=Fri

            // ── New day: roll RTH context, then reset ──────────────────────
            if (now.Date != lastDay)
            {
                if (rthClose > 0)
                    prevClose = rthClose;
                if (dayHi > 0 && dayLo < double.MaxValue)
                {
                    prevDayHigh = dayHi;
                    prevDayLow  = dayLo;
                    dailyRanges.Enqueue(dayHi - dayLo);
                    if (dailyRanges.Count > REGIME_LEN) dailyRanges.Dequeue();
                }
                if (dayVwapV > 0)
                    prevDayVwap = dayVwapPV / dayVwapV;
                ResetDay();
                lastDay = now.Date;
            }

            // ── CME close capture (17:00) ──────────────────────────────────
            if (ts >= new TimeSpan(17, 0, 0) && ts < new TimeSpan(17, 2, 0) && cmeClose == 0)
            {
                cmeClose = Close[0];
                return;
            }

            // ── Asia session ───────────────────────────────────────────────
            if (ts >= new TimeSpan(18, 0, 0) && ts < new TimeSpan(21, 0, 0))
            {
                if (AsiaEnabled && !EvalMode && !asiaTraded && RampUnlocked()
                        && ts >= new TimeSpan(18, 15, 0) && ts < new TimeSpan(18, 16, 0))
                    TryAsiaEntry(month, dow);
                return;
            }

            // ── RTH only (9:30-16:00); evening bars must not touch context ─
            if (ts < new TimeSpan(9, 30, 0))  return;
            if (ts >= new TimeSpan(16, 0, 0)) return;

            // ── Context tracking (all days, incl. skip days) ───────────────
            if (Volume[0] > 0)
            {
                dayVwapPV += Close[0] * Volume[0];      // close-based (confidence score, Python parity)
                dayVwapV  += Volume[0];
                dayHi = Math.Max(dayHi, High[0]);
                dayLo = Math.Min(dayLo, Low[0]);

                rejSumPV  += (High[0] + Low[0] + Close[0]) / 3.0 * Volume[0];  // typical (rejection)
                rejSumVol += Volume[0];
                rejVwap    = rejSumPV / rejSumVol;
            }
            rthClose = Close[0];
            if (ts.Hours == 9 && ts.Minutes == 35 && vwapAt935 == 0 && dayVwapV > 0)
                vwapAt935 = dayVwapPV / dayVwapV;
            if (ts.Hours == 9 && ts.Minutes == 44 && dayVwapV > 0)
            {
                orClosePx = Close[0];
                vwapAt944 = dayVwapPV / dayVwapV;
            }

            // Rejection cross/extension state (from 10:00, every RTH day)
            if (rejVwap > 0 && ts >= new TimeSpan(10, 0, 0))
            {
                bool above = Close[0] > rejVwap;
                if (rejPrevSet)
                {
                    if (!rejExtended && Math.Abs(Close[0] - rejVwap) > REJ_EXTEND)
                        rejExtended = true;
                    if (rejExtended && !rejTraded && ts >= new TimeSpan(11, 0, 0)
                            && ts < new TimeSpan(13, 0, 0))
                    {
                        bool cu = !rejPrevAbove && above;
                        bool cd = rejPrevAbove && !above;
                        if (!rejSawCross)
                        {
                            if (cu)      { rejSawCross = true; rejCrossUp = true;  }
                            else if (cd) { rejSawCross = true; rejCrossUp = false; }
                        }
                        else if (cu || cd)
                        {
                            bool rejectShort = rejCrossUp && cd;    // up-reclaim failed
                            bool rejectLong  = !rejCrossUp && cu;   // down-reclaim failed
                            if (rejectShort || rejectLong)
                                TryRejectionEntry(rejectLong, month, dow);
                        }
                    }
                }
                rejPrevAbove = above;
                rejPrevSet   = true;
            }
            else if (rejVwap > 0)
            {
                rejPrevAbove = Close[0] > rejVwap;
                rejPrevSet   = true;
            }

            if (ts >= new TimeSpan(15, 55, 0)) return;

            // ── Force-flat rejection position at 13:00 (no return — the same
            //    bar must still feed PM OR building below) ────────────────────
            if (inPos && activeSig == "REJ" && ts >= new TimeSpan(13, 0, 0))
            {
                if (Position.MarketPosition == MarketPosition.Long)       ExitLong("REJ");
                else if (Position.MarketPosition == MarketPosition.Short) ExitShort("REJ");
                else inPos = false;
            }

            // ── Day-level filters (morning/PM path) ────────────────────────
            if (SkipMondays && dow == 1) return;
            if (SkipFridays && dow == 5) return;
            if (dailyPnL <= -DLL) return;

            // ── Build morning OR (9:30-9:44) ───────────────────────────────
            if (ts < new TimeSpan(9, 45, 0))
            {
                orHi = orHi == double.MinValue ? High[0] : Math.Max(orHi, High[0]);
                orLo = orLo == double.MaxValue ? Low[0]  : Math.Min(orLo, Low[0]);

                if (ts >= new TimeSpan(9, 44, 0) && !orBuilt)
                {
                    orBuilt = true;
                    Draw.HorizontalLine(this, "orH", false, orHi, Brushes.Cyan,    DashStyleHelper.Dot, 1);
                    Draw.HorizontalLine(this, "orL", false, orLo, Brushes.Magenta, DashStyleHelper.Dot, 1);
                }
                return;
            }

            // ── Build PM OR (13:00-13:14) — before morning validity ────────
            if (PmOrbEnabled && ts >= new TimeSpan(13, 0, 0) && ts < new TimeSpan(13, 15, 0))
            {
                if (!pmOrBuilt)
                {
                    pmOrHi = pmOrHi == 0               ? High[0] : Math.Max(pmOrHi, High[0]);
                    pmOrLo = pmOrLo == double.MaxValue ? Low[0]  : Math.Min(pmOrLo, Low[0]);
                }
                if (ts >= new TimeSpan(13, 14, 0)) pmOrBuilt = true;
            }

            // ── PM ORB entry (13:15-14:00) ─────────────────────────────────
            if (PmOrbEnabled && pmOrBuilt && ts >= new TimeSpan(13, 15, 0)
                    && ts <= new TimeSpan(14, 0, 0) && !pmTraded && !inPos)
            {
                TryPMEntry(month, dow);
                return;
            }

            // ── Morning OR validity + regime gate ──────────────────────────
            if (!orBuilt) return;
            double orRange = orHi - orLo;
            if (orRange < MIN_OR || orRange > MAX_OR) return;
            if (dailyRanges.Count >= 3)
            {
                double avgRange = 0;
                foreach (double r in dailyRanges) avgRange += r;
                avgRange /= dailyRanges.Count;
                if (orRange < avgRange * REGIME_MIN) return;
            }

            if (ts >= new TimeSpan(10, 30, 0) && !inPos) return;

            if (inPos) { TryPyramid(); return; }

            // ── Morning entries (9:46-10:30) ───────────────────────────────
            if (!tradedToday)
                TryUSEntry(ts, month, "ORB1");
            else if (SecondBreakoutEnabled && secondReady && ts >= new TimeSpan(10, 0, 0))
                TryUSEntry(ts, month, "ORB2");
        }

        // ── Morning ORB entry ──────────────────────────────────────────────
        private void TryUSEntry(TimeSpan ts, int month, string sigName)
        {
            if (Position.MarketPosition != MarketPosition.Flat) return;

            double close = Close[0];
            bool goLong  = close > orHi + BRK_BUF;
            bool goShort = close < orLo - BRK_BUF;
            if (!goLong && !goShort) return;

            if (ts < new TimeSpan(9, 46, 0)) return;   // first-bar skip

            if (prevClose <= 0) return;                 // gap filter (Python parity)
            double gap = (orHi + orLo) / 2.0 - prevClose;
            if (goLong  && gap <=  GAP_FILTER) return;
            if (goShort && gap >= -GAP_FILTER) return;

            if (Volume[0] < MIN_BRK_VOL) return;

            if (CalcConfidenceScore(goLong) < 3) return;

            double rr      = EvalMode ? EVAL_RR : FUNDED_RR;
            double effStop = STOP_PT + STOP_BUF;
            int    qty     = (!EvalMode && lifetimePnL >= SCALE_GATE) ? 2 : 1;
            if (qty == 2 && WEAK_MONTHS.Contains(month)) qty = 1;
            qty = Math.Min(qty, MaxContracts);
            qty = RoomQty(qty, RISK_ORB);
            if (qty <= 0) return;

            if (goLong)
            {
                EnterLong(qty, sigName);
                SetStopLoss(sigName,    CalculationMode.Price, close - effStop,        false);
                SetProfitTarget(sigName, CalculationMode.Price, close + effStop * rr);
                posLong = true;
            }
            else
            {
                EnterShort(qty, sigName);
                SetStopLoss(sigName,    CalculationMode.Price, close + effStop,        false);
                SetProfitTarget(sigName, CalculationMode.Price, close - effStop * rr);
                posLong = false;
            }

            entryPx     = close;
            inPos       = true;
            tradedToday = true;
            pyramidDone = false;
            activeSig   = sigName;
            if (sigName == "ORB2") secondReady = false;   // exactly one re-entry
        }

        // ── Rejection entry (11:00-13:00, ONLY when morning didn't trade) ──
        private void TryRejectionEntry(bool goLong, int month, int dow)
        {
            if (!RejectionEnabled || rejTraded || inPos) return;
            if (Position.MarketPosition != MarketPosition.Flat) return;
            if (tradedToday) return;                    // portfolio sim: PF 0.46-0.97 on morning
                                                        // days vs 1.45 on no-morning days
            if (!RampUnlocked()) return;
            if (SkipMondays && dow == 1) return;
            if (REJ_WEAK.Contains(month)) return;
            if (dailyPnL <= -DLL) return;

            int qty = RoomQty(1, RISK_REJ);
            if (qty <= 0) return;

            double close = Close[0];
            if (goLong)
            {
                EnterLong(qty, "REJ");
                SetStopLoss("REJ",    CalculationMode.Price, close - REJ_STOP,          false);
                SetProfitTarget("REJ", CalculationMode.Price, close + REJ_STOP * REJ_RR);
                posLong = true;
            }
            else
            {
                EnterShort(qty, "REJ");
                SetStopLoss("REJ",    CalculationMode.Price, close + REJ_STOP,          false);
                SetProfitTarget("REJ", CalculationMode.Price, close - REJ_STOP * REJ_RR);
                posLong = false;
            }

            rejTraded = true;
            inPos     = true;
            entryPx   = close;
            activeSig = "REJ";
        }

        // ── PM ORB entry ───────────────────────────────────────────────────
        private void TryPMEntry(int month, int dow)
        {
            if (Position.MarketPosition != MarketPosition.Flat) return;
            if (SkipMondays   && dow == 1) return;
            if (PmSkipFridays && dow == 5) return;
            if (!RampUnlocked()) return;
            if (dailyPnL <= -DLL) return;
            // v11.2: skip PM when the morning ORB traded and lost — that cohort
            // is PF 0.81 (-$3,374 / 53 trades, final_polish.py A1). Morning-win
            // days stay (PF 1.58). Also caps morning-loss days at one stop.
            if (tradedToday && morningPnL < 0) return;

            double pmRange = pmOrHi - pmOrLo;
            if (pmRange < PM_MIN_OR || pmRange > PM_MAX_OR) return;

            double close = Close[0];
            bool goLong  = close > pmOrHi + PM_BRK_BUF;
            bool goShort = close < pmOrLo - PM_BRK_BUF;
            if (!goLong && !goShort) return;

            int qty = RoomQty(1, RISK_PM);
            if (qty <= 0) return;

            if (goLong)
            {
                EnterLong(qty, "PM_ORB");
                SetStopLoss("PM_ORB",    CalculationMode.Price, close - PM_STOP,        false);
                SetProfitTarget("PM_ORB", CalculationMode.Price, close + PM_STOP * PM_RR);
                posLong = true;
            }
            else
            {
                EnterShort(qty, "PM_ORB");
                SetStopLoss("PM_ORB",    CalculationMode.Price, close + PM_STOP,        false);
                SetProfitTarget("PM_ORB", CalculationMode.Price, close - PM_STOP * PM_RR);
                posLong = false;
            }

            pmTraded  = true;
            inPos     = true;
            entryPx   = close;
            activeSig = "PM_ORB";
        }

        // ── Pyramid (morning signals only, funded) ─────────────────────────
        private void TryPyramid()
        {
            if (!PyramidEnabled || EvalMode || pyramidDone) return;
            if (MaxContracts < 2) return;   // pyramiding needs 2c headroom — never on 50K
            if (activeSig != "ORB1" && activeSig != "ORB2") return;
            if (totalTrades < PYR_WARMUP) return;
            if (lifetimePnL < SCALE_GATE) return;
            if (Position.MarketPosition == MarketPosition.Flat) { inPos = false; return; }
            if (Position.Quantity >= Math.Min(MAX_CON, MaxContracts)) return;
            if (RoomQty(1, RISK_ORB) <= 0) return;

            double effStop = STOP_PT + STOP_BUF;

            if (posLong && Close[0] >= entryPx + effStop)
            {
                EnterLong(1, "PYR");
                SetStopLoss(activeSig, CalculationMode.Price, entryPx, false);
                SetStopLoss("PYR",    CalculationMode.Price, entryPx, false);
                pyramidDone = true;
            }
            else if (!posLong && Close[0] <= entryPx - effStop)
            {
                EnterShort(1, "PYR");
                SetStopLoss(activeSig, CalculationMode.Price, entryPx, false);
                SetStopLoss("PYR",    CalculationMode.Price, entryPx, false);
                pyramidDone = true;
            }
        }

        // ── Asia gap entry (18:15 bar only) ────────────────────────────────
        private void TryAsiaEntry(int month, int dow)
        {
            if (Position.MarketPosition != MarketPosition.Flat) return;
            if (dow == 4) return;                    // Thursday
            if (ASIA_WK.Contains(month)) return;     // Aug, Nov
            if (cmeClose == 0) return;
            if (dailyPnL <= -DLL) return;

            double gap = Close[0] - cmeClose;
            if (Math.Abs(gap) < ASIA_GAP_LO || Math.Abs(gap) > ASIA_GAP_HI) return;

            int qty = (lifetimePnL >= SCALE_GATE) ? 2 : 1;
            qty = Math.Min(qty, MaxContracts);
            qty = RoomQty(qty, RISK_ASIA);
            if (qty <= 0) return;

            if (gap > 0)
            {
                EnterLong(qty, "ASIA");
                SetStopLoss("ASIA",    CalculationMode.Price, Close[0] - ASIA_STOP,             false);
                SetProfitTarget("ASIA", CalculationMode.Price, Close[0] + ASIA_STOP * ASIA_RR);
            }
            else
            {
                EnterShort(qty, "ASIA");
                SetStopLoss("ASIA",    CalculationMode.Price, Close[0] + ASIA_STOP,             false);
                SetProfitTarget("ASIA", CalculationMode.Price, Close[0] - ASIA_STOP * ASIA_RR);
            }

            asiaTraded = true;
        }

        // ── Confidence score (0-4) ─────────────────────────────────────────
        private int CalcConfidenceScore(bool isLong)
        {
            if (prevDayHigh <= 0 || prevDayLow <= 0 || prevDayHigh <= prevDayLow) return 4;
            if (orClosePx <= 0) return 4;

            double H  = prevDayHigh, L = prevDayLow, C = prevClose;
            double P  = (H + L + C) / 3.0;
            double R1 = 2 * P - L;
            double R2 = P + (H - L);
            double S1 = 2 * P - H;
            double S2 = P - (H - L);

            int conf = 0;
            double px = orClosePx;

            if ((isLong  && px > P) || (!isLong && px < P)) conf++;

            if (prevDayVwap > 0)
                if ((isLong  && px > prevDayVwap) || (!isLong && px < prevDayVwap)) conf++;

            if ( isLong && px >= R1 && px <= R2) conf++;
            else if (!isLong && px >= S2 && px <= S1) conf++;

            if (vwapAt935 > 0 && vwapAt944 > 0)
            {
                double slope = vwapAt944 - vwapAt935;
                if ((isLong && slope > 0) || (!isLong && slope < 0)) conf++;
            }

            return conf;
        }

        // ── P&L tracking; ORB2 arming from ORB1 target exits only ──────────
        protected override void OnExecutionUpdate(Execution execution,
            string executionId, double price, int quantity,
            MarketPosition marketPosition, string orderId, DateTime time)
        {
            if (execution.Order == null) return;
            if (execution.Order.OrderState != OrderState.Filled) return;

            OrderAction action = execution.Order.OrderAction;
            bool isClose = (action == OrderAction.Sell || action == OrderAction.BuyToCover);
            if (!isClose) return;

            int n = SystemPerformance.AllTrades.Count;
            if (n > 0)
            {
                double tradePnL = SystemPerformance.AllTrades[n - 1].ProfitCurrency;
                dailyPnL    += tradePnL;
                lifetimePnL += tradePnL;

                string fromSig = execution.Order.FromEntrySignal ?? "";
                if (fromSig == "ORB1" || fromSig == "ORB2" || fromSig == "PYR")
                    morningPnL += tradePnL;      // feeds the v11.2 PM gate

                if (tradePnL > 0
                        && execution.Order.Name == "Profit target"
                        && fromSig == "ORB1")
                    secondReady = true;
            }

            totalTrades++;
            inPos = false;
        }

        // ── Daily reset ────────────────────────────────────────────────────
        private void ResetDay()
        {
            orHi        = double.MinValue;
            orLo        = double.MaxValue;
            orBuilt     = false;
            tradedToday = false;
            secondReady = false;
            pyramidDone = false;
            inPos       = false;
            entryPx     = 0;
            activeSig   = "";
            dailyPnL    = 0;
            morningPnL  = 0;
            cmeClose    = 0;
            asiaTraded  = false;
            pmOrHi      = 0;
            pmOrLo      = double.MaxValue;
            pmOrBuilt   = false;
            pmTraded    = false;
            rejSumPV    = 0; rejSumVol = 0; rejVwap = 0;
            rejExtended = false; rejSawCross = false; rejCrossUp = false;
            rejPrevAbove = false; rejPrevSet = false;
            rejTraded   = false;
            dayHi       = 0;
            dayLo       = double.MaxValue;
            dayVwapPV   = 0;
            dayVwapV    = 0;
            rthClose    = 0;
            vwapAt935   = 0;
            vwapAt944   = 0;
            orClosePx   = 0;
        }
    }
}
