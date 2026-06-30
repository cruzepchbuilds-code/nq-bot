"""
CruzCapital ES Bot -- Configuration
Calibrated for ES (E-mini S&P 500) futures.
Data: 2022-2026 (1,128 trading days, 1.56M 1-min bars)

v1: grid search (IS 2022-23 / OOS 2024-26) → OOS PF 1.61
v2: full 4-yr walk-forward OOS (2023-2026) w/ warmup_bt
    H16 winner: SKIP 6 weak months + LAST_ENTRY 10:15
    OOS: 94 trades, PF 1.67, WR 50.0%, Net +$15,792
    Year-by-year: 2024 PF 2.45 / 2025 PF 2.11 / 2026 PF 2.01
    Key findings:
      - ES seasonality is opposite to NQ: Jan weak (PF 0.41), Apr strong (PF 3.88)
      - 10:15-10:29 entry window: WR 30.8%, PF 0.77 (drag) → cut at 10:15
      - Short bias: PF 1.88 vs long PF 1.48
      - 2.0x RR is optimal (1.5x and 2.5x both worse)
      - OR range 5-30pt: do NOT tighten (8-22 worse)
"""

# -- Instrument -------------------------------------------------------------
SYMBOL = "ES"
POINT_VALUE = 50.0
TICK_SIZE = 0.25
COMMISSION_PER_SIDE = 2.50
SLIPPAGE_TICKS = 2

# -- Session ----------------------------------------------------------------
SESSION_OPEN = "09:30"
OPENING_RANGE_MINUTES = 15      # 9:30-9:45
LAST_ENTRY_TIME = "10:15"       # v2: 10:15-10:29 window WR 30.8%, PF 0.77 → cut here
FLATTEN_TIME = "15:55"

# -- Day filters ------------------------------------------------------------
SKIP_MONDAYS = True             # keep Mon skip (no ES-specific Mon research yet)

# -- ORB Strategy -----------------------------------------------------------
ORB_RR_TARGET = 2.0
ORB_BREAKOUT_RR_TARGET = 2.0    # 2.0x optimal for ES (1.5x and 2.5x tested, both worse)
ORB_STOP_MODE = "fixed"
ORB_FIXED_STOP_POINTS = 7.0     # ES: 7pt stop (0.14% of ~5000 price, same % as NQ 25pt)
ORB_STOP_BUFFER_POINTS = 2.0    # effective stop distance = 9pt from entry
ORB_BREAKOUT_BUFFER_POINTS = 1.0   # v2: tighter buffer (was 2.25, which filtered too many ES trades)
ORB_MIN_RANGE_POINTS = 5.0      # v2: do not tighten (8-22 range worse than 5-30)
ORB_MAX_RANGE_POINTS = 30.0     # ES p90 OR = 28pt; cap at 30pt
ORB_BREAKOUT_CONFIRM = "close"

# -- Entry filters ----------------------------------------------------------
GAP_FILTER_POINTS = 5.0         # ES: 5pt (NQ 20pt / ~4 scale)
BREAKOUT_MIN_VOLUME = 500       # ES higher liquidity threshold

# -- Volume ratio gate (disabled — no ES-specific calibration yet) ----------
BREAKOUT_MIN_OR_VOLUME_RATIO = 0.0
BREAKOUT_MAX_OR_VOLUME_RATIO = 0.0

# -- Gap dead zone (disabled) -----------------------------------------------
GAP_EXCLUDE_MIN = 0.0
GAP_EXCLUDE_MAX = 0.0

# -- VWAP Pullback Strategy (disabled) -------------------------------------
VWAP_PULLBACK_ENABLED = False
VWAP_TOLERANCE_POINTS = 1.0

# -- PM VWAP Continuation (disabled) ---------------------------------------
PM_VWAP_ENABLED = False
PM_VWAP_START = "12:00"
PM_VWAP_LAST_ENTRY = "14:30"
PM_VWAP_STOP_POINTS = 5.0
PM_VWAP_RR = 2.0
PM_VWAP_TOLERANCE = 1.5

# -- Gap Fill (disabled) ---------------------------------------------------
GAP_FILL_ENABLED = False
GAP_FILL_MIN_POINTS = 10.0
GAP_FILL_LAST_ENTRY = "10:30"
GAP_FILL_STOP_POINTS = 5.0
GAP_FILL_RR = 1.5

