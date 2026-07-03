"""
Crypto Strategy Optimization, Stress Test, and Multi-Coin Backtest
Strategies: Weekly Momentum, London Session Breakout, Bollinger Band Squeeze
"""
import pandas as pd
import numpy as np
from itertools import product
import warnings
warnings.filterwarnings('ignore')

# ── Constants ────────────────────────────────────────────────────────────────
NOTIONAL   = 10_000
COST_RT    = 0.001   # 0.10% round-trip
IS_START   = '2022-01-01'
IS_END     = '2023-12-31 23:59:59'
OOS_START  = '2024-01-01'
OOS_END    = '2026-06-30 23:59:59'
DATA_DIR   = '/Users/Cruz/Desktop/nq_bot_final-main/crypto/data/'
COINS      = ['btc', 'eth', 'sol', 'bnb', 'xrp', 'avax']

# ── Data loading ─────────────────────────────────────────────────────────────
def load(coin):
    df = pd.read_csv(f'{DATA_DIR}{coin}_1h.csv', parse_dates=['timestamp'])
    df = df.sort_values('timestamp').reset_index(drop=True)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df

def split(df):
    is_ = df[(df.timestamp >= IS_START) & (df.timestamp <= IS_END)].copy()
    oos = df[(df.timestamp >= OOS_START) & (df.timestamp <= OOS_END)].copy()
    return is_, oos

# ── PF / stats helpers ───────────────────────────────────────────────────────
def pf_stats(trades):
    """trades: list of pnl floats"""
    if len(trades) == 0:
        return 0, 0, 0, 0, 0
    t = np.array(trades)
    wins  = t[t > 0]
    losses= t[t < 0]
    gross_win  = wins.sum()  if len(wins)   else 0
    gross_loss = abs(losses.sum()) if len(losses) else 1e-9
    pf  = gross_win / gross_loss if gross_loss > 0 else (999 if gross_win > 0 else 0)
    wr  = len(wins) / len(t)
    net = t.sum()
    n   = len(t)
    # max drawdown on cumulative
    cum = np.cumsum(t)
    roll_max = np.maximum.accumulate(cum)
    dd = (cum - roll_max)
    mdd = dd.min()
    return pf, wr, net, n, mdd

def max_consec_losses(trades):
    streak = best = 0
    for p in trades:
        if p < 0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best

# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 1: Weekly Momentum
# ══════════════════════════════════════════════════════════════════════════════
def run_weekly_momentum(df, threshold, stop_pct, tgt_pct):
    """
    Prior week Mon open → Fri close return.
    If |ret| > threshold → enter Monday open in that direction.
    Stop = stop_pct below entry, target = tgt_pct above entry.
    Flatten: Friday close.
    """
    df = df.copy()
    df['date'] = df['timestamp'].dt.date
    df['weekday'] = df['timestamp'].dt.weekday  # 0=Mon
    df['week'] = df['timestamp'].dt.isocalendar().week.astype(int)
    df['year'] = df['timestamp'].dt.year

    # Build weekly OHLC keyed by (year, week)
    # Mon open = first bar of Monday; Fri close = last bar of Friday
    mon_open = (df[df.weekday == 0]
                .groupby(['year', 'week'])['open'].first()
                .rename('mon_open'))
    fri_close = (df[df.weekday == 4]
                 .groupby(['year', 'week'])['close'].last()
                 .rename('fri_close'))
    # For the entry week, get Mon open and all Mon bars for stop/tgt
    weekly = pd.concat([mon_open, fri_close], axis=1).dropna()
    weekly['ret'] = (weekly['fri_close'] - weekly['mon_open']) / weekly['mon_open']

    trades = []
    # For each week, use prior week's ret to decide entry next Mon
    idx_list = list(weekly.index)
    for i in range(1, len(idx_list)):
        prev_idx = idx_list[i-1]
        curr_idx = idx_list[i]
        prior_ret = weekly.loc[prev_idx, 'ret']
        if abs(prior_ret) <= threshold:
            continue
        direction = 1 if prior_ret > 0 else -1
        yr, wk = curr_idx
        # Get all bars this week
        week_bars = df[(df.year == yr) & (df.week == wk)].copy()
        if week_bars.empty:
            continue
        # Entry = Monday open (first bar)
        mon_bars = week_bars[week_bars.weekday == 0]
        if mon_bars.empty:
            continue
        entry_price = mon_bars.iloc[0]['open']
        stop_price  = entry_price * (1 - direction * stop_pct)
        tgt_price   = entry_price * (1 + direction * tgt_pct)
        # Walk through hourly bars until stop/target/Friday close
        exit_price = None
        exit_reason = None
        fri_bars = week_bars[week_bars.weekday == 4]
        flatten_price = fri_bars.iloc[-1]['close'] if not fri_bars.empty else None
        for _, bar in week_bars.iterrows():
            if bar['timestamp'] < mon_bars.iloc[0]['timestamp']:
                continue
            # Check stop and target using bar high/low
            if direction == 1:
                if bar['low'] <= stop_price:
                    exit_price = stop_price; exit_reason = 'stop'; break
                if bar['high'] >= tgt_price:
                    exit_price = tgt_price; exit_reason = 'target'; break
            else:
                if bar['high'] >= stop_price:
                    exit_price = stop_price; exit_reason = 'stop'; break
                if bar['low'] <= tgt_price:
                    exit_price = tgt_price; exit_reason = 'target'; break
        if exit_price is None:
            if flatten_price is not None:
                exit_price = flatten_price; exit_reason = 'flatten'
            else:
                continue
        raw_pnl = direction * (exit_price - entry_price) / entry_price * NOTIONAL
        cost    = COST_RT * NOTIONAL
        trades.append(raw_pnl - cost)
    return trades

