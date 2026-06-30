"""
NQ + ES Combined 3-Year Benchmark  (Jun 2023 → Jun 2026)
Both instruments run independently, share no capital.
Asia session enabled on both.

Run: python3 brain/research/combined_benchmark.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from datetime import date
from backtest import load_csv, Backtester
from walk_forward import _run_silent, _summary
import config

Backtester.run_silent = _run_silent
Backtester.summary    = _summary

# ── Window definitions ──────────────────────────────────────────────────────
WINDOWS = [
    ('H2-2023',  date(2023,6,29), date(2024,1,1)),
    ('2024',     date(2024,1,1),  date(2025,1,1)),
    ('2025',     date(2025,1,1),  date(2026,1,1)),
    ('H1-2026',  date(2026,1,1),  date(2026,6,30)),
]

# ── ES config overrides (all NQ params scaled by ~3.75×) ────────────────────
ES_OVERRIDES = {
    'SYMBOL':                    'ES',
    'POINT_VALUE':               50.0,
    'TICK_SIZE':                 0.25,
    'COMMISSION_PER_SIDE':       2.50,
    'SLIPPAGE_TICKS':            2,
    # OR range
    'ORB_MIN_RANGE_POINTS':      10.0,   # NQ 55 ÷ 3.75 ≈ 15 → use 10 (conservative)
    'ORB_MAX_RANGE_POINTS':      30.0,   # NQ 110 ÷ 3.75 ≈ 29
    # Stop
    'ORB_FIXED_STOP_POINTS':     7.0,    # NQ 22 ÷ 3.75 ≈ 6 → 7pt = $350/c
    'ORB_STOP_BUFFER_POINTS':    1.5,    # NQ 5 ÷ 3.75 ≈ 1.3
    'ORB_BREAKOUT_BUFFER_POINTS':1.0,    # NQ 4 ÷ 3.75 ≈ 1
    # Filters
    'GAP_FILTER_POINTS':         5.0,    # NQ 20 ÷ 3.75 ≈ 5
    'BREAKOUT_MIN_VOLUME':       500,    # ES much more liquid
    # Signal strength OR size bounds (scaled)
    'OR_SIZE_SCORE_BOUNDS':      (17.0, 25.0, 34.0),  # NQ (62,86,120) ÷ 3.75
    # Asia
    'ASIA_GAP_MIN_POINTS':       8.0,    # NQ 30 ÷ 3.75 ≈ 8
    'ASIA_GAP_MAX_POINTS':       22.0,   # NQ 80 ÷ 3.75 ≈ 21
    'ASIA_STOP_POINTS':          4.0,    # NQ 15 ÷ 3.75 = 4
    # Keep same RR targets, month filters, eval mode, pyramiding
}

# ── Helpers ─────────────────────────────────────────────────────────────────
def _set_config(overrides: dict):
    for k, v in overrides.items():
        setattr(config, k, v)

def _trade_stats(trades):
    if not trades:
        return 0, 0.0, 0.0, 0.0
    wins = [t for t in trades if t['pnl'] > 0]
    gw   = sum(t['pnl'] for t in wins)
    gl   = abs(sum(t['pnl'] for t in trades if t['pnl'] <= 0))
    pf   = gw / gl if gl else float('inf')
    wr   = len(wins) / len(trades)
    net  = sum(t['pnl'] for t in trades)
    return len(trades), wr, pf, net

def run_instrument(label, data_path, overrides=None):
    """Run 3-year walk-forward for one instrument. Returns per-period rows."""
    if overrides:
        _set_config(overrides)

    bars = load_csv(data_path)
    rows = []
    for period, start, end in WINDOWS:
        subset = [b for b in bars if start <= b['timestamp'].date() < end]
        bt = Backtester()
        bt.run_silent(subset)
        s  = bt.summary(period)

        log       = bt.bank.trade_log
        orb_trades  = [t for t in log if t['mode'] not in ('london','asia_gap')]
        asia_trades = [t for t in log if t['mode'] == 'asia_gap']

        rows.append({
            'period':  period,
            'total':   s,
            'orb':     _trade_stats(orb_trades),
            'asia':    _trade_stats(asia_trades),
            'log':     log,
        })
    return rows

# ── Print helpers ────────────────────────────────────────────────────────────
def _pf(v):
    return f'{v:.2f}' if v < 999 else ' inf'

def print_instrument_table(instrument, rows):
    tot_t = tot_net = tot_ow = tot_ot = tot_aw = tot_at = 0
    tot_on = tot_an = 0.0

    print(f'\n  {"PERIOD":10}  {"T":>4}  {"WR":>6}  {"PF":>5}  {"NET P&L":>10}  {"MAX DD":>8}')
    print('  ' + '─'*58)
    for r in rows:
        s   = r['total']
        t, wr, pf, net, mdd = s['trades'], s['win_rate'], s['pf'] or 0.0, s['net'], s.get('max_dd',0)
        tag = '✅' if pf >= 1.5 else ('⚠' if pf >= 1.1 else '❌')
        print(f'  {r["period"]:10}  {t:>4}  {wr:>5.1%}  {_pf(pf):>5}  ${net:>+9,.0f}  ${mdd:>7,.0f}  {tag}')
        tot_t += t; tot_net += net
        ot,ow,op,on = r['orb']; at,aw,ap,an = r['asia']
        tot_ot += ot; tot_ow += round(ow*ot) if ot else 0; tot_on += on
        tot_at += at; tot_aw += round(aw*at) if at else 0; tot_an += an

    print('  ' + '═'*58)
    print(f'  {"3-YR TOTAL":10}  {tot_t:>4}  {"":6}  {"":5}  ${tot_net:>+9,.0f}')
    print(f'  Avg/year: ${tot_net/3:+,.0f}   Avg trades/yr: {tot_t//3}')

    # per-strategy breakdown
    print()
    print(f'  {"PERIOD":10}  {"ORB T":>5}  {"WR":>6}  {"PF":>5}  {"NET":>10}  │  {"ASIA T":>6}  {"WR":>6}  {"PF":>5}  {"NET":>10}')
    print('  ' + '─'*78)
    tot_on2 = tot_an2 = 0.0
    for r in rows:
        ot,ow,op,on = r['orb']; at,aw,ap,an = r['asia']
        tot_on2 += on; tot_an2 += an
        ow_s = f'{ow:.1%}' if ot else '   —'
        aw_s = f'{aw:.1%}' if at else '   —'
        op_s = _pf(op)    if ot else '  — '
        ap_s = _pf(ap)    if at else '  — '
        print(f'  {r["period"]:10}  {ot:>5}  {ow_s:>6}  {op_s:>5}  ${on:>+9,.0f}  │  {at:>6}  {aw_s:>6}  {ap_s:>5}  ${an:>+9,.0f}')
    print('  ' + '─'*78)
    print(f'  {"TOTAL":10}  {tot_ot:>5}  {"":6}  {"":5}  ${tot_on2:>+9,.0f}  │  {tot_at:>6}  {"":6}  {"":5}  ${tot_an2:>+9,.0f}')

# ── Main ─────────────────────────────────────────────────────────────────────
print('\nLoading data ...')

# ── NQ ───────────────────────────────────────────────────────────────────────
NQ_DEFAULTS = {k: getattr(config, k) for k in ES_OVERRIDES}
NQ_DEFAULTS['SYMBOL'] = 'NQ'

print('Running NQ ...')
nq_rows = run_instrument('NQ', 'data/nq_full.csv', overrides=None)
_set_config(NQ_DEFAULTS)  # ensure NQ state restored

# ── ES ───────────────────────────────────────────────────────────────────────
print('Running ES ...')
es_rows = run_instrument('ES', 'data/es_1min.csv', overrides=ES_OVERRIDES)
_set_config(NQ_DEFAULTS)  # restore NQ config

# ═══════════════════════════════════════════════════════════════════════════
print()
print('═' * 72)
print('  NQ BENCHMARK  (ORB 9:30 + Asia 6pm  |  3R target  |  Databento CME)')
print('═' * 72)
print_instrument_table('NQ', nq_rows)

print()
print('═' * 72)
print('  ES BENCHMARK  (ORB 9:30 + Asia 6pm  |  3R target  |  initial calibration)')
print('═' * 72)
print_instrument_table('ES', es_rows)

# ── Combined ─────────────────────────────────────────────────────────────────
print()
print('═' * 72)
print('  COMBINED  NQ + ES  (independent accounts, same strategies)')
print('═' * 72)

# Merge per-period by summing trades and P&L
PERIODS = [r['period'] for r in nq_rows]
print(f'\n  {"PERIOD":10}  {"NQ NET":>10}  {"ES NET":>10}  {"COMBINED":>10}  {"COMBINED T":>10}')
print('  ' + '─'*60)
grand_nq = grand_es = grand_t = 0.0
for nq, es in zip(nq_rows, es_rows):
    nq_net = nq['total']['net'];  es_net = es['total']['net']
    nq_t   = nq['total']['trades']; es_t = es['total']['trades']
    combined = nq_net + es_net
    grand_nq += nq_net; grand_es += es_net; grand_t += nq_t + es_t
    print(f'  {nq["period"]:10}  ${nq_net:>+9,.0f}  ${es_net:>+9,.0f}  ${combined:>+9,.0f}  {nq_t+es_t:>10}')

print('  ' + '═'*60)
grand_combined = grand_nq + grand_es
print(f'  {"3-YR TOTAL":10}  ${grand_nq:>+9,.0f}  ${grand_es:>+9,.0f}  ${grand_combined:>+9,.0f}  {int(grand_t):>10}')
print(f'\n  Avg combined P&L/year : ${grand_combined/3:+,.0f}')
print(f'  Avg combined trades/yr: {int(grand_t)//3}')
print(f'\n  NOTE: ES params are initial calibration (scaled from NQ), not OOS-optimized.')
print(f'  NOTE: Databento API key expired — data through 2026-06-11.')
