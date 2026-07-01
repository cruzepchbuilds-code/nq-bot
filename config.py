"""
CruzCapital NQ Bot -- Configuration
Optimized for real NQ data (Databento CME, 2024-2026)
v2: disabled fade, 2.5x VWAP RR, 2.0x breakout RR, 3pt buffer, 12% DD halt
v3: added London/NY Overlap Momentum strategy (8:00-9:25 ET)
v4: breakout-only (VWAP pull -$10k OOS, London drag disabled),
    ORB_BREAKOUT_RR_TARGET 2.0x, buffer 4pt, Apex DD 7k -- OOS PF 1.33 (+$22k)
v5: LAST_ENTRY_TIME 11:15, signal strength scoring, partial exits, regime calendar
v6: fixed neutral-gap bug (gap>0 strict), OR range 55-130 -- OOS PF 1.63 (+$20.5k)
v7: improvement_runner.py -- 5 winners from 10-improvement test
    LAST_ENTRY_TIME 10:30, WEAK_MONTHS=[6,9,12], STRONG_MONTHS=[1-5,10,11]
    ORB_MAX_RANGE_POINTS=110, PYRAMID_WARMUP_TRADES=5
    OOS 2025-26: PF 2.14, WR 47.2%, Net +$36,675, MaxDD 3.6%
    MC pass: 93.2%, avg 9.1 wk (baseline was 84.7%, 10.7 wk)
v8: stop_size_test.py + branch merge (best of both)
    ORB_FIXED_STOP_POINTS=22pt (was 25pt): PF 2.01→2.18, WR 46.7→48.8%, same 3/4 pass
    Net +$56,395 over 4yr OOS (2023 halts all sizes; 2024/25/26 pass)
    APEX_TRAILING_DD=$7k for simulation headroom (real Apex 50k = $2,500; see stop_size_test.py)
v9: Lucid Trading 50K Pro — 2 contracts, second breakout enabled
    STARTING_BALANCE=50K, MAX_CONTRACTS=2, SECOND_BREAKOUT_ENABLED=True
    MAX_LOSSES_PER_DAY=1 (2c × 27pt = $1,090/loss, safe under $1,200 DLL and $2,000 max)
    APEX_TRAILING_DD=8000 (sim headroom; Lucid uses fixed EOD floor, not trailing)
    4yr OOS: PF 1.83, Net +$77,470, T=231 | avg $387/wk
v10: Asia session scaled to 2 contracts (was 1c)
v10.1: 1c until $1,500 profit gate (Lucid $2k DD floor — was 2c which risked $1,099/trade)
    1c risk: 27pt × $20 × 1c + comm = ~$550/loss → 3 stop cushion before DD breach
    Asia always separate calendar day from ORB, so losses never stack intraday
    4yr OOS: PF 1.81, Net +$83,825, T=231 | avg $419/wk (+$32/wk over v9)
    Path to $1k/week: run 3 Lucid 50K accounts simultaneously (~$1,257/wk avg)
v10.2: US ORB quality overhaul — OOS PF 2.5 -> 4.94 (target 3-5, hit it)
    CONFIDENCE_SCORE_SKIP_BELOW = 3  (was 1) — only trade score>=3
    ORB_SKIP_FIRST_BAR = True        — skip 9:45 first-bar (OOS PF 0.839 even at score>=3)
    CONFIDENCE_SCORE_DOUBLE_AT = 99  — disable doubling (hurts PF when skip<3)
    SKIP_FRIDAYS = True              — Fri OOS PF 1.21 vs 3.85+ Tue-Thu
    OOS 2025-26: PF 4.94  WR 53%  N=36  Net $40,330  Avg $1,120/trade
    IS  2022-24: PF 1.42  WR 29%  N=75  Net $15,435  (IS/OOS consistent)
"""

# -- Instrument -------------------------------------------------------------
SYMBOL = "NQ"
POINT_VALUE = 20.0
TICK_SIZE = 0.25
COMMISSION_PER_SIDE = 2.50
SLIPPAGE_TICKS = 2

# -- Session ----------------------------------------------------------------
SESSION_OPEN = "09:30"
OPENING_RANGE_MINUTES = 15      # 9:30-9:45
LAST_ENTRY_TIME = "10:30"       # v7: brain data WR 8.7% after 10:30 -- best cutoff
FLATTEN_TIME = "15:55"

