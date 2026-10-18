#!/usr/bin/env python3
"""
GFS RSI scanner for PSX — the GFS multi-timeframe RSI screener applied to every
Pakistan Stock Exchange equity, with Telegram alerts.

Bullish GFS : monthly RSI > 60  AND  weekly RSI > 60  AND  daily RSI <= 40
Bearish GFS : monthly RSI < 40  AND  weekly RSI < 40  AND  daily RSI >= 60

Data: TradingView's Pakistan screener. One POST returns, for every PSX common
stock, the last session bar and TradingView's own 14-period RSI on the daily,
weekly and monthly charts — the exact numbers a chart shows after the close
(bonus/split-adjusted history). Weekly and monthly RSI are on the forming
candle, daily on the closed session candle: the same "M/W live, D closed" basis
as the crypto gfs-scanner. Verified against RSI computed from SCS Trade raw
history on 2026-10-18 (verify_rsi.py, CLAUDE.md).

A cheap SCS Trade probe of a bellwether (OGDC, …) decides whether a missing
"today" bar means a PSX holiday or a screener that has not updated yet.

Runs Mon–Fri at 18:00 PKT on GitHub Actions. Configuration is via environment
variables — see CLAUDE.md. Telegram credentials come ONLY from env vars.
"""
import collections
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime

import requests

try:                                              # Windows consoles default to a legacy code page
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


# ── Config (env) ───────────────────────────────────────────────────
def _env_float(name, default):
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else float(default)
    except ValueError:
        return float(default)


def _env_int(name, default):
    return int(_env_float(name, default))


# Strategy thresholds — same rule and env names as the crypto gfs-scanner (CLAUDE.md: do not change unless asked)
BULL_M_MIN = _env_float("GFS_BULL_M_MIN", 60)     # monthly RSI must be ABOVE this
BULL_W_MIN = _env_float("GFS_BULL_W_MIN", 60)     # weekly  RSI must be ABOVE this
BULL_D_MAX = _env_float("GFS_BULL_D_MAX", 40)     # daily   RSI must be AT OR BELOW this
BEAR_M_MAX = _env_float("GFS_BEAR_M_MAX", 40)     # monthly RSI must be BELOW this
BEAR_W_MAX = _env_float("GFS_BEAR_W_MAX", 40)     # weekly  RSI must be BELOW this
BEAR_D_MIN = _env_float("GFS_BEAR_D_MIN", 60)     # daily   RSI must be AT OR ABOVE this
RSI_LENGTH = 14                                   # TradingView's screener exposes the 14-period RSI only

MAX_STALE_DAYS = _env_int("PSX_MAX_STALE_DAYS", 10)      # stock must have traded within N calendar days of the session
MIN_AVG_VOLUME = _env_float("PSX_MIN_AVG_VOLUME", 0)     # 10-day average shares/day floor (0 = every equity)
SESSION_WAIT   = _env_float("PSX_SESSION_WAIT", 1800)    # s to wait for the screener to show today's bar on a trading day
SESSION_POLL   = _env_float("PSX_SESSION_POLL", 300)     # s between screener re-fetches while waiting
MAX_SYMBOLS    = _env_int("MAX_SYMBOLS", 0)              # smoke tests only: scan just the N largest stocks (0 = all)
DRY_RUN        = os.environ.get("DRY_RUN", "0").strip() == "1"
RESEND         = os.environ.get("PSX_RESEND", "0").strip() == "1"   # alert again for a session already in state.json

STATE_FILE     = os.environ.get("STATE_FILE", "state.json")
ALERTS_ARCHIVE = os.environ.get("ALERTS_ARCHIVE", "alerts_archive.txt")
MATCHES_LOG    = os.environ.get("MATCHES_LOG", "matches.jsonl")

TV_SCAN  = "https://scanner.tradingview.com/pakistan/scan"
SCS_BASE = "https://www.scstrade.com"
PKT = timezone(timedelta(hours=5))
SESSION_CUTOFF = dtime(16, 45)                    # PKT: PSX closes 15:30 Mon–Thu / 16:30 Fri; after this today's bar is final
BELLWETHERS = ("OGDC", "MEBL", "HUBC", "PSO")     # any of these trades every session
MIN_ROWS = 100                                    # fewer screener rows than this = the feed is broken, keep the previous list
_SHARE_CLASS_SUFFIXES = ("CPS", "PRS", "PS", "R")  # preference / rights tickers of a listed company (GCWLPRS, EFULR)

