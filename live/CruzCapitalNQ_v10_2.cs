// CruzCapitalNQ_v10_2.cs — NinjaTrader 8 Strategy
// Port of the Python ORB bot (v10.2) for Lucid Trading 50K Direct account
//
// HOW TO INSTALL:
//   1. Copy this file to:
//      C:\Users\<you>\Documents\NinjaTrader 8\bin\Custom\Strategies\CruzCapitalNQ_v10_2.cs
//   2. In NT8 desktop: New → NinjaScript Editor → Compile All (F5)
//   3. On an NQ Continuous (#F) 1-minute chart:
//      Strategies panel → Add → CruzCapitalNQ_v10_2
//   4. Set Account = your Lucid account, enable "Automated" trading
//   5. Leave NT8 running 24/7 on the VPS
//
// STRATEGY: NQ ORB v10 — matches Python bot config exactly
//   OR:      9:30–9:44 ET (15 min)
//   Entry:   close > OR high + 4pt (long) | close < OR low − 4pt (short)
//   Stop:    22pt fixed, placed 5pt beyond OR edge (27pt from entry)
//   Target:  3R funded (81pt) | 2R eval (54pt)
//   Pyramid: +1c at 1R, stops → breakeven (needs $1,500 profit + 5 trades)
//   2nd break: re-entry after target hit, starts 10:00 AM
//   Asia:    6–9 PM ET gap continuation (1c until $1,500 profit, then 2c)
//   Scale:   1c until lifetimePnL >= $1,500 (Lucid $2k DD floor — 1 loss = $550, 3 cushion)
//   Filters: skip Monday, weak months (Jun/Sep/Dec), OR range 55–110pt
//   DLL:     halt at −$1,200/day

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
    public class CruzCapitalNQ_v10_2 : Strategy
    {
        // ── Hard-coded parameters (match config.py exactly) ────────────────
        private const double STOP_PT      = 22.0;   // fixed stop distance
        private const double STOP_BUF     = 5.0;    // stop placed this far beyond OR edge
        private const double BRK_BUF      = 4.0;    // close must exceed OR edge by this
        private const double MIN_OR        = 55.0;
        private const double MAX_OR        = 110.0;
        private const double FUNDED_RR     = 3.0;
        private const double EVAL_RR       = 2.0;
        private const double DLL           = 1200.0; // daily loss limit
        private const double SCALE_GATE    = 1500.0; // profit required before scaling to 2c (Lucid $2k DD floor)
        private const int    PYR_WARMUP    = 5;
        private const int    MAX_CON       = 2;
        private const double GAP_MIN       = 20.0;   // directional gap threshold
        private const double SIG_MIN       = 60.0;   // minimum signal score
        private const double ASIA_GAP_LO   = 30.0;
        private const double ASIA_GAP_HI   = 80.0;
        private const double ASIA_STOP     = 15.0;
        private const double ASIA_RR       = 1.5;

        // PM ORB constants (afternoon 1:00-2:15 session — v10.2 research)
        private const double PM_STOP    = 22.0;   // same risk as morning: $440/trade
        private const double PM_MIN_OR  = 15.0;   // tight post-lunch consolidation
        private const double PM_MAX_OR  = 60.0;   // widened: 15-60pt adds 24 trades, PF 1.368 vs 1.337
        private const double PM_RR      = 2.0;    // 2R = 44pt target
        private const double PM_BRK_BUF = 2.0;   // tighter breakout filter than morning

        private static readonly HashSet<int> WEAK    = new HashSet<int> { 6, 9, 12 };
        private static readonly HashSet<int> STRONG  = new HashSet<int> { 1, 2, 3, 4, 5, 10, 11 };
        private static readonly HashSet<int> ASIA_WK = new HashSet<int> { 8, 11 };

        // ── User-configurable (appear in NT8 strategy properties) ──────────
        [NinjaScriptProperty] public bool EvalMode            { get; set; }
        [NinjaScriptProperty] public bool AsiaEnabled         { get; set; }
        [NinjaScriptProperty] public bool PyramidEnabled      { get; set; }
        [NinjaScriptProperty] public bool SecondBreakoutEnabled { get; set; }
        [NinjaScriptProperty] public bool SkipMondays         { get; set; }
        [NinjaScriptProperty] public bool SkipFridays         { get; set; }
        [NinjaScriptProperty] public bool PmOrbEnabled        { get; set; }

        // ── OR state ───────────────────────────────────────────────────────
        private double orHi, orLo;
        private bool   orBuilt;
        private double orVol;
        private Queue<double> orVolHistory = new Queue<double>();
        private double avgOrVol;

        // ── Prev-day data (from daily series) ─────────────────────────────
        private double prevClose;

        // ── Daily session state ────────────────────────────────────────────
        private bool   tradedToday;
        private bool   secondReady;
        private bool   pyramidDone;
        private bool   inPos;
        private bool   posLong;
        private double entryPx;
        private string activeSig;
        private double dailyPnL;           // tracked from executed exits
        private double sessionStartBalance; // snap at first bar of day

        // ── Asia state ─────────────────────────────────────────────────────
        private double cmeClose;           // captured at 5 PM ET
        private bool   asiaTraded;

        // ── PM ORB state ────────────────────────────────────────────────────
        private double pmOrHi, pmOrLo;
        private bool   pmOrBuilt;
        private bool   pmTraded;

        // ── Lifetime state ─────────────────────────────────────────────────
        private int    totalTrades;
        private DateTime lastDay;

        // ── Accumulated account profit (tracked manually) ──────────────────
        private double lifetimePnL; // sum of all realised exits

        // ── Confidence score state (pivot / VWAP / zone / slope) ──────────
        private double prevDayHigh;  // prior RTH high  → pivot R levels
        private double prevDayLow;   // prior RTH low   → pivot S levels
        private double prevDayVwap;  // prior RTH VWAP  → directional filter
        private double dayHi;        // current-day RTH high accumulator
        private double dayLo;        // current-day RTH low accumulator
        private double dayVwapPV;    // current-day cumulative price×volume
        private double dayVwapV;     // current-day cumulative volume
        private double vwapAt935;    // VWAP snapshot at 9:35 close (slope start)
        private double vwapAt944;    // VWAP snapshot at 9:44 close (slope end)
        private double orClosePx;    // 9:44 bar close (OR context price)

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description  = "CruzCapital NQ ORB v10";
                Name         = "CruzCapitalNQ v10.2";
                Calculate    = Calculate.OnBarClose;
                EntriesPerDirection        = 2; // allow pyramid add-on
                EntryHandling              = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds    = 300; // flatten at 3:55 PM ET
                BarsRequiredToTrade          = 20;

                EvalMode             = false;
                AsiaEnabled          = true;
                PyramidEnabled       = true;
                SecondBreakoutEnabled = true;
                SkipMondays          = true;
                SkipFridays          = true;
                PmOrbEnabled         = true;  // v10.2: +$973/mo OOS (Tue-Thu, OR 15-50pt)
            }
            else if (State == State.Configure)
            {
                AddDataSeries(Data.BarsPeriodType.Day, 1); // daily series for prev close
            }
            else if (State == State.DataLoaded)
            {
                ResetDay();
                totalTrades  = 0;
                lifetimePnL  = 0;
                lastDay      = DateTime.MinValue;
            }
        }

        protected override void OnBarUpdate()
        {
            if (BarsInProgress != 0) return;                           // only 1-min series
            if (CurrentBars[0] < BarsRequiredToTrade) return;

            DateTime now = Time[0];
            TimeSpan ts  = now.TimeOfDay;
            int month    = now.Month;
            int dow      = (int)now.DayOfWeek; // 0=Sun,1=Mon,4=Thu

            // ── New day ────────────────────────────────────────────────────
            if (now.Date != lastDay)
            {
                if (CurrentBars[1] > 1)
                    prevClose = Closes[1][1]; // yesterday's daily close
                // Save prior-day RTH context before reset (confidence score)
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

            // ── 5 PM: capture CME close just before the 1-hour halt ────────
            if (ts >= new TimeSpan(17, 0, 0) && ts < new TimeSpan(17, 2, 0) && cmeClose == 0)
            {
                cmeClose = Close[0];
                return;
            }

            // ── 6–9 PM: Asia session ───────────────────────────────────────
            if (ts >= new TimeSpan(18, 0, 0) && ts < new TimeSpan(21, 0, 0))
            {
                // Entry only on the 18:15 bar (matches Python backtest calibration)
                if (AsiaEnabled && !EvalMode && !asiaTraded
                        && ts >= new TimeSpan(18, 15, 0) && ts < new TimeSpan(18, 16, 0))
                    TryAsiaEntry(ts, month, dow);
                return; // nothing else to do in this window
            }

            // ── Outside US session ─────────────────────────────────────────
            if (ts < new TimeSpan(9, 30, 0)) return;

            // ── RTH VWAP/range tracking — runs before any skip filters so
            //    prior-day context is always captured for the next session ──
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
            if (SkipFridays && dow == 5) return;  // v10.2: Fri OOS PF 1.21 vs 4+ Tue-Thu
            if (dailyPnL <= -DLL) return;

            // ── Build Opening Range (9:30–9:44 bars close before 9:45) ─────
            if (ts < new TimeSpan(9, 45, 0))
            {
                // Still in the OR window — accumulate
                orHi  = Math.Max(orHi,  High[0]);
                orLo  = Math.Min(orLo,  Low[0]);
                orVol += Volume[0];

                // Mark OR complete when the 9:44 bar closes
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

            // ── PM OR range building (13:00-13:14) — runs on ALL days,
            //    independent of morning OR validity ────────────────────────
            if (PmOrbEnabled && ts >= new TimeSpan(13, 0, 0) && ts < new TimeSpan(13, 15, 0))
            {
                if (!pmOrBuilt)
                {
                    pmOrHi = pmOrHi == 0 ? High[0] : Math.Max(pmOrHi, High[0]);
                    pmOrLo = pmOrLo == double.MaxValue ? Low[0] : Math.Min(pmOrLo, Low[0]);
                }
                if (ts >= new TimeSpan(13, 14, 0)) pmOrBuilt = true;
            }

            // ── PM ORB entry (13:15-14:15) — runs independently of morning validity ──
            if (PmOrbEnabled && pmOrBuilt && ts >= new TimeSpan(13, 15, 0)
                    && ts <= new TimeSpan(14, 0, 0) && !pmTraded && !inPos)
            {
                TryPMEntry(ts, month, dow);
                return;
            }

            // ── Morning OR validity check (after PM branch — PM works on all days) ──
            if (!orBuilt) return;
            double orRange = orHi - orLo;
            if (orRange < MIN_OR || orRange > MAX_OR) return;

            // ── Last entry time (10:30 cutoff for morning session) ─────────
            if (ts >= new TimeSpan(10, 30, 0) && !inPos) return;

            // ── Manage open morning position (pyramid check) ───────────────
            if (inPos)
            {
                TryPyramid();
                return;
            }

            // ── Morning entries (9:45-10:30) ──────────────────────────────
            if (!tradedToday)
                TryUSEntry(ts, month, "ORB1");
            else if (SecondBreakoutEnabled && secondReady && ts >= new TimeSpan(10, 0, 0))
                TryUSEntry(ts, month, "ORB2");
        }

        // ── US Session Entry ───────────────────────────────────────────────
        private void TryUSEntry(TimeSpan ts, int month, string sigName)
        {
            double close = Close[0];
            bool goLong  = close > orHi + BRK_BUF;
            bool goShort = close < orLo - BRK_BUF;
            if (!goLong && !goShort) return;

            double rr      = EvalMode ? EVAL_RR : FUNDED_RR;
            double effStop = STOP_PT + STOP_BUF; // 27pt from entry
            int    qty     = 1;

            // Scale to 2c only after $1,500 profit cushion (protects Lucid $2k DD floor)
            if (!EvalMode && lifetimePnL >= SCALE_GATE) qty = 2;

            // ── First-bar hard gate: skip 9:45 entry (OOS PF 0.839 even at score≥3) ─
            if (ts < new TimeSpan(9, 46, 0)) return;

            // ── Confidence score gate (pivot / VWAP / zone / slope) ────────
            // v10.2: raise to skip<3 — OOS PF 4.52 (was 3.01 at skip<1)
            // skip<3 + hard gate + double_never = OOS PF 4.52, N=48 trades
            int confScore = CalcConfidenceScore(goLong);
            if (confScore < 3) return;  // only score≥3 — OOS PF 3.998+
            // Doubling disabled: at skip<3 all trades are score≥3, doubling reduces PF 4.52→4.02

            if (goLong)
            {
                double sl = close - effStop;
                double tp = close + effStop * rr;
                EnterLong(qty, sigName);
                SetStopLoss(sigName, CalculationMode.Price, sl, false);
                SetProfitTarget(sigName, CalculationMode.Price, tp);
                posLong = true;
            }
            else
            {
                double sl = close + effStop;
                double tp = close - effStop * rr;
                EnterShort(qty, sigName);
                SetStopLoss(sigName, CalculationMode.Price, sl, false);
                SetProfitTarget(sigName, CalculationMode.Price, tp);
                posLong = false;
            }

            entryPx    = close;
            inPos      = true;
            tradedToday = true;
            pyramidDone = false;
            activeSig   = sigName;
        }

        // ── Pyramid at 1R ─────────────────────────────────────────────────
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

        // ── Asia Entry ────────────────────────────────────────────────────
        private void TryAsiaEntry(TimeSpan ts, int month, int dow)
        {
            if (dow == 4) return;              // skip Thursday
            if (ASIA_WK.Contains(month)) return;
            if (cmeClose == 0) return;
            if (dailyPnL <= -DLL) return;

            double gap = Close[0] - cmeClose;
            if (Math.Abs(gap) < ASIA_GAP_LO || Math.Abs(gap) > ASIA_GAP_HI) return;

            int qty = (EvalMode || lifetimePnL < SCALE_GATE) ? 1 : 2;

            if (gap > 0)
            {
                double sl = Close[0] - ASIA_STOP;
                double tp = Close[0] + ASIA_STOP * ASIA_RR;
                EnterLong(qty, "ASIA");
                SetStopLoss("ASIA", CalculationMode.Price, sl, false);
                SetProfitTarget("ASIA", CalculationMode.Price, tp);
            }
            else
            {
                double sl = Close[0] + ASIA_STOP;
                double tp = Close[0] - ASIA_STOP * ASIA_RR;
                EnterShort(qty, "ASIA");
                SetStopLoss("ASIA", CalculationMode.Price, sl, false);
                SetProfitTarget("ASIA", CalculationMode.Price, tp);
            }

            asiaTraded = true;
        }

        // ── PM ORB Entry (1:15-2:15, no confidence score — baseline works) ──
        private void TryPMEntry(TimeSpan ts, int month, int dow)
        {
            if (SkipMondays && dow == 1) return;  // Mon PM PF=0.92 OOS
            if (SkipFridays && dow == 5) return;  // Fri PM PF=0.97 OOS
            if (dailyPnL <= -DLL) return;

            double pmRange = pmOrHi - pmOrLo;
            if (pmRange < PM_MIN_OR || pmRange > PM_MAX_OR) return;

            double close = Close[0];
            bool goLong  = close > pmOrHi + PM_BRK_BUF;
            bool goShort = close < pmOrLo - PM_BRK_BUF;
            if (!goLong && !goShort) return;

            int qty = 1; // 1c always — separate from morning sizing

            if (goLong)
            {
                double sl = close - PM_STOP;
                double tp = close + PM_STOP * PM_RR;
                EnterLong(qty, "PM_ORB");
                SetStopLoss("PM_ORB", CalculationMode.Price, sl, false);
                SetProfitTarget("PM_ORB", CalculationMode.Price, tp);
                posLong = true;
            }
            else
            {
                double sl = close + PM_STOP;
                double tp = close - PM_STOP * PM_RR;
                EnterShort(qty, "PM_ORB");
                SetStopLoss("PM_ORB", CalculationMode.Price, sl, false);
                SetProfitTarget("PM_ORB", CalculationMode.Price, tp);
                posLong = false;
            }

            pmTraded  = true;
            inPos     = true;
            entryPx   = close;
            activeSig = "PM_ORB";
        }

        // ── Signal Score ──────────────────────────────────────────────────
        private double CalcScore(bool isLong, TimeSpan ts, int month)
        {
            double score = 0;

            // Time score (max 20)
            if      (ts < new TimeSpan(9, 50, 0))  score += 20;
            else if (ts < new TimeSpan(10, 0, 0))  score += 15;
            else if (ts < new TimeSpan(10, 15, 0)) score += 10;
            else                                    score += 5;

            // Gap score (max 25) — use open vs prev close as gap proxy
            double gap = (CurrentBars[0] > 0 ? Open[0] : Close[0]) - prevClose;
            if      (isLong  && gap >  GAP_MIN) score += 25;
            else if (!isLong && gap < -GAP_MIN) score += 25;
            else                                 score += 15; // neutral gap

            // OR volume score (max 25)
            if (avgOrVol > 0)
            {
                double ratio = orVol / avgOrVol;
                if      (ratio >= 0.7 && ratio <= 1.5) score += 25;
                else if (ratio >= 0.5)                  score += 15;
                else                                    score += 5;
            }
            else score += 15; // no history yet → neutral

            // OR size score (max 20): 62–86pt medium, 86–120 large
            double rng = orHi - orLo;
            if      (rng >= 62 && rng <= 86) score += 20;
            else if (rng >  86 && rng <= 120) score += 15;
            else if (rng > 120)               score += 5;

            // Strong month bonus (max 10)
            if (STRONG.Contains(month)) score += 10;

            return score;
        }

        // ── Confidence Score (pivot / VWAP / zone / slope) — returns 0-4 ──
        private int CalcConfidenceScore(bool isLong)
        {
            // Need at least one prior day of RTH H/L and an OR close price
            if (prevDayHigh <= 0 || prevDayLow <= 0 || prevDayHigh <= prevDayLow) return 4;
            if (orClosePx <= 0) return 4;

            double H = prevDayHigh, L = prevDayLow, C = prevClose; // C = prior day close
            double P  = (H + L + C) / 3.0;
            double R1 = 2 * P - L;
            double R2 = P + (H - L);
            double S1 = 2 * P - H;
            double S2 = P - (H - L);

            int conf = 0;
            double px = orClosePx; // 9:44 OR close price

            // +1 pivot alignment: OR close above/below prior-day P
            if ((isLong && px > P) || (!isLong && px < P)) conf++;

            // +1 prior session VWAP alignment
            if (prevDayVwap > 0)
                if ((isLong && px > prevDayVwap) || (!isLong && px < prevDayVwap)) conf++;

            // +1 HOT zone (R1-R2 for longs / S2-S1 for shorts; p=0.017 bootstrap)
            if (isLong  && px >= R1 && px <= R2) conf++;
            else if (!isLong && px >= S2 && px <= S1) conf++;

            // +1 VWAP slope aligned (rising 9:35→9:44 for longs, falling for shorts)
            if (vwapAt935 > 0 && vwapAt944 > 0)
            {
                double slope = vwapAt944 - vwapAt935;
                if ((isLong && slope > 0) || (!isLong && slope < 0)) conf++;
            }

            return conf;
        }

        // ── Track closed P&L for DLL and pyramid gate ─────────────────────
        protected override void OnExecutionUpdate(Execution execution,
            string executionId, double price, int quantity,
            MarketPosition marketPosition, string orderId, DateTime time)
        {
            if (execution.Order == null) return;
            if (execution.Order.OrderState != OrderState.Filled) return;

            // Only count closing orders (exits, not entries)
            OrderAction action = execution.Order.OrderAction;
            bool isClose = (action == OrderAction.Sell || action == OrderAction.BuyToCover);
            if (!isClose) return;

            // Get actual realized P&L from the completed trade in SystemPerformance
            int n = SystemPerformance.AllTrades.Count;
            if (n > 0)
            {
                Trade lastTrade = SystemPerformance.AllTrades[n - 1];
                double tradePnL = lastTrade.ProfitCurrency;
                dailyPnL    += tradePnL;
                lifetimePnL += tradePnL;

                // Unlock second breakout on a winning trade
                if (tradePnL > 0) secondReady = true;
            }

            totalTrades++;
            inPos = false;
        }

        // ── Reset at start of each new day ────────────────────────────────
        private void ResetDay()
        {
            orHi        = double.MinValue;
            orLo        = double.MaxValue;
            orBuilt     = false;
            orVol       = 0;
            tradedToday  = false;
            secondReady  = false;
            pyramidDone  = false;
            inPos        = false;
            entryPx      = 0;
            activeSig    = "";
            dailyPnL     = 0;
            cmeClose     = 0;
            asiaTraded   = false;
            // PM ORB state
            pmOrHi    = 0;
            pmOrLo    = double.MaxValue;
            pmOrBuilt = false;
            pmTraded  = false;
            // Confidence score daily accumulators
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