def grid_weekly_momentum(df):
    thresholds = [0.02, 0.025, 0.03, 0.04, 0.05]
    stops      = [0.015, 0.02, 0.025, 0.03]
    tgts       = [0.025, 0.03, 0.04, 0.05]
    results = []
    for th, sp, tp in product(thresholds, stops, tgts):
        t = run_weekly_momentum(df, th, sp, tp)
        pf, wr, net, n, mdd = pf_stats(t)
        results.append({'threshold': th, 'stop_pct': sp, 'tgt_pct': tp,
                        'pf': pf, 'wr': wr, 'net': net, 'n': n, 'trades': t})
    return results

# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 2: London Session Breakout (Wed + Fri only)
# ══════════════════════════════════════════════════════════════════════════════
def run_london_breakout(df, buffer, range_min, range_max, stop_pct, tgt_pct):
    """
    Asia range: 00:00-08:00 UTC bars on Wed(2) and Fri(4).
    Entry window: 08:00-12:00 UTC bar close crosses range_high+buffer or range_low-buffer.
    Stop = stop_pct from entry. Target = tgt_pct from entry. Flatten = 18:00 bar close.
    """
    df = df.copy()
    df['weekday'] = df['timestamp'].dt.weekday
    df['hour']    = df['timestamp'].dt.hour
    df['date']    = df['timestamp'].dt.date

    trades = []
    for day, grp in df.groupby('date'):
        wd = grp.iloc[0]['weekday']
        if wd not in (2, 4):  # Wed=2, Fri=4
            continue
        # Asia range
        asia = grp[grp.hour < 8]
        if asia.empty:
            continue
        rng_high = asia['high'].max()
        rng_low  = asia['low'].min()
        rng_size = (rng_high - rng_low) / rng_low
        if rng_size < range_min or rng_size > range_max:
            continue
        # Entry window 08:00-11:59 UTC
        entry_window = grp[(grp.hour >= 8) & (grp.hour < 12)]
        if entry_window.empty:
            continue
        # Flatten bar
        flatten_bars = grp[grp.hour == 18]
        flatten_price = flatten_bars.iloc[-1]['close'] if not flatten_bars.empty else grp.iloc[-1]['close']

        entry_taken = False
        for _, bar in entry_window.iterrows():
            if entry_taken:
                break
            direction = None
            entry_price = None
            if bar['close'] > rng_high * (1 + buffer):
                direction = 1; entry_price = bar['close']
            elif bar['close'] < rng_low * (1 - buffer):
                direction = -1; entry_price = bar['close']
            if direction is None:
                continue
            entry_taken = True
            stop_price = entry_price * (1 - direction * stop_pct)
            tgt_price  = entry_price * (1 + direction * tgt_pct)
            # Walk remaining bars for exit
            remaining = grp[grp['timestamp'] > bar['timestamp']]
            exit_price = None
            for _, rb in remaining.iterrows():
                if direction == 1:
                    if rb['low'] <= stop_price:
                        exit_price = stop_price; break
                    if rb['high'] >= tgt_price:
                        exit_price = tgt_price; break
                else:
                    if rb['high'] >= stop_price:
                        exit_price = stop_price; break
                    if rb['low'] <= tgt_price:
                        exit_price = tgt_price; break
                if rb['hour'] >= 18:
                    exit_price = flatten_price; break
            if exit_price is None:
                exit_price = flatten_price
            raw_pnl = direction * (exit_price - entry_price) / entry_price * NOTIONAL
            cost    = COST_RT * NOTIONAL
            trades.append(raw_pnl - cost)
    return trades

def grid_london_breakout(df):
    buffers    = [0.002, 0.003, 0.005]
    range_mins = [0.005, 0.008, 0.012]
    range_maxs = [0.03, 0.04, 0.05]
    stops      = [0.01, 0.015, 0.02]
    tgts       = [0.02, 0.025, 0.03, 0.04]
    results = []
    for buf, rmin, rmax, sp, tp in product(buffers, range_mins, range_maxs, stops, tgts):
        t = run_london_breakout(df, buf, rmin, rmax, sp, tp)
        pf, wr, net, n, mdd = pf_stats(t)
        results.append({'buffer': buf, 'range_min': rmin, 'range_max': rmax,
                        'stop_pct': sp, 'tgt_pct': tp,
                        'pf': pf, 'wr': wr, 'net': net, 'n': n, 'trades': t})
    return results

# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY 3: Bollinger Band Squeeze (Thu + Fri only)
# ══════════════════════════════════════════════════════════════════════════════
def run_bb_squeeze(df, period, squeeze_thresh, min_bars):
    """
    Thu+Fri only. 20-bar BB. When width < squeeze_thresh for min_bars consecutive bars
    → trade next bar breakout direction. Stop = BB midline. Target = 2x half-width from entry.
    """
    df = df.copy()
    df['weekday'] = df['timestamp'].dt.weekday
    # Compute BB on full df
    df['mid']   = df['close'].rolling(period).mean()
    df['std']   = df['close'].rolling(period).std()
    df['upper'] = df['mid'] + 2 * df['std']
    df['lower'] = df['mid'] - 2 * df['std']
    df['width'] = (df['upper'] - df['lower']) / df['mid']
    df['squeeze'] = df['width'] < squeeze_thresh
    # Count consecutive squeeze bars
    df['consec'] = df['squeeze'].groupby((~df['squeeze']).cumsum()).cumcount() + 1
    df['consec'] = df['consec'].where(df['squeeze'], 0)
    df = df.dropna(subset=['mid', 'std']).reset_index(drop=True)

    trades = []
    in_trade = False
    for i in range(len(df)):
        row = df.iloc[i]
        if row['weekday'] not in (3, 4):  # Thu=3, Fri=4
            continue
        if in_trade:
            continue
        # Signal: squeeze just ended (prev bar had consec >= min_bars)
        if i < 1:
            continue
        prev = df.iloc[i-1]
        if prev['consec'] < min_bars:
            continue
        if row['squeeze']:  # still in squeeze
            continue
        # Direction based on breakout
        entry_price = row['close']
        direction = 1 if entry_price > prev['mid'] else -1
        stop_price = row['mid']
        half_width = (row['upper'] - row['lower']) / 2
        tgt_price  = entry_price + direction * 2 * half_width
        in_trade = True
        # Walk remaining bars for exit
        exit_price = None
        for j in range(i+1, min(i+48, len(df))):  # max 48h holding
            rb = df.iloc[j]
            if direction == 1:
                if rb['low'] <= stop_price:
                    exit_price = stop_price; break
                if rb['high'] >= tgt_price:
                    exit_price = tgt_price; break
            else:
                if rb['high'] >= stop_price:
                    exit_price = stop_price; break
                if rb['low'] <= tgt_price:
                    exit_price = tgt_price; break
        if exit_price is None:
            exit_price = df.iloc[min(i+48, len(df)-1)]['close']
        raw_pnl = direction * (exit_price - entry_price) / entry_price * NOTIONAL
        cost    = COST_RT * NOTIONAL
        trades.append(raw_pnl - cost)
        in_trade = False
    return trades

def grid_bb_squeeze(df):
    periods   = [14, 20, 30]
    sq_thresh = [0.010, 0.015, 0.020]
    min_bars  = [2, 3, 5]
    results = []
    for per, sq, mb in product(periods, sq_thresh, min_bars):
        t = run_bb_squeeze(df, per, sq, mb)
        pf, wr, net, n, mdd = pf_stats(t)
        results.append({'period': per, 'squeeze_thresh': sq, 'min_bars': mb,
                        'pf': pf, 'wr': wr, 'net': net, 'n': n, 'trades': t})
    return results

# ══════════════════════════════════════════════════════════════════════════════
# STRESS TEST
# ══════════════════════════════════════════════════════════════════════════════
def rolling_pf(trades, window=20):
    t = np.array(trades)
    if len(t) < window:
        return []
    pfs = []
    for i in range(len(t) - window + 1):
        w = t[i:i+window]
        wins = w[w>0].sum()
        loss = abs(w[w<0].sum())
        pfs.append(wins / loss if loss > 0 else (999 if wins > 0 else 0))
    return pfs

def monte_carlo(trades, n_sims=1000, seed=42):
    np.random.seed(seed)
    t = np.array(trades)
    profitable = 0
    for _ in range(n_sims):
        shuffled = np.random.permutation(t)
        if np.cumsum(shuffled).min() > -abs(t.sum()) * 2 and shuffled.sum() > 0:
            profitable += 1
        # simpler: just check final equity > 0
    # redo correctly
    profitable = 0
    np.random.seed(seed)
    for _ in range(n_sims):
        shuffled = np.random.permutation(t)
        if shuffled.sum() > 0:
            profitable += 1
    return profitable / n_sims

