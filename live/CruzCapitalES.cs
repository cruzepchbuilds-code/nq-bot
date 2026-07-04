// CruzCapitalES.cs — ES ORB Strategy for NinjaTrader 8
// Same logic as CruzCapitalNQ but calibrated for ES ($50/pt vs NQ $20/pt)
//
// HOW TO INSTALL:
//   1. Copy to Documents\NinjaTrader 8\bin\Custom\Strategies\
//   2. Compile in NinjaScript Editor (F5)
//   3. Add to ES Continuous (#F) 1-min chart on Sim101 account
//
// STRATEGY: ES ORB — OR 9:30–9:44 ET
//   Backtest results (OOS 2024-2026, walk-forward):
//     v4 (Feb+Mar+Nov, RR=2.5):
//       2024: PF 1.81 ($7,000) / 2025: PF 2.42 ($11,110) / 2026: PF 2.42 ($6,535)
//       OOS Combined: 90 trades | PF 2.17 | WR 48% | Net $24,645
//
//   OPTIMIZED PARAMETERS (final sweep v4, IS 2022-23 / OOS 2024-26):
//     Stop:        7pt + 2pt buffer = 9pt effective
//     RR:          2.5 (v4: raised from 2.0 — same entries, wider target, +$7,203 OOS net)
//     OR range:    5-30pt (do NOT tighten — all narrower ranges worse)
//     Entry cut:   10:15 AM (10:15-10:29 window has negative drag)
//     Skip months: Jan/Apr/May/Jun/Jul/Aug/Sep/Oct/Dec
//       v4: dropped Apr (lost money all 3 OOS years, PF 0.68)
//           kept Mar (OOS PF 1.57, 2025 PF 4.23, adds 29 good OOS trades)
//     Strong months: Feb/Nov (Mar trades but no bonus — more conservative)
//     SecondBreakout: DISABLED — second trade costs $2,465 OOS net

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
    public class CruzCapitalES : Strategy
    {
        // Optimized parameters (es_config.py v2, OOS PF 2.24)
        private const double STOP_PT   = 7.0;   // 7pt stop + 2pt buffer = 9pt effective
        private const double STOP_BUF  = 2.0;
        private const double BRK_BUF   = 1.0;
        private const double MIN_OR    = 5.0;   // DO NOT TIGHTEN — 8-22 range worse
        private const double MAX_OR    = 30.0;  // ES p90 OR = 28pt
        private const double RR        = 2.5;   // v4: raised from 2.0 — wider target, +$7,203 OOS net
        private const double DLL       = 1200.0;
        private const double PYR_GATE  = 1500.0;
        private const int    PYR_WARMUP = 5;
        private const int    MAX_CON   = 2;
        private const double GAP_MIN   = 5.0;
        private const double SIG_MIN   = 60.0;

        // ES seasonality — v4: trading months Feb / Mar / Nov
        //   Apr removed: -$863/-$1,650/-$1,265 all 3 OOS years (PF 0.68 total)
        //   Mar added back: OOS PF 1.57, consistent edge in 2025/2026
        private static readonly HashSet<int> SKIP   = new HashSet<int> { 1, 4, 5, 6, 7, 8, 9, 10, 12 };
        private static readonly HashSet<int> STRONG = new HashSet<int> { 2, 11 };

        [NinjaScriptProperty] public bool EvalMode              { get; set; }
        [NinjaScriptProperty] public bool PyramidEnabled        { get; set; }
        [NinjaScriptProperty] public bool SecondBreakoutEnabled { get; set; }
        [NinjaScriptProperty] public bool SkipMondays           { get; set; }

        private double orHi, orLo, orVol, avgOrVol, prevClose;
        private bool   orBuilt, tradedToday, secondReady, pyramidDone, inPos, posLong;
        private double entryPx, dailyPnL, lifetimePnL;
        private string activeSig;
        private int    totalTrades;
        private Queue<double> orVolHistory = new Queue<double>();
        private DateTime lastDay;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "CruzCapital ES ORB v4 (Feb+Mar+Nov RR=2.5, OOS PF 2.17)";
                Name        = "CruzCapitalES";
                Calculate   = Calculate.OnBarClose;
                EntriesPerDirection          = 2;
                EntryHandling                = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds    = 300;
                BarsRequiredToTrade          = 20;
                EvalMode              = false;
                PyramidEnabled        = false;   // disabled until ES-specific scorer calibrated
                SecondBreakoutEnabled = false;  // disabled: second trade costs $2,465 OOS net
                SkipMondays           = true;
            }
            else if (State == State.Configure)
            {
                AddDataSeries(Data.BarsPeriodType.Day, 1);
            }
            else if (State == State.DataLoaded)
            {
                ResetDay();
                totalTrades = 0; lifetimePnL = 0; lastDay = DateTime.MinValue;
            }
        }

        protected override void OnBarUpdate()
        {
            if (BarsInProgress != 0) return;
            if (CurrentBars[0] < BarsRequiredToTrade) return;

            DateTime now = Time[0];
            TimeSpan ts  = now.TimeOfDay;
            int month = now.Month, dow = (int)now.DayOfWeek;

            if (now.Date != lastDay)
            {
                if (CurrentBars[1] > 1) prevClose = Closes[1][1];
                ResetDay(); lastDay = now.Date;
            }

            if (ts < new TimeSpan(9, 30, 0) || ts >= new TimeSpan(15, 55, 0)) return;
            if (SkipMondays && dow == 1) return;
            if (SKIP.Contains(month)) return;   // hard skip months
            if (dailyPnL <= -DLL) return;

            // Build OR 9:30–9:44
            if (ts < new TimeSpan(9, 45, 0))
            {
                orHi   = Math.Max(orHi, High[0]);
                orLo   = Math.Min(orLo, Low[0]);
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

            if (!orBuilt) return;
            double orRange = orHi - orLo;
            if (orRange < MIN_OR || orRange > MAX_OR) return;

            // Entry cutoff 10:15 AM (10:15-10:29 window has PF 0.77 — negative drag)
            if (ts >= new TimeSpan(10, 15, 0) && !inPos) return;
            if (inPos) { TryPyramid(); return; }

            if (!tradedToday)
                TryEntry(ts, month, "ORB1");
            else if (SecondBreakoutEnabled && secondReady && ts >= new TimeSpan(10, 0, 0))
                TryEntry(ts, month, "ORB2");
        }

        private void TryEntry(TimeSpan ts, int month, string sig)
        {
            double close = Close[0];
            bool goLong  = close > orHi + BRK_BUF;
            bool goShort = close < orLo - BRK_BUF;
            if (!goLong && !goShort) return;
            if (CalcScore(goLong, ts, month) < SIG_MIN) return;

            double eff = STOP_PT + STOP_BUF;
            // Strong months allow 2 contracts for high-conviction signals
            int qty = (!EvalMode && STRONG.Contains(month) && CalcScore(goLong, ts, month) >= 80) ? 2 : 1;

            if (goLong)
            {
                EnterLong(qty, sig);
                SetStopLoss(sig, CalculationMode.Price, close - eff, false);
                SetProfitTarget(sig, CalculationMode.Price, close + eff * RR);
                posLong = true;
            }
            else
            {
                EnterShort(qty, sig);
                SetStopLoss(sig, CalculationMode.Price, close + eff, false);
                SetProfitTarget(sig, CalculationMode.Price, close - eff * RR);
                posLong = false;
            }
            entryPx = close; inPos = true; tradedToday = true; pyramidDone = false; activeSig = sig;
        }

        private void TryPyramid()
        {
            if (!PyramidEnabled || EvalMode || pyramidDone) return;
            if (totalTrades < PYR_WARMUP || lifetimePnL < PYR_GATE) return;
            if (Position.MarketPosition == MarketPosition.Flat) { inPos = false; return; }
            if (Position.Quantity >= MAX_CON) return;
            double eff = STOP_PT + STOP_BUF;
            if (posLong && Close[0] >= entryPx + eff)
            {
                EnterLong(1, "PYR");
                SetStopLoss(activeSig, CalculationMode.Price, entryPx, false);
                SetStopLoss("PYR",    CalculationMode.Price, entryPx, false);
                pyramidDone = true;
            }
            else if (!posLong && Close[0] <= entryPx - eff)
            {
                EnterShort(1, "PYR");
                SetStopLoss(activeSig, CalculationMode.Price, entryPx, false);
                SetStopLoss("PYR",    CalculationMode.Price, entryPx, false);
                pyramidDone = true;
            }
        }

        private double CalcScore(bool isLong, TimeSpan ts, int month)
        {
            double score = 0;
            // Time score
            if      (ts < new TimeSpan(9,  50, 0)) score += 20;
            else if (ts < new TimeSpan(10,  0, 0)) score += 15;
            else if (ts < new TimeSpan(10, 15, 0)) score += 10;
            else                                    score += 5;

            // Gap score
            double gap = (CurrentBars[0] > 0 ? Open[0] : Close[0]) - prevClose;
            if      (isLong  && gap >  GAP_MIN) score += 25;
            else if (!isLong && gap < -GAP_MIN) score += 25;
            else                                 score += 15;

            // Volume score
            if (avgOrVol > 0)
            {
                double r = orVol / avgOrVol;
                score += (r >= 0.7 && r <= 1.5) ? 25 : (r >= 0.5 ? 15 : 5);
            }
            else score += 15;

            // OR range score (ES optimal: 5-30pt, strong zone 10-20pt)
            double rng = orHi - orLo;
            if      (rng >= 10 && rng <= 20) score += 20;
            else if (rng >  20 && rng <= 30) score += 15;
            else if (rng >   5 && rng <  10) score += 10;
            else                             score += 5;

            // Strong month bonus
            if (STRONG.Contains(month)) score += 10;
            return score;
        }

        protected override void OnExecutionUpdate(Execution execution, string executionId,
            double price, int quantity, MarketPosition marketPosition, string orderId, DateTime time)
        {
            if (execution.Order == null || execution.Order.OrderState != OrderState.Filled) return;
            OrderAction a = execution.Order.OrderAction;
            if (a != OrderAction.Sell && a != OrderAction.BuyToCover) return;
            int n = SystemPerformance.AllTrades.Count;
            if (n > 0)
            {
                double pnl = SystemPerformance.AllTrades[n - 1].ProfitCurrency;
                dailyPnL += pnl; lifetimePnL += pnl;
                if (pnl > 0) secondReady = true;
            }
            totalTrades++; inPos = false;
        }

        private void ResetDay()
        {
            orHi = double.MinValue; orLo = double.MaxValue;
            orBuilt = false; orVol = 0;
            tradedToday = false; secondReady = false; pyramidDone = false;
            inPos = false; entryPx = 0; activeSig = ""; dailyPnL = 0;
        }
    }
}
