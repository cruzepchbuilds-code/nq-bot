// CruzCapitalVWAP.cs — NQ VWAP Reclaim v10 (entry 11:00 AM–1:00 PM ET)
// NinjaTrader 8 Strategy
//
// HOW TO INSTALL:
//   1. Copy to Documents\NinjaTrader 8\bin\Custom\Strategies\
//   2. Compile (F5) in NinjaScript Editor
//   3. Add to NQ 1-min chart — SEPARATE account from CruzCapitalNQ v11
//      (v11's rejection strategy triggers off the same VWAP cross)
//
// STRATEGY: Fade overextended moves back to VWAP
//
//   v10 FINAL (2026-07-03) — structural pass (vwap_outside_box.py):
//     1. ASYMMETRIC 13:00 EXIT (bug-fix + upgrade in one): the research sims
//        always modeled "flatten at 13:00" but this C# never actually did it —
//        open positions silently rode their stop/target into the afternoon
//        (the worst exit variant, OOS -$1,444). New rule: at 13:00+, a position
//        AT/BELOW breakeven is cut; a position in PROFIT keeps working its
//        target/stop (session close flattens 15:55). Beats flat-13:00 in BOTH
//        periods: IS +$1,840, OOS +$1,096 → OOS $17,438 PF 1.473.
//     2. FlipOnStop property (default OFF): a stopped reclaim IS a failed
//        reclaim — the validated rejection signal (E4). Flip on stop-out:
//        OOS +$6,928 (flip PF 1.31) but IS -$4,860 (flip PF 0.90) — same
//        regime profile as the rejection edge. Opt-in only, eyes open.
//     3. TESTED, REJECTED: percent-scaled stop/extension (fixed points beat
//        every %-config OOS despite NQ doubling 11k→23k); trend-strength
//        floor; extension recency; hold-EVERYTHING-to-14:00/15:55 (holding
//        losers is the mistake — holding winners is the edge).
//     v10-final year-by-year (asym exit): 2022 PF 1.20 +$4,984 | 2023 1.04
//       +$968 | 2024 1.87 +$19,144 | 2025 1.35 +$9,472 | 2026 1.85 +$7,965
//     Account card: worst trade -$414 | best +$1,206 | worst month -$2,230 |
//       max streak 13 | 2024+ fresh-start deaths 21% | the Jan-2025 fresh
//       start now SURVIVES the -$2k floor (min equity -$1,532, was -$2,050)
//
//   v10 (2026-07-03) — direction investigation (vwap_long_only.py):
//     NEW: LongOnly property (default FALSE). The Analyzer's long PF 2.25 /
//     short 0.93 slice suggested cutting shorts — full 2022-2026 data says NO:
//       Year   Long PF / Net      Short PF / Net
//       2022    0.82  -$2,445      1.53  +$6,160   ← shorts carried the year
//       2023    1.55  +$6,204      0.81  -$2,496   ← longs carried
//       2024    1.86  +$8,268      1.63  +$7,565
//       2025    1.29  +$4,204      1.36  +$4,414   ← shorts BEAT longs
//       2026    2.85  +$7,676      1.01     +$48
//     Directions ALTERNATE by regime — that's a hedge, not decay. Long-only
//     breaks the all-years-positive property (2022 goes red) and loses $4,462
//     even in OOS 2025-26. Default keeps both; flip LongOnly at your own risk.
//     ALSO TESTED, REJECTED: window end 13:30/14:00/14:30 for both-directions
//     (helps longs only; the extra short entries drop PF and raise worst month
//     to -$2,645); short double-confirm gate (kills OOS); stop/RR/ext/lock
//     re-confirmed at v7 values through another full grid.
//
//   v7 — deployment-grade reopt (vwap_final_opt.py):
//     CHANGED: target 3R → 2.75R (60pt → 55pt). Beats 3R on ALL FOUR metrics:
//       IS $+23,256 v $22,956 | OOS $+16,342 v $15,292 | PF up both periods.
//     Year-by-year at v7: 2022 PF 1.15 | 2023 1.15 | 2024 1.73 | 2025 1.32
//       | 2026 1.87 — positive every year.
//     CONFIRMED (tested, no change): Monday skip (full-data PF 1.01);
//       trend-vs-open definition (vs-VWAP variant much worse OOS);
//       both directions kept (shorts weaker but positive);
//       12:00-13:00 entries kept (IS positive, OOS thin — no pruning on
//       mixed evidence).  DOW gradient: Tue 1.11 < Wed 1.24 < Thu 1.39
//       < Fri 1.72 — Friday is the best day; never skip it.
//
//   ── STANDALONE-ACCOUNT REALITY (v10-final asym, 1c, 50K, floor -$2,000) ──
//     ~1.9 trades/wk | worst trade -$414 | best +$1,206 | max streak 13
//     median month +$600 | worst month -$2,230
//     2024+ fresh-start death rate: 21% | Jan-2025 start survives (-$1,532 min)
//     ⇒ DEPLOY AS ACCOUNT #4-5, AFTER the v11 copies. A v11 copy is a
//       strictly better prefunded ($2,755/mo, ~0% recent death rate) —
//       this strategy's value is DIVERSIFICATION, not raw $/account.
//     KILL SWITCH: pause if rolling 3-month PF < 1.0.
//
//   v6: full-data validation — trend lock 10:30→11:00, weak months → {May},
//       extension 25→35pt (fixed the 2022-23 hole; +81% total).
//   v5: RR 2.5→3.0. Breakeven-trail tested = catastrophic (never use).
//   v4: 11:00 entry gate. Fridays kept.  v3: Monday skip.
//
//   Parameters:
//     Stop:         20pt
//     Target:       2.75R = 55pt
//     Extend min:   35pt from VWAP required before reclaim counts
//     Track from:   10:00 AM (extension tracking begins)
//     Entry from:   11:00 AM
//     Entries end:  1:00 PM ET; at 13:00 losers are cut, winners keep
//                   working target/stop until the 15:55 session close
//     Max trades:   1 per day (re-entry tested: dilutes, rejected)
//     Filter:       trend-aligned only (11:00 AM trend lock vs 9:30 open)
//     Weak months:  skip May only

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
    public class CruzCapitalVWAP : Strategy
    {
        // v7 full-data optimized (2022-2026): all-years-positive config
        private const double STOP_PT    = 20.0;
        private const double RR         = 2.75;  // 55pt target — beats 3R on all 4 IS/OOS metrics
        private const double DLL        = 1200.0;
        private const double MIN_EXTEND = 35.0;  // v6: 25→35 — bigger stretch, better snap-back

        // v6: only May is weak on full data (PF 0.75); old {4,5,6,9,12} was 2024+ overfit
        private static readonly HashSet<int> WEAK = new HashSet<int> { 5 };

        [NinjaScriptProperty] public bool SkipMondays     { get; set; }
        [NinjaScriptProperty] public bool SkipWeakMonths  { get; set; }
        [NinjaScriptProperty] public bool TrendAligned    { get; set; } // only trade with AM trend
        [NinjaScriptProperty] public bool LongOnly        { get; set; } // v10: see header — costs the 2022 hedge
        [NinjaScriptProperty] public bool FlipOnStop      { get; set; } // v10: OOS +$6.9k / IS -$4.9k — regime bet, default OFF
        [NinjaScriptProperty] public int  MaxTrades       { get; set; }

        // VWAP (calculated manually from session start 9:30 AM)
        private double sumPV, sumVol, vwap;

        // Morning trend anchor
        private double openPrice930;
        private string amTrend;  // "bull" or "bear" locked at 11:00 AM

        // State
        private bool   wasExtended;
        private bool   prevAboveVWAP;
        private bool   prevSet;
        private int    tradesToday;
        private bool   inPos;
        private double entryPx;        // v10: needed for the asymmetric 13:00 exit
        private int    flipPending;    // v10: 0=none, +1=flip long, -1=flip short
        private bool   flippedToday;
        private double dailyPnL;
        private DateTime lastDay;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "CruzCapital VWAP Reclaim 11AM–1PM NQ (v10 — LongOnly toggle, deploy-ready)";
                Name        = "CruzCapitalVWAP";
                Calculate   = Calculate.OnBarClose;
                EntriesPerDirection          = 1;
                EntryHandling                = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds    = 300;
                BarsRequiredToTrade          = 20;
                SkipMondays    = true;
                SkipWeakMonths = true;
                TrendAligned   = true;
                LongOnly       = false;  // both directions — see v10 header table
                FlipOnStop     = false;  // opt-in regime bet — see v10 header
                MaxTrades      = 1;
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
            int month = now.Month;

            if (now.Date != lastDay) { ResetDay(); lastDay = now.Date; }
            if (ts < new TimeSpan(9, 30, 0) || ts >= new TimeSpan(15, 55, 0)) return;
            if (SkipMondays && now.DayOfWeek == DayOfWeek.Monday) return;
            if (SkipWeakMonths && WEAK.Contains(month)) return;
            if (dailyPnL <= -DLL) return;

            // Capture 9:30 AM open
            if (ts >= new TimeSpan(9, 30, 0) && ts < new TimeSpan(9, 31, 0) && openPrice930 == 0)
                openPrice930 = Open[0];

            // Update session VWAP from 9:30 AM onwards
            double typical = (High[0] + Low[0] + Close[0]) / 3.0;
            sumPV  += typical * Volume[0];
            sumVol += Volume[0];
            if (sumVol > 0)
            {
                vwap = sumPV / sumVol;
                Draw.HorizontalLine(this, "vwap", false, vwap, Brushes.White, DashStyleHelper.Solid, 1);
            }

            // Lock trend at 11:00 AM (v6: was 10:30 — fresher read at entry-window
            // start; IS 2022-24 $5,428 → $21,073 with all years positive)
            if (amTrend == null && ts >= new TimeSpan(11, 0, 0) && openPrice930 != 0)
                amTrend = Close[0] > openPrice930 ? "bull" : "bear";

            // ── Manage open position (ALL RTH bars, incl. post-13:00) ──────
            // v10 asymmetric exit: at 13:00+ cut positions at/below breakeven;
            // positions in profit keep working target/stop (15:55 session
            // close flattens the rest). Pre-v10 the C# silently held ALL
            // positions past 13:00 — the worst exit variant.
            if (inPos)
            {
                if (Position.MarketPosition == MarketPosition.Flat)
                    inPos = false;
                else
                {
                    if (ts >= new TimeSpan(13, 0, 0))
                    {
                        double upnl = Position.MarketPosition == MarketPosition.Long
                                      ? Close[0] - entryPx : entryPx - Close[0];
                        if (upnl <= 0)
                        {
                            if (Position.MarketPosition == MarketPosition.Long) ExitLong();
                            else ExitShort();
                        }
                    }
                    return;
                }
            }

            // ── Execute pending rejection-flip (armed by a pre-13:00 stop-out) ─
            if (FlipOnStop && flipPending != 0 && !flippedToday
                    && ts < new TimeSpan(13, 0, 0)
                    && Position.MarketPosition == MarketPosition.Flat)
            {
                double fc = Close[0];
                if (flipPending > 0)
                {
                    EnterLong(1, "VWAPFLIP");
                    SetStopLoss("VWAPFLIP",    CalculationMode.Price, fc - STOP_PT,      false);
                    SetProfitTarget("VWAPFLIP", CalculationMode.Price, fc + STOP_PT * RR);
                }
                else
                {
                    EnterShort(1, "VWAPFLIP");
                    SetStopLoss("VWAPFLIP",    CalculationMode.Price, fc + STOP_PT,      false);
                    SetProfitTarget("VWAPFLIP", CalculationMode.Price, fc - STOP_PT * RR);
                }
                entryPx = fc; inPos = true; flippedToday = true; flipPending = 0;
                if (vwap > 0) { prevAboveVWAP = fc > vwap; prevSet = true; }
                return;
            }

            // Extension tracking window: 10 AM – 1 PM
            if (ts < new TimeSpan(10, 0, 0) || ts >= new TimeSpan(13, 0, 0)) return;
            if (vwap == 0 || tradesToday >= MaxTrades) return;

            double close         = Close[0];
            bool   currAboveVWAP = close > vwap;

            // Track if price was sufficiently extended from VWAP
            if (!wasExtended && Math.Abs(close - vwap) > MIN_EXTEND)
                wasExtended = true;

            // Entry gate: 11 AM only — 10-11 AM crosses are weak (OOS PF 1.16 vs 1.80+ after 11 AM)
            if (wasExtended && prevSet && ts >= new TimeSpan(11, 0, 0))
            {
                bool crossedUp   = !prevAboveVWAP && currAboveVWAP;
                bool crossedDown =  prevAboveVWAP && !currAboveVWAP;

                // Trend-alignment filter: only enter in direction of morning trend
                bool canLong  = !TrendAligned || amTrend == "bull";
                bool canShort = (!TrendAligned || amTrend == "bear") && !LongOnly;

                if (crossedUp && canLong)
                {
                    EnterLong(1, "VWAP");
                    SetStopLoss("VWAP",    CalculationMode.Price, close - STOP_PT,      false);
                    SetProfitTarget("VWAP", CalculationMode.Price, close + STOP_PT * RR);
                    entryPx = close; inPos = true; tradesToday++; wasExtended = false;
                }
                else if (crossedDown && canShort)
                {
                    EnterShort(1, "VWAP");
                    SetStopLoss("VWAP",    CalculationMode.Price, close + STOP_PT,      false);
                    SetProfitTarget("VWAP", CalculationMode.Price, close - STOP_PT * RR);
                    entryPx = close; inPos = true; tradesToday++; wasExtended = false;
                }
            }

            prevAboveVWAP = currAboveVWAP;
            prevSet       = true;
        }

        protected override void OnExecutionUpdate(Execution execution, string executionId,
            double price, int quantity, MarketPosition marketPosition, string orderId, DateTime time)
        {
            if (execution.Order == null || execution.Order.OrderState != OrderState.Filled) return;
            OrderAction a = execution.Order.OrderAction;
            if (a != OrderAction.Sell && a != OrderAction.BuyToCover) return;
            int n = SystemPerformance.AllTrades.Count;
            double tradePnL = 0;
            if (n > 0)
            {
                tradePnL = SystemPerformance.AllTrades[n - 1].ProfitCurrency;
                dailyPnL += tradePnL;
            }
            // v10: arm the rejection-flip when the MAIN reclaim stops out
            // pre-13:00 (a failed reclaim = the validated rejection signal).
            // Sell closed a long → flip short; BuyToCover closed a short → flip long.
            if (FlipOnStop && !flippedToday && tradePnL < 0
                    && execution.Order.FromEntrySignal == "VWAP"
                    && time.TimeOfDay < new TimeSpan(12, 58, 0))
                flipPending = (a == OrderAction.Sell) ? -1 : +1;
            inPos = false;
        }

        private void ResetDay()
        {
            sumPV        = 0; sumVol = 0; vwap = 0;
            openPrice930 = 0; amTrend = null;
            wasExtended  = false; prevAboveVWAP = false; prevSet = false;
            tradesToday  = 0; inPos = false; dailyPnL = 0;
            entryPx      = 0; flipPending = 0; flippedToday = false;
        }
    }
}