# -- Day filters ------------------------------------------------------------
SKIP_MONDAYS = True             # ORB weakest on Mondays historically
SKIP_FRIDAYS = True             # v10.2: OOS PF 1.21 on Fri vs 4.0+ Tue-Thu; skip improves OOS PF 3.36→4.94

# -- ORB Strategy -----------------------------------------------------------
ORB_RR_TARGET = 2.0             # (VWAP pull disabled -- kept for reference)
ORB_BREAKOUT_RR_TARGET = 2.0    # eval mode target (2R = 54pt at 27pt eff stop)
ORB_FUNDED_RR_TARGET   = 3.0    # funded mode target (3R = 81pt) -- OOS +$7,480 vs 2R
ORB_STOP_MODE = "fixed"
ORB_FIXED_STOP_POINTS = 22.0     # v8: optimized for $2,500 Apex DD (was 25pt)
                                  # 22pt+5pt=27pt eff | $540/trade | 75% pass rate
                                  # 25pt fails 2/4 yrs; 22pt tighter loss preserves DD headroom
ORB_STOP_BUFFER_POINTS = 5.0    # effective stop distance = 27pts from entry
ORB_BREAKOUT_BUFFER_POINTS = 4.0   # close must exceed OR edge by this much
ORB_MIN_RANGE_POINTS = 55.0     # v10.2: 55+ OOS PF 4.94 with skip_fri+skip3 (70+ = 5.78 but N=28)
ORB_MAX_RANGE_POINTS = 110.0    # v7: OR 110-130 drags PF; cap at 110 optimal
ORB_BREAKOUT_CONFIRM = "close"

# -- Entry filters ----------------------------------------------------------
GAP_FILTER_POINTS = 20.0        # min gap size to trigger directional filter
BREAKOUT_MIN_VOLUME = 200       # min volume on breakout bar for confirmation

# -- VWAP Pullback Strategy (AM second trade, within LAST_ENTRY_TIME) -------
VWAP_PULLBACK_ENABLED = False   # AM second trade: OOS drag (-$1.4k), PF 1.46
VWAP_TOLERANCE_POINTS = 3.0

# -- PM VWAP Continuation (12:00-14:30, after AM session closes) -----------
PM_VWAP_ENABLED = False         # PM VWAP: OOS -$11.5k, halts account (cascade)
PM_VWAP_START = "12:00"
PM_VWAP_LAST_ENTRY = "14:30"
PM_VWAP_STOP_POINTS = 20.0
PM_VWAP_RR = 2.0                # 40pt target at 20pt stop (exceeds MIN_RR 1.9)
PM_VWAP_TOLERANCE = 5.0

# -- PM ORB (afternoon 1:00-1:14 range, 1:15-2:15 entry) -------------------
# Research basis (OOS 2025-2026, Tue-Thu, OR 15-50pt):
#   N=116  WR=47%  PF=1.64  Net=+$17,520  Avg=+$151/trade
#   IS 2022-2024: N=337  WR=44%  PF=1.54  (consistent, not overfit)
#   DOW: Mon PF=0.92 / Fri PF=0.97 → skip both (same as morning session)
PM_ORB_ENABLED                = True
PM_ORB_MIN_RANGE_POINTS       = 15.0   # tight consolidation only
PM_ORB_MAX_RANGE_POINTS       = 60.0   # widened from 50: adds 24 trades, OOS PF 1.368 vs 1.337
PM_ORB_FIXED_STOP_POINTS      = 22.0   # same as morning (familiar risk: $440/trade)
PM_ORB_BREAKOUT_BUFFER_POINTS = 2.0    # close must exceed OR edge by 2pt
PM_ORB_RR_TARGET              = 2.0    # 2R = 44pt target — best net in sweep
PM_ORB_MAX_CONTRACTS          = 1      # eval: always 1c; funded: set to MAX_CONTRACTS

# -- Gap Fill (large gap days, fade toward prior close) --------------------
GAP_FILL_ENABLED = False        # Gap fill: OOS -$4.4k drag
GAP_FILL_MIN_POINTS = 40.0
GAP_FILL_LAST_ENTRY = "10:30"
GAP_FILL_STOP_POINTS = 20.0
GAP_FILL_RR = 1.5

