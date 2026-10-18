#!/usr/bin/env python3
"""
Cross-check TradingView's screener RSI (what scanner.py alerts on) against RSI
computed independently from SCS Trade's raw daily history.

    python verify_rsi.py                 # a dozen liquid names
    python verify_rsi.py OGDC LUCK SYS   # your own list

For each symbol: SCS Trade daily bars (raw exchange prints, from 2006) →
bonus/split back-adjustment (an overnight drop bigger than 1.5× PSX's daily limit
of 10 % / Re 1 can only be a corporate action — the same rule as the owner's PSX
ATH scanner) → ISO-week and calendar-month closes → Wilder RSI 14 (TradingView's
ta.rsi: SMA seed, then alpha = 1/14) → printed next to the screener's values.
Differences of a few hundredths are rounding; a few tenths on the monthly means
TradingView has a few more years of history or a slightly different split ratio.

Not used by the workflow — a tool for re-validating the feed. Takes ~5–15 s per symbol.
"""
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

PKT = timezone(timedelta(hours=5))
SCS_BASE = "https://www.scstrade.com"
TV_SCAN = "https://scanner.tradingview.com/pakistan/scan"
DEFAULT = ["OGDC", "LUCK", "HBL", "SYS", "AABS", "ZTL", "FFC", "MEBL", "PSO", "HUBC", "TRG", "786"]

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (gfs-psx-scanner verify)"})


def scs_history(sym):
    """Ascending (date, open, close) from SCS Trade, raw prices."""
    end = (datetime.now(PKT) + timedelta(days=1)).strftime("%m/%d/%Y")
    r = session.post(f"{SCS_BASE}/stockscreening/SS_CompanySnapShotHP.aspx/chart",
                     json={"par": sym, "date1": "01/01/1990", "date2": end},
                     headers={"Content-Type": "application/json"}, timeout=(15, 90))
    r.raise_for_status()
    rows = {}
    for x in r.json().get("d") or []:
        m = re.search(r"-?\d+", x.get("trading_Date") or "")
        c = x.get("trading_close")
        if not m or not c or c <= 0:
            continue
        d = datetime.fromtimestamp(int(m.group()) / 1000, tz=timezone.utc).astimezone(PKT).date()
        rows[d] = (d, float(x.get("trading_open") or 0), float(c))
    return [rows[k] for k in sorted(rows)]


def is_corp_action(prev_close, ref):
    return prev_close > 0 and ref > 0 and (prev_close - ref) > 1.5 * max(0.10 * prev_close, 1.0)


def adjusted_closes(rows):
    """Closes with every bar before a corporate-action gap scaled by the gap ratio (newest → oldest)."""
    out, factor, events = [0.0] * len(rows), 1.0, []
    for i in range(len(rows) - 1, -1, -1):
        out[i] = rows[i][2] * factor
        if i > 0:
            prev_c, ref = rows[i - 1][2], (rows[i][1] if rows[i][1] > 0 else rows[i][2])
            if is_corp_action(prev_c, ref):
                factor *= ref / prev_c
                events.append((rows[i][0].isoformat(), round(ref / prev_c, 4)))
    return out, events


def resample(rows, closes, key):
    out, cur = [], None
    for r, c in zip(rows, closes):
        k = key(r[0])
        if k != cur:
            out.append(c)
            cur = k
        else:
            out[-1] = c
    return out


def rsi(closes, length=14):
    if len(closes) < length + 1:
        return None
    gains = losses = 0.0
    for i in range(1, length + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_gain, avg_loss = gains / length, losses / length
    for i in range(length + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (length - 1) + max(d, 0.0)) / length
        avg_loss = (avg_loss * (length - 1) + max(-d, 0.0)) / length
    return 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def screener():
    cols = ["name", "time", "close", "RSI", "RSI|1W", "RSI|1M"]
    body = {"columns": cols, "filter": [{"left": "type", "operation": "equal", "right": "stock"}], "range": [0, 5000]}
    r = session.post(TV_SCAN, json=body, timeout=(15, 60))
    r.raise_for_status()
    return {x["d"][0]: dict(zip(cols, x["d"])) for x in r.json().get("data") or []}


def f(v):
    return "  n/a" if v is None else f"{v:5.2f}"


def main():
    syms = [s.upper() for s in sys.argv[1:]] or DEFAULT
    tv = screener()
    print(f"{'sym':7} {'bars':>5} {'first':10}  {'TV D':>5} {'own D':>5}  {'TV W':>5} {'own W':>5}  {'TV M':>5} {'own M':>5}  events")
    for sym in syms:
        t0 = time.time()
        try:
            rows = scs_history(sym)
        except Exception as e:
            print(f"{sym:7} SCS Trade error {type(e).__name__}")
            continue
        if not rows:
            print(f"{sym:7} no SCS Trade history")
            continue
        adj, events = adjusted_closes(rows)
        own_d = rsi(adj)
        own_w = rsi(resample(rows, adj, lambda d: d.isocalendar()[:2]))
        own_m = rsi(resample(rows, adj, lambda d: (d.year, d.month)))
        s = tv.get(sym) or {}
        print(f"{sym:7} {len(rows):5d} {rows[0][0].isoformat()}  {f(s.get('RSI'))} {f(own_d)}  "
              f"{f(s.get('RSI|1W'))} {f(own_w)}  {f(s.get('RSI|1M'))} {f(own_m)}  "
              f"{events[:3]}{' …' if len(events) > 3 else ''}  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