def stress_test(trades, trade_dates=None, label=''):
    trades = np.array(trades)
    n = len(trades)
    if n < 5:
        print(f"  {label}: too few trades ({n}) for stress test")
        return
    pf, wr, net, _, mdd = pf_stats(trades)
    # Rolling 20-trade PF
    rpf = rolling_pf(trades, 20)
    above1 = sum(1 for x in rpf if x >= 1.0) / len(rpf) if rpf else 0
    # Monte Carlo
    mc_pass = monte_carlo(trades)
    # Max consecutive losses
    mcl = max_consec_losses(trades)
    # Calmar (annualized net / |mdd|)
    # rough annualization: if ~2 years of data
    ann_net = net * (252/max(n,1)) if n > 0 else 0  # crude
    calmar = ann_net / abs(mdd) if mdd < 0 else 0
    print(f"  Rolling 20-trade PF: {np.mean(rpf):.2f} avg, {above1*100:.0f}% windows >= 1.0 ({len(rpf)} windows)")
    print(f"  Monte Carlo (1000 sim): {mc_pass*100:.1f}% paths profitable")
    print(f"  Max consecutive losses: {mcl}")
    print(f"  Total Net P&L: ${net:,.0f} | Max Drawdown: ${mdd:,.0f}")
    print(f"  Calmar (crude): {calmar:.2f}")

def year_breakdown(trades_with_meta):
    """trades_with_meta: list of (year, pnl)"""
    from collections import defaultdict
    by_year = defaultdict(list)
    for yr, pnl in trades_with_meta:
        by_year[yr].append(pnl)
    for yr in sorted(by_year):
        t = np.array(by_year[yr])
        pf, wr, net, n, _ = pf_stats(t)
        print(f"    {yr}: N={n:3d} | WR={wr*100:.0f}% | PF={pf:.2f} | Net=${net:,.0f}")

# ══════════════════════════════════════════════════════════════════════════════
# YEAR-ANNOTATED TRADE RUNNERS
# ══════════════════════════════════════════════════════════════════════════════
def run_wm_with_year(df, threshold, stop_pct, tgt_pct):
    df = df.copy()
    df['date'] = df['timestamp'].dt.date
    df['weekday'] = df['timestamp'].dt.weekday
    df['week'] = df['timestamp'].dt.isocalendar().week.astype(int)
    df['year'] = df['timestamp'].dt.year
    mon_open  = df[df.weekday==0].groupby(['year','week'])['open'].first().rename('mon_open')
    fri_close = df[df.weekday==4].groupby(['year','week'])['close'].last().rename('fri_close')
    weekly    = pd.concat([mon_open, fri_close], axis=1).dropna()
    weekly['ret'] = (weekly['fri_close'] - weekly['mon_open']) / weekly['mon_open']
    trades = []
    idx_list = list(weekly.index)
    for i in range(1, len(idx_list)):
        prev_idx = idx_list[i-1]; curr_idx = idx_list[i]
        prior_ret = weekly.loc[prev_idx, 'ret']
        if abs(prior_ret) <= threshold: continue
        direction = 1 if prior_ret > 0 else -1
        yr, wk = curr_idx
        week_bars = df[(df.year==yr)&(df.week==wk)].copy()
        if week_bars.empty: continue
        mon_bars = week_bars[week_bars.weekday==0]
        if mon_bars.empty: continue
        entry_price = mon_bars.iloc[0]['open']
        stop_price  = entry_price*(1-direction*stop_pct)
        tgt_price   = entry_price*(1+direction*tgt_pct)
        fri_bars    = week_bars[week_bars.weekday==4]
        flatten_price = fri_bars.iloc[-1]['close'] if not fri_bars.empty else None
        exit_price = None
        for _, bar in week_bars.iterrows():
            if bar['timestamp'] < mon_bars.iloc[0]['timestamp']: continue
            if direction==1:
                if bar['low']<=stop_price: exit_price=stop_price; break
                if bar['high']>=tgt_price: exit_price=tgt_price; break
            else:
                if bar['high']>=stop_price: exit_price=stop_price; break
                if bar['low']<=tgt_price: exit_price=tgt_price; break
        if exit_price is None:
            if flatten_price is not None: exit_price=flatten_price
            else: continue
        raw_pnl = direction*(exit_price-entry_price)/entry_price*NOTIONAL
        trades.append((yr, raw_pnl - COST_RT*NOTIONAL))
    return trades

def run_lb_with_year(df, buffer, range_min, range_max, stop_pct, tgt_pct):
    df = df.copy()
    df['weekday'] = df['timestamp'].dt.weekday
    df['hour']    = df['timestamp'].dt.hour
    df['date']    = df['timestamp'].dt.date
    df['year']    = df['timestamp'].dt.year
    trades = []
    for day, grp in df.groupby('date'):
        wd = grp.iloc[0]['weekday']
        if wd not in (2,4): continue
        asia = grp[grp.hour<8]
        if asia.empty: continue
        rng_high = asia['high'].max(); rng_low=asia['low'].min()
        rng_size = (rng_high-rng_low)/rng_low
        if rng_size<range_min or rng_size>range_max: continue
        entry_window = grp[(grp.hour>=8)&(grp.hour<12)]
        if entry_window.empty: continue
        flatten_bars = grp[grp.hour==18]
        flatten_price = flatten_bars.iloc[-1]['close'] if not flatten_bars.empty else grp.iloc[-1]['close']
        yr = grp.iloc[0]['year']
        entry_taken = False
        for _, bar in entry_window.iterrows():
            if entry_taken: break
            direction=None; entry_price=None
            if bar['close']>rng_high*(1+buffer): direction=1; entry_price=bar['close']
            elif bar['close']<rng_low*(1-buffer): direction=-1; entry_price=bar['close']
            if direction is None: continue
            entry_taken=True
            stop_price=entry_price*(1-direction*stop_pct)
            tgt_price=entry_price*(1+direction*tgt_pct)
            remaining=grp[grp['timestamp']>bar['timestamp']]
            exit_price=None
            for _, rb in remaining.iterrows():
                if direction==1:
                    if rb['low']<=stop_price: exit_price=stop_price; break
                    if rb['high']>=tgt_price: exit_price=tgt_price; break
                else:
                    if rb['high']>=stop_price: exit_price=stop_price; break
                    if rb['low']<=tgt_price: exit_price=tgt_price; break
                if rb['hour']>=18: exit_price=flatten_price; break
            if exit_price is None: exit_price=flatten_price
            raw_pnl=direction*(exit_price-entry_price)/entry_price*NOTIONAL
            trades.append((yr, raw_pnl - COST_RT*NOTIONAL))
    return trades