# -- London/NY Overlap Momentum ---------------------------------------------
# Window: 8:00 AM - 9:25 AM ET only. Hard exit at 9:25, never overlaps ORB.
# Range built 8:00-8:59. Trend classified at 9:00 bar close. Entry at 9:05.
LONDON_ENABLED = False          # disabled: OOS drag; re-enable to experiment
LONDON_MIN_RANGE_POINTS = 20.0  # skip if London range < this (too quiet)
LONDON_MAX_RANGE_POINTS = 200.0 # skip if London range > this (news chaos)
LONDON_STOP_POINTS = 20.0       # fixed stop distance in NQ points
LONDON_TARGET_POINTS = 30.0     # fixed target (= 1.5R with 20pt stop)
LONDON_TREND_THRESHOLD = 0.30   # price in top/bottom 30% of range to qualify

# -- Regime Detector --------------------------------------------------------
REGIME_LOOKBACK_DAYS = 5
REGIME_ATR_PERIOD = 14
REGIME_BREAKOUT_THRESHOLD = 0.18
REGIME_FADE_THRESHOLD = 0.18  # equal to breakout threshold -> fade disabled

# -- Bankroll Manager -------------------------------------------------------
STARTING_BALANCE = 50000.0       # Lucid Trading 50K Pro account

RISK_PER_TRADE_PCT = 0.01
MIN_RR = 1.9
MAX_CONTRACTS = 1                # Start 1c — Lucid $2k DD: 1 loss=$550, 3 stop cushion
SCALE_TO_2C_GATE = 1500.0       # NinjaScript unlocks 2c when lifetimePnL >= this

DAILY_LOSS_LIMIT_PCT = 0.024     # $1,200 DLL on 50K account (Lucid rule)
MAX_CONSECUTIVE_LOSING_DAYS = 2
MAX_TRADES_PER_DAY = 4          # 2 ORB + 1 second breakout + 1 Asia
MAX_LOSSES_PER_DAY = 1          # 2 contracts × $545/loss = $1,090 — 2nd loss would breach $2,000 max
DAILY_PROFIT_LOCK_PCT = 0.03

WEEKLY_LOSS_LIMIT_PCT = 0.05

MAX_TOTAL_DRAWDOWN_PCT = 0.12    # career halt — Lucid DD resets per account
RECOVERY_MODE_TRIGGER_PCT = 0.03
RECOVERY_SIZE_MULTIPLIER = 0.5

# -- Lucid Trading Drawdown (EOD, not trailing) -----------------------------
# Lucid's $2,000 max loss is from STARTING balance (fixed floor $48,000), not trailing.
# Our daily limits (DLL $1,200, MAX_LOSSES_PER_DAY=1) enforce this per-session.
# Set trailing DD high so the backtest doesn't halt on a Apex-style trailing rule.
APEX_TRAILING_DD = 8000.0        # sim headroom — real protection via daily limits above
ENFORCE_APEX_RULES = True

# -- Signal Strength Filter -------------------------------------------------
SIGNAL_STRENGTH_MIN_SCORE = 60  # skip signals scoring below this
OR_SIZE_SCORE_BOUNDS = (62.0, 86.0, 120.0)  # (med_lo, med_hi, large_hi) in instrument points

# -- Regime Calendar --------------------------------------------------------
STRONG_MONTHS = [1, 2, 3, 4, 5, 10, 11]  # v7: Apr WR 48%, May WR 48% moved to strong
WEAK_MONTHS   = [6, 9, 12]               # v7: Sep WR 25%, Jun 33%, Dec 38% weak
SKIP_MONTHS   = []                        # hard skip (no trading at all); [] = disabled

# -- Dynamic Exits ----------------------------------------------------------
PARTIAL_EXIT_ENABLED = False     # disabled: incompatible with PYRAMIDING_ENABLED
PARTIAL_EXIT_STALL_BARS = 30     # exit all if stalled at 1R for this many minutes

