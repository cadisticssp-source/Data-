#!/usr/bin/env python3
"""
Myfxbook Community Outlook logger.

Logs long/short positioning for chosen symbols to a CSV at a fixed interval,
aligned to the clock (e.g. :00, :15, :30, :45) so rows line up with chart candles.

Setup:
    pip install requests
    export MYFXBOOK_EMAIL="you@example.com"
    export MYFXBOOK_PASSWORD="your-password"
    python collector.py --symbols EURUSD --interval 15

Limits (Myfxbook API): free = 100 requests / 24h, paid = 2880 / 24h.
15 min = 96 requests/day, which is the fastest sustainable rate on a free account.
Leave it running (tmux, screen, systemd, a cheap VPS, a Raspberry Pi) - history
is NOT available from the API, so data only exists from the moment you start logging.
"""
import argparse
import csv
import datetime as dt
import os
import sys
import time

import requests

API = "https://www.myfxbook.com/api"
FIELDS = [
    "timestamp_utc", "symbol", "short_pct", "long_pct", "short_vol", "long_vol",
    "short_pos", "long_pos", "avg_short_price", "avg_long_price",
]


def login(email, password):
    r = requests.get(f"{API}/login.json", params={"email": email, "password": password}, timeout=30)
    d = r.json()
    if d.get("error"):
        raise RuntimeError(f"Login failed: {d.get('message')}")
    return d["session"]


def logout(session):
    try:
        requests.get(f"{API}/logout.json", params={"session": session}, timeout=15)
    except requests.RequestException:
        pass


def fetch_outlook(session):
    r = requests.get(f"{API}/get-community-outlook.json", params={"session": session}, timeout=30)
    d = r.json()
    if d.get("error"):
        raise RuntimeError(d.get("message", "unknown API error"))
    return d["symbols"]


def append_rows(path, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    new_file = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerows(rows)


def seconds_until_next_slot(interval_s):
    now = time.time()
    return (interval_s - (now % interval_s)) % interval_s or interval_s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="EURUSD", help="comma-separated, e.g. EURUSD,GBPUSD")
    ap.add_argument("--interval", type=float, default=15, help="minutes between readings (default 15)")
    ap.add_argument("--out", default="outlook_log.csv")
    ap.add_argument("--paid", action="store_true", help="paid plan (allows >100 requests/day)")
    ap.add_argument("--once", action="store_true",
                    help="take one reading and exit (for cron / GitHub Actions / cloud schedulers)")
    args = ap.parse_args()

    email, pw = os.environ.get("MYFXBOOK_EMAIL"), os.environ.get("MYFXBOOK_PASSWORD")
    if not email or not pw:
        sys.exit("Set MYFXBOOK_EMAIL and MYFXBOOK_PASSWORD environment variables.")

    interval_s = args.interval * 60
    per_day = 86400 / interval_s
    limit = 2880 if args.paid else 100
    if not args.once and per_day > limit:
        sys.exit(f"{per_day:.0f} requests/day exceeds the {limit}/day limit. Use a longer --interval.")

    wanted = {s.strip().upper() for s in args.symbols.split(",")}
    session = login(email, pw)
    print(f"Logged in. Logging {sorted(wanted)} every {args.interval:g} min -> {args.out}")

    try:
        while True:
            if not args.once:
                time.sleep(seconds_until_next_slot(interval_s))
            ts = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
            try:
                try:
                    symbols = fetch_outlook(session)
                except RuntimeError as e:
                    if "session" in str(e).lower():  # expired -> log in again once
                        session = login(email, pw)
                        symbols = fetch_outlook(session)
                    else:
                        raise
                rows = [{
                    "timestamp_utc": ts, "symbol": s["name"],
                    "short_pct": s["shortPercentage"], "long_pct": s["longPercentage"],
                    "short_vol": s["shortVolume"], "long_vol": s["longVolume"],
                    "short_pos": s["shortPositions"], "long_pos": s["longPositions"],
                    "avg_short_price": s.get("avgShortPrice"), "avg_long_price": s.get("avgLongPrice"),
                } for s in symbols if s["name"].upper() in wanted]
                append_rows(args.out, rows)
                for r in rows:
                    print(f"{ts}  {r['symbol']}  long {r['long_pct']}% / short {r['short_pct']}%")
            except Exception as e:  # keep running through transient errors
                print(f"{ts}  ERROR: {e}", file=sys.stderr)
                if args.once:
                    sys.exit(1)
            if args.once:
                break
    except KeyboardInterrupt:
        pass
    finally:
        logout(session)
        print("Logged out.")


if __name__ == "__main__":
    main()