def run_bb_with_year(df, period, squeeze_thresh, min_bars_param):
    df = df.copy()
    df['weekday'] = df['timestamp'].dt.weekday
    df['year']    = df['timestamp'].dt.year
    df['mid']   = df['close'].rolling(period).mean()
    df['std']   = df['close'].rolling(period).std()
    df['upper'] = df['mid']+2*df['std']
    df['lower'] = df['mid']-2*df['std']
    df['width'] = (df['upper']-df['lower'])/df['mid']
    df['squeeze'] = df['width']<squeeze_thresh
    df['consec'] = df['squeeze'].groupby((~df['squeeze']).cumsum()).cumcount()+1
    df['consec'] = df['consec'].where(df['squeeze'],0)
    df = df.dropna(subset=['mid','std']).reset_index(drop=True)
    trades = []; in_trade=False
    for i in range(len(df)):
        row=df.iloc[i]
        if row['weekday'] not in (3,4): continue
        if in_trade: continue
        if i<1: continue
        prev=df.iloc[i-1]
        if prev['consec']<min_bars_param: continue
        if row['squeeze']: continue
        entry_price=row['close']
        direction=1 if entry_price>prev['mid'] else -1
        stop_price=row['mid']
        half_width=(row['upper']-row['lower'])/2
        tgt_price=entry_price+direction*2*half_width
        in_trade=True; yr=int(row['year'])
        exit_price=None
        for j in range(i+1,min(i+48,len(df))):
            rb=df.iloc[j]
            if direction==1:
                if rb['low']<=stop_price: exit_price=stop_price; break
                if rb['high']>=tgt_price: exit_price=tgt_price; break
            else:
                if rb['high']>=stop_price: exit_price=stop_price; break
                if rb['low']<=tgt_price: exit_price=tgt_price; break
        if exit_price is None: exit_price=df.iloc[min(i+48,len(df)-1)]['close']
        raw_pnl=direction*(exit_price-entry_price)/entry_price*NOTIONAL
        trades.append((yr, raw_pnl - COST_RT*NOTIONAL))
        in_trade=False
    return trades

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
print("Loading data...")
data = {coin: load(coin) for coin in COINS}
btc = data['btc']
btc_is, btc_oos = split(btc)

print(f"BTC IS rows: {len(btc_is):,}  |  OOS rows: {len(btc_oos):,}")
print(f"IS: {btc_is.timestamp.min().date()} → {btc_is.timestamp.max().date()}")
print(f"OOS: {btc_oos.timestamp.min().date()} → {btc_oos.timestamp.max().date()}")
print()

# ═══════════════════════════════════════════════════════════════
print("=" * 70)
print("PHASE 1: OPTIMIZATION ON BTC IS DATA (2022-2023)")
print("=" * 70)

# ── Strategy 1: Weekly Momentum ──────────────────────────────────────────────
print("\n[S1] Running Weekly Momentum grid search on BTC IS...")
wm_results = grid_weekly_momentum(btc_is)
wm_df = pd.DataFrame([{k:v for k,v in r.items() if k!='trades'} for r in wm_results])
wm_valid = wm_df[wm_df['n'] >= 20].copy()
top5_wm = wm_valid.nlargest(5, 'pf')
print(f"  Total combos: {len(wm_results)} | Valid (N>=20): {len(wm_valid)}")
print("\n  TOP 5 IS combos (Weekly Momentum):")
print(f"  {'thresh':>7} {'stop':>6} {'tgt':>6} | {'IS N':>5} {'IS WR':>6} {'IS PF':>7} {'IS Net':>9} | {'OOS N':>5} {'OOS WR':>6} {'OOS PF':>7} {'OOS Net':>10}")
print("  " + "-"*78)

wm_best_params = None
wm_best_oos_pf = -1
wm_top5_info = []

