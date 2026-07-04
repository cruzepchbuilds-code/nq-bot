"""
brain/research/crypto_structures.py

Crypto session-structure sweep beyond London  (MISSION A)  +  Databento pilot
pricing via free metadata.get_cost (MISSION B).

HOUSE LAW
  IS  = 2022-01-01 .. 2024-12-31
  OOS = 2025-01-01 .. 2026-07-01
  Select on BTC+ETH IS (pooled, both coins individually coherent, N>=80/coin),
  confirm on BTC+ETH OOS, generalize on SOL/BNB/XRP/AVAX OOS (need >=3 of 4
  with PF >= 1.2). Cost model stated below. Kill-your-own-finding: fade modes
  and grid-median honesty stats are reported alongside the grid best.

DATA
  crypto/data/{coin}_1h.csv — Binance.US spot, 1-HOUR bars (NOT 1-min; the
  1-min file referenced by crypto/fetch_data.py was never kept). Timestamps
  are UTC bar-OPEN times (verified: fetch_data.py writes tz=timezone.utc and
  the median-volume peak sits at 14-18 UTC = US morning). ET/JST are whole-
  hour offsets from UTC so hourly bars align exactly with session anchors.
  XRP starts 2023-07-14 (first day dropped — listing artifact bar).

COSTS (stated number)
  3.0 bps taker per side = 6.0 bps round-trip on notional (liquid-venue
  BTC/ETH taker+spread). Survivors are stress-checked at 4 bps/side (8 bps RT).
  NOTE: Binance.US late-sample volume is thin; prices arb-track global venues
  so structure inference stands, but live execution assumes a liquid venue.

STRUCTURES
  S1  US equity-open spillover: direction of the 8:00 or 9:00 ET hour bar
      (contains the 8:30 print / 9:30 open), continue or fade, hold 2-6 h.
  S2  Asia-open (9:00 JST = 00:00 UTC) breakout of the prior K-hour range,
      first close-through within W hours, hold 4-8 h.
  S3  UTC 00:00 boundary: momentum vs reversal of the prior P-hour move,
      hold 1-2 h (30-120 min per spec); plus unconditional drift both sides.
  S4  Vol-regime overlay on the IS-chosen config of S1/S2/S3: 7-day realized
      vol percentile (90-day trailing rank), band chosen on IS, held OOS.
  S5  ETH/BTC ratio N-day range break, trade ETH in break direction, hold
      24-72 h. Generalization: same rule on SOL/BNB/XRP/AVAX vs BTC.

RUN (from repo root)
  python3 brain/research/crypto_structures.py            # everything
  python3 brain/research/crypto_structures.py --skip-pilots
  python3 brain/research/crypto_structures.py --only s1  # s1..s5 | pilots

Mission B uses ONLY metadata.get_cost (free). It never calls
timeseries.get_range and never downloads data.
"""

import os
import sys
import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

BASE      = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR  = os.path.join(BASE, "crypto", "data")

NOTIONAL   = 10_000.0
COST_SIDE  = 0.0003          # 3 bps per side  -> 6 bps round trip (stated)
COST_SIDE_STRESS = 0.0004    # 4 bps per side  -> 8 bps round trip (stress)

IS_START,  IS_END  = pd.Timestamp("2022-01-01", tz="UTC"), pd.Timestamp("2024-12-31 23:59:59", tz="UTC")
OOS_START, OOS_END = pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2026-12-31 23:59:59", tz="UTC")

SELECT_COINS  = ["btc", "eth"]
CONFIRM_COINS = ["sol", "bnb", "xrp", "avax"]
ALL_COINS     = SELECT_COINS + CONFIRM_COINS

MIN_N_IS   = 80      # per select coin, IS
PF_BAR     = 1.20    # survivor bar (IS select, OOS confirm, cross-coin)

ET  = ZoneInfo("America/New_York")
JST = ZoneInfo("Asia/Tokyo")

