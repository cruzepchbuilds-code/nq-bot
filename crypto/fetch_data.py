"""
crypto/fetch_data.py

Downloads BTC/USDT 1-minute OHLCV + 8h funding rate history from Binance.
Run this once before any research scripts.

    pip install ccxt
    python crypto/fetch_data.py

Output:
    crypto/data/btc_1min.csv
    crypto/data/btc_funding.csv
"""

import csv, time, os
from datetime import datetime, timezone

try:
    import ccxt
except ImportError:
    raise SystemExit("Run: pip install ccxt")

SYMBOL_SPOT = "BTC/USDT"
SYMBOL_PERP = "BTC/USDT"
START_DATE  = "2022-01-01"
END_DATE    = "2026-07-01"
OUT_OHLCV   = "crypto/data/btc_1min.csv"
OUT_FUNDING = "crypto/data/btc_funding.csv"


def _to_ms(date_str):
    return int(datetime.fromisoformat(date_str)
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


def fetch_ohlcv(exchange, symbol, since_ms, end_ms, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = []
    while since_ms < end_ms:
        chunk = exchange.fetch_ohlcv(symbol, "1m", since=since_ms, limit=1000)
        if not chunk:
            break
        rows.extend(chunk)
        since_ms = chunk[-1][0] + 60_000
        time.sleep(0.12)
        print(f"\r  {len(rows):,} bars...", end="", flush=True)
    print()
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for r in rows:
            dt = datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc)
            w.writerow([dt.strftime("%Y-%m-%d %H:%M:%S"),
                        r[1], r[2], r[3], r[4], r[5]])
    print(f"  Saved {len(rows):,} bars → {path}")


def fetch_funding(exchange, symbol, since_ms, end_ms, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = []
    while since_ms < end_ms:
        chunk = exchange.fetch_funding_rate_history(
            symbol, since=since_ms, limit=1000)
        if not chunk:
            break
        rows.extend(chunk)
        since_ms = int(chunk[-1]["timestamp"]) + 1
        time.sleep(0.12)
        print(f"\r  {len(rows):,} funding rows...", end="", flush=True)
    print()
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "rate"])
        for r in rows:
            dt = datetime.fromtimestamp(r["timestamp"] / 1000, tz=timezone.utc)
            w.writerow([dt.strftime("%Y-%m-%d %H:%M:%S"), r["fundingRate"]])
    print(f"  Saved {len(rows):,} rows → {path}")


if __name__ == "__main__":
    since = _to_ms(START_DATE)
    end   = _to_ms(END_DATE)

    spot = ccxt.binance()
    perp = ccxt.binance({"options": {"defaultType": "future"}})

    print(f"Fetching BTC/USDT 1-min spot ({START_DATE} → {END_DATE})...")
    print("  This will take ~15-20 min for 4.5 years of 1-min data.")
    fetch_ohlcv(spot, SYMBOL_SPOT, since, end, OUT_OHLCV)

    print(f"Fetching BTC/USDT 8h funding rate history...")
    fetch_funding(perp, SYMBOL_PERP, since, end, OUT_FUNDING)

    print("\nDone. Run: python crypto/research/sweep.py")
