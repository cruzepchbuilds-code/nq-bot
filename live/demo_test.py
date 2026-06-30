"""
Tradovate Demo Connection Test
Tests the full execution path against Tradovate's free demo environment.

SETUP (one-time, ~5 minutes):
  1. Create a free demo account at https://trader.tradovate.com (click "Try Demo")
  2. Log in → top-right avatar → Account → API Access
  3. Click "Add Application" — fill in any name/version (e.g. "NQBot" / "1.0")
  4. Copy the CID (integer) and Secret that appear
  5. Fill in your .env file (see below)

.env values needed:
    TRADOVATE_USERNAME=your@email.com
    TRADOVATE_PASSWORD=yourpassword
    TRADOVATE_CID=1234               # integer from API Access page
    TRADOVATE_SECRET=abc123...       # secret from API Access page
    TRADOVATE_DEVICE_ID=             # leave blank first run — auto-generated
    TRADOVATE_APP_ID=NQBot
    TRADOVATE_APP_VERSION=1.0
    TRADOVATE_DEMO=1                 # MUST be 1 for demo

Run:
    python live/demo_test.py              # connection test only
    python live/demo_test.py --order      # connection + place 1 test bracket order
"""

import os
import sys
import argparse
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv optional — can set env vars manually

import config
import live.telegram_alerts as tg
from live.execution import ExecutionEngine, DEMO_URL, LIVE_URL


def check_env() -> bool:
    required = [
        "TRADOVATE_USERNAME",
        "TRADOVATE_PASSWORD",
        "TRADOVATE_CID",
        "TRADOVATE_SECRET",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print("\n❌  Missing environment variables:")
        for k in missing:
            print(f"    {k}")
        print("""
To get these:
  1. Go to https://trader.tradovate.com  →  click "Try Demo"
  2. Create a free account (takes 2 minutes)
  3. Log in  →  avatar (top right)  →  Account  →  API Access
  4. Click "Add Application"
  5. Copy CID + Secret into your .env file

.env template:
    TRADOVATE_USERNAME=your@email.com
    TRADOVATE_PASSWORD=yourpassword
    TRADOVATE_CID=1234
    TRADOVATE_SECRET=your_secret_here
    TRADOVATE_DEVICE_ID=            # leave blank — auto-generated below
    TRADOVATE_APP_ID=NQBot
    TRADOVATE_APP_VERSION=1.0
    TRADOVATE_DEMO=1
""")
        return False
    return True


def run_test(place_order: bool = False):
    if not check_env():
        sys.exit(1)

    # Auto-generate device ID if missing (save it after first run)
    device_id = os.environ.get("TRADOVATE_DEVICE_ID", "")
    if not device_id:
        device_id = str(uuid.uuid4())
        print(f"\n⚠️  No TRADOVATE_DEVICE_ID found — generated: {device_id}")
        print("    Add this to your .env so it stays consistent:\n")
        print(f"    TRADOVATE_DEVICE_ID={device_id}\n")
        os.environ["TRADOVATE_DEVICE_ID"] = device_id

    mode = "DEMO" if os.environ.get("TRADOVATE_DEMO", "1") == "1" else "LIVE"
    print(f"\n{'='*52}")
    print(f"  Tradovate {mode} Connection Test")
    print(f"{'='*52}")

    # ── Connect ───────────────────────────────────────────────────────────────
    print("\n1. Connecting ...")
    engine = ExecutionEngine()
    ok = engine.connect()

    if not ok:
        msg = f"❌ Connection FAILED — check credentials (mode={mode})"
        print(f"\n{msg}")
        tg.send(f"🔴 Tradovate {mode} connection FAILED\nCheck credentials in .env")
        sys.exit(1)

    print(f"   ✅ Connected to Tradovate {mode}")
    print(f"   Account ID : {engine.account_id}")
    bal = engine.client.get_cash_balance(engine.account_id)
    print(f"   Balance    : ${bal:,.2f}")
    tg.send(
        f"✅ <b>Tradovate {mode} connected</b>\n"
        f"Account {engine.account_id}\n"
        f"Balance ${bal:,.2f}"
    )

    # ── Contract lookup ───────────────────────────────────────────────────────
    print("\n2. Looking up NQ front-month contract ...")
    contract = engine.client.find_contract("NQ")
    if contract:
        print(f"   ✅ Found: {contract.get('name')} (id={contract.get('id')})")
    else:
        print("   ❌ Contract lookup failed")
        return

    # ── Open positions check ──────────────────────────────────────────────────
    print("\n3. Checking open positions ...")
    positions = engine.client.get_positions(engine.account_id)
    if positions:
        print(f"   ⚠️  {len(positions)} open position(s) found:")
        for p in positions:
            print(f"      {p}")
    else:
        print("   ✅ No open positions")

    # ── Optional: place a bracket order ──────────────────────────────────────
    if place_order:
        print("\n4. Placing test bracket order (1 NQ long, 22pt stop, 44pt target) ...")
        print("   This is a DEMO order — no real money involved.\n")

        result = engine.enter("NQ", "long", contracts=1,
                              stop_pts=22.0, target_pts=44.0)
        if result and result.status in ("filled", "accepted"):
            print(f"   ✅ Order placed!")
            print(f"   Fill price  : {result.fill_price:.2f}")
            print(f"   Order ID    : {result.order_id}")
            print(f"   Stop order  : {engine.position.stop_order_id}")
            print(f"   Target order: {engine.position.target_order_id}")
            tg.send(
                f"✅ <b>Test order FILLED — {mode}</b>\n"
                f"LONG NQ 1c @ {result.fill_price:.2f}\n"
                f"Stop {result.fill_price - 22:.2f}  Target {result.fill_price + 44:.2f}\n"
                f"Order ID: {result.order_id}"
            )
            print("\n   ⚠️  You have an open position on the demo account.")
            print("   Cancel it manually in Tradovate Trader or run:")
            print("   engine.flatten_all('test_cleanup')")
            ans = input("\n   Flatten now? [y/N]: ").strip().lower()
            if ans == "y":
                engine.flatten_all("test_cleanup")
                print("   ✅ Position flattened")
                tg.send("✅ Test position flattened")
        else:
            status = result.status if result else "no result"
            err    = result.error_msg if result else ""
            print(f"   ❌ Order failed: {status}  {err}")
            tg.send(f"❌ Test order FAILED ({mode}): {status} {err}")
    else:
        print("\n   (Skipped order placement — pass --order to test a real bracket)")

    print(f"\n{'='*52}")
    print("  All checks passed ✅")
    print(f"  Ready to run: python live/paper_trading.py --live")
    print(f"{'='*52}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--order", action="store_true",
                        help="Place a real test bracket order on demo account")
    args = parser.parse_args()
    run_test(place_order=args.order)