# ────────────────────────────────────────────────────────────────────────────
# Data
# ────────────────────────────────────────────────────────────────────────────
_cache = {}

def load(coin):
    """Hourly UTC frame with numpy arrays + session hour columns."""
    if coin in _cache:
        return _cache[coin]
    df = pd.read_csv(os.path.join(DATA_DIR, f"{coin}_1h.csv"), parse_dates=["timestamp"])
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")
    if coin == "xrp":                                # listing-artifact first day
        df = df[df["timestamp"] >= pd.Timestamp("2023-07-16", tz="UTC")].reset_index(drop=True)
    ts_et  = df["timestamp"].dt.tz_convert(ET)
    ts_jst = df["timestamp"].dt.tz_convert(JST)
    d = {
        "ts":     df["timestamp"],
        "o":      df["open"].to_numpy(float),
        "h":      df["high"].to_numpy(float),
        "l":      df["low"].to_numpy(float),
        "c":      df["close"].to_numpy(float),
        "utc_h":  df["timestamp"].dt.hour.to_numpy(),
        "et_h":   ts_et.dt.hour.to_numpy(),
        "et_dow": ts_et.dt.weekday.to_numpy(),
        "jst_dow": ts_jst.dt.weekday.to_numpy(),
        "year":   df["timestamp"].dt.year.to_numpy(),
        "n":      len(df),
    }
    lr = np.concatenate([[np.nan], np.diff(np.log(d["c"]))])
    rv = pd.Series(lr).rolling(168).std()
    # shift(1): pct at bar i uses data through close of bar i-1 -> no lookahead
    d["volpct"] = rv.rolling(2160, min_periods=1000).rank(pct=True).shift(1).to_numpy()
    d["ts2pct"] = dict(zip(d["ts"], d["volpct"]))
    _cache[coin] = d
    return d

# ────────────────────────────────────────────────────────────────────────────
# Grading helpers
# ────────────────────────────────────────────────────────────────────────────
def pnl_dollars(direction, entry, exit_, cost_side=COST_SIDE):
    gross = direction * (exit_ / entry - 1.0)
    return (gross - 2.0 * cost_side) * NOTIONAL

def stats(trades):
    """trades: list of (ts_entry, pnl$). -> dict"""
    if not trades:
        return dict(pf=0.0, wr=0.0, net=0.0, n=0, mdd=0.0)
    p = np.array([t[1] for t in trades])
    gw = p[p > 0].sum(); gl = abs(p[p < 0].sum())
    pf = gw / gl if gl > 0 else (99.0 if gw > 0 else 0.0)
    cum = np.cumsum(p); mdd = float((cum - np.maximum.accumulate(cum)).min())
    return dict(pf=float(pf), wr=float((p > 0).mean()), net=float(p.sum()),
                n=len(p), mdd=mdd)

def split_era(trades):
    is_  = [t for t in trades if IS_START  <= t[0] <= IS_END]
    oos  = [t for t in trades if OOS_START <= t[0] <= OOS_END]
    return is_, oos

def year_table(trades_by_coin, label):
    print(f"    year-by-year ({label}):")
    print(f"      {'year':<6}{'PF':>7}{'net$':>10}{'N':>6}{'WR':>7}{'maxDD$':>10}")
    pooled = [t for tl in trades_by_coin.values() for t in tl]
    for y in range(2022, 2027):
        yt = [t for t in pooled if t[0].year == y]
        s = stats(yt)
        if s["n"] == 0:
            continue
        era = "IS " if y <= 2024 else "OOS"
        print(f"      {y} {era}{s['pf']:>6.2f}{s['net']:>10.0f}{s['n']:>6}{s['wr']:>7.1%}{s['mdd']:>10.0f}")

def fmt(s):
    return f"PF {s['pf']:.2f} net ${s['net']:,.0f} N {s['n']} WR {s['wr']:.0%} DD ${s['mdd']:,.0f}"