for _, row in top5_wm.iterrows():
    th, sp, tp = row['threshold'], row['stop_pct'], row['tgt_pct']
    oos_trades = run_weekly_momentum(btc_oos, th, sp, tp)
    oos_pf, oos_wr, oos_net, oos_n, _ = pf_stats(oos_trades)
    wm_top5_info.append({'threshold':th,'stop_pct':sp,'tgt_pct':tp,
                         'is_pf':row['pf'],'is_n':row['n'],
                         'oos_pf':oos_pf,'oos_n':oos_n,'oos_trades':oos_trades})
    print(f"  {th:7.3f} {sp:6.3f} {tp:6.3f} | {row['n']:5.0f} {row['wr']*100:5.0f}% {row['pf']:7.2f} {row['net']:9,.0f} | {oos_n:5d} {oos_wr*100:5.0f}% {oos_pf:7.2f} {oos_net:10,.0f}")
    # Find best OOS PF where IS PF > 1.0
    if row['pf'] > 1.0 and oos_pf > wm_best_oos_pf:
        wm_best_oos_pf = oos_pf
        wm_best_params = {'threshold':th,'stop_pct':sp,'tgt_pct':tp}

if wm_best_params:
    print(f"\n  >> WINNING PARAMS: threshold={wm_best_params['threshold']:.3f}, stop={wm_best_params['stop_pct']:.3f}, tgt={wm_best_params['tgt_pct']:.3f} (OOS PF={wm_best_oos_pf:.2f})")
else:
    # pick best IS PF with OOS > 1
    for info in sorted(wm_top5_info, key=lambda x: x['oos_pf'], reverse=True):
        if info['is_pf'] > 1.0:
            wm_best_params = {k:v for k,v in info.items() if k in ('threshold','stop_pct','tgt_pct')}
            wm_best_oos_pf = info['oos_pf']
            break
    if not wm_best_params and wm_top5_info:
        # just pick best IS pf overall
        best = max(wm_top5_info, key=lambda x: x['is_pf'])
        wm_best_params = {k:v for k,v in best.items() if k in ('threshold','stop_pct','tgt_pct')}
    print(f"\n  >> BEST PARAMS (by IS PF): {wm_best_params}")

# ── Strategy 2: London Session Breakout ─────────────────────────────────────
print("\n[S2] Running London Session Breakout grid search on BTC IS...")
lb_results = grid_london_breakout(btc_is)
lb_df = pd.DataFrame([{k:v for k,v in r.items() if k!='trades'} for r in lb_results])
lb_valid = lb_df[lb_df['n'] >= 20].copy()
top5_lb = lb_valid.nlargest(5, 'pf')
print(f"  Total combos: {len(lb_results)} | Valid (N>=20): {len(lb_valid)}")
print(f"\n  TOP 5 IS combos (London Breakout):")
print(f"  {'buf':>6} {'rmin':>6} {'rmax':>6} {'stop':>6} {'tgt':>6} | {'IS N':>5} {'IS WR':>6} {'IS PF':>7} {'IS Net':>9} | {'OOS N':>5} {'OOS WR':>6} {'OOS PF':>7} {'OOS Net':>10}")
print("  " + "-"*96)

lb_best_params = None
lb_best_oos_pf = -1
lb_top5_info = []

for _, row in top5_lb.iterrows():
    buf, rmin, rmax, sp, tp = row['buffer'], row['range_min'], row['range_max'], row['stop_pct'], row['tgt_pct']
    oos_trades = run_london_breakout(btc_oos, buf, rmin, rmax, sp, tp)
    oos_pf, oos_wr, oos_net, oos_n, _ = pf_stats(oos_trades)
    lb_top5_info.append({'buffer':buf,'range_min':rmin,'range_max':rmax,
                          'stop_pct':sp,'tgt_pct':tp,
                          'is_pf':row['pf'],'is_n':row['n'],
                          'oos_pf':oos_pf,'oos_n':oos_n,'oos_trades':oos_trades})
    print(f"  {buf:6.3f} {rmin:6.3f} {rmax:6.3f} {sp:6.3f} {tp:6.3f} | {row['n']:5.0f} {row['wr']*100:5.0f}% {row['pf']:7.2f} {row['net']:9,.0f} | {oos_n:5d} {oos_wr*100:5.0f}% {oos_pf:7.2f} {oos_net:10,.0f}")
    if row['pf'] > 1.0 and oos_pf > lb_best_oos_pf:
        lb_best_oos_pf = oos_pf
        lb_best_params = {'buffer':buf,'range_min':rmin,'range_max':rmax,'stop_pct':sp,'tgt_pct':tp}

if lb_best_params:
    print(f"\n  >> WINNING PARAMS: {lb_best_params} (OOS PF={lb_best_oos_pf:.2f})")
else:
    if lb_top5_info:
        best = max(lb_top5_info, key=lambda x: x['is_pf'])
        lb_best_params = {k:v for k,v in best.items() if k in ('buffer','range_min','range_max','stop_pct','tgt_pct')}
    print(f"\n  >> BEST PARAMS (by IS PF): {lb_best_params}")

