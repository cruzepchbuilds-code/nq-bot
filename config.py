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
ORB_MIN_RANGE_POINTS = 55.0     # research: small OR (<62pts) PF 0.93 drag
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
STARTING_BALANCE = 50000.0

RISK_PER_TRADE_PCT = 0.01
MIN_RR = 1.9
MAX_CONTRACTS = 2

DAILY_LOSS_LIMIT_PCT = 0.015
MAX_CONSECUTIVE_LOSING_DAYS = 2  # sit out next day after N losing days in row
MAX_TRADES_PER_DAY = 2          # 1 AM breakout + 1 partial exit slot
MAX_LOSSES_PER_DAY = 2
DAILY_PROFIT_LOCK_PCT = 0.03

WEEKLY_LOSS_LIMIT_PCT = 0.05

MAX_TOTAL_DRAWDOWN_PCT = 0.12
RECOVERY_MODE_TRIGGER_PCT = 0.05
RECOVERY_SIZE_MULTIPLIER = 0.5

# -- Apex Trailing Drawdown -------------------------------------------------
APEX_TRAILING_DD = 7000.0        # simulation headroom: 3/4 yr pass rate with 22pt stop
                                  # real Apex 50k standard = $2,500; all stop sizes fail 2/4 yrs
                                  # at $2,500 — use stop_size_test.py to verify before live trading
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
# Funded account: set False (pyramiding and sizing scale normally)
EVAL_MODE = False                # set True when attempting a prop firm eval

# -- Second Breakout (re-entry after first trade hits 2R target) ------------
# One additional breakout per day if the first trade hit its target.
# Only fires after SECOND_BREAKOUT_MIN_TIME (avoids chasing early fills).
SECOND_BREAKOUT_ENABLED = False       # set True to allow one re-entry per day
SECOND_BREAKOUT_MIN_TIME = "10:30"    # no re-entry before this time ET

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

# -- Asia Gap Continuation (6:00 PM - 9:00 PM ET) --------------------------
# Funded phase only. Disabled during eval (real slippage kills edge at 3.6% US vol).
# Edge: CME 1-hour halt gap (5pm-6pm ET) → institutional positioning signal.
# Best config: halt gap 30-80pt, skip Thu → OOS PF 1.80, WR 56%, n=77 trades (2024-26)
# Year-over-year improving trend: 2024 PF 1.42 → 2025 PF 1.82 → 2026 PF 2.31
ASIA_ENABLED            = True    # True only in funded phase (not during eval)
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
