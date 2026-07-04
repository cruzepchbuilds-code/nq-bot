"""
brain/research/gc_structures.py

THE DEFINITIVE GOLD SESSION-STRUCTURE SWEEP  (supersedes gc_playbook.py)

Data: data/gc_1min_v2.csv — 1.57M GC 1-min bars, volume-ranked continuous,
timestamps US/Eastern, 2022-01-02 .. 2026-06-30, full Globex (17:00 halt dark).

Families (anchored to gold's day):
  A  COMEX pit open 8:20 ET      — opening-range breakout (OR 5/10/15m sweeps)
  B  8:30 ET US data releases    — impulse continuation / fade (5-min move proxy)
  C  London PM fix ~10:00 ET     — pre-fix drift + post-fix reversion
  D  London/NY overlap end 11-12 — morning-trend continuation / fade
  E  Asia demand 20:00-24:00 ET  — drift + 18-20h range breakout

House law: IS 2022-2024, OOS 2025-2026. Best config chosen on IS ONLY, then
confirmed OOS. N >= ~80 total. Year-by-year table required. Kill-battery on
any survivor (cost x2, next-open fills, weekday split, top-5-day concentration,
parameter neighborhood).

UNITS: gold price 2.6x'd over the sample (median 8:20-35 OR: 5.3pt 2022 ->
15.7pt 2026), so stops/thresholds are swept in %-of-price and OR-multiples
(era-coherent) AND raw points (per mission; expected to die by era).

CONTRACT / COSTS (verified: GC 100oz, tick 0.10=$10 => $100/pt; MGC 10oz => $10/pt)
  GC : $24.50 RT  = $1.60/side commission x2 ($3.20) + ~2.1 ticks spread/slip RT
  MGC: $ 4.85 RT  = $0.90/side commission x2 ($1.80) + ~3 ticks spread/slip RT
Fills: signals on bar close, filled at that close; bracket exits stop-first
(pessimistic); time exits at first bar-open past the flat time.

Run:  python3 brain/research/gc_structures.py            (full study)
      python3 brain/research/gc_structures.py cov        (coverage only)
"""

import csv
import os
import sys
from collections import defaultdict
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(BASE, "data", "gc_1min_v2.csv")
V12 = os.path.join(BASE, "data", "v12_daily_stream.csv")

PT_GC, COST_GC = 100.0, 24.50
PT_MGC, COST_MGC = 10.0, 4.85
TICK = 0.10
BUF = 0.10                       # breakout buffer: 1 tick
IS_Y, OOS_Y = (2022, 2023, 2024), (2025, 2026)
ALL_Y = (2022, 2023, 2024, 2025, 2026)

# NQ v12 live windows (ET minutes) for collision report
NQ_WINDOWS = [("ORB 9:46-10:30", 586, 630), ("REJ 11:00-13:00", 660, 780),
              ("PM 13:15-14:00", 795, 840), ("ASIA 18:15", 1095, 1100)]


# ────────────────────────────── data ──────────────────────────────

def load_days():
    days = defaultdict(list)
    with open(DATA) as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            s = row[0]
            d = date(int(s[:4]), int(s[5:7]), int(s[8:10]))
            tm = int(s[11:13]) * 60 + int(s[14:16])
            days[d].append((tm, float(row[1]), float(row[2]),
                            float(row[3]), float(row[4])))
    out = {}
    for d, bars in days.items():
        bars.sort()
        out[d] = bars
    return out


def px_at(bars, tmin):
    """(index, close) of last bar strictly before tmin; None if none."""
    lo, hi = 0, len(bars)
    while lo < hi:
        mid = (lo + hi) // 2
        if bars[mid][0] < tmin:
            lo = mid + 1
        else:
            hi = mid
    if lo == 0:
        return None
    return lo - 1, bars[lo - 1][4]


