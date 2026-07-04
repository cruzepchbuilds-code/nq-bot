"""
live/eod_digest.py — nightly one-shot: NT8 Output dump -> journal -> phone digest.

Usage (nightly routine):
  1. Copy the NT8 Output window and save it as a text file (any name, overlap OK).
  2. Run:   python3 live/eod_digest.py path/to/nt8_output.txt
  3. Add --send to also push the digest to Telegram (default is print-only).
  4. Add --post to also print a copy-paste social post (build-in-public lane).
     --journal PATH overrides the journal file (mainly for testing).
"""

import os
import re
import subprocess
import sys
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Reuse the [v12] line formats from the drift meter — single source of truth.
from brain.journal import ENTRY_RE, EXIT_RE

JOURNAL_PATH = os.path.join(REPO_ROOT, "data", "live_journal.txt")
JOURNAL_TOOL = os.path.join(REPO_ROOT, "brain", "journal.py")

TS_RE      = re.compile(r"\[v12\]\s+(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})")
DAYLIFE_RE = re.compile(r"\|\s*day\s+([+-]?[\d,]+)\s*\|\s*life\s+([+-]?[\d,]+)")
RULE = "─" * 26


def usd(v):
    return f"{'+' if v >= 0 else '-'}${abs(v):,.0f}"


def line_ts(line):
    m = TS_RE.search(line)
    return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M") if m else None


def extract_v12_lines(text):
    """All [v12] telemetry lines, right-stripped (handles CRLF from the VPS)."""
    return [ln.rstrip() for ln in text.splitlines() if "[v12]" in ln]


def merge_journal(new_lines):
    """Merge into the cumulative journal: exact-line dedup, chronological order.

    Returns (n_added, merged_lines). Idempotent — rerunning the same dump adds 0.
    """
    existing = []
    if os.path.exists(JOURNAL_PATH):
        with open(JOURNAL_PATH, encoding="utf-8") as f:
            existing = [ln.rstrip() for ln in f if ln.strip()]

    seen, added = set(existing), []
    for ln in new_lines:
        if ln not in seen:
            seen.add(ln)
            added.append(ln)

    merged = existing + added
    # Stable chronological sort; a stray line with no timestamp keeps its
    # neighbor's timestamp so it stays in place.
    keyed, last_ts = [], datetime.min
    for ln in merged:
        ts = line_ts(ln)
        if ts is not None:
            last_ts = ts
        keyed.append((last_ts, ln))
    keyed.sort(key=lambda p: p[0])
    merged = [ln for _, ln in keyed]

    if added or not os.path.exists(JOURNAL_PATH):
        os.makedirs(os.path.dirname(JOURNAL_PATH), exist_ok=True)
        with open(JOURNAL_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(merged) + ("\n" if merged else ""))
    return len(added), merged


def pair_trades(lines):
    """Pair ENTRY/EXIT per signal (same pairing rule as brain/journal.py).

    Returns (closed_trades, open_entries): closed trades carry the day/life
    running totals parsed from the exit line's '| day X | life Y' fields.
    """
    open_entries, trades = {}, []
    for ln in lines:
        m = ENTRY_RE.search(ln)
        if m:
            d, t, sig, dirn, qty, px = m.groups()
            open_entries[sig] = {"date": d, "time": t, "dir": dirn,
                                 "qty": int(qty), "px": float(px)}
            continue
        m = EXIT_RE.search(ln)
        if m:
            d, t, sig, pnl = m.groups()
            e = open_entries.pop(sig, None)
            dl = DAYLIFE_RE.search(ln)
            trades.append({
                "date": d, "time": t, "sig": sig,
                "dir": e["dir"] if e else "?",
                "etime": e["time"] if e else None,
                "pnl": float(pnl),
                "day":  int(dl.group(1).replace(",", "")) if dl else None,
                "life": int(dl.group(2).replace(",", "")) if dl else None,
            })
    return trades, open_entries