# Screener columns. `RSI` is the daily chart's RSI 14 on the last bar; `RSI|1W` / `RSI|1M`
# are the weekly / monthly charts' RSI 14 on their forming candle. None = too little history.
TV_COLUMNS = ["name", "description", "type", "subtype", "time", "close", "volume",
              "RSI", "RSI|1W", "RSI|1M", "market_cap_basic", "average_volume_10d_calc"]

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (gfs-psx-scanner)", "Accept": "application/json"})


def now_pkt():
    return datetime.now(PKT)


def expected_session(now=None):
    """The latest weekday whose session has closed by `now` (PKT): today after SESSION_CUTOFF, else the previous weekday.

    Everything is judged against this, never against the run's own calendar date:
    GitHub's cron drifts hours late (past midnight PKT on the owner's other repos).
    """
    now = now or now_pkt()
    d = now.date() if now.time() >= SESSION_CUTOFF else now.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


# ── TradingView screener (universe + bars + RSI) ───────────────────
def tradingview_scan(tries=3):
    """{symbol: row} for every PSX common stock: last session bar plus RSI on D / W / M.

    `time` is the last bar's open time (UTC); its PKT date is the session the close
    belongs to. Rows without a bar, a close or a symbol are dropped. Network errors,
    429 and 5xx are retried `tries` times and then raised.
    """
    body = {"columns": TV_COLUMNS, "filter": [{"left": "type", "operation": "equal", "right": "stock"}],
            "range": [0, 5000], "sort": {"sortBy": "name", "sortOrder": "asc"}}
    err = None
    for attempt in range(tries):
        try:
            r = session.post(TV_SCAN, json=body, timeout=(15, 60))
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
            r.raise_for_status()
            data = r.json().get("data") or []
            break
        except (requests.RequestException, ValueError) as e:
            err = e
            if attempt < tries - 1:
                time.sleep(5.0 * (attempt + 1))
    else:
        raise err
    rows = {}
    for x in data:
        d = x.get("d") or []
        if len(d) < len(TV_COLUMNS):
            continue
        v = dict(zip(TV_COLUMNS, d))
        sym, close, t = v["name"], v["close"], v["time"]
        if v["type"] != "stock" or v["subtype"] != "common" or not sym or not t or not close or close <= 0:
            continue
        rows[sym] = {
            "symbol": sym, "name": (v["description"] or "").strip(),
            "bar_date": datetime.fromtimestamp(int(t), tz=timezone.utc).astimezone(PKT).date(),
            "close": float(close), "volume": int(v["volume"] or 0),
            "avg_volume": float(v["average_volume_10d_calc"] or 0), "mcap": v["market_cap_basic"],
            "rsi_d": v["RSI"], "rsi_w": v["RSI|1W"], "rsi_m": v["RSI|1M"],
        }
    return rows


def rank_by_mcap(rows):
    """#1 = largest market cap. Stocks TradingView has no market cap for are unranked (None)."""
    ranked = sorted((r for r in rows.values() if r["mcap"]), key=lambda r: -r["mcap"])
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    for r in rows.values():
        r.setdefault("rank", None)


def is_share_class(sym, universe):
    """True for a preference / rights ticker of a company already in the universe (GCWLPRS → GCWL, EFULR → EFUL)."""
    for suf in _SHARE_CLASS_SUFFIXES:
        if sym.endswith(suf) and len(sym) > len(suf) and sym[:-len(suf)] in universe:
            return True
    return False


# ── Session date ───────────────────────────────────────────────────
def tv_session_date(rows, min_rows=50):
    """Most common bar date across the screener rows, if at least `min_rows` share it."""
    if not rows:
        return None
    d, n = collections.Counter(r["bar_date"] for r in rows.values()).most_common(1)[0]
    return d if n >= min_rows else None


def scs_traded(day):
    """True / False = a bellwether has / has no SCS Trade bar for `day`; None = SCS Trade unreachable."""
    start = (day - timedelta(days=14)).strftime("%m/%d/%Y")
    end = (day + timedelta(days=1)).strftime("%m/%d/%Y")
    for sym in BELLWETHERS:
        try:
            r = session.post(f"{SCS_BASE}/stockscreening/SS_CompanySnapShotHP.aspx/chart",
                             json={"par": sym, "date1": start, "date2": end},
                             headers={"Content-Type": "application/json"}, timeout=(15, 60))
            r.raise_for_status()
            data = r.json().get("d") or []
        except (requests.RequestException, ValueError) as e:
            print(f"  SCS Trade probe {sym}: {type(e).__name__}")
            continue
        dates = set()
        for x in data:
            m = re.search(r"-?\d+", x.get("trading_Date") or "")
            if m and (x.get("trading_close") or 0) > 0:
                dates.add(datetime.fromtimestamp(int(m.group()) / 1000, tz=timezone.utc).astimezone(PKT).date())
        if dates:
            return day in dates
    return None