def grade_structure(name, run_fn, grid, extra_note=""):
    """
    run_fn(coin_data, cfg, cost_side) -> list of (ts, pnl$)
    IS-selects on BTC+ETH (pooled PF, both coins IS PF>=1.0, N>=80 each),
    OOS-confirms, cross-coin generalizes, cost-stresses. Prints everything.
    Returns (survived: bool, chosen_cfg, per_coin_trades or None)
    """
    print(f"\n{'='*76}\n{name}\n{'='*76}")
    if extra_note:
        print(f"  note: {extra_note}")
    rows = []
    for cfg in grid:
        per = {}
        ok = True
        for coin in SELECT_COINS:
            tr = run_fn(load(coin), cfg)
            i, o = split_era(tr)
            per[coin] = dict(tr=tr, is_=stats(i), oos=stats(o))
            if per[coin]["is_"]["n"] < MIN_N_IS or per[coin]["is_"]["pf"] < 1.0:
                ok = False
        pooled_is  = stats([t for c in SELECT_COINS for t in split_era(per[c]["tr"])[0]])
        pooled_oos = stats([t for c in SELECT_COINS for t in split_era(per[c]["tr"])[1]])
        rows.append(dict(cfg=cfg, eligible=ok, pooled_is=pooled_is,
                         pooled_oos=pooled_oos, per=per))
    # honesty stats over the whole grid
    is_pfs = [r["pooled_is"]["pf"] for r in rows if r["pooled_is"]["n"] >= 2 * MIN_N_IS]
    if is_pfs:
        print(f"  grid honesty: {len(rows)} configs | pooled-IS PF median "
              f"{np.median(is_pfs):.2f} · best {max(is_pfs):.2f}")
    eligible = [r for r in rows if r["eligible"]]
    if not eligible:
        best = max(rows, key=lambda r: r["pooled_is"]["pf"]) if rows else None
        if best:
            print(f"  KILLED at IS gate (no config with both coins IS PF>=1.0 & N>={MIN_N_IS}).")
            print(f"    best raw: cfg={best['cfg']}  IS {fmt(best['pooled_is'])} | OOS {fmt(best['pooled_oos'])}")
        return False, None, None
    chosen = max(eligible, key=lambda r: r["pooled_is"]["pf"])
    cfg = chosen["cfg"]
    print(f"  IS-chosen cfg: {cfg}")
    for coin in SELECT_COINS:
        p = chosen["per"][coin]
        print(f"    {coin.upper():<4} IS  {fmt(p['is_'])}")
        print(f"    {coin.upper():<4} OOS {fmt(p['oos'])}")
    print(f"    POOLED IS  {fmt(chosen['pooled_is'])}")
    print(f"    POOLED OOS {fmt(chosen['pooled_oos'])}")

    if chosen["pooled_is"]["pf"] < PF_BAR:
        print(f"  KILLED: IS pooled PF {chosen['pooled_is']['pf']:.2f} < {PF_BAR} — no edge to carry forward.")
        return False, cfg, None
    oos_ok = (chosen["pooled_oos"]["pf"] >= PF_BAR
              and all(chosen["per"][c]["oos"]["pf"] >= 1.0 for c in SELECT_COINS))
    if not oos_ok:
        print(f"  KILLED: OOS confirm failed (pooled OOS PF {chosen['pooled_oos']['pf']:.2f}, "
              f"need >= {PF_BAR} with both coins >= 1.0).")
        return False, cfg, None

    # cross-coin generalization on OOS
    print("  cross-coin OOS generalization (same cfg):")
    passes = 0
    per_coin_trades = {c: chosen["per"][c]["tr"] for c in SELECT_COINS}
    for coin in CONFIRM_COINS:
        tr = run_fn(load(coin), cfg)
        per_coin_trades[coin] = tr
        _, o = split_era(tr)
        s = stats(o)
        mark = "PASS" if s["pf"] >= PF_BAR and s["n"] >= 20 else "fail"
        passes += (mark == "PASS")
        print(f"    {coin.upper():<5} OOS {fmt(s)}  [{mark}]")
    gen_ok = passes >= 3
    print(f"  generalization: {passes}/4 coins PF>={PF_BAR} -> {'PASS' if gen_ok else 'FAIL'}")

    # cost stress
    stress_tr = []
    for coin in SELECT_COINS:
        stress_tr += run_fn(load(coin), cfg, cost_side=COST_SIDE_STRESS)
    _, so = split_era(stress_tr)
    ss = stats(so)
    stress_ok = ss["pf"] >= 1.10
    print(f"  cost stress 4bps/side, BTC+ETH OOS: {fmt(ss)} -> {'PASS' if stress_ok else 'FAIL'}")

    survived = gen_ok and stress_ok
    print(f"  VERDICT: {'SURVIVOR' if survived else 'KILLED (post-OOS gates)'}")
    if survived:
        year_table({c: per_coin_trades[c] for c in SELECT_COINS}, "BTC+ETH pooled")
    return survived, cfg, per_coin_trades

