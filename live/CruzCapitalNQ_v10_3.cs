// CruzCapitalNQ_v10_3.cs — NinjaTrader 8 Strategy
// CruzCapital NQ ORB — Lucid Trading 50K Direct / Apex Eval
//
// HOW TO INSTALL:
//   1. Copy this file to:
//      C:\Users\<you>\Documents\NinjaTrader 8\bin\Custom\Strategies\CruzCapitalNQ_v10_3.cs
//   2. NT8: New → NinjaScript Editor → Compile All (F5)
//   3. On NQ Continuous (#F) 1-min chart:
//      Strategies panel → Add → CruzCapitalNQ v10.3
//   4. Set Account = your Lucid account, enable "Automated" trading
//
// ── SESSION OVERVIEW ──────────────────────────────────────────────────────────
//
//  MORNING ORB   9:30-9:44 range  |  9:46-10:30 entry
//    OR filter:  55-110pt
//    Entry:      close > OR high + 4pt (long) | close < OR low - 4pt (short)
//    Stop:       27pt from entry (22pt fixed + 5pt buffer beyond OR edge)
//    Target:     2R eval (54pt) | 3R funded (81pt)
//    Filter:     confidence score ≥ 3 (pivot + VWAP + zone + slope, 0-4 scale)
//    Skip:       Monday, Friday, first bar (9:45)
//    OOS PF:     4.94  N=36/18mo  WR=53%  Net=$40,330
//
//  PM ORB       13:00-13:14 range  |  13:15-14:00 entry
//    OR filter:  15-60pt
//    Entry:      close > OR high + 2pt (long) | close < OR low - 2pt (short)
//    Stop:       22pt from entry
//    Target:     2R (44pt)
//    Skip:       Monday, Friday
//    OOS PF:     1.368  N=140/18mo  WR=42%  Net=$13,255
//
//  ASIA GAP     18:15 bar only
//    Gap filter: 30-80pt from 17:00 CME close
//    Stop:       15pt  |  Target: 1.5R (22.5pt)
//    Skip:       Thursday, August, November
//    Disabled in eval mode
//
//  PYRAMID      +1c at 1R, stops → breakeven
//    Requires:   $1,500 lifetime profit + 5 prior trades
//    Disabled in eval mode
//
//  SECOND BREAK re-entry after first trade wins, starts 10:00 AM
//
//  DLL:  halt at -$1,200/day
//  SCALE: 1c until lifetimePnL ≥ $1,500 (then 2c funded)
//
// ── CHANGELOG ────────────────────────────────────────────────────────────────
//  v10.3 (2026-07-01):
//    - PM ORB OR max widened 50→60pt; entry cutoff 14:15→14:00 (sweep-confirmed)
//    - PM ORB now fires even on morning-skip days (OR build before validity check)
//    - PM ORB respects SkipMondays/SkipFridays (was missing DOW filter)
//    - Removed CalcScore gate from TryUSEntry; confidence score ≥ 3 is sole gate
//    - Scaling simplified: 2c after $1,500 lifetime profit (no month restriction)
//  v10.2 (2026-07-01):
//    - PM ORB strategy added (1pm session, OOS PF 1.64)
//  v10.1 (prior):
//    - SkipFridays added (Fri OOS PF 1.21 vs 4+ Tue-Thu)
//    - Confidence score gate raised to ≥ 3
//    - First-bar skip (9:45 OOS PF 0.839)

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
    public class CruzCapitalNQ_v10_3 : Strategy
    {
        // ── Morning ORB parameters ─────────────────────────────────────────
        private const double STOP_PT   = 22.0;  // fixed stop distance
        private const double STOP_BUF  = 5.0;   // stop placed this far beyond OR edge
        private const double BRK_BUF   = 4.0;   // close must exceed OR edge by this
        private const double MIN_OR    = 55.0;
        private const double MAX_OR    = 110.0;
        private const double FUNDED_RR = 3.0;
        private const double EVAL_RR   = 2.0;

        // ── PM ORB parameters ──────────────────────────────────────────────
        private const double PM_STOP    = 22.0;
        private const double PM_MIN_OR  = 15.0;
        private const double PM_MAX_OR  = 60.0;  // widened from 50: +24 trades, PF 1.368
        private const double PM_RR      = 2.0;
        private const double PM_BRK_BUF = 2.0;

        // ── Asia parameters ────────────────────────────────────────────────
        private const double ASIA_GAP_LO = 30.0;
        private const double ASIA_GAP_HI = 80.0;
        private const double ASIA_STOP   = 15.0;
        private const double ASIA_RR     = 1.5;
        private static readonly HashSet<int> ASIA_WK = new HashSet<int> { 8, 11 };

        // ── Account / risk ─────────────────────────────────────────────────
        private const double DLL        = 1200.0;  // daily loss limit
        private const double SCALE_GATE = 1500.0;  // lifetime profit before 2c
        private const int    MAX_CON    = 2;
        private const int    PYR_WARMUP = 5;

        // ── User-configurable (NT8 strategy properties) ────────────────────
        [NinjaScriptProperty] public bool EvalMode             { get; set; }
        [NinjaScriptProperty] public bool AsiaEnabled          { get; set; }
        [NinjaScriptProperty] public bool PyramidEnabled       { get; set; }
        [NinjaScriptProperty] public bool SecondBreakoutEnabled { get; set; }
        [NinjaScriptProperty] public bool SkipMondays          { get; set; }
        [NinjaScriptProperty] public bool SkipFridays          { get; set; }
        [NinjaScriptProperty] public bool PmOrbEnabled         { get; set; }

        // ── Morning OR state ───────────────────────────────────────────────
        private double orHi, orLo;
        private bool   orBuilt;
        private double orVol;
        private Queue<double> orVolHistory = new Queue<double>();
        private double avgOrVol;

        // ── PM OR state ────────────────────────────────────────────────────
        private double pmOrHi, pmOrLo;
        private bool   pmOrBuilt;
        private bool   pmTraded;

        // ── Daily session state ────────────────────────────────────────────
        private bool   tradedToday;
        private bool   secondReady;
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

        // ── Confidence score state (prev-day context + intraday VWAP) ─────
        private double prevClose;
        private double prevDayHigh, prevDayLow, prevDayVwap;
        private double dayHi, dayLo, dayVwapPV, dayVwapV;
        private double vwapAt935, vwapAt944;
        private double orClosePx;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description  = "CruzCapital NQ ORB v10.3 — morning + PM + Asia";
                Name         = "CruzCapitalNQ v10.3";
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
                SkipFridays           = true;
                PmOrbEnabled          = true;
            }
            else if (State == State.Configure)
            {
                AddDataSeries(Data.BarsPeriodType.Day, 1);
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

            // ── New day ────────────────────────────────────────────────────
            if (now.Date != lastDay)
            {
                if (CurrentBars[1] > 1)
                    prevClose = Closes[1][1];
                if (dayHi > 0 && dayLo < double.MaxValue)
                {
                    prevDayHigh = dayHi;
                    prevDayLow  = dayLo;
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

            // ── Before US open ─────────────────────────────────────────────
            if (ts < new TimeSpan(9, 30, 0)) return;

            // ── RTH VWAP + range tracking (always runs — builds prev-day context) ─
            if (Volume[0] > 0)
            {
                dayVwapPV += Close[0] * Volume[0];
                dayVwapV  += Volume[0];
                dayHi = Math.Max(dayHi, High[0]);
                dayLo = Math.Min(dayLo, Low[0]);
            }
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
                orHi  = Math.Max(orHi,  High[0]);
                orLo  = Math.Min(orLo,  Low[0]);
                orVol += Volume[0];

                if (ts >= new TimeSpan(9, 44, 0) && !orBuilt)
                {
                    orBuilt = true;
                    orVolHistory.Enqueue(orVol);
                    if (orVolHistory.Count > 20) orVolHistory.Dequeue();
                    avgOrVol = 0;
                    foreach (double v in orVolHistory) avgOrVol += v;
                    avgOrVol /= orVolHistory.Count;

                    Draw.HorizontalLine(this, "orH", false, orHi, Brushes.Cyan,    DashStyleHelper.Dot, 1);
                    Draw.HorizontalLine(this, "orL", false, orLo, Brushes.Magenta, DashStyleHelper.Dot, 1);
                }
                return;
            }

            // ── Build PM OR (13:00-13:14) — runs on ALL days regardless of
            //    morning OR validity, so PM fires even on morning-skip days ─
            if (PmOrbEnabled && ts >= new TimeSpan(13, 0, 0) && ts < new TimeSpan(13, 15, 0))
            {
                if (!pmOrBuilt)
                {
                    pmOrHi = pmOrHi == 0          ? High[0] : Math.Max(pmOrHi, High[0]);
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

            // ── Morning OR validity (checked after PM branch) ──────────────
            if (!orBuilt) return;
            double orRange = orHi - orLo;
            if (orRange < MIN_OR || orRange > MAX_OR) return;

            // ── 10:30 cutoff for morning entries ───────────────────────────
            if (ts >= new TimeSpan(10, 30, 0) && !inPos) return;

            // ── Manage open position (pyramid) ─────────────────────────────
            if (inPos) { TryPyramid(); return; }

            // ── Morning entries (9:46-10:30) ──────────────────────────────
            if (!tradedToday)
                TryUSEntry(ts, "ORB1");
            else if (SecondBreakoutEnabled && secondReady && ts >= new TimeSpan(10, 0, 0))
                TryUSEntry(ts, "ORB2");
        }

        // ── Morning ORB entry ──────────────────────────────────────────────
        private void TryUSEntry(TimeSpan ts, string sigName)
        {
            double close = Close[0];
            bool goLong  = close > orHi + BRK_BUF;
            bool goShort = close < orLo - BRK_BUF;
            if (!goLong && !goShort) return;

            // Skip first bar (9:45): OOS PF 0.839 even at confidence score ≥ 3
            if (ts < new TimeSpan(9, 46, 0)) return;

            // Confidence score gate: pivot + VWAP + zone + slope (0-4), need ≥ 3
            if (CalcConfidenceScore(goLong) < 3) return;

            double rr      = EvalMode ? EVAL_RR : FUNDED_RR;
            double effStop = STOP_PT + STOP_BUF;
            int    qty     = (!EvalMode && lifetimePnL >= SCALE_GATE) ? 2 : 1;

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
        }

        // ── PM ORB entry (13:15-14:00) ────────────────────────────────────
        private void TryPMEntry(int month, int dow)
        {
            if (SkipMondays && dow == 1) return;  // Mon PM OOS PF 0.92
            if (SkipFridays && dow == 5) return;  // Fri PM OOS PF 0.97
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

        // ── Pyramid add-on at 1R (funded only, after warmup) ──────────────
        private void TryPyramid()
        {
            if (!PyramidEnabled || EvalMode || pyramidDone) return;
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

        // ── Track P&L for DLL, pyramid gate, and second breakout arm ──────
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
                if (tradePnL > 0) secondReady = true;
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
            orVol       = 0;
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
            vwapAt935   = 0;
            vwapAt944   = 0;
            orClosePx   = 0;
        }
    }
}