def resolve_session(rows, expected):
    """(rows, session_date, status) with status "ok" | "holiday" | "stale" | "unconfirmed".

    `expected` is the session that should have closed by now. When the screener's
    latest session is older, SCS Trade decides: no bar for `expected` there either →
    PSX holiday. A bar there → the screener is late: re-fetch it every SESSION_POLL s
    for up to SESSION_WAIT s, then give up as "stale". SCS Trade unreachable → same
    wait, then "unconfirmed" (most likely a holiday; the message says so).
    """
    sess = tv_session_date(rows)
    if sess is None or sess >= expected:
        return rows, sess, "ok"
    deadline = time.time() + SESSION_WAIT
    while True:
        scs = scs_traded(expected)
        if scs is False:
            return rows, sess, "holiday"
        if time.time() >= deadline:
            return rows, sess, ("stale" if scs else "unconfirmed")
        print(f"  screener bars are from {sess}; SCS Trade {'has' if scs else 'cannot confirm'} the {expected} bar"
              f" — re-fetching in {SESSION_POLL:.0f}s")
        time.sleep(SESSION_POLL)
        try:
            fresh = tradingview_scan()
        except Exception as e:
            print(f"  screener re-fetch failed ({type(e).__name__})")
            continue
        if len(fresh) >= MIN_ROWS:
            rows, sess = fresh, tv_session_date(fresh)
            rank_by_mcap(rows)
            if sess is not None and sess >= expected:
                return rows, sess, "ok"


# ── Strategy ───────────────────────────────────────────────────────
def gfs_side(rsi_m, rsi_w, rsi_d):
    if rsi_m > BULL_M_MIN and rsi_w > BULL_W_MIN and rsi_d <= BULL_D_MAX:
        return "bull"
    if rsi_m < BEAR_M_MAX and rsi_w < BEAR_W_MAX and rsi_d >= BEAR_D_MIN:
        return "bear"
    return None


def scan(rows, sess):
    """Apply the rule to every row. Returns (results, skip counts) — skips go to the job log only."""
    results, skipped = [], {"share_class": 0, "stale": 0, "history": 0, "volume": 0}
    universe = set(rows)
    for sym in sorted(rows):
        r = rows[sym]
        if is_share_class(sym, universe):
            skipped["share_class"] += 1
        elif (sess - r["bar_date"]).days > MAX_STALE_DAYS:
            skipped["stale"] += 1                                          # suspended / not traded lately
        elif r["rsi_d"] is None or r["rsi_w"] is None or r["rsi_m"] is None:
            skipped["history"] += 1                                        # fewer than 15 candles on some timeframe
        elif MIN_AVG_VOLUME and r["avg_volume"] < MIN_AVG_VOLUME:
            skipped["volume"] += 1
        else:
            results.append({**r, "side": gfs_side(r["rsi_m"], r["rsi_w"], r["rsi_d"])})
    return results, skipped


# ── State / logs ───────────────────────────────────────────────────
def load_state(path=STATE_FILE):
    st = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
    st.setdefault("bull", {})
    st.setdefault("bear", {})
    return st