# ────────────────────────────────────────────────────────────────────────────
# S1  US equity-open spillover
# ────────────────────────────────────────────────────────────────────────────
def run_s1(d, cfg, cost_side=COST_SIDE):
    sig_h, thr, hold, mode = cfg
    idx = np.where((d["et_h"] == sig_h) & (d["et_dow"] < 5))[0]
    out = []
    for i in idx:
        if i + hold >= d["n"]:
            continue
        r = d["c"][i] / d["o"][i] - 1.0
        if abs(r) < thr or r == 0.0:
            continue
        direction = np.sign(r) * (1 if mode == "cont" else -1)
        out.append((d["ts"].iloc[i], pnl_dollars(direction, d["c"][i], d["c"][i + hold], cost_side)))
    return out

S1_GRID = [(sh, th, ho, mo)
           for sh in (8, 9)
           for th in (0.0, 0.0015, 0.003)
           for ho in (2, 4, 6)
           for mo in ("cont", "fade")]

# ────────────────────────────────────────────────────────────────────────────
# S2  Asia-open breakout (9:00 JST = 00:00 UTC)
# ────────────────────────────────────────────────────────────────────────────
def run_s2(d, cfg, cost_side=COST_SIDE):
    K, W, hold, dowf = cfg
    idx = np.where(d["utc_h"] == 0)[0]
    out = []
    for i in idx:
        if i - K < 0 or i + W + hold >= d["n"]:
            continue
        if dowf == "wd" and d["jst_dow"][i] >= 5:
            continue
        hi = d["h"][i - K:i].max()
        lo = d["l"][i - K:i].min()
        for j in range(i, i + W):
            c = d["c"][j]
            if c > hi or c < lo:
                direction = 1.0 if c > hi else -1.0
                out.append((d["ts"].iloc[j],
                            pnl_dollars(direction, c, d["c"][j + hold], cost_side)))
                break
    return out

S2_GRID = [(k, w, h, f)
           for k in (4, 6, 12)
           for w in (3, 6)
           for h in (4, 8)
           for f in ("all", "wd")]

# ────────────────────────────────────────────────────────────────────────────
# S3  UTC 00:00 boundary drift / reversal (hold 1-2 h = 60-120 min)
# ────────────────────────────────────────────────────────────────────────────
def run_s3(d, cfg, cost_side=COST_SIDE):
    P, thr, hold, mode = cfg
    idx = np.where(d["utc_h"] == 0)[0]
    out = []
    for i in idx:
        if i + hold - 1 >= d["n"]:
            continue
        if mode in ("mom", "rev"):
            if i - 1 - P < 0:
                continue
            r = d["c"][i - 1] / d["c"][i - 1 - P] - 1.0
            if abs(r) < thr or r == 0.0:
                continue
            direction = np.sign(r) * (1 if mode == "mom" else -1)
        else:                       # unconditional drift
            direction = 1.0 if mode == "long" else -1.0
        out.append((d["ts"].iloc[i],
                    pnl_dollars(direction, d["o"][i], d["c"][i + hold - 1], cost_side)))
    return out