def coverage(days):
    print("── DATA COVERAGE " + "─" * 60)
    byh = defaultdict(int)
    byy = defaultdict(set)
    eve = defaultdict(set)
    rth = defaultdict(set)
    for d, bars in days.items():
        byy[d.year].add(d)
        for tm, *_ in bars:
            byh[tm // 60] += 1
        if any(1200 <= b[0] < 1440 for b in bars):
            eve[d.year].add(d)
        if any(500 <= b[0] < 900 for b in bars):
            rth[d.year].add(d)
    print("  bars/hour: " + " ".join(f"{h:02d}h:{byh[h]//1000}k" for h in range(24) if byh[h]))
    print("  (17:00-18:00 maintenance halt is the only dark hour — full Globex)")
    for y in sorted(byy):
        print(f"  {y}: {len(byy[y]):>3} dates | {len(rth[y]):>3} with RTH(8:20-15) bars"
              f" | {len(eve[y]):>3} with Asia(20-24h) bars")
    print()


# ─────────────────────────── simulation ───────────────────────────

def bracket(bars, i0, is_long, entry, stop_d, tgt_d, flat, next_open=False):
    """Points captured. Entry at bars[i0] close (or next bar open if next_open).
    stop_d/tgt_d in points (None = absent). Stop checked before target
    (pessimistic); gaps fill at open. Time exit at first bar-open >= flat."""
    if next_open:
        if i0 + 1 >= len(bars):
            return 0.0
        i0 += 1
        entry = bars[i0][1]
    sl = (entry - stop_d if is_long else entry + stop_d) if stop_d else None
    tp = (entry + tgt_d if is_long else entry - tgt_d) if tgt_d else None
    sgn = 1.0 if is_long else -1.0
    for j in range(i0 + 1, len(bars)):
        tm, o, h, l, c = bars[j]
        if tm >= flat:
            return (o - entry) * sgn
        if sl is not None:
            if is_long:
                if o <= sl:
                    return o - entry
                if l <= sl:
                    return sl - entry
            else:
                if o >= sl:
                    return entry - o
                if h >= sl:
                    return entry - sl
        if tp is not None:
            if is_long:
                if o >= tp:
                    return o - entry
                if h >= tp:
                    return tp - entry
            else:
                if o <= tp:
                    return entry - o
                if l <= tp:
                    return entry - tp
    return (bars[-1][4] - entry) * sgn


def stop_points(mode, entry, ref):
    """mode: ('or', m) -> m*ref | ('pct', p) -> p% of entry | ('pt', x) | None"""
    if mode is None:
        return None
    kind, v = mode
    if kind == "or":
        return max(v * ref, 2 * TICK)
    if kind == "pct":
        return max(v * entry / 100.0, 2 * TICK)
    return float(v)


def smode_s(mode):
    if mode is None:
        return "none"
    k, v = mode
    return {"or": f"{v}xOR", "pct": f"{v}%", "pt": f"{v}pt"}[k] if k != "or" else f"{v}xOR"


# ─────────────────────────── family engines ───────────────────────────
# Each engine returns {date: (points, entry_tm, exit_side_note)} — one trade/day max.

def run_A(days, orwin, cutoff, smode, tgtR, flat, next_open=False):
    """Pit-open ORB: OR 8:20+orwin, first close through OR+/-1 tick before cutoff."""
    out = {}
    o_end = 500 + orwin
    for d, bars in days.items():
        orh = orl = None
        nb = 0
        ent = None
        for i, (tm, o, h, l, c) in enumerate(bars):
            if tm < 500:
                continue
            if tm < o_end:
                orh = h if orh is None else max(orh, h)
                orl = l if orl is None else min(orl, l)
                nb += 1
                continue
            if nb < int(orwin * 0.8):
                break
            if tm >= cutoff:
                break
            if c > orh + BUF:
                ent = (i, c, True)
                break
            if c < orl - BUF:
                ent = (i, c, False)
                break
        if ent is None:
            continue
        i, e, lng = ent
        sd = stop_points(smode, e, orh - orl)
        td = sd * tgtR if (sd and tgtR) else None
        pts = bracket(bars, i, lng, e, sd, td, flat, next_open)
        out[d] = (pts, bars[i][0])
    return out


def run_B(days, thr, cont, smode_imp, tgtR, flat, next_open=False):
    """8:30 impulse: m = px(8:35)-px(8:30); |m|>=thr; enter cont/fade at 8:35.
    thr: ('pct',p) or ('pt',x). smode_imp: ('imp',m) stop = m*|impulse| | ('pct',p)."""
    out = {}
    for d, bars in days.items():
        a = px_at(bars, 510)
        b = px_at(bars, 515)
        if not a or not b:
            continue
        i0, p0 = a
        i1, p1 = b
        if bars[i0][0] < 500 or bars[i1][0] < 510 or i1 <= i0:
            continue
        m = p1 - p0
        t = thr[1] * p1 / 100.0 if thr[0] == "pct" else float(thr[1])
        if abs(m) < t or m == 0:
            continue
        lng = (m > 0) if cont else (m < 0)
        kind, v = smode_imp
        sd = max(v * abs(m), 2 * TICK) if kind == "imp" else max(v * p1 / 100.0, 2 * TICK)
        td = sd * tgtR if tgtR else None
        pts = bracket(bars, i1, lng, p1, sd, td, flat, next_open)
        out[d] = (pts, bars[i1][0])
    return out


def run_C_pre(days, t_in, is_long, smode, next_open=False):
    """Pre-fix drift: enter t_in, exit at 10:00 (London PM fix)."""
    out = {}
    for d, bars in days.items():
        a = px_at(bars, t_in)
        if not a:
            continue
        i, e = a
        if bars[i][0] < 500 or bars[i][0] < t_in - 10:
            continue
        sd = stop_points(smode, e, 0)
        pts = bracket(bars, i, is_long, e, sd, None, 600, next_open)
        out[d] = (pts, bars[i][0])
    return out


def run_C_post(days, thr, cont, smode, flat, next_open=False):
    """Post-fix: m = px(10:00)-px(9:30); fade (or cont) at 10:00, exit flat."""
    out = {}
    for d, bars in days.items():
        a = px_at(bars, 570)
        b = px_at(bars, 600)
        if not a or not b:
            continue
        i0, p0 = a
        i1, p1 = b
        if bars[i0][0] < 560 or bars[i1][0] < 590 or i1 <= i0:
            continue
        m = p1 - p0
        t = thr[1] * p1 / 100.0 if thr[0] == "pct" else float(thr[1])
        if abs(m) < t or m == 0:
            continue
        lng = (m > 0) if cont else (m < 0)
        if smode and smode[0] == "imp":
            sd = max(smode[1] * abs(m), 2 * TICK)
        else:
            sd = stop_points(smode, p1, 0)
        pts = bracket(bars, i1, lng, p1, sd, None, flat, next_open)
        out[d] = (pts, bars[i1][0])
    return out


def run_D(days, t_in, thr_pct, cont, smode, flat, next_open=False):
    """Overlap-end: morning move = px(t_in) - pit open 8:20; cont/fade at t_in."""
    out = {}
    for d, bars in days.items():
        ref = None
        for tm, o, h, l, c in bars:
            if tm >= 500:
                ref = o
                break
        a = px_at(bars, t_in)
        if ref is None or not a:
            continue
        i, e = a
        if bars[i][0] < t_in - 10:
            continue
        m = e - ref
        if abs(m) < thr_pct * e / 100.0 or m == 0:
            continue
        lng = (m > 0) if cont else (m < 0)
        sd = stop_points(smode, e, 0)
        pts = bracket(bars, i, lng, e, sd, None, flat, next_open)
        out[d] = (pts, bars[i][0])
    return out


def run_E_drift(days, t_in, is_long, smode, cond=None, next_open=False):
    """Asia drift: enter t_in evening, exit 23:55. cond: None|'cont'|'fade' vs
    same-day RTH move (8:20 open -> 15:00)."""
    out = {}
    for d, bars in days.items():
        a = px_at(bars, t_in)
        if not a:
            continue
        i, e = a
        if bars[i][0] < t_in - 10 or bars[i][0] < 1080:
            continue
        lng = is_long
        if cond:
            ref = None
            for tm, o, h, l, c in bars:
                if tm >= 500:
                    ref = o
                    break
            b = px_at(bars, 900)
            if ref is None or not b or b[1] == ref:
                continue
            up = b[1] > ref
            lng = up if cond == "cont" else not up
        sd = stop_points(smode, e, 0)
        pts = bracket(bars, i, lng, e, sd, None, 1435, next_open)
        out[d] = (pts, bars[i][0])
    return out


def run_E_rbo(days, smult, tgtR, next_open=False):
    """Asia range BO: 18:00-20:00 range, first close through 20:00-23:00,
    stop = smult*range, flat 23:55."""
    out = {}
    for d, bars in days.items():
        rh = rl = None
        nb = 0
        ent = None
        for i, (tm, o, h, l, c) in enumerate(bars):
            if tm < 1080:
                continue
            if tm < 1200:
                rh = h if rh is None else max(rh, h)
                rl = l if rl is None else min(rl, l)
                nb += 1
                continue
            if nb < 90 or rh is None:
                break
            if tm >= 1380:
                break
            if c > rh + BUF:
                ent = (i, c, True)
                break
            if c < rl - BUF:
                ent = (i, c, False)
                break
        if ent is None:
            continue
        i, e, lng = ent
        rng = rh - rl
        sd = max(smult * rng, 2 * TICK)
        td = sd * tgtR if tgtR else None
        pts = bracket(bars, i, lng, e, sd, td, 1435, next_open)
        out[d] = (pts, bars[i][0])
    return out


# ─────────────────────────── grading ───────────────────────────

def pf(v):
    w = sum(x for x in v if x > 0)
    l = abs(sum(x for x in v if x <= 0))
    return round(w / l, 2) if l else (99.0 if w else 0.0)


def to_usd(pts_by_day, pt=PT_GC, cost=COST_GC):
    return {d: r[0] * pt - cost for d, r in pts_by_day.items()}


def grade(usd):
    """usd: {date: $}. Returns metrics dict."""
    if not usd:
        return None
    ds = sorted(usd)
    vis = [usd[d] for d in ds if d.year in IS_Y]
    vos = [usd[d] for d in ds if d.year in OOS_Y]
    allv = [usd[d] for d in ds]
    cum = mx = dd = 0.0
    for d in ds:
        cum += usd[d]
        mx = max(mx, cum)
        dd = max(dd, mx - cum)
    return dict(n=len(allv), net=sum(allv), pf=pf(allv),
                wr=100.0 * sum(1 for x in allv if x > 0) / len(allv),
                nis=len(vis), pfis=pf(vis), netis=sum(vis),
                nos=len(vos), pfos=pf(vos), netos=sum(vos),
                maxdd=dd, worst=min(allv), avg=sum(allv) / len(allv))


def year_rows(usd):
    rows = []
    for y in ALL_Y:
        v = [p for d, p in usd.items() if d.year == y]
        if v:
            rows.append((y, len(v), pf(v), sum(v),
                         100.0 * sum(1 for x in v if x > 0) / len(v)))
    return rows


def show_year_table(usd, indent="      "):
    for y, n, p, net, wr in year_rows(usd):
        era = "IS " if y in IS_Y else "OOS"
        print(f"{indent}{y} {era} N={n:>3}  PF={p:>5.2f}  WR={wr:4.1f}%  ${net:>+9,.0f}")


def rank_family(fam, results, top=10):
    """results: list of (name, spec, pts_by_day). Choose best on IS PF
    (N_IS>=60, N>=80). Print top table. Return (name, spec, usd, g) of winner."""
    graded = []
    for name, spec, pbd in results:
        usd = to_usd(pbd)
        g = grade(usd)
        if g is None or g["n"] < 80 or g["nis"] < 60:
            continue
        graded.append((name, spec, pbd, usd, g))
    if not graded:
        print("  (no config met N>=80 / N_IS>=60)")
        return None
    graded.sort(key=lambda x: (-x[4]["pfis"], -x[4]["netis"]))
    print(f"  {'config':<46} {'N':>4} {'ISpf':>5} {'ISnet':>9} {'OOSpf':>5} "
          f"{'OOSnet':>9} {'$/tr':>7} {'MGC$/tr':>7}")
    for name, spec, pbd, usd, g in graded[:top]:
        mgc = sum(r[0] * PT_MGC - COST_MGC for r in pbd.values()) / g["n"]
        print(f"  {name:<46} {g['n']:>4} {g['pfis']:>5.2f} {g['netis']:>+9,.0f} "
              f"{g['pfos']:>5.2f} {g['netos']:>+9,.0f} {g['avg']:>+7,.0f} {mgc:>+7.1f}")
    name, spec, pbd, usd, g = graded[0]
    print(f"  ── IS-chosen winner: {name}")
    show_year_table(usd)
    print(f"      maxDD ${g['maxdd']:,.0f} | worst day ${g['worst']:,.0f} | "
          f"WR {g['wr']:.1f}% | avg ${g['avg']:+,.0f}/tr GC")
    return dict(fam=fam, name=name, spec=spec, pbd=pbd, usd=usd, g=g,
                ranked=graded)


# ─────────────────────────── kill battery ───────────────────────────

def kill_battery(w, days, rerun):
    """rerun(next_open=True) -> pts_by_day for winner spec with next-open fills."""
    name, pbd, usd, g = w["name"], w["pbd"], w["usd"], w["g"]
    print(f"\n  KILL BATTERY — {name}")
    ok = True

    # era bar
    era = g["netis"] > 0 and g["netos"] > 0 and g["pfis"] >= 1.10 and g["pfos"] >= 1.05
    print(f"  [{'PASS' if era else 'FAIL'}] era bar: IS PF {g['pfis']:.2f} "
          f"(${g['netis']:+,.0f})  OOS PF {g['pfos']:.2f} (${g['netos']:+,.0f})")
    ok &= era

    # 2x spread stress: GC spread ~$21.30 of the $24.50 -> stress cost $45.80
    usd_x = {d: r[0] * PT_GC - 45.80 for d, r in pbd.items()}
    gx = grade(usd_x)
    c = gx["pfis"] >= 1.05 and gx["pfos"] >= 1.00 and gx["net"] > 0
    print(f"  [{'PASS' if c else 'FAIL'}] 2x spread ($45.80 RT): IS PF {gx['pfis']:.2f} "
          f"OOS PF {gx['pfos']:.2f} net ${gx['net']:+,.0f}")
    ok &= c

    # next-open fills
    pbd_no = rerun()
    gn = grade(to_usd(pbd_no))
    c = gn["pfis"] >= 1.05 and gn["pfos"] >= 1.00
    print(f"  [{'PASS' if c else 'FAIL'}] next-bar-open fills: IS PF {gn['pfis']:.2f} "
          f"OOS PF {gn['pfos']:.2f} net ${gn['net']:+,.0f}")
    ok &= c

    # top-5 win-day concentration
    wins = sorted(usd.values(), reverse=True)[:5]
    resid = g["net"] - sum(wins)
    c = resid > 0
    print(f"  [{'PASS' if c else 'FAIL'}] minus top-5 win days: ${resid:+,.0f} "
          f"(top5 = ${sum(wins):,.0f} of ${g['net']:,.0f})")
    ok &= c

    # weekday split
    line = []
    bad = 0
    for wd, lab in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sun")):
        wdx = 6 if lab == "Sun" else wd
        v = [p for d, p in usd.items() if d.weekday() == wdx]
        if len(v) >= 15:
            line.append(f"{lab} N={len(v)} PF={pf(v):.2f}")
            if pf(v) < 0.85:
                bad += 1
    c = bad <= 1
    print(f"  [{'PASS' if c else 'WARN'}] weekdays: " + " | ".join(line))

    print(f"  => kill battery {'SURVIVED' if ok else 'KILLED'}")
    return ok


def neighborhood(w, results):
    """Print IS/OOS PF of configs differing from winner in exactly one axis."""
    spec = w["spec"]
    print("  parameter neighborhood (one axis varied):")
    for name, s2, pbd in results:
        diff = [k for k in spec if s2.get(k) != spec.get(k)]
        if len(diff) == 1:
            g = grade(to_usd(pbd))
            if g and g["n"] >= 80:
                print(f"    {name:<46} IS {g['pfis']:>5.2f}  OOS {g['pfos']:>5.2f}  "
                      f"net ${g['net']:>+9,.0f}")


# ─────────────────────────── verdict helpers ───────────────────────────

def load_v12():
    out = {}
    with open(V12) as f:
        for row in csv.DictReader(f):
            y, m, d = row["date"].split("-")
            out[date(int(y), int(m), int(d))] = float(row["pnl"])
    return out


def corr_v12(usd, v12):
    both = [(v12[d], p) for d, p in usd.items() if d in v12]
    if len(both) < 30:
        return None, None, 0
    xs, ys = zip(*both)
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((a - mx) * (b - my) for a, b in both)
    vx = sum((a - mx) ** 2 for a in xs) ** 0.5
    vy = sum((b - my) ** 2 for b in ys) ** 0.5
    corr = cov / (vx * vy) if vx * vy else 0.0
    on_loss = [p for d, p in usd.items() if d in v12 and v12[d] < 0]
    return corr, pf(on_loss), len(both)


# ─────────────────────────── sweeps ───────────────────────────

STOPS_A = [("or", 0.5), ("or", 1.0), ("pct", 0.10), ("pct", 0.20),
           ("pt", 5), ("pt", 10)]
TGTS = [2.0, 3.0, None]


def sweep_A(days):
    print("\n" + "═" * 100)
    print("FAMILY A — COMEX pit-open 8:20 ORB   (OR 5/10/15m x cutoff x stop x tgt x flat)")
    print("═" * 100)
    results = []
    n = 0
    for orwin in (5, 10, 15):
        for cutoff in (585, 630):
            for sm in STOPS_A:
                for tR in TGTS:
                    for flat in (780, 900):
                        name = (f"A or{orwin:<2} cut{cutoff//60}:{cutoff%60:02d} "
                                f"stop{smode_s(sm):<7} tgt{str(tR)+'R' if tR else 'time':<4} "
                                f"flat{flat//60}h")
                        spec = dict(orwin=orwin, cutoff=cutoff, sm=sm, tR=tR, flat=flat)
                        results.append((name, spec, run_A(days, orwin, cutoff, sm, tR, flat)))
                        n += 1
                        if n % 36 == 0:
                            print(f"  ... {n}/216 configs", flush=True)
    w = rank_family("A", results)
    if w:
        s = w["spec"]
        w["rerun"] = lambda: run_A(days, s["orwin"], s["cutoff"], s["sm"], s["tR"],
                                   s["flat"], next_open=True)
        w["results"] = results
    return w


def sweep_B(days):
    print("\n" + "═" * 100)
    print("FAMILY B — 8:30 data-release impulse   (5-min move >= thr; cont & fade)")
    print("═" * 100)
    results = []
    n = 0
    thrs = [("pct", 0.08), ("pct", 0.15), ("pct", 0.25), ("pt", 2), ("pt", 4), ("pt", 8)]
    stops = [("imp", 0.5), ("imp", 1.0), ("pct", 0.15)]
    for thr in thrs:
        for cont in (True, False):
            for sm in stops:
                for tR in (1.0, 2.0, None):
                    for flat in (600, 780):
                        nm = (f"B thr{thr[1]}{'%' if thr[0]=='pct' else 'pt':<2} "
                              f"{'cont' if cont else 'fade'} stop{sm[1]}{'xImp' if sm[0]=='imp' else '%':<4} "
                              f"tgt{str(tR)+'R' if tR else 'time':<4} flat{flat//60}:{flat%60:02d}")
                        spec = dict(thr=thr, cont=cont, sm=sm, tR=tR, flat=flat)
                        results.append((nm, spec, run_B(days, thr, cont, sm, tR, flat)))
                        n += 1
                        if n % 36 == 0:
                            print(f"  ... {n}/216 configs", flush=True)
    w = rank_family("B", results)
    if w:
        s = w["spec"]
        w["rerun"] = lambda: run_B(days, s["thr"], s["cont"], s["sm"], s["tR"],
                                   s["flat"], next_open=True)
        w["results"] = results
    return w


def sweep_C(days):
    print("\n" + "═" * 100)
    print("FAMILY C — London PM fix ~10:00 ET   (pre-fix drift; post-fix reversion)")
    print("═" * 100)
    results = []
    for t_in in (540, 555, 570):
        for lng in (True, False):
            for sm in (None, ("pct", 0.15)):
                nm = (f"C pre-fix {t_in//60}:{t_in%60:02d}->10:00 "
                      f"{'LONG ' if lng else 'SHORT'} stop{smode_s(sm)}")
                spec = dict(kind="pre", t_in=t_in, lng=lng, sm=sm)
                results.append((nm, spec, run_C_pre(days, t_in, lng, sm)))
    print("  ... pre-fix done (12)", flush=True)
    thrs = [("pct", 0.05), ("pct", 0.10), ("pct", 0.18), ("pt", 3)]
    for thr in thrs:
        for cont in (False, True):
            for sm in (None, ("imp", 1.0), ("pct", 0.15)):
                for flat in (660, 720):
                    nm = (f"C post-fix thr{thr[1]}{'%' if thr[0]=='pct' else 'pt'} "
                          f"{'cont' if cont else 'FADE'} stop{('1xMove' if sm and sm[0]=='imp' else smode_s(sm)):<6} "
                          f"exit{flat//60}h")
                    spec = dict(kind="post", thr=thr, cont=cont, sm=sm, flat=flat)
                    results.append((nm, spec, run_C_post(days, thr, cont, sm, flat)))
    print("  ... post-fix done (48)", flush=True)
    w = rank_family("C", results)
    if w:
        s = w["spec"]
        if s["kind"] == "pre":
            w["rerun"] = lambda: run_C_pre(days, s["t_in"], s["lng"], s["sm"], next_open=True)
        else:
            w["rerun"] = lambda: run_C_post(days, s["thr"], s["cont"], s["sm"], s["flat"],
                                            next_open=True)
        w["results"] = results
    return w


def sweep_D(days):
    print("\n" + "═" * 100)
    print("FAMILY D — London/NY overlap end 11-12   (morning-trend cont/fade)")
    print("═" * 100)
    results = []
    n = 0
    for t_in in (660, 720):
        for thr in (0.0, 0.15, 0.30):
            for cont in (True, False):
                for sm in (("pct", 0.15), ("pct", 0.30), None):
                    for flat in (780, 900):
                        nm = (f"D {t_in//60}:00 thr{thr}% {'cont' if cont else 'fade'} "
                              f"stop{smode_s(sm):<6} flat{flat//60}h")
                        spec = dict(t_in=t_in, thr=thr, cont=cont, sm=sm, flat=flat)
                        results.append((nm, spec, run_D(days, t_in, thr, cont, sm, flat)))
                        n += 1
        print(f"  ... entry {t_in//60}:00 done", flush=True)
    w = rank_family("D", results)
    if w:
        s = w["spec"]
        w["rerun"] = lambda: run_D(days, s["t_in"], s["thr"], s["cont"], s["sm"],
                                   s["flat"], next_open=True)
        w["results"] = results
    return w


def sweep_E(days):
    print("\n" + "═" * 100)
    print("FAMILY E — Asia demand 20:00-24:00   (drift, conditional drift, 18-20h range BO)")
    print("═" * 100)
    results = []
    for t_in in (1140, 1200, 1260):
        for lng in (True, False):
            for sm in (None, ("pct", 0.15)):
                nm = (f"E drift {t_in//60}:00->23:55 {'LONG ' if lng else 'SHORT'} "
                      f"stop{smode_s(sm)}")
                spec = dict(kind="drift", t_in=t_in, lng=lng, sm=sm, cond=None)
                results.append((nm, spec, run_E_drift(days, t_in, lng, sm)))
    for cond in ("cont", "fade"):
        nm = f"E drift 20:00->23:55 {cond.upper()}-of-RTH stopnone"
        spec = dict(kind="drift", t_in=1200, lng=True, sm=None, cond=cond)
        results.append((nm, spec, run_E_drift(days, 1200, True, None, cond=cond)))
    print("  ... drift done (14)", flush=True)
    for smult in (0.5, 1.0):
        for tR in (1.0, 2.0, None):
            nm = f"E rangeBO 18-20h stop{smult}xRng tgt{str(tR)+'R' if tR else 'time'}"
            spec = dict(kind="rbo", smult=smult, tR=tR)
            results.append((nm, spec, run_E_rbo(days, smult, tR)))
    print("  ... range BO done (6)", flush=True)
    w = rank_family("E", results)
    if w:
        s = w["spec"]
        if s["kind"] == "drift":
            w["rerun"] = lambda: run_E_drift(days, s["t_in"], s["lng"], s["sm"],
                                             cond=s["cond"], next_open=True)
        else:
            w["rerun"] = lambda: run_E_rbo(days, s["smult"], s["tR"], next_open=True)
        w["results"] = results
    return w


# ─────────────────────────── stage 2 ───────────────────────────
# The family-level IS-argmax picks all failed OOS (run 1). Two honest follow-ups,
# both still IS-only selections (declared BEFORE looking at their OOS batteries):
#   S2a  Family B bundles two distinct hypotheses (continuation vs fade — mission
#        says "test both"). Select best-IS config WITHIN each direction
#        (selection multiplicity = 2, disclosed), battery on any era-bar pass.
#   S2b  Family A plateau context: the 0.5xOR/time-exit cells across orwin/cutoff/
#        flat — reported to show fragility, NOT selectable.
#   S2c  Named-anomaly graveyard for the record: pre-fix short, post-fix fade,
#        Asia long drift.

def dossier(w, days, v12):
    g, usd = w["g"], w["usd"]
    print(f"\n── SURVIVOR DOSSIER: {w['name']}")
    show_year_table(usd, indent="  ")
    print(f"  worst realized single loss: ${g['worst']:,.0f} GC "
          f"(${g['worst']/10:,.0f} MGC) vs $1,200 DLL"
          f"{'  ** EXCEEDS DLL **' if abs(g['worst']) > 1200 else ''}")
    print(f"  maxDD ${g['maxdd']:,.0f} GC | NQ book risks $415-565/trade -> "
          f"stacked worst day ~${abs(g['worst'])+565:,.0f} GC / "
          f"${abs(g['worst'])/10+565:,.0f} w/ MGC")
    tms = sorted(r[1] for r in w["pbd"].values())
    med_tm = tms[len(tms) // 2]
    print(f"  entry times: median {med_tm//60}:{med_tm%60:02d} "
          f"range {tms[0]//60}:{tms[0]%60:02d}-{tms[-1]//60}:{tms[-1]%60:02d}")
    for lab, a, b in NQ_WINDOWS:
        n_in = sum(1 for t in tms if a <= t <= b)
        print(f"    entries inside NQ {lab}: {n_in} ({100*n_in/len(tms):.0f}%)")
    corr, pfl, nboth = corr_v12(usd, v12)
    if corr is None:
        print("  v12 daily-P&L corr: <30 common days — n/a")
    else:
        print(f"  v12 daily-P&L corr: {corr:+.3f} (N={nboth} common days) | "
              f"GC PF on v12-losing days: {pfl:.2f}")
    mgc_usd = {d: r[0] * PT_MGC - COST_MGC for d, r in w["pbd"].items()}
    gm = grade(mgc_usd)
    if gm:
        print(f"  MGC line: net ${gm['net']:+,.0f}  PF {gm['pf']:.2f}  "
              f"avg ${gm['avg']:+,.2f}/tr  IS PF {gm['pfis']:.2f} OOS PF {gm['pfos']:.2f}")


def stage2(days):
    v12 = load_v12()
    print("\n" + "═" * 100)
    print("STAGE 2a — FAMILY B split by direction (continuation | fade), IS-argmax each")
    print("═" * 100)
    thrs = [("pct", 0.08), ("pct", 0.15), ("pct", 0.25), ("pt", 2), ("pt", 4), ("pt", 8)]
    stops = [("imp", 0.5), ("imp", 1.0), ("pct", 0.15)]
    results = {True: [], False: []}
    for thr in thrs:
        for cont in (True, False):
            for sm in stops:
                for tR in (1.0, 2.0, None):
                    for flat in (600, 780):
                        nm = (f"B thr{thr[1]}{'%' if thr[0]=='pct' else 'pt':<2} "
                              f"{'cont' if cont else 'fade'} stop{sm[1]}{'xImp' if sm[0]=='imp' else '%':<4} "
                              f"tgt{str(tR)+'R' if tR else 'time':<4} flat{flat//60}:{flat%60:02d}")
                        spec = dict(thr=thr, cont=cont, sm=sm, tR=tR, flat=flat)
                        results[cont].append((nm, spec, run_B(days, thr, cont, sm, tR, flat)))
    print("  ... grid rebuilt (216)", flush=True)
    for cont in (True, False):
        lab = "CONTINUATION" if cont else "FADE"
        print(f"\n  ── sub-family: {lab} (108 configs)")
        w = rank_family(f"B-{lab.lower()}", results[cont], top=12)
        if not w:
            continue
        g = w["g"]
        # meaning-drift: N and median |impulse| (%) by year for the picked threshold
        thr = w["spec"]["thr"]
        by_y = defaultdict(list)
        for d in w["pbd"]:
            bars = days[d]
            a, b = px_at(bars, 510), px_at(bars, 515)
            if a and b:
                by_y[d.year].append(abs(b[1] - a[1]) / b[1] * 100.0)
        drift = "  meaning drift (traded days): " + "  ".join(
            f"{y}:N={len(v)},med|imp|={sorted(v)[len(v)//2]:.2f}%"
            for y, v in sorted(by_y.items()))
        print(drift)
        if (g["netis"] > 0 and g["netos"] > 0 and g["pfis"] >= 1.10
                and g["pfos"] >= 1.05 and g["n"] >= 80):
            s = w["spec"]
            rerun = lambda: run_B(days, s["thr"], s["cont"], s["sm"], s["tR"],
                                  s["flat"], next_open=True)
            alive = kill_battery(w, days, rerun)
            neighborhood(w, results[cont])
            dossier(w, days, v12)
            print(f"  => B-{lab} pick {'SURVIVES stage 2' if alive else 'KILLED in battery'}")
        else:
            print(f"  => B-{lab} pick fails era bar (IS PF {g['pfis']:.2f}, "
                  f"OOS PF {g['pfos']:.2f}). DEAD.")

    print("\n" + "═" * 100)
    print("STAGE 2b — FAMILY A plateau context (stop 0.5xOR, time exit) — NOT selectable")
    print("═" * 100)
    for orwin in (5, 10, 15):
        for cutoff in (585, 630):
            for flat in (780, 900):
                pbd = run_A(days, orwin, cutoff, ("or", 0.5), None, flat)
                g = grade(to_usd(pbd))
                if g:
                    print(f"  or{orwin:<2} cut{cutoff//60}:{cutoff%60:02d} flat{flat//60}h  "
                          f"N={g['n']:>4}  IS PF {g['pfis']:>5.2f} (${g['netis']:>+8,.0f})  "
                          f"OOS PF {g['pfos']:>5.2f} (${g['netos']:>+8,.0f})")
    print("  -> mixed-sign OOS across adjacent cells = fragile structure, no pick.")

    print("\n" + "═" * 100)
    print("STAGE 2c — named-anomaly graveyard (for the record)")
    print("═" * 100)
    named = [
        ("pre-PM-fix SHORT 9:00->10:00 (no stop)", run_C_pre(days, 540, False, None)),
        ("pre-PM-fix SHORT 9:30->10:00 (no stop)", run_C_pre(days, 570, False, None)),
        ("post-fix FADE 0.1% ->11:00 (no stop)", run_C_post(days, ("pct", 0.10), False, None, 660)),
        ("Asia LONG 20:00->23:55 (no stop)", run_E_drift(days, 1200, True, None)),
        ("Asia LONG 19:00->23:55 (no stop)", run_E_drift(days, 1140, True, None)),
    ]
    for nm, pbd in named:
        g = grade(to_usd(pbd))
        if g:
            print(f"  {nm:<42} N={g['n']:>4}  IS PF {g['pfis']:>5.2f} (${g['netis']:>+8,.0f})  "
                  f"OOS PF {g['pfos']:>5.2f} (${g['netos']:>+8,.0f})")
    print("\n  stage 2 done.")


# ─────────────────────────── main ───────────────────────────

def main():
    args = sys.argv[1:] or ["all"]
    print("Loading GC 1-min (full Globex)...", flush=True)
    days = load_days()
    print(f"  {len(days)} dates, {sum(len(b) for b in days.values()):,} bars", flush=True)
    coverage(days)
    if args == ["cov"]:
        return
    if args == ["stage2"]:
        stage2(days)
        return

    winners = []
    fams = {"A": sweep_A, "B": sweep_B, "C": sweep_C, "D": sweep_D, "E": sweep_E}
    todo = [f for f in "ABCDE" if ("all" in args or f in args)]
    for f in todo:
        w = fams[f](days)
        if w:
            winners.append(w)

    # ── verdict ──
    print("\n" + "═" * 100)
    print("VERDICT STAGE — kill battery + risk/collision/correlation on family winners")
    print("═" * 100)
    v12 = load_v12()
    survivors = []
    for w in winners:
        g = w["g"]
        print(f"\n[{w['fam']}] {w['name']}: N={g['n']}  IS PF {g['pfis']:.2f} "
              f"(${g['netis']:+,.0f})  OOS PF {g['pfos']:.2f} (${g['netos']:+,.0f})")
        if not (g["netis"] > 0 and g["netos"] > 0 and g["pfis"] >= 1.10 and g["pfos"] >= 1.05
                and g["n"] >= 80):
            print("  -> fails era bar outright; no battery needed. DEAD.")
            continue
        alive = kill_battery(w, days, w["rerun"])
        neighborhood(w, w["results"])
        if alive:
            survivors.append(w)

    for w in survivors:
        g = w["g"]
        usd = w["usd"]
        print(f"\n── SURVIVOR DOSSIER: {w['name']}")
        show_year_table(usd, indent="  ")
        # risk fit
        stop_worst = min(r[0] for r in w["pbd"].values()) * PT_GC - COST_GC
        print(f"  worst realized single loss: ${g['worst']:,.0f} GC "
              f"(${g['worst']/10:,.0f} MGC) vs $1,200 DLL")
        print(f"  maxDD ${g['maxdd']:,.0f} GC | NQ book risks $415-565/trade -> "
              f"combined worst-day if stacked: ${abs(g['worst'])+565:,.0f} GC / "
              f"${abs(g['worst'])/10+565:,.0f} with MGC")
        # collision
        tms = sorted(r[1] for r in w["pbd"].values())
        med_tm = tms[len(tms) // 2]
        print(f"  entry times: median {med_tm//60}:{med_tm%60:02d} "
              f"range {tms[0]//60}:{tms[0]%60:02d}-{tms[-1]//60}:{tms[-1]%60:02d}")
        for lab, a, b in NQ_WINDOWS:
            n_in = sum(1 for t in tms if a <= t <= b)
            print(f"    entries inside NQ {lab}: {n_in} ({100*n_in/len(tms):.0f}%)")
        # correlation
        corr, pfl, nboth = corr_v12(usd, v12)
        if corr is None:
            print("  v12 correlation: <30 common days — n/a")
        else:
            print(f"  v12 daily-P&L corr: {corr:+.3f} (N={nboth} common days) | "
                  f"GC PF on v12-losing days: {pfl:.2f}")
        mgc_usd = {d: r[0] * PT_MGC - COST_MGC for d, r in w["pbd"].items()}
        gm = grade(mgc_usd)
        print(f"  MGC line: net ${gm['net']:+,.0f}  PF {gm['pf']:.2f}  "
              f"avg ${gm['avg']:+,.2f}/tr (cost drag {COST_MGC/(abs(gm['avg'])+COST_MGC)*100:.0f}%)"
              if gm else "")

    print("\n" + "═" * 100)
    if survivors:
        b = max(survivors, key=lambda w: w["g"]["pfos"])
        print(f"BEST SURVIVOR: [{b['fam']}] {b['name']} — IS PF {b['g']['pfis']:.2f} / "
              f"OOS PF {b['g']['pfos']:.2f} / ${b['g']['avg']:+,.0f} per trade GC")
    else:
        print("NO SURVIVORS — every family winner failed era bar or kill battery.")
    print("═" * 100 + "\n  gc_structures done.")


if __name__ == "__main__":
    main()