def save_state(state, path=STATE_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def append_archive(text, path=ALERTS_ARCHIVE):
    divider = ("\n" + "=" * 50 + "\n\n") if os.path.exists(path) and os.path.getsize(path) else ""
    with open(path, "a", encoding="utf-8") as f:
        f.write(divider + text.rstrip() + "\n")


def log_matches(matches, sess, path=MATCHES_LOG):
    with open(path, "a", encoding="utf-8") as f:
        for m in matches:
            f.write(json.dumps({
                "session": sess.isoformat(), "side": m["side"], "symbol": m["symbol"], "name": m["name"],
                "rank": m["rank"], "rsi_m": round(m["rsi_m"], 2), "rsi_w": round(m["rsi_w"], 2),
                "rsi_d": round(m["rsi_d"], 2), "close": m["close"], "volume": m["volume"],
                "first_seen": m["first_seen"], "new": m["new"],
            }) + "\n")


# ── Telegram + formatting ──────────────────────────────────────────
def _chunks(text, limit=4000):
    """Split on line boundaries so no entry is cut in half (Telegram max 4096)."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + 1 + len(line) > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts


TELEGRAM_ATTEMPTS = 4          # per message part; waits 5/10/15 s between attempts (or Telegram's retry_after)


def send_telegram(text):
    """Post the message in ≤4000-char parts, retrying network errors, 429 and 5xx on a fresh connection.

    4xx other than 429 (bad token / chat id) is raised immediately, not retried.
    """
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for part in _chunks(text):
        payload = {"chat_id": chat_id, "text": part, "disable_web_page_preview": True}
        for attempt in range(1, TELEGRAM_ATTEMPTS + 1):
            wait = 5.0 * attempt
            try:
                resp = requests.post(url, json=payload, timeout=(10, 30))
                if resp.status_code < 400:
                    break
                if resp.status_code == 429 or resp.status_code >= 500:
                    try:
                        wait = max(wait, float(resp.json()["parameters"]["retry_after"]))
                    except (ValueError, KeyError, TypeError):
                        pass
                    problem = f"HTTP {resp.status_code}"
                else:
                    resp.raise_for_status()
            except (requests.ConnectionError, requests.Timeout) as e:
                problem = type(e).__name__
            if attempt == TELEGRAM_ATTEMPTS:
                raise RuntimeError(f"Telegram sendMessage failed after {TELEGRAM_ATTEMPTS} attempts ({problem})")
            print(f"  Telegram send failed ({problem}) — retrying in {wait:.0f}s")
            time.sleep(wait)


def fmt_price(p):
    return f"{p:,.2f}"


def fmt_match(m):
    new = "🆕 " if m["new"] else ""
    rank = f"  #{m['rank']}" if m["rank"] else ""
    age = "" if m["new"] else f"  ({m['days']}d)"
    return (f"  • {new}{m['symbol']} [PSX]  "
            f"M {m['rsi_m']:.0f} · W {m['rsi_w']:.0f} · D {m['rsi_d']:.0f}"
            f"  @ {fmt_price(m['close'])}{rank}{age}")


def fmt_section(title, matches):
    if not matches:
        return f"{title}: none"
    return "\n".join([f"{title} ({len(matches)}):"] + [fmt_match(m) for m in matches])


def rules_line():
    return (f"Rules: 🟢 M>{BULL_M_MIN:g} W>{BULL_W_MIN:g} D≤{BULL_D_MAX:g} · "
            f"🔴 M<{BEAR_M_MAX:g} W<{BEAR_W_MAX:g} D≥{BEAR_D_MIN:g} · RSI {RSI_LENGTH} · M/W live, D closed")


def render_message(head, bulls, bears, left, notes, universe_n, scanned_n):
    """The Telegram text. Skip counts and other diagnostics go to the job log, not here."""
    cov = f"Universe: {universe_n} PSX equities · {scanned_n} scanned"
    parts = [head, rules_line(), cov] + notes
    parts.append(fmt_section("🟢 Bullish GFS (M/W strong, D pulled back)", bulls))
    parts.append(fmt_section("🔴 Bearish GFS (M/W weak, D bounced)", bears))
    if left:
        parts.append("↩️ Left since last run: " + ", ".join(left))
    return "\n\n".join(parts)


def finish_with_note(head, note):
    """Send a one-line status message (feed down / holiday); state, log and archive stay as they were."""
    text = f"{head}\n\n{rules_line()}\n\n{note}"
    print(note)
    if DRY_RUN:
        print("\n----- DRY RUN: message that would be sent -----\n")
        print(text)
        print("\n----- (no Telegram, no files written) -----")
        return
    send_telegram(text)
    append_archive(text)
    print("Telegram message sent (state unchanged).")


# ── Main ───────────────────────────────────────────────────────────
def main():
    today = now_pkt().date()
    print(f"GFS RSI Scan PSX {today.isoformat()} | bull M>{BULL_M_MIN:g} W>{BULL_W_MIN:g} D<={BULL_D_MAX:g} | "
          f"bear M<{BEAR_M_MAX:g} W<{BEAR_W_MAX:g} D>={BEAR_D_MIN:g} | RSI {RSI_LENGTH} | "
          f"stale>{MAX_STALE_DAYS}d | min_avg_vol {MIN_AVG_VOLUME:g} | dry_run={DRY_RUN}")

    head = f"📊 GFS RSI Scan — PSX — {today:%d %b %Y}"      # re-dated by session once one is known (cron drift)
    print("Fetching TradingView's PSX screener (bars + RSI D/W/M)...")
    try:
        rows = tradingview_scan()
    except Exception as e:
        print(f"  screener failed: {type(e).__name__}: {e}")
        rows = {}
    rank_by_mcap(rows)
    if len(rows) < MIN_ROWS:
        print(f"  only {len(rows)} rows")
        return finish_with_note(head, "⚠️ TradingView's PSX screener is unavailable this run — previous list kept.")

    expected = expected_session()
    rows, sess, status = resolve_session(rows, expected)
    if sess is None:
        return finish_with_note(head, "⚠️ TradingView's PSX screener returned no usable session — previous list kept.")
    print(f"  {len(rows)} common stocks, latest session {sess}, expected by now {expected} ({status})")
    head = f"📊 GFS RSI Scan — PSX — {expected:%d %b %Y}"
    if status in ("holiday", "unconfirmed"):
        note = f"PSX market closed on {expected:%d %b %Y} (last session {sess:%d %b}) — no new candle."
        if status == "unconfirmed":
            note += " (SCS Trade could not confirm.)"
        return finish_with_note(head, note)
    if status == "stale":
        return finish_with_note(head, f"⚠️ TradingView's PSX bars are still from {sess:%d %b} although PSX traded on "
                                      f"{expected:%d %b} — previous list kept.")

    intraday = sess > expected                       # a run before today's cutoff sees the forming daily candle
    head = f"📊 GFS RSI Scan — PSX — {sess:%d %b %Y} " + \
           ("(intraday — daily candle still forming)" if intraday else "(after the close)")
    notes = []
    if MAX_SYMBOLS:
        keep = sorted(rows.values(), key=lambda r: (r["rank"] is None, r["rank"] or 0))[:MAX_SYMBOLS]
        rows = {r["symbol"]: r for r in keep}
        notes.append(f"⚠️ MAX_SYMBOLS={MAX_SYMBOLS}: partial scan (testing).")

    results, skipped = scan(rows, sess)
    print(f"  scanned {len(results)} | skipped: {skipped['stale']} not traded in {MAX_STALE_DAYS}d, "
          f"{skipped['history']} too little history, {skipped['share_class']} pref/rights classes, "
          f"{skipped['volume']} under volume floor")

    state = load_state()
    if state.get("session") == sess.isoformat() and not RESEND and not DRY_RUN:
        # GitHub's cron double-fires now and then; the second run would repeat the message.
        print(f"Already alerted for session {sess} (state.json) — nothing to send. PSX_RESEND=1 sends again.")
        return
    bulls, bears, matches = [], [], []
    for r in results:
        if not r["side"]:
            continue
        prev = state[r["side"]].get(r["symbol"])
        first_seen = prev["first_seen"] if prev else sess.isoformat()
        m = {**r, "first_seen": first_seen, "new": prev is None,
             "days": (sess - date.fromisoformat(first_seen)).days}
        (bulls if r["side"] == "bull" else bears).append(m)
        matches.append(m)
    bulls.sort(key=lambda m: (not m["new"], m["rsi_d"]))          # new first, most oversold daily first
    bears.sort(key=lambda m: (not m["new"], -m["rsi_d"]))         # new first, most overbought daily first

    new_state = {"bull": {}, "bear": {}, "session": sess.isoformat(),
                 "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for m in matches:
        new_state[m["side"]][m["symbol"]] = {"first_seen": m["first_seen"]}
    left = [f"{sym} ({side})" for side in ("bull", "bear")
            for sym in sorted(state[side]) if sym not in new_state[side]]

    text = render_message(head, bulls, bears, left, notes, len(rows), len(results))
    print(f"{len(bulls)} bullish, {len(bears)} bearish, {len(left)} left")

    if DRY_RUN:
        print("\n----- DRY RUN: message that would be sent -----\n")
        print(text)
        print("\n----- (no Telegram, no files written) -----")
        return

    send_telegram(text)
    print("Telegram message sent.")
    append_archive(text)
    if intraday:
        print("Intraday run: state and matches log left untouched for the after-close run.")
        return
    log_matches(matches, sess)
    save_state(new_state)
    print(f"Wrote {STATE_FILE}, {ALERTS_ARCHIVE}, {MATCHES_LOG} ({len(matches)} matches).")


if __name__ == "__main__":
    main()