S3_GRID = ([(p, th, h, mo)
            for p in (2, 4, 8)
            for th in (0.0, 0.002)
            for h in (1, 2)
            for mo in ("mom", "rev")]
           + [(0, 0.0, h, mo) for h in (1, 2) for mo in ("long", "short")])

# ────────────────────────────────────────────────────────────────────────────
# S4  Vol-regime overlay (band chosen on IS, held OOS)
# ────────────────────────────────────────────────────────────────────────────
VOL_BANDS = [(0.0, 0.33), (0.33, 0.66), (0.66, 1.01), (0.0, 0.50), (0.50, 1.01)]

def overlay_vol(base_run, base_cfg, band):
    lo, hi = band
    def run(d, cfg=None, cost_side=COST_SIDE):
        tr = base_run(d, base_cfg, cost_side)
        out = []
        for t in tr:
            v = d["ts2pct"].get(t[0], np.nan)
            if not np.isnan(v) and lo <= v < hi:
                out.append(t)
        return out
    return run

def s4_overlay(base_name, base_run, base_cfg):
    """IS-chooses a vol band for a base structure's chosen cfg, OOS-checks."""
    print(f"\n  S4 overlay on {base_name} cfg={base_cfg}:")
    base_is  = stats([t for c in SELECT_COINS for t in split_era(base_run(load(c), base_cfg))[0]])
    base_oos = stats([t for c in SELECT_COINS for t in split_era(base_run(load(c), base_cfg))[1]])
    print(f"    baseline    IS {fmt(base_is)} | OOS {fmt(base_oos)}")
    best = None
    for band in VOL_BANDS:
        f = overlay_vol(base_run, base_cfg, band)
        is_ = stats([t for c in SELECT_COINS for t in split_era(f(load(c)))[0]])
        if is_["n"] >= 2 * MIN_N_IS and (best is None or is_["pf"] > best[1]["pf"]):
            best = (band, is_)
    if best is None:
        print("    no band keeps N >= 160 IS — overlay KILLED (sample too thin).")
        return False
    band, is_ = best
    f = overlay_vol(base_run, base_cfg, band)
    oos = stats([t for c in SELECT_COINS for t in split_era(f(load(c)))[1]])
    print(f"    IS-chosen band {band}: IS {fmt(is_)} | OOS {fmt(oos)}")
    improved_is  = is_["pf"]  > base_is["pf"]  + 0.05
    holds_oos    = oos["pf"] >= max(PF_BAR, base_oos["pf"]) and oos["n"] >= 40
    verdict = improved_is and holds_oos
    print(f"    verdict: {'SURVIVOR (band adds value IS and holds OOS)' if verdict else 'KILLED (band does not add durable value)'}")
    return verdict

# ────────────────────────────────────────────────────────────────────────────
# S5  ETH/BTC (coin/BTC) ratio range break
# ────────────────────────────────────────────────────────────────────────────
def ratio_frame(coin):
    a, b = load(coin), load("btc")
    fa = pd.DataFrame({"ts": a["ts"], "ac": a["c"]}).set_index("ts")
    fb = pd.DataFrame({"ts": b["ts"], "bc": b["c"]}).set_index("ts")
    f = fa.join(fb, how="inner").dropna()
    f["ratio"] = f["ac"] / f["bc"]
    return f.reset_index()