# -- Pyramiding -------------------------------------------------------------
# Conservative: add 1 contract at 1R (stop -> breakeven). Both exit at original 2R target.
# Guards: account must be up $1,500+ AND 20 trades logged before pyramiding is armed.
# PARTIAL_EXIT must be False (incompatible).
PYRAMIDING_ENABLED = True        # single add-on at 1R milestone
PYRAMID_MAX_CONTRACTS = 2        # original + 1 add-on only (never 2 adds)
PYRAMID_MIN_PROFIT_BUFFER = 1500.0   # account must be up this much (from starting balance)
PYRAMID_WARMUP_TRADES = 5        # v7: reduced from 20; arms faster in monthly eval runs

# -- Eval Mode --------------------------------------------------------------
# Trade conservatively during a prop firm evaluation attempt:
#   1 contract only (no signal-strength scaling, no strong-month bonus)
#   Pyramiding disabled (no add-ons, no breakeven-stop risk)
#   Asia session disabled (low volume risk during evaluation)
# Funded account: set EVAL_MODE=False (pyramiding and sizing scale normally)
EVAL_MODE = False                # True = evaluation phase | False = funded phase

# -- Eval Profit Target (Lucid 50K Pro) ------------------------------------
# Bot halts ALL trading the moment the account hits starting + target.
# This locks in the pass — do NOT keep trading after hitting the target.
# Lucid 50K Pro: $3,000 profit target (account reaches $53,000)
EVAL_PROFIT_TARGET = 3000.0      # halt and alert when this profit is reached

# -- Eval EOD Max Loss Floor (Lucid 50K Pro) --------------------------------
# Lucid measures drawdown at EOD (not trailing like Apex).
# If EOD balance ever falls below $48,000 ($50k - $2k), the eval fails.
# Our DLL + MAX_LOSSES_PER_DAY already prevent this, but this is a hard backstop.
EVAL_MAX_LOSS = 2000.0           # fail if balance drops this far below STARTING_BALANCE

# -- Eval Start Date --------------------------------------------------------
# Set this when you begin the evaluation so morning_check.py can track
# elapsed days and project completion. Format: "YYYY-MM-DD"
EVAL_START_DATE = ""             # e.g. "2026-07-01" — leave blank to auto-detect from log

# -- Second Breakout (re-entry after first trade hits 2R target) ------------
# One additional breakout per day if the first trade hit its target.
# Only fires after SECOND_BREAKOUT_MIN_TIME (avoids chasing early fills).
SECOND_BREAKOUT_ENABLED = True        # re-entry after first trade hits target
SECOND_BREAKOUT_MIN_TIME = "10:00"    # no re-entry before this time ET

# -- High-Gap Signal Threshold ----------------------------------------------
# Gap > HIGH_GAP_THRESHOLD uses a potentially lower score threshold.
# Defaults match MIN_SCORE so there is no effect unless explicitly lowered.
HIGH_GAP_THRESHOLD = 40.0
SIGNAL_STRENGTH_MIN_SCORE_HIGH_GAP = 60  # set below SIGNAL_STRENGTH_MIN_SCORE to loosen

# -- Gap Dead Zone Exclude --------------------------------------------------
# Gaps in the range (GAP_EXCLUDE_MIN, GAP_EXCLUDE_MAX] are treated as neutral.
# Set both to 0.0 to disable (default). Research: gap 40-60pt WR 32.5% (dead zone).
GAP_EXCLUDE_MIN = 0.0       # lower bound of dead zone (exclusive)
GAP_EXCLUDE_MAX = 0.0       # upper bound of dead zone (inclusive); 0 = disabled

# -- Volume Ratio Gate ------------------------------------------------------
# Floor: skip if OR vol_ratio below threshold. Ceiling: skip spike bars above threshold.
# Research (206 OOS trades): vol 0.7-0.9x WR 52.8% (best); vol >1.5x WR 27.3% (worst).
BREAKOUT_MIN_OR_VOLUME_RATIO = 0.0   # 0.0 = disabled; floor — skip if ratio below this
BREAKOUT_MAX_OR_VOLUME_RATIO = 0.0   # 0.0 = disabled; ceiling — skip spike bars above this (try 1.5)

