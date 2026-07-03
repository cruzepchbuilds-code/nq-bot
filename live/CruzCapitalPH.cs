// CruzCapitalPH.cs — NQ Power Hour Momentum (2:00–3:30 PM ET)
// NinjaTrader 8 Strategy
//
// *** BACKTESTED — NO EDGE FOUND ***
// NQ: IS (2024) PF 0.85 LOSING / OOS (2025-2026) PF 1.45 — reversed, overfitted
// ES: IS (2022-2023) PF 1.34 / OOS (2024-2026) PF 1.01 — flat after costs
// DO NOT DEPLOY LIVE. Keep file for reference only.
//
// HOW TO INSTALL (if testing on Sim101 only):
//   1. Copy to Documents\NinjaTrader 8\bin\Custom\Strategies\
//   2. Compile (F5) in NinjaScript Editor
//   3. Add to NQ 1-min chart on Sim101 account
//
// STRATEGY: After 2 PM, trade in direction of morning trend
//   Morning trend = price above/below 9:30 AM open at 2 PM
//   Entry:  break of the 2 PM candle's high (long) or low (short)
//   Stop:   20pt fixed
//   Target: 2R = 40pt
//   Max:    1 trade per day
//   Filters: skip weak months

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
    public class CruzCapitalPH : Strategy
    {
        private const double STOP_PT = 20.0;
        private const double RR      = 2.0;
        private const double DLL     = 1200.0;

        private static readonly HashSet<int> WEAK = new HashSet<int> { 6, 9, 12 };

        [NinjaScriptProperty] public bool SkipMondays    { get; set; }
        [NinjaScriptProperty] public bool SkipWeakMonths { get; set; }

        private double openPrice930;
        private double ph2pmHi, ph2pmLo;
        private bool   ph2pmSet, tradedToday, inPos;
        private double dailyPnL;
        private DateTime lastDay;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "CruzCapital Power Hour 2–3:30 PM NQ";
                Name        = "CruzCapitalPH";
                Calculate   = Calculate.OnBarClose;
                EntriesPerDirection          = 1;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds    = 300;
                BarsRequiredToTrade          = 20;
                SkipMondays    = false;
                SkipWeakMonths = true;
            }
            else if (State == State.DataLoaded)
            {
                ResetDay(); lastDay = DateTime.MinValue;
            }
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBars[0] < BarsRequiredToTrade) return;

            DateTime now = Time[0];
            TimeSpan ts  = now.TimeOfDay;
            int month = now.Month, dow = (int)now.DayOfWeek;

            if (now.Date != lastDay) { ResetDay(); lastDay = now.Date; }
            if (ts < new TimeSpan(9, 30, 0) || ts >= new TimeSpan(15, 55, 0)) return;
            if (SkipMondays && dow == 1) return;
            if (SkipWeakMonths && WEAK.Contains(month)) return;
            if (dailyPnL <= -DLL) return;

            // Capture 9:30 AM open
            if (ts >= new TimeSpan(9, 30, 0) && ts < new TimeSpan(9, 31, 0) && openPrice930 == 0)
                openPrice930 = Open[0];

            if (ts < new TimeSpan(14, 0, 0)) return;

            // Capture the 2:00 PM candle for the breakout levels
            if (!ph2pmSet && ts >= new TimeSpan(14, 0, 0) && ts < new TimeSpan(14, 1, 0))
            {
                ph2pmHi  = High[0];
                ph2pmLo  = Low[0];
                ph2pmSet = true;
                Draw.HorizontalLine(this, "ph2H", false, ph2pmHi, Brushes.Yellow, DashStyleHelper.Dot, 1);
                Draw.HorizontalLine(this, "ph2L", false, ph2pmLo, Brushes.Orange, DashStyleHelper.Dot, 1);
                return;
            }

            if (!ph2pmSet || tradedToday) return;
            if (ts >= new TimeSpan(15, 30, 0)) return; // no new entries after 3:30 PM

            if (inPos)
            {
                if (Position.MarketPosition == MarketPosition.Flat) inPos = false;
                return;
            }

            if (openPrice930 == 0) return;

            double close          = Close[0];
            bool   morningBullish = close > openPrice930;
            bool   morningBearish = close < openPrice930;

            if (morningBullish && close > ph2pmHi)
            {
                EnterLong(1, "PH");
                SetStopLoss("PH",    CalculationMode.Price, close - STOP_PT,      false);
                SetProfitTarget("PH", CalculationMode.Price, close + STOP_PT * RR);
                inPos = true; tradedToday = true;
            }
            else if (morningBearish && close < ph2pmLo)
            {
                EnterShort(1, "PH");
                SetStopLoss("PH",    CalculationMode.Price, close + STOP_PT,      false);
                SetProfitTarget("PH", CalculationMode.Price, close - STOP_PT * RR);
                inPos = true; tradedToday = true;
            }
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
            openPrice930 = 0;
            ph2pmHi      = 0;
            ph2pmLo      = double.MaxValue;
            ph2pmSet     = false;
            tradedToday  = false;
            inPos        = false;
            dailyPnL     = 0;
        }
    }
}
