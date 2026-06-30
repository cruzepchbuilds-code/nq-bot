"""
Download ES and additional NQ data from Databento.
Runs non-interactively.
"""
import databento as db
import csv
import os
import sys

API_KEY = os.environ.get("DATABENTO_API_KEY", "")

def to_eastern(ts):
    """Convert timestamp to Eastern time."""
    if hasattr(ts, 'tz_convert'):
        return ts.tz_convert("US/Eastern")
    return ts

def download_and_save(client, symbol, start, end, output_path, label):
    print(f"\n[{label}] Downloading {symbol} {start} -> {end}...")
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
    try:
        data = client.timeseries.get_range(
            dataset="GLBX.MDP3",
            symbols=[symbol],
            schema="ohlcv-1m",
            start=start,
            end=end,
            stype_in="continuous",
        )
        df = data.to_df()
        print(f"  Got {len(df):,} raw bars")
        rows = 0
        with open(output_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for ts, row in df.iterrows():
                dt = to_eastern(ts)
                w.writerow([
                    dt.strftime("%Y-%m-%d %H:%M:%S"),
                    round(float(row.get("open", 0)), 2),
                    round(float(row.get("high", 0)), 2),
                    round(float(row.get("low", 0)), 2),
                    round(float(row.get("close", 0)), 2),
                    int(row.get("volume", 0)),
                ])
                rows += 1
        print(f"  Saved {rows:,} bars -> {output_path}")
        return True
    except Exception as e:
        print(f"  ERROR: {e}")
        return False

def merge_nq(nq_old_path, nq_new_path, output_path):
    """Merge two NQ CSVs (old=2022-2023, new=2024-2026) into one sorted file."""
    print(f"\nMerging NQ files into {output_path}...")
    rows = []
    for p in [nq_old_path, nq_new_path]:
        with open(p) as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
    rows.sort(key=lambda r: r["timestamp"])
    # Deduplicate by timestamp
    seen = set()
    deduped = []
    for r in rows:
        if r["timestamp"] not in seen:
            seen.add(r["timestamp"])
            deduped.append(r)
    with open(output_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp","open","high","low","close","volume"])
        w.writeheader()
        w.writerows(deduped)
    print(f"  Merged {len(deduped):,} bars ({len(rows)-len(deduped)} duplicates removed)")
    return True

def main():
    client = db.Historical(API_KEY)

    # 1. ES 2022-2026
    es_ok = download_and_save(
        client, "ES.c.0", "2022-01-01", "2026-06-12",
        "data/es_1min.csv", "ES 2022-2026"
    )

    # 2. NQ 2022-2023
    nq_old_ok = download_and_save(
        client, "NQ.c.0", "2022-01-01", "2024-01-01",
        "data/nq_2022_2023.csv", "NQ 2022-2023"
    )

    # 3. Merge NQ files
    if nq_old_ok and os.path.exists("data/nq_1min.csv"):
        merge_nq("data/nq_2022_2023.csv", "data/nq_1min.csv", "data/nq_full.csv")
    else:
        print("Skipping NQ merge (missing files)")

    print("\nDone.")
    if es_ok:
        print("  ES data:    data/es_1min.csv")
    if nq_old_ok:
        print("  NQ 2022-23: data/nq_2022_2023.csv")
    if os.path.exists("data/nq_full.csv"):
        print("  NQ full:    data/nq_full.csv (2022-2026)")

if __name__ == "__main__":
    main()