def run_s5_on(f, cfg, cost_side=COST_SIDE):
    Nd, hold = cfg
    w = Nd * 24
    r = f["ratio"].to_numpy()
    ac = f["ac"].to_numpy()
    ts = f["ts"]
    hi = pd.Series(r).shift(1).rolling(w).max().to_numpy()
    lo = pd.Series(r).shift(1).rolling(w).min().to_numpy()
    out = []
    i, n = w + 1, len(f)
    while i < n - hold:
        if r[i] > hi[i]:
            out.append((ts.iloc[i], pnl_dollars(1.0,  ac[i], ac[i + hold], cost_side)))
            i += hold
        elif r[i] < lo[i]:
            out.append((ts.iloc[i], pnl_dollars(-1.0, ac[i], ac[i + hold], cost_side)))
            i += hold
        else:
            i += 1
    return out

S5_GRID = [(nd, h) for nd in (10, 20, 30) for h in (24, 48, 72)]

def mission_s5():
    print(f"\n{'='*76}\nS5  ETH/BTC ratio N-day range break (trade ETH, spot terms)\n{'='*76}")
    f_eth = ratio_frame("eth")
    rows = []
    for cfg in S5_GRID:
        tr = run_s5_on(f_eth, cfg)
        i, o = split_era(tr)
        rows.append((cfg, stats(i), stats(o), tr))
    pfs = [r[1]["pf"] for r in rows if r[1]["n"] >= MIN_N_IS]
    print(f"  grid honesty: {len(rows)} configs | IS PF median "
          f"{np.median(pfs) if pfs else 0:.2f} · best {max(pfs) if pfs else 0:.2f} (N>={MIN_N_IS} only)")
    eligible = [r for r in rows if r[1]["n"] >= MIN_N_IS]
    if not eligible:
        print("  KILLED: no config reaches N>=80 IS.")
        return False
    cfg, is_, oos, tr = max(eligible, key=lambda r: r[1]["pf"])
    print(f"  IS-chosen cfg (N-day, hold-h): {cfg}")
    print(f"    ETH IS  {fmt(is_)}")
    print(f"    ETH OOS {fmt(oos)}")
    if is_["pf"] < PF_BAR:
        print(f"  KILLED: IS PF {is_['pf']:.2f} < {PF_BAR}.")
        return False
    if oos["pf"] < PF_BAR or oos["n"] < 20:
        print(f"  KILLED: OOS confirm failed (PF {oos['pf']:.2f}).")
        return False
    print("  cross-pair OOS generalization (same cfg, coin/BTC):")
    passes = 0
    for coin in CONFIRM_COINS:
        trc = run_s5_on(ratio_frame(coin), cfg)
        _, o = split_era(trc)
        s = stats(o)
        mark = "PASS" if s["pf"] >= PF_BAR and s["n"] >= 15 else "fail"
        passes += (mark == "PASS")
        print(f"    {coin.upper():<5}/BTC OOS {fmt(s)}  [{mark}]")
    gen_ok = passes >= 3
    stress = run_s5_on(f_eth, cfg, cost_side=COST_SIDE_STRESS)
    _, so = split_era(stress)
    ss = stats(so)
    stress_ok = ss["pf"] >= 1.10
    print(f"  generalization: {passes}/4 -> {'PASS' if gen_ok else 'FAIL'}")
    print(f"  cost stress 4bps/side ETH OOS: {fmt(ss)} -> {'PASS' if stress_ok else 'FAIL'}")
    survived = gen_ok and stress_ok
    print(f"  VERDICT: {'SURVIVOR' if survived else 'KILLED (post-OOS gates)'}")
    if survived:
        year_table({"eth": tr}, "ETH/BTC")
    return survived