# -- Confidence Score Filter (pivot / VWAP / zone / slope) -----------------
# v10 research (threshold_sweep.py + entry_time_score.py, brain/research/):
#   score=0 → OOS PF 0.691  skip
#   score=1 → OOS PF 2.565
#   score=2 → OOS PF 2.773
#   score=3 → OOS PF 3.998  ← only trade these
#   score=4 → OOS PF 1.552  (slope noise at peak; doubling hurts PF)
#   skip<3 + double_never: OOS PF 3.31, N=48 trades — TARGET HIT (was PF 2.48 at skip<1)
#   Entry at 9:45 (first breakout bar) OOS PF 0.839 even at score≥3 → skip it
# Score components (each +1 point, max score=4):
#   pivot : OR close (9:44) > prior-day P=(H+L+C)/3 for longs, < P for shorts
#   vwap  : OR close > prior RTH VWAP for longs, < for shorts
#   zone  : R1 ≤ or_px ≤ R2 (long HOT zone)  or  S2 ≤ or_px ≤ S1 (short HOT zone)
#   slope : session VWAP rising 9:35→9:44 for longs, falling for shorts
CONFIDENCE_SCORE_ENABLED    = True
CONFIDENCE_SCORE_SKIP_BELOW = 3     # skip score < 3  (PF jumps from 2.48 → 3.31 OOS)
CONFIDENCE_SCORE_DOUBLE_AT  = 99    # never double — doubling at score≥3 reduces PF 3.31→3.06

# -- ORB First-Bar Gate -----------------------------------------------------
# Skip entries at exactly 9:45 (first breakout bar after OR close).
# Research: 9:45 entries with score≥3 still show OOS PF 0.839 (losers).
# 9:46-9:59 window OOS PF 2.910, score≥3 OOS PF 3.942.
ORB_SKIP_FIRST_BAR = True           # True = hard gate on 9:45 first-bar entries

# -- Asia Gap Continuation (6:00 PM - 9:00 PM ET) --------------------------
# Funded phase only. Disabled during eval (real slippage kills edge at 3.6% US vol).
# Edge: CME 1-hour halt gap (5pm-6pm ET) → institutional positioning signal.
# Best config: halt gap 30-80pt, skip Thu → OOS PF 1.80, WR 56%, n=77 trades (2024-26)
# Year-over-year improving trend: 2024 PF 1.42 → 2025 PF 1.82 → 2026 PF 2.31
ASIA_ENABLED            = True    # True only in funded phase (not during eval)
ASIA_MAX_CONTRACTS      = 2       # v10: 2c (was 1c) — +$6,355 net over 4yr OOS
                                  # Risk: 15pt × $20 × 2c + comm ≈ $620/loss (< $1,200 DLL)
ASIA_GAP_MIN_POINTS     = 30.0    # skip if abs(halt_gap) < this
ASIA_GAP_MAX_POINTS     = 80.0    # skip if abs(halt_gap) > this (noise/news)
ASIA_STOP_POINTS        = 15.0    # fixed stop distance in NQ points
ASIA_RR_TARGET          = 1.5     # target = stop × RR (22.5pt target at 15pt stop)
ASIA_SKIP_THURSDAYS     = True    # Thu OOS PF 0.82 — worst DOW, skip
# Month filters (independent of US session STRONG/WEAK_MONTHS)
ASIA_WEAK_MONTHS        = [8, 11]           # Aug PF 0.71, Nov PF 0.33 — always skip
ASIA_STRONG_MONTHS      = [2, 6, 9, 10]    # Feb 1.89, Jun 1.42, Sep 1.88, Oct 1.46
ASIA_STRONG_MONTHS_ONLY = False             # False = trade all non-weak months

# -- Telegram Alerts --------------------------------------------------------
# Live trade notifications. Set ENABLED=True and fill token + chat_id for live use.
import os as _os
TELEGRAM_ALERTS_ENABLED = _os.environ.get("TELEGRAM_ENABLED", "true").lower() == "true"
TELEGRAM_BOT_TOKEN = _os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = _os.environ.get("TELEGRAM_CHAT_ID", "")

# -- Discord Alerts ----------------------------------------------------------
# Live trade notifications -> the quant-desk Discord server (discord_ops_bot/).
# Posts signals/#trade-log/#daily-pnl/#risk-alerts/#account-status/#pre-market
# via live/discord_alerts.py using the Ops Agent bot token + channel IDs from
# discord_ops_bot/.env. Safe no-op if that file/token/channels aren't present.
DISCORD_ALERTS_ENABLED = True
