"""
brain/sentinel.py — THE IMMUNE SYSTEM (Empire Pillar 3)

Monthly (or on-demand) health check for the v12 edge stack:
  1. Optionally refreshes NQ 1-min data from Databento (--refresh, ~$0.10/mo)
  2. Recomputes each component's rolling 60-trade PF over its full history
  3. Compares the LATEST rolling window against that component's own
     historical distribution (percentile bands)
  4. Verdicts: HEALTHY (inside bands) / WATCH (below p20) / ALERT (below p05
     or under the kill-switch line PF 1.0)

Usage:
    python3 brain/sentinel.py            # run on existing data
    python3 brain/sentinel.py --refresh  # pull latest month from Databento first

The kill-switch law stays HUMAN: this tool flags, you decide (auto-gating was
tested and loses — brain/research/v12_lab2.py).
"""

import sys, os, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain", "research"))

from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data", "nq_full.csv")
WINDOW = 60          # rolling trades per component
KILL_LINE = 1.00     # the written kill-switch law


def refresh_data():
    import databento as db
    key = None
    env_path = os.path.join(BASE, ".env")
    with open(env_path) as f:
        for line in f:
            if line.startswith("DATABENTO_API_KEY"):
                key = line.strip().split("=", 1)[1]
    client = db.Historical(key)
    # find last date in file
    last = None
    with open(DATA) as f:
        for line in f:
            pass
        last = line.split(",")[0][:10]
    start = last
    end = datetime.utcnow().strftime("%Y-%m-%d")
    if start >= end:
        print(f"  data already current through {last}")
        return
    print(f"  pulling NQ.v.0 {start} -> {end} ...")
    data = client.timeseries.get_range(dataset="GLBX.MDP3", symbols=["NQ.v.0"],
                                       schema="ohlcv-1m", start=start, end=end,
                                       stype_in="continuous")
    df = data.to_df()
    added = 0
    with open(DATA, "a", newline="") as f:
        w = csv.writer(f)
        for ts, row in df.iterrows():
            dt = ts.tz_convert("US/Eastern")
            stamp = dt.strftime("%Y-%m-%d %H:%M:%S")
            if stamp[:10] <= last:
                continue
            w.writerow([stamp, round(float(row["open"]), 2), round(float(row["high"]), 2),
                        round(float(row["low"]), 2), round(float(row["close"]), 2),
                        int(row["volume"])])
            added += 1
    print(f"  appended {added:,} bars")


def rolling_pf(pnls, window=WINDOW):
    out = []
    for i in range(window, len(pnls) + 1):
        w = pnls[i - window:i]
        g = sum(p for p in w if p > 0)
        l = abs(sum(p for p in w if p <= 0))
        out.append(g / l if l else 9.99)
    return out


def pct_rank(series, value):
    if not series:
        return 0.5
    return sum(1 for s in series if s <= value) / len(series)


def main():
    if "--refresh" in sys.argv:
        print("REFRESH:")
        refresh_data()

    print("\nBuilding component trade streams (full history)...", flush=True)
    from eval_boost import build_components
    comp = build_components()

    streams = {}
    for k, label in [("ORB3", "Morning ORB"), ("REJ", "Rejection"),
                     ("PM", "PM ORB"), ("ASIA", "Asia Gap")]:
        rows = []
        for d in sorted(comp[k]):
            for t in comp[k][d]:
                pnl = t[3]
                if k == "ORB3":
                    pass  # already 1c-normalized in build
                rows.append((d, pnl))
        streams[label] = rows

    print(f"\n{'═'*88}")
    print(f"  SENTINEL REPORT — {datetime.now().strftime('%Y-%m-%d %H:%M')}   "
          f"(rolling {WINDOW}-trade PF vs own history)")
    print(f"{'═'*88}")
    print(f"  {'component':<14}{'trades':>7}{'last date':>12}{'roll PF':>9}"
          f"{'hist p20':>10}{'hist p05':>10}{'pct-rank':>10}   verdict")
    print(f"  {'─'*84}")

    worst = "HEALTHY"
    for label, rows in streams.items():
        pnls = [p for _, p in rows]
        if len(pnls) < WINDOW + 20:
            print(f"  {label:<14}{len(pnls):>7}{'—':>12}{'—':>9}{'—':>10}{'—':>10}{'—':>10}   INSUFFICIENT")
            continue
        rp = rolling_pf(pnls)
        hist, current = rp[:-1], rp[-1]
        hs = sorted(hist)
        p20, p05 = hs[len(hs)//5], hs[len(hs)//20]
        pr = pct_rank(hist, current)
        if current < KILL_LINE and current < p05:
            verdict = "ALERT — kill-switch review"
            worst = "ALERT"
        elif current < p20:
            verdict = "WATCH"
            if worst == "HEALTHY":
                worst = "WATCH"
        else:
            verdict = "HEALTHY"
        print(f"  {label:<14}{len(pnls):>7}{str(rows[-1][0]):>12}{current:>9.2f}"
              f"{p20:>10.2f}{p05:>10.2f}{pr:>9.0%}   {verdict}")

    print(f"\n  OVERALL: {worst}")
    print(f"  Law: this tool FLAGS — the human decides. Auto-gating loses (v12_lab2.py: -$8.7k to -$13.7k).")
    print(f"{'═'*88}")


if __name__ == "__main__":
    main()