# ────────────────────────────────────────────────────────────────────────────
# MISSION B — pilot pricing, FREE metadata.get_cost ONLY
# ────────────────────────────────────────────────────────────────────────────
def mission_pilots():
    print(f"\n{'='*76}\nMISSION B  Databento GLBX.MDP3 ohlcv-1m pilot quotes (metadata.get_cost)\n{'='*76}")
    import databento as db
    key = None
    with open(os.path.join(BASE, ".env")) as fh:
        for line in fh:
            if line.startswith("DATABENTO_API_KEY"):
                key = line.strip().split("=", 1)[1]
    if not key:
        print("  no DATABENTO_API_KEY in .env — skipped")
        return {}
    client = db.Historical(key)
    now = pd.Timestamp.now(tz="UTC")
    quotes = {}
    for sym in ("ZN.v.0", "6E.v.0", "SI.v.0"):
        cost = None
        for end in (now.strftime("%Y-%m-%d"), (now - pd.Timedelta(days=1)).strftime("%Y-%m-%d")):
            try:
                cost = client.metadata.get_cost(
                    dataset="GLBX.MDP3", symbols=[sym], schema="ohlcv-1m",
                    start="2022-01-01", end=end, stype_in="continuous",
                    mode="historical")
                break
            except Exception as e:
                err = e
        if cost is None:
            print(f"  {sym}: FAILED ({err})")
        else:
            quotes[sym] = cost
            print(f"  {sym}: ${cost:.2f}   (2022-01-01 -> {end}, ohlcv-1m, continuous)")
    return quotes

# ────────────────────────────────────────────────────────────────────────────
def main():
    args = sys.argv[1:]
    only = None
    for a in args:
        if a.startswith("--only"):
            only = args[args.index(a) + 1] if a == "--only" else a.split("=", 1)[1]
    skip_pilots = "--skip-pilots" in args

    print("crypto_structures.py — house-law session-structure sweep")
    print(f"  data: Binance.US spot 1h UTC | IS 2022-2024, OOS 2025-2026")
    print(f"  costs: {COST_SIDE*1e4:.0f} bps/side ({COST_SIDE*2e4:.0f} bps RT), stress {COST_SIDE_STRESS*1e4:.0f} bps/side")
    for c in ALL_COINS:
        d = load(c)
        print(f"  loaded {c}: {d['n']} bars  {d['ts'].iloc[0].date()} -> {d['ts'].iloc[-1].date()}")

    results = {}
    chosen  = {}
    if only in (None, "s1"):
        s, cfg, _ = grade_structure(
            "S1  US equity-open spillover (8:30 print / 9:30 open hour, ET)",
            run_s1, S1_GRID,
            "signal bar = ET hour bar containing the event; entry at its close")
        results["S1"] = s; chosen["S1"] = (run_s1, cfg)
    if only in (None, "s2"):
        s, cfg, _ = grade_structure(
            "S2  Asia-open breakout (9:00 JST = 00:00 UTC), prior K-h range",
            run_s2, S2_GRID)
        results["S2"] = s; chosen["S2"] = (run_s2, cfg)
    if only in (None, "s3"):
        s, cfg, _ = grade_structure(
            "S3  UTC 00:00 boundary drift/reversal (hold 60-120 min)",
            run_s3, S3_GRID,
            "mom/rev of prior P-h move + unconditional long/short drift")
        results["S3"] = s; chosen["S3"] = (run_s3, cfg)
    if only in (None, "s4"):
        print(f"\n{'='*76}\nS4  Vol-regime overlay (7-day RV, 90-day pct rank; band IS-chosen)\n{'='*76}")
        any_s4 = False
        for nm in ("S1", "S2", "S3"):
            if nm in chosen and chosen[nm][1] is not None:
                any_s4 |= s4_overlay(nm, chosen[nm][0], chosen[nm][1])
        if not any(nm in chosen and chosen[nm][1] is not None for nm in ("S1", "S2", "S3")):
            print("  no base structure cleared the IS gate — nothing to overlay. KILLED.")
        results["S4"] = any_s4
    if only in (None, "s5"):
        results["S5"] = mission_s5()

    if not skip_pilots and only in (None, "pilots"):
        try:
            mission_pilots()
        except Exception as e:
            print(f"  MISSION B failed: {e}")

    if results:
        print(f"\n{'='*76}\nFINAL: {sum(results.values())} survivor(s) of {len(results)} structures")
        for k, v in results.items():
            print(f"  {k}: {'SURVIVOR' if v else 'KILLED'}")

if __name__ == "__main__":
    main()