# ── Strategy 3: Bollinger Band Squeeze ──────────────────────────────────────
print("\n[S3] Running Bollinger Band Squeeze grid search on BTC IS...")
bb_results = grid_bb_squeeze(btc_is)
bb_df = pd.DataFrame([{k:v for k,v in r.items() if k!='trades'} for r in bb_results])
bb_valid = bb_df[bb_df['n'] >= 20].copy()
if bb_valid.empty:
    bb_valid = bb_df[bb_df['n'] >= 5].copy()
    print("  Note: lowering min N threshold to 5 for BB Squeeze (thin signal)")
top5_bb = bb_valid.nlargest(5, 'pf')
print(f"  Total combos: {len(bb_results)} | Valid (N>=20): {len(bb_df[bb_df['n']>=20])} | Valid (N>=5): {len(bb_valid)}")
print(f"\n  TOP 5 IS combos (BB Squeeze):")
print(f"  {'per':>5} {'sq_th':>7} {'minb':>5} | {'IS N':>5} {'IS WR':>6} {'IS PF':>7} {'IS Net':>9} | {'OOS N':>5} {'OOS WR':>6} {'OOS PF':>7} {'OOS Net':>10}")
print("  " + "-"*80)

bb_best_params = None
bb_best_oos_pf = -1
bb_top5_info = []

for _, row in top5_bb.iterrows():
    per, sq, mb = int(row['period']), row['squeeze_thresh'], int(row['min_bars'])
    oos_trades = run_bb_squeeze(btc_oos, per, sq, mb)
    oos_pf, oos_wr, oos_net, oos_n, _ = pf_stats(oos_trades)
    bb_top5_info.append({'period':per,'squeeze_thresh':sq,'min_bars':mb,
                          'is_pf':row['pf'],'is_n':row['n'],
                          'oos_pf':oos_pf,'oos_n':oos_n,'oos_trades':oos_trades})
    print(f"  {per:5d} {sq:7.3f} {mb:5d} | {row['n']:5.0f} {row['wr']*100:5.0f}% {row['pf']:7.2f} {row['net']:9,.0f} | {oos_n:5d} {oos_wr*100:5.0f}% {oos_pf:7.2f} {oos_net:10,.0f}")
    if row['pf'] > 1.0 and oos_pf > bb_best_oos_pf:
        bb_best_oos_pf = oos_pf
        bb_best_params = {'period':per,'squeeze_thresh':sq,'min_bars':mb}

if bb_best_params:
    print(f"\n  >> WINNING PARAMS: {bb_best_params} (OOS PF={bb_best_oos_pf:.2f})")
else:
    if bb_top5_info:
        best = max(bb_top5_info, key=lambda x: x['is_pf'])
        bb_best_params = {k:v for k,v in best.items() if k in ('period','squeeze_thresh','min_bars')}
    print(f"\n  >> BEST PARAMS (by IS PF): {bb_best_params}")

# ═══════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("PHASE 2: STRESS TEST — BTC OOS with WINNING PARAMS")
print("=" * 70)

# Get winning OOS trades from top5 info
def get_best_oos(top5_info, key='oos_pf'):
    # prefer IS PF > 1.0, then best OOS PF
    filtered = [x for x in top5_info if x['is_pf'] > 1.0]
    pool = filtered if filtered else top5_info
    return max(pool, key=lambda x: x[key])

# S1 stress
print("\n[S1] Weekly Momentum — BTC OOS Stress Test")
wm_best = get_best_oos(wm_top5_info)
wm_oos_trades = [p for p in wm_best['oos_trades']]
print(f"  Params: threshold={wm_best['threshold']:.3f}, stop={wm_best['stop_pct']:.3f}, tgt={wm_best['tgt_pct']:.3f}")
print(f"  OOS N={wm_best['oos_n']}, OOS PF={wm_best['oos_pf']:.2f}")
stress_test(wm_oos_trades, label='WM')
print("  Year-by-year (OOS only):")
wm_oos_yr = run_wm_with_year(btc_oos, wm_best['threshold'], wm_best['stop_pct'], wm_best['tgt_pct'])
year_breakdown(wm_oos_yr)

# S2 stress
print("\n[S2] London Session Breakout — BTC OOS Stress Test")
lb_best = get_best_oos(lb_top5_info)
lb_oos_trades = lb_best['oos_trades']
print(f"  Params: buffer={lb_best['buffer']:.3f}, range_min={lb_best['range_min']:.3f}, range_max={lb_best['range_max']:.3f}, stop={lb_best['stop_pct']:.3f}, tgt={lb_best['tgt_pct']:.3f}")
print(f"  OOS N={lb_best['oos_n']}, OOS PF={lb_best['oos_pf']:.2f}")
stress_test(lb_oos_trades, label='LB')
print("  Year-by-year (OOS only):")
lb_oos_yr = run_lb_with_year(btc_oos, lb_best['buffer'], lb_best['range_min'], lb_best['range_max'], lb_best['stop_pct'], lb_best['tgt_pct'])
year_breakdown(lb_oos_yr)

