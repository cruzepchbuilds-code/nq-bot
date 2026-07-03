// CruzCapitalNQ_v10_4.cs — NinjaTrader 8 Strategy
// CruzCapital NQ ORB — Lucid Trading 50K Direct / Apex Eval
//
// HOW TO INSTALL:
//   1. Copy this file to:
//      C:\Users\<you>\Documents\NinjaTrader 8\bin\Custom\Strategies\CruzCapitalNQ_v10_4.cs
//   2. NT8: New → NinjaScript Editor → Compile All (F5)
//   3. On NQ Continuous (#F) 1-min chart:
//      Strategies panel → Add → CruzCapitalNQ v10.4
//   4. Set Account = your Lucid account, enable "Automated" trading
//   5. Remove/disable the old v10.3 instance first — never run both at once
//
// ── SESSION OVERVIEW ──────────────────────────────────────────────────────────
//
//  MORNING ORB   9:30-9:44 range  |  9:46-10:30 entry
//    OR filter:  55-110pt AND ≥18% of 14-day avg daily range (regime gate)
//    Gap filter: OR-mid must be >20pt above prior RTH close (longs)
//                or >20pt below (shorts) — no-gap days = no morning trade
//    Entry:      close > OR high + 4pt (long) | close < OR low - 4pt (short)
//                breakout bar volume ≥ 200
//    Stop:       27pt from entry (22pt fixed + 5pt buffer)
//    Target:     2R eval (54pt) | 3R funded (81pt)
//    Filter:     confidence score ≥ 3 (pivot + VWAP + zone + slope, 0-4)
//    Skip:       Monday, first bar (9:45); Friday optional (default trades Fri)
//    OOS PF:     5.42  N=31/18mo  Net=$37,770 (with gap+regime filters)
//
//  PM ORB       13:00-13:14 range  |  13:15-14:00 entry
//    OR filter:  15-60pt
//    Entry:      close > OR high + 2pt (long) | close < OR low - 2pt (short)
//    Stop:       22pt  |  Target: 2.5R (55pt) — v10.4: was 2R, sweep says 2.5R
//    Skip:       Monday, Friday
//    OOS PF:     1.363  N=140/18mo  Net=$14,200
//
//  ASIA GAP     18:15 bar only
//    Gap filter: 30-80pt from 17:00 CME close
//    Stop:       25pt  |  Target: 3R (75pt) — v10.4: was 15pt/1.5R (IS PF 0.92,
//                too tight for overnight vol); 25/3.0 → IS 1.24, OOS +$3,675
//    Skip:       Thursday, August, November
//    Disabled in eval mode
//
//  PYRAMID      +1c at 1R, stops → breakeven — MORNING SIGNALS ONLY
//    Requires:   $1,500 lifetime profit + 5 prior trades
//    Disabled in eval mode
//
//  SECOND BREAK exactly ONE re-entry, only after ORB1 exits at its profit
//               target, entries from 10:00 AM
//
//  DLL:  halt at -$900/day (internal; Lucid enforces $1,200 — 900 blocks Asia
//        after morning -$540 + PM -$440 = -$980 both stop out)
//  SCALE: 1c until lifetimePnL ≥ $1,500, then 2c funded (1c in Jun/Sep/Dec)
//
// ── CHANGELOG ────────────────────────────────────────────────────────────────
//  v10.4 (2026-07-02) — parity audit vs Python backtest + PM sweep:
//    FIX  Prev-day context (high/low/VWAP) was contaminated by evening bars
//         16:00-24:00 — now RTH-only (9:30-16:00), matching Python. Pivots and
//         prev-VWAP in the confidence score were systematically wrong before.
//    FIX  prevClose now = prior 16:00 RTH close tracked manually (was daily
//         series Closes[1][1] — settlement value, off-by-one session risk).
//         Daily data series removed entirely.
//    FIX  Second breakout: exactly ONE re-entry per day, armed only when ORB1
//         exits at its profit target (was: re-armed on ANY profitable close,
//         including overnight Asia wins → could re-enter after a morning LOSS;
//         also allowed unlimited chained re-entries).
//    FIX  Pyramid restricted to ORB1/ORB2 (could previously fire on PM trades
//         using the morning 27pt R instead of PM's 22pt).
//    ADD  Gap filter (was in Python all along, missing here): the 8 extra OOS
//         trades without it lost -$4,545 collectively. OOS PF 3.54 → 5.42.
//    ADD  Regime gate: OR ≥ 18% of 14-day avg daily range (Python parity).
//    ADD  Volume ≥ 200 on breakout bar (Python parity; almost always true).
//    ADD  Funded 2c capped to 1c in weak months Jun/Sep/Dec (Python parity).
//    CHG  PM ORB target 2R → 2.5R (44 → 55pt): stop×RR sweep beats 2R on
//         IS PF 1.24v1.16, OOS PF 1.36v1.35, OOS net +$1,520. Same stop.
//  v10.3 (2026-07-01):
//    - PM OR max 50→60pt; entry cutoff 14:15→14:00; PM fires on morning-skip
//      days; PM DOW filter added; CalcScore double-gate removed
//  v10.2 (2026-07-01): PM ORB added
//  v10.1: SkipFridays, confidence ≥3, first-bar skip

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
    public class CruzCapitalNQ_v10_4 : Strategy
    {
        // ── Morning ORB parameters ─────────────────────────────────────────
        private const double STOP_PT    = 22.0;  // fixed stop distance
        private const double STOP_BUF   = 5.0;   // stop placed this far beyond OR edge
        private const double BRK_BUF    = 4.0;   // close must exceed OR edge by this
        private const double MIN_OR     = 55.0;
        private const double MAX_OR     = 110.0;
        private const double FUNDED_RR  = 3.0;
        private const double EVAL_RR    = 2.0;
        private const double GAP_FILTER = 20.0;  // OR-mid vs prior RTH close, aligned
        private const double MIN_BRK_VOL = 200.0; // breakout bar volume floor
        private const double REGIME_MIN  = 0.18;  // OR / 14-day avg range floor
        private const int    REGIME_LEN  = 14;

        // ── PM ORB parameters ──────────────────────────────────────────────
        private const double PM_STOP    = 22.0;
        private const double PM_MIN_OR  = 15.0;
        private const double PM_MAX_OR  = 60.0;
        private const double PM_RR      = 2.5;   // v10.4: was 2.0 — sweep-confirmed
        private const double PM_BRK_BUF = 2.0;

        // ── Asia parameters ────────────────────────────────────────────────
        private const double ASIA_GAP_LO = 30.0;
        private const double ASIA_GAP_HI = 80.0;
        private const double ASIA_STOP   = 25.0;  // v10.4 sweep: 15pt was IS PF 0.92 (too tight overnight); 25pt IS 1.24
        private const double ASIA_RR     = 3.0;   // 75pt target — OOS $8,708 vs $5,033 at 15/1.5
        private static readonly HashSet<int> ASIA_WK = new HashSet<int> { 8, 11 };

        // ── Account / risk ─────────────────────────────────────────────────
        private const double DLL        = 900.0;   // internal DLL — see header
        private const double SCALE_GATE = 1500.0;  // lifetime profit before 2c
        private const int    MAX_CON    = 2;
        private const int    PYR_WARMUP = 5;
        private static readonly HashSet<int> WEAK_MONTHS = new HashSet<int> { 6, 9, 12 };

        // ── User-configurable (NT8 strategy properties) ────────────────────
        [NinjaScriptProperty] public bool EvalMode             { get; set; }
        [NinjaScriptProperty] public bool AsiaEnabled          { get; set; }
        [NinjaScriptProperty] public bool PyramidEnabled       { get; set; }
        [NinjaScriptProperty] public bool SecondBreakoutEnabled { get; set; }
        [NinjaScriptProperty] public bool SkipMondays          { get; set; }
        [NinjaScriptProperty] public bool SkipFridays          { get; set; }  // morning: Fri OOS PF 1.21 (net +, default OFF)
        [NinjaScriptProperty] public bool PmOrbEnabled         { get; set; }
        [NinjaScriptProperty] public bool PmSkipFridays        { get; set; }  // PM: Fri OOS PF 0.97 (net -, keep true)

        // ── Morning OR state ───────────────────────────────────────────────
        private double orHi, orLo;
        private bool   orBuilt;

        // ── PM OR state ────────────────────────────────────────────────────
        private double pmOrHi, pmOrLo;
        private bool   pmOrBuilt;
        private bool   pmTraded;

        // ── Daily session state ────────────────────────────────────────────
        private bool   tradedToday;
        private bool   secondReady;   // armed by ORB1 profit-target exit only
        private bool   pyramidDone;
        private bool   inPos;
        private bool   posLong;
        private double entryPx;
        private string activeSig;
        private double dailyPnL;

        // ── Asia state ─────────────────────────────────────────────────────
        private double cmeClose;
        private bool   asiaTraded;

        // ── Lifetime tracking ──────────────────────────────────────────────
        private int      totalTrades;
        private double   lifetimePnL;
        private DateTime lastDay;

        // ── Prev-day context (RTH 9:30-16:00 only) ─────────────────────────
        private double prevClose;                 // prior RTH session close
        private double prevDayHigh, prevDayLow, prevDayVwap;
        private double dayHi, dayLo, dayVwapPV, dayVwapV;
        private double rthClose;                  // running last RTH close today
        private double vwapAt935, vwapAt944;
        private double orClosePx;
        private Queue<double> dailyRanges = new Queue<double>();  // 14-day regime window

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description  = "CruzCapital NQ ORB v10.4 — morning + PM + Asia (Python parity)";
                Name         = "CruzCapitalNQ v10.4";
                Calculate    = Calculate.OnBarClose;
                EntriesPerDirection         = 2;
                EntryHandling               = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds    = 300;  // flatten at 3:55 PM ET
                BarsRequiredToTrade          = 20;

                EvalMode              = false;
                AsiaEnabled           = true;
                PyramidEnabled        = true;
                SecondBreakoutEnabled = true;
                SkipMondays           = true;
                SkipFridays           = false;  // Fri morning OOS PF 1.21 — net positive, leave ON
                PmOrbEnabled          = true;
                PmSkipFridays         = true;   // Fri PM OOS PF 0.97 — net negative, always skip
            }
            else if (State == State.DataLoaded)
            {
                ResetDay();
                totalTrades = 0;
                lifetimePnL = 0;
                lastDay     = DateTime.MinValue;
            }
        }

        protected override void OnBarUpdate()
        {
            if (BarsInProgress != 0) return;
            if (CurrentBars[0] < BarsRequiredToTrade) return;

            DateTime now  = Time[0];
            TimeSpan ts   = now.TimeOfDay;
            int month     = now.Month;
            int dow       = (int)now.DayOfWeek;  // 0=Sun 1=Mon 2=Tue 3=Wed 4=Thu 5=Fri

            // ── New day: roll RTH context, then reset ──────────────────────
            if (now.Date != lastDay)
            {
                if (rthClose > 0)
                    prevClose = rthClose;
                if (dayHi > 0 && dayLo < double.MaxValue)
                {
                    prevDayHigh = dayHi;
                    prevDayLow  = dayLo;
                    dailyRanges.Enqueue(dayHi - dayLo);      // regime window
                    if (dailyRanges.Count > REGIME_LEN) dailyRanges.Dequeue();
                }
                if (dayVwapV > 0)
                    prevDayVwap = dayVwapPV / dayVwapV;
                ResetDay();
                lastDay = now.Date;
            }

            // ── Capture CME close at 5 PM ──────────────────────────────────
            if (ts >= new TimeSpan(17, 0, 0) && ts < new TimeSpan(17, 2, 0) && cmeClose == 0)
            {
                cmeClose = Close[0];
                return;
            }

            // ── Asia session (6-9 PM ET) ───────────────────────────────────
            if (ts >= new TimeSpan(18, 0, 0) && ts < new TimeSpan(21, 0, 0))
            {
                if (AsiaEnabled && !EvalMode && !asiaTraded
                        && ts >= new TimeSpan(18, 15, 0) && ts < new TimeSpan(18, 16, 0))
                    TryAsiaEntry(month, dow);
                return;
            }

            // ── RTH only past this point (9:30-16:00) ──────────────────────
            // v10.4 FIX: evening bars (16:00-24:00) previously fell through to
            // the tracking block and contaminated prev-day high/low/VWAP.
            if (ts < new TimeSpan(9, 30, 0))  return;
            if (ts >= new TimeSpan(16, 0, 0)) return;

            // ── RTH VWAP + range tracking (runs on ALL days incl. skip days) ─
            if (Volume[0] > 0)
            {
                dayVwapPV += Close[0] * Volume[0];
                dayVwapV  += Volume[0];
                dayHi = Math.Max(dayHi, High[0]);
                dayLo = Math.Min(dayLo, Low[0]);
            }
            rthClose = Close[0];
            if (ts.Hours == 9 && ts.Minutes == 35 && vwapAt935 == 0 && dayVwapV > 0)
                vwapAt935 = dayVwapPV / dayVwapV;
            if (ts.Hours == 9 && ts.Minutes == 44 && dayVwapV > 0)
            {
                orClosePx = Close[0];
                vwapAt944 = dayVwapPV / dayVwapV;
            }

            if (ts >= new TimeSpan(15, 55, 0)) return;

            // ── Day-level filters ──────────────────────────────────────────
            if (SkipMondays && dow == 1) return;
            if (SkipFridays && dow == 5) return;
            if (dailyPnL <= -DLL) return;

            // ── Build morning OR (9:30-9:44) ──────────────────────────────
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

            // ── Build PM OR (13:00-13:14) — before morning validity so PM
            //    fires even on morning-skip days ─────────────────────────────
            if (PmOrbEnabled && ts >= new TimeSpan(13, 0, 0) && ts < new TimeSpan(13, 15, 0))
            {
                if (!pmOrBuilt)
                {
                    pmOrHi = pmOrHi == 0               ? High[0] : Math.Max(pmOrHi, High[0]);
                    pmOrLo = pmOrLo == double.MaxValue ? Low[0]  : Math.Min(pmOrLo, Low[0]);
                }
                if (ts >= new TimeSpan(13, 14, 0)) pmOrBuilt = true;
            }

            // ── PM ORB entry (13:15-14:00) ────────────────────────────────
            if (PmOrbEnabled && pmOrBuilt && ts >= new TimeSpan(13, 15, 0)
                    && ts <= new TimeSpan(14, 0, 0) && !pmTraded && !inPos)
            {
                TryPMEntry(month, dow);
                return;
            }

            // ── Morning OR validity ────────────────────────────────────────
            if (!orBuilt) return;
            double orRange = orHi - orLo;
            if (orRange < MIN_OR || orRange > MAX_OR) return;

            // Regime gate: OR must be ≥18% of the 14-day avg daily RTH range.
            // Matches Python regime.classify(); passes when <3 days of history.
            if (dailyRanges.Count >= 3)
            {
                double avgRange = 0;
                foreach (double r in dailyRanges) avgRange += r;
                avgRange /= dailyRanges.Count;
                if (orRange < avgRange * REGIME_MIN) return;
            }

            // ── 10:30 cutoff for morning entries ───────────────────────────
            if (ts >= new TimeSpan(10, 30, 0) && !inPos) return;

            // ── Manage open position (pyramid) ─────────────────────────────
            if (inPos) { TryPyramid(); return; }

            // ── Morning entries (9:46-10:30) ──────────────────────────────
            if (!tradedToday)
                TryUSEntry(ts, month, "ORB1");
            else if (SecondBreakoutEnabled && secondReady && ts >= new TimeSpan(10, 0, 0))
                TryUSEntry(ts, month, "ORB2");
        }

        // ── Morning ORB entry ──────────────────────────────────────────────
        private void TryUSEntry(TimeSpan ts, int month, string sigName)
        {
            // Hard guard: never stack on a live position (e.g. an Asia trade
            // that survived overnight — inPos resets at midnight, the real
            // position doesn't)
            if (Position.MarketPosition != MarketPosition.Flat) return;

            double close = Close[0];
            bool goLong  = close > orHi + BRK_BUF;
            bool goShort = close < orLo - BRK_BUF;
            if (!goLong && !goShort) return;

            // Skip first bar (9:45): OOS PF 0.839 even at confidence score ≥ 3
            if (ts < new TimeSpan(9, 46, 0)) return;

            // Gap filter (Python parity): OR-mid must gap >20pt from prior RTH
            // close, aligned with trade direction. No prior close = no trade.
            if (prevClose <= 0) return;
            double gap = (orHi + orLo) / 2.0 - prevClose;
            if (goLong  && gap <=  GAP_FILTER) return;
            if (goShort && gap >= -GAP_FILTER) return;

            // Volume confirmation on the breakout bar (Python parity)
            if (Volume[0] < MIN_BRK_VOL) return;

            // Confidence score gate: pivot + VWAP + zone + slope (0-4), need ≥ 3
            if (CalcConfidenceScore(goLong) < 3) return;

            double rr      = EvalMode ? EVAL_RR : FUNDED_RR;
            double effStop = STOP_PT + STOP_BUF;
            int    qty     = (!EvalMode && lifetimePnL >= SCALE_GATE) ? 2 : 1;
            if (qty == 2 && WEAK_MONTHS.Contains(month)) qty = 1;  // Jun/Sep/Dec cap

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
            if (sigName == "ORB2") secondReady = false;  // exactly one re-entry per day
        }

        // ── PM ORB entry (13:15-14:00) ────────────────────────────────────
        private void TryPMEntry(int month, int dow)
        {
            if (Position.MarketPosition != MarketPosition.Flat) return;  // never stack
            if (SkipMondays   && dow == 1) return;  // Mon PM OOS PF 0.92
            if (PmSkipFridays && dow == 5) return;  // Fri PM OOS PF 0.97 — separate from morning
            if (dailyPnL <= -DLL) return;

            double pmRange = pmOrHi - pmOrLo;
            if (pmRange < PM_MIN_OR || pmRange > PM_MAX_OR) return;

            double close = Close[0];
            bool goLong  = close > pmOrHi + PM_BRK_BUF;
            bool goShort = close < pmOrLo - PM_BRK_BUF;
            if (!goLong && !goShort) return;

            if (goLong)
            {
                EnterLong(1, "PM_ORB");
                SetStopLoss("PM_ORB",    CalculationMode.Price, close - PM_STOP,        false);
                SetProfitTarget("PM_ORB", CalculationMode.Price, close + PM_STOP * PM_RR);
                posLong = true;
            }
            else
            {
                EnterShort(1, "PM_ORB");
                SetStopLoss("PM_ORB",    CalculationMode.Price, close + PM_STOP,        false);
                SetProfitTarget("PM_ORB", CalculationMode.Price, close - PM_STOP * PM_RR);
                posLong = false;
            }

            pmTraded  = true;
            inPos     = true;
            entryPx   = close;
            activeSig = "PM_ORB";
        }

        // ── Pyramid add-on at 1R (funded, morning signals ONLY) ───────────
        private void TryPyramid()
        {
            if (!PyramidEnabled || EvalMode || pyramidDone) return;
            if (activeSig != "ORB1" && activeSig != "ORB2") return;  // never PM/Asia
            if (totalTrades < PYR_WARMUP) return;
            if (lifetimePnL < SCALE_GATE) return;
            if (Position.MarketPosition == MarketPosition.Flat) { inPos = false; return; }
            if (Position.Quantity >= MAX_CON) return;

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

        // ── Asia gap continuation (18:15 bar only) ────────────────────────
        private void TryAsiaEntry(int month, int dow)
        {
            if (dow == 4) return;                   // skip Thursday
            if (ASIA_WK.Contains(month)) return;    // skip Aug, Nov
            if (cmeClose == 0) return;
            if (dailyPnL <= -DLL) return;

            double gap = Close[0] - cmeClose;
            if (Math.Abs(gap) < ASIA_GAP_LO || Math.Abs(gap) > ASIA_GAP_HI) return;

            int qty = (lifetimePnL >= SCALE_GATE) ? 2 : 1;

            if (gap > 0)
            {
                EnterLong(qty, "ASIA");
                SetStopLoss("ASIA",    CalculationMode.Price, Close[0] - ASIA_STOP,            false);
                SetProfitTarget("ASIA", CalculationMode.Price, Close[0] + ASIA_STOP * ASIA_RR);
            }
            else
            {
                EnterShort(qty, "ASIA");
                SetStopLoss("ASIA",    CalculationMode.Price, Close[0] + ASIA_STOP,            false);
                SetProfitTarget("ASIA", CalculationMode.Price, Close[0] - ASIA_STOP * ASIA_RR);
            }

            asiaTraded = true;
        }

        // ── Confidence score: pivot + VWAP + zone + slope (returns 0-4) ───
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

        // ── Track P&L; arm second breakout on ORB1 target exits only ──────
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

                // v10.4: arm re-entry ONLY when ORB1 exits at its profit target
                // (was: any profitable close — incl. Asia wins at 3 AM, which
                // let a losing morning re-enter)
                if (tradePnL > 0
                        && execution.Order.Name == "Profit target"
                        && execution.Order.FromEntrySignal == "ORB1")
                    secondReady = true;
            }

            totalTrades++;
            inPos = false;
        }

        // ── Reset daily state ──────────────────────────────────────────────
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
            cmeClose    = 0;
            asiaTraded  = false;
            pmOrHi      = 0;
            pmOrLo      = double.MaxValue;
            pmOrBuilt   = false;
            pmTraded    = false;
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