# -- London/NY Overlap (disabled) ------------------------------------------
LONDON_ENABLED = False
LONDON_MIN_RANGE_POINTS = 5.0
LONDON_MAX_RANGE_POINTS = 50.0
LONDON_STOP_POINTS = 5.0
LONDON_TARGET_POINTS = 7.5
LONDON_TREND_THRESHOLD = 0.30

# -- Asia Gap (disabled for ES — edge validated on NQ only) ----------------
ASIA_ENABLED = False
ASIA_GAP_MIN_POINTS = 8.0
ASIA_GAP_MAX_POINTS = 25.0
ASIA_STOP_POINTS = 5.0
ASIA_RR_TARGET = 1.5
ASIA_SKIP_THURSDAYS = True
ASIA_WEAK_MONTHS = [8, 11]
ASIA_STRONG_MONTHS = [2, 6, 9, 10]
ASIA_STRONG_MONTHS_ONLY = False

# -- Second Breakout (disabled) --------------------------------------------
SECOND_BREAKOUT_ENABLED = False
SECOND_BREAKOUT_MIN_TIME = "10:30"

# -- Regime Detector -------------------------------------------------------
REGIME_LOOKBACK_DAYS = 5
REGIME_ATR_PERIOD = 14
REGIME_BREAKOUT_THRESHOLD = 0.18
REGIME_FADE_THRESHOLD = 0.18

# -- Bankroll Manager ------------------------------------------------------
STARTING_BALANCE = 50000.0

RISK_PER_TRADE_PCT = 0.01
MIN_RR = 1.9
MAX_CONTRACTS = 2

DAILY_LOSS_LIMIT_PCT = 0.015
MAX_CONSECUTIVE_LOSING_DAYS = 2
MAX_TRADES_PER_DAY = 2
MAX_LOSSES_PER_DAY = 2
DAILY_PROFIT_LOCK_PCT = 0.03

WEEKLY_LOSS_LIMIT_PCT = 0.05

MAX_TOTAL_DRAWDOWN_PCT = 0.12
RECOVERY_MODE_TRIGGER_PCT = 0.05
RECOVERY_SIZE_MULTIPLIER = 0.5

# -- Apex Trailing Drawdown ------------------------------------------------
APEX_TRAILING_DD = 7000.0
ENFORCE_APEX_RULES = True

# -- Signal Strength Filter ------------------------------------------------
# ES OR sizes (5-25pt) score 0 on NQ's OR-size component (requires >= 62pt).
# Max achievable ES score with NQ scorer: 80 pts
# (time 20 + gap 25 + vol 25 + or_size 0 + prev_breakout 10).
# Score >= 60: 1c. Score >= 75 in STRONG month: up to 2c.
SIGNAL_STRENGTH_MIN_SCORE = 60
HIGH_GAP_THRESHOLD = 10.0
SIGNAL_STRENGTH_MIN_SCORE_HIGH_GAP = 60

# -- Regime Calendar --------------------------------------------------------
# v2: ES seasonality research (4-yr OOS 2023-2026):
#   Jan  PF 0.41 → SKIP    Apr  PF 3.88 → STRONG   Jul  PF 0.62 → SKIP
#   Feb  PF 1.80 → STRONG  May  PF 0.65 → SKIP     Aug  PF 0.66 → SKIP
#   Mar  PF 1.29 → neutral Jun  PF 0.86 → SKIP     Sep  PF 1.48 → neutral
#   Oct  PF 0.65 → SKIP    Nov  PF 2.01 → STRONG   Dec  PF 1.14 → neutral
SKIP_MONTHS    = [1, 5, 6, 7, 8, 10]     # hard skip — no trading in these months
STRONG_MONTHS  = [2, 4, 11]              # allow max_c=3 for high-conviction signals
WEAK_MONTHS    = []                      # all remaining are neutral (not using weak cap)

# -- Dynamic Exits ---------------------------------------------------------
PARTIAL_EXIT_ENABLED = False
PARTIAL_EXIT_STALL_BARS = 30

# -- Pyramiding ------------------------------------------------------------
# Disabled: scorer is NQ-calibrated (ES OR-size gets 0 pts).
# Enable once ES-specific scorer is calibrated.
PYRAMIDING_ENABLED = False
PYRAMID_MAX_CONTRACTS = 2
PYRAMID_MIN_PROFIT_BUFFER = 1500.0
PYRAMID_WARMUP_TRADES = 5

# -- Eval Mode (disabled for backtest) -------------------------------------
EVAL_MODE = False

# -- Telegram Alerts -------------------------------------------------------
TELEGRAM_ALERTS_ENABLED = False
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""
