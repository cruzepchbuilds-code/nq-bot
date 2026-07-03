// CruzCapitalREJ.cs — NQ VWAP Rejection (failed-reclaim continuation)
// NinjaTrader 8 Strategy
//
// HOW TO INSTALL:
//   1. Copy to Documents\NinjaTrader 8\bin\Custom\Strategies\
//   2. Compile (F5) in NinjaScript Editor
//   3. Add to NQ 1-min chart
//
// STRATEGY: When price attempts a VWAP reclaim (crosses VWAP after being
// extended ≥25pt) and the reclaim FAILS (price crosses back), enter in the
// direction of the failure. Trapped reclaim traders fuel the continuation.
//
//   Sequence:  extended ≥25pt from VWAP (tracked from 10:00)
//              → first VWAP cross at/after 11:00 (the reclaim attempt)
//              → price crosses BACK through VWAP (the rejection)
//              → enter in re-cross direction, 1 trade/day
//   Stop:      20pt   |   Target: 3R = 60pt   |   Force-flat: 1:00 PM
//   Skip:      Monday + weak months {Apr, May, Jun, Sep, Dec}
//
//   Backtest (nq_full.csv, brain/research/rejection_expanded_is.py):
//     IS  2022-2024: N=196  PF=1.208  Net=$+10,308  (positive EVERY year)
//     OOS 2025-2026: N= 94  PF=1.584  Net=$+13,647
//     Year-by-year PF: 2022 1.01 | 2023 1.18 | 2024 1.18 | 2025 1.54 | 2026 1.70
//     The edge STRENGTHENS over time — reclaim failures continue harder in
//     the current regime.
//
// ── ACCOUNT PAIRING ─────────────────────────────────────────────────────────
//   Best home: SAME account as CruzCapitalNQ v10.4 — the 11:00-13:00 window
//   fills the dead zone between morning ORB (9:46-10:30) and PM ORB (13:15+).
//   All positions flat by 13:00, so no overlap ever.
//   DLL note: worst-case morning stop (-$540) + rejection stop (-$415) =
//   -$955 → internal DLL 900 then blocks PM + Asia for the day (acceptable —
//   that IS the risk brake working).
//
//   Do NOT pair on the same account as CruzCapitalVWAP (reclaim): they trigger
//   off the SAME VWAP cross in opposite scenarios and can hold opposing
//   positions simultaneously (capital-inefficient, confusing fills).

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
    public class CruzCapitalREJ : Strategy
    {
        private const double STOP_PT    = 20.0;
        private const double RR         = 3.0;    // 60pt target
        private const double MIN_EXTEND = 25.0;   // extension required before a cross counts
        private const double DLL        = 900.0;  // shared-account internal daily loss halt

        private static readonly HashSet<int> WEAK = new HashSet<int> { 4, 5, 6, 9, 12 };

        [NinjaScriptProperty] public bool SkipMondays    { get; set; }
        [NinjaScriptProperty] public bool SkipWeakMonths { get; set; }

        // VWAP accumulation (from 9:30)
        private double sumPV, sumVol, vwap;

        // Rejection state machine
        private bool   wasExtended;    // price got ≥25pt from VWAP after 10:00
        private bool   sawReclaim;     // first cross ≥11:00 seen
        private bool   reclaimUp;      // direction of that first cross
        private bool   prevAboveVWAP;
        private bool   prevSet;
        private bool   traded;
        private bool   inPos;
        private double dailyPnL;
        private DateTime lastDay;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "CruzCapital VWAP Rejection — failed reclaim continuation (11:00-13:00)";
                Name        = "CruzCapitalREJ";
                Calculate   = Calculate.OnBarClose;
                EntriesPerDirection          = 1;
                EntryHandling                = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds    = 300;
                BarsRequiredToTrade          = 20;
                SkipMondays    = true;
                SkipWeakMonths = true;
            }
            else if (State == State.DataLoaded)
            {
                ResetDay();
                lastDay = DateTime.MinValue;
            }
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBars[0] < BarsRequiredToTrade) return;

            DateTime now = Time[0];
            TimeSpan ts  = now.TimeOfDay;

            if (now.Date != lastDay) { ResetDay(); lastDay = now.Date; }

            // RTH window only
            if (ts < new TimeSpan(9, 30, 0) || ts >= new TimeSpan(16, 0, 0)) return;
            if (SkipMondays && now.DayOfWeek == DayOfWeek.Monday) return;
            if (SkipWeakMonths && WEAK.Contains(now.Month)) return;

            // Session VWAP from 9:30 (typical price)
            if (Volume[0] > 0)
            {
                sumPV  += (High[0] + Low[0] + Close[0]) / 3.0 * Volume[0];
                sumVol += Volume[0];
                vwap    = sumPV / sumVol;
            }
            if (vwap == 0) return;

            double close     = Close[0];
            bool   currAbove = close > vwap;

            // Force-flat at 13:00 (backtest exits at window end)
            if (inPos)
            {
                if (Position.MarketPosition == MarketPosition.Flat) { inPos = false; }
                else if (ts >= new TimeSpan(13, 0, 0))
                {
                    if (Position.MarketPosition == MarketPosition.Long)  ExitLong("REJ");
                    if (Position.MarketPosition == MarketPosition.Short) ExitShort("REJ");
                }
                prevAboveVWAP = currAbove; prevSet = true;
                return;
            }

            // State machine runs 10:00 onward
            if (ts < new TimeSpan(10, 0, 0))
            {
                prevAboveVWAP = currAbove; prevSet = true;
                return;
            }

            if (!wasExtended && Math.Abs(close - vwap) > MIN_EXTEND)
                wasExtended = true;

            bool inEntryWindow = ts >= new TimeSpan(11, 0, 0) && ts < new TimeSpan(13, 0, 0);

            if (wasExtended && prevSet && inEntryWindow && !traded && dailyPnL > -DLL)
            {
                bool crossedUp   = !prevAboveVWAP && currAbove;
                bool crossedDown =  prevAboveVWAP && !currAbove;

                if (!sawReclaim)
                {
                    // First cross ≥11:00 = the reclaim attempt (no entry yet)
                    if (crossedUp)   { sawReclaim = true; reclaimUp = true;  }
                    if (crossedDown) { sawReclaim = true; reclaimUp = false; }
                }
                else
                {
                    // Opposite re-cross = the rejection → enter with the failure
                    if (reclaimUp && crossedDown)
                    {
                        EnterShort(1, "REJ");
                        SetStopLoss("REJ",    CalculationMode.Price, close + STOP_PT, false);
                        SetProfitTarget("REJ", CalculationMode.Price, close - STOP_PT * RR);
                        inPos = true; traded = true;
                    }
                    else if (!reclaimUp && crossedUp)
                    {
                        EnterLong(1, "REJ");
                        SetStopLoss("REJ",    CalculationMode.Price, close - STOP_PT, false);
                        SetProfitTarget("REJ", CalculationMode.Price, close + STOP_PT * RR);
                        inPos = true; traded = true;
                    }
                }
            }

            prevAboveVWAP = currAbove;
            prevSet       = true;
        }

        protected override void OnExecutionUpdate(Execution execution, string executionId,
            double price, int quantity, MarketPosition marketPosition, string orderId, DateTime time)
        {
            if (execution.Order == null || execution.Order.OrderState != OrderState.Filled) return;
            OrderAction a = execution.Order.OrderAction;
            if (a != OrderAction.Sell && a != OrderAction.BuyToCover) return;
            int n = SystemPerformance.AllTrades.Count;
            if (n > 0) dailyPnL += SystemPerformance.AllTrades[n - 1].ProfitCurrency;
            inPos = false;
        }

        private void ResetDay()
        {
            sumPV = 0; sumVol = 0; vwap = 0;
            wasExtended = false; sawReclaim = false; reclaimUp = false;
            prevAboveVWAP = false; prevSet = false;
            traded = false; inPos = false; dailyPnL = 0;
        }
    }
}