def drift_summary(journal_path=None):
    """Fill-drift verdict over the FULL cumulative journal via brain/journal.py.

    The verdict logic lives inside journal.main(), so run it as a subprocess
    and lift the 'avg drift' + 'VERDICT' lines from its stdout.
    """
    try:
        out = subprocess.run([sys.executable, JOURNAL_TOOL,
                              journal_path or JOURNAL_PATH],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ["Drift: unavailable (journal.py failed)"]
    lines = []
    m = re.search(r"resolved trades:\s*(\d+)\s+avg drift:\s*(\$[+-][\d,]+)/trade", out)
    if m:
        lines.append(f"Drift ({m.group(1)} resolved): {m.group(2)}/trade")
    for ln in out.splitlines():
        if ln.startswith("VERDICT:"):
            lines.append(ln.split("VERDICT:", 1)[1].strip())
    return lines or ["Drift: pending (no resolved trades yet)"]


def build_digest(target_date, trades, open_entries, n_added, n_total):
    """Phone-formatted digest for target_date (short lines)."""
    day_trades = [t for t in trades if t["date"] == target_date]
    day_open   = {s: e for s, e in open_entries.items() if e["date"] == target_date}

    out = [f"EOD DIGEST — {target_date} (v12)", RULE]

    if day_trades or day_open:
        for t in day_trades:
            out.append(f"{t['sig']:<8}{t['dir']:<6}{usd(t['pnl']):>9}")
        for sig, e in day_open.items():
            out.append(f"{sig:<8}{e['dir']:<6}{'open':>9}")
        out.append(RULE)
        last = day_trades[-1] if day_trades else None
        day_pnl = (last["day"] if last and last["day"] is not None
                   else sum(t["pnl"] for t in day_trades)) if day_trades else 0
        out.append(f"Day P&L   {usd(day_pnl)}")
    else:
        out.append("No trades today.")
        out.append(RULE)

    life = next((t["life"] for t in reversed(trades) if t["life"] is not None), None)
    if day_trades and day_trades[-1]["life"] is not None:
        life = day_trades[-1]["life"]
    out.append(f"Life P&L  {usd(life)}" if life is not None else "Life P&L  n/a")

    # Asia timing guard (param_stability 07-03: the 18:15 halt-gap signal decays
    # within minutes — entry at 18:17+ collapses the leg's edge, PF 1.63 -> 1.30)
    late = [t for t in day_trades
            if t["sig"] == "ASIA" and t.get("etime") and t["etime"] >= "18:17"]
    late += [e for s, e in day_open.items()
             if s == "ASIA" and e["time"] >= "18:17"]
    if late:
        out.append(RULE)
        out.append("!! ASIA ENTRY LATE (18:17+) — edge dies past")
        out.append("   18:16. Two late fills = disable Asia leg.")

    out.append(RULE)
    out.extend(drift_summary())
    out.append(RULE)
    out.append(f"Journal: +{n_added} new · {n_total} lines")
    return "\n".join(out)


FRIENDLY = {"ORB1": "morning breakout", "ORB2": "morning breakout (re-entry)",
            "REJ": "lunch fade", "PM_ORB": "afternoon breakout",
            "ASIA": "evening gap", "PYR": "add-on"}


def build_post(target_date, trades, merged, journal_path=None):
    """Copy-paste social post for the build-in-public account. Receipts only."""
    session_n = len({m.group(1) for ln in merged for m in [TS_RE.search(ln)] if m})
    day_trades = [t for t in trades if t["date"] == target_date]
    life = next((t["life"] for t in reversed(trades) if t["life"] is not None), None)

    out = [f"Session {session_n} of running my AI-built trading robot "
           f"on a funded account."]
    if day_trades:
        out.append("")
        for t in day_trades:
            name = FRIENDLY.get(t["sig"], t["sig"].lower())
            out.append(f"• {name} {t['dir'].lower()} {usd(t['pnl'])}")
        last = day_trades[-1]
        day_pnl = last["day"] if last["day"] is not None else sum(t["pnl"] for t in day_trades)
        out.append("")
        out.append(f"Day: {usd(day_pnl)}")
    else:
        out.append("")
        out.append("No trades today — the robot found no clean setup, so it sat out.")
        out.append("Sitting out is a position. That's the discipline, not a bug.")
        out.append("")
    if life is not None:
        out.append(f"Since start: {usd(life)}")

    for ln in drift_summary(journal_path):
        if "DRIFT OK" in ln:
            out.append("Robot self-check: live fills match the backtest.")
            break
        if "DRIFT BAD" in ln:
            out.append("Robot self-check flagged fill slippage — not scaling until it clears.")
            break

    out.append("")
    out.append("Wins, losses, dead accounts — all get posted. Receipts only.")
    return "\n".join(out)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    global JOURNAL_PATH
    send_flag = "--send" in args
    if send_flag:
        args.remove("--send")
    post_flag = "--post" in args
    if post_flag:
        args.remove("--post")
    if "--journal" in args:
        i = args.index("--journal")
        JOURNAL_PATH = os.path.abspath(args[i + 1])
        del args[i:i + 2]
    if not args:
        print(__doc__.strip())
        return 1

    try:
        with open(args[0], encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
    except OSError as exc:
        print(f"cannot read {args[0]}: {exc}")
        return 1

    new_lines = extract_v12_lines(text)
    if not new_lines:
        print(f"No [v12] lines found in {args[0]} — nothing merged.")
        return 1

    n_added, merged = merge_journal(new_lines)
    target_date = max(m.group(1) for ln in new_lines for m in [TS_RE.search(ln)] if m)
    trades, open_entries = pair_trades(merged)
    digest = build_digest(target_date, trades, open_entries, n_added, len(merged))
    print(digest)

    if post_flag:
        print("\nPOST-READY (copy everything below the line)")
        print("─" * 44)
        print(build_post(target_date, trades, merged))

    if send_flag:
        from live import telegram_alerts   # lazy: print-only runs never touch config
        safe = (digest.replace("&", "&amp;")
                      .replace("<", "&lt;").replace(">", "&gt;"))
        ok = telegram_alerts.send(safe)
        print("\ntelegram: sent" if ok else "\ntelegram: FAILED (check config/creds)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