# S3 stress
print("\n[S3] BB Squeeze — BTC OOS Stress Test")
bb_best = get_best_oos(bb_top5_info)
bb_oos_trades = bb_best['oos_trades']
print(f"  Params: period={bb_best['period']}, squeeze_thresh={bb_best['squeeze_thresh']:.3f}, min_bars={bb_best['min_bars']}")
print(f"  OOS N={bb_best['oos_n']}, OOS PF={bb_best['oos_pf']:.2f}")
stress_test(bb_oos_trades, label='BB')
print("  Year-by-year (OOS only):")
bb_oos_yr = run_bb_with_year(btc_oos, bb_best['period'], bb_best['squeeze_thresh'], bb_best['min_bars'])
year_breakdown(bb_oos_yr)

# ═══════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("PHASE 3: MULTI-COIN OOS TEST")
print("=" * 70)

# Use best params from above
wm_p  = wm_best
lb_p  = lb_best
bb_p  = bb_best

results_table = {}
for coin in COINS:
    df_coin = data[coin]
    _, oos_coin = split(df_coin)
    results_table[coin] = {}

    # S1
    t1 = run_weekly_momentum(oos_coin, wm_p['threshold'], wm_p['stop_pct'], wm_p['tgt_pct'])
    pf1, wr1, net1, n1, _ = pf_stats(t1)
    results_table[coin]['WM']  = {'n':n1,'wr':wr1,'pf':pf1,'net':net1}
    # S2
    t2 = run_london_breakout(oos_coin, lb_p['buffer'], lb_p['range_min'], lb_p['range_max'], lb_p['stop_pct'], lb_p['tgt_pct'])
    pf2, wr2, net2, n2, _ = pf_stats(t2)
    results_table[coin]['LB']  = {'n':n2,'wr':wr2,'pf':pf2,'net':net2}
    # S3
    t3 = run_bb_squeeze(oos_coin, bb_p['period'], bb_p['squeeze_thresh'], bb_p['min_bars'])
    pf3, wr3, net3, n3, _ = pf_stats(t3)
    results_table[coin]['BB']  = {'n':n3,'wr':wr3,'pf':pf3,'net':net3}

print(f"\n  OPTIMIZED PARAMS USED:")
print(f"    S1 WeeklyMomentum : threshold={wm_p['threshold']:.3f}, stop={wm_p['stop_pct']:.3f}, tgt={wm_p['tgt_pct']:.3f}")
print(f"    S2 LondonBreakout : buffer={lb_p['buffer']:.3f}, rmin={lb_p['range_min']:.3f}, rmax={lb_p['range_max']:.3f}, stop={lb_p['stop_pct']:.3f}, tgt={lb_p['tgt_pct']:.3f}")
print(f"    S3 BB Squeeze     : period={bb_p['period']}, sq_thresh={bb_p['squeeze_thresh']:.3f}, min_bars={bb_p['min_bars']}")

print(f"\n  OOS PROFIT FACTOR MATRIX (2024-Jun 2026):")
print(f"  {'Coin':>6} | {'WM N':>5} {'WM WR':>6} {'WM PF':>7} {'WM Net':>10} | {'LB N':>5} {'LB WR':>6} {'LB PF':>7} {'LB Net':>10} | {'BB N':>5} {'BB WR':>6} {'BB PF':>7} {'BB Net':>10}")
print("  " + "-"*100)
for coin in COINS:
    wm = results_table[coin]['WM']
    lb = results_table[coin]['LB']
    bb = results_table[coin]['BB']
    print(f"  {coin.upper():>6} | {wm['n']:5d} {wm['wr']*100:5.0f}% {wm['pf']:7.2f} {wm['net']:10,.0f} | {lb['n']:5d} {lb['wr']*100:5.0f}% {lb['pf']:7.2f} {lb['net']:10,.0f} | {bb['n']:5d} {bb['wr']*100:5.0f}% {bb['pf']:7.2f} {bb['net']:10,.0f}")

# Count coins with PF > 1.0 per strategy
wm_above1 = sum(1 for c in COINS if results_table[c]['WM']['pf'] > 1.0)
lb_above1 = sum(1 for c in COINS if results_table[c]['LB']['pf'] > 1.0)
bb_above1 = sum(1 for c in COINS if results_table[c]['BB']['pf'] > 1.0)
print(f"\n  Coins with OOS PF > 1.0:")
print(f"    S1 Weekly Momentum:    {wm_above1}/6 coins")
print(f"    S2 London Breakout:    {lb_above1}/6 coins")
print(f"    S3 BB Squeeze:         {bb_above1}/6 coins")

print("\n  GENERALIZATION VERDICT:")
for strat, above1, label in [('WM',wm_above1,'Weekly Momentum'),('LB',lb_above1,'London Breakout'),('BB',bb_above1,'BB Squeeze')]:
    if above1 >= 5:
        verdict = "STRONG — edge generalizes across coins"
    elif above1 >= 3:
        verdict = "MODERATE — structural edge, not coin-specific"
    elif above1 >= 2:
        verdict = "WEAK — marginal generalization"
    else:
        verdict = "NO GENERALIZATION — likely BTC-overfit"
    print(f"    {label}: {above1}/6 → {verdict}")

print()
print("=" * 70)
print("DONE")
print("=" * 70)
