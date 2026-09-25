#!/usr/bin/env python3
"""
Spinning-top scanner — high-volume spinning-top candles with Telegram alerts.

Signal (on the LAST CLOSED candle of the selected timeframe):
  * body <= 30 % of the high-low range
  * upper wick >= body AND lower wick >= body (both shadows real: each >= 20 % of range)
  * volume >= 1.5 x the average of the previous 20 candles
  * swing extreme: the candle's high is the highest high OR its low is the lowest
    low of the previous 10 candles (so the indecision happens at a turning point)

Timeframe comes from the TIMEFRAME env var (1d / 1w / 1M). The workflow runs
three schedules — daily, Mondays (weekly), the 1st (monthly) — right after each
close. Signals fire only on closed candles.

Universe: every Binance USDT spot pair, every Bitget USDT spot pair Binance lacks,
plus the CoinGecko top-500 coins by market cap. Top-500 coins on neither exchange
are scanned on MEXC, then KuCoin. Stablecoins, wrapped/staked tokens and
leveraged tokens are excluded.

Configuration is via environment variables — see CLAUDE.md. Telegram
credentials come ONLY from env vars.
"""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

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


# Pattern rules — agreed with the owner on 2026-09-20 (CLAUDE.md: do not change unless asked)
BODY_MAX_PCT  = _env_float("BODY_MAX_PCT", 30)     # body ≤ 30 % of the high-low range
WICK_MIN_BODY = _env_float("WICK_MIN_BODY", 1.0)   # each wick ≥ 1.0 × body
WICK_MIN_PCT  = _env_float("WICK_MIN_PCT", 20)     # each wick ≥ 20 % of range (keeps hammers / gravestones out; 0 = off)
VOL_MULT      = _env_float("VOL_MULT", 1.5)        # volume ≥ 1.5 × average of the previous VOL_LOOKBACK candles
VOL_LOOKBACK  = _env_int("VOL_LOOKBACK", 20)
VOL_MIN_BARS  = _env_int("VOL_MIN_BARS", 10)       # need at least this many prior candles for the average
DOJI_PCT      = _env_float("DOJI_PCT", 10)         # body under this % of range is tagged "doji"
TREND_BARS    = _env_int("TREND_BARS", 5)          # context: % move over the N candles before the signal candle
SWING_BARS    = _env_int("SWING_BARS", 10)         # candle must set the highest high OR lowest low of the previous N (0 = off)

_TF_ALIASES = {"1d": "1d", "d": "1d", "daily": "1d", "day": "1d",
               "1w": "1w", "w": "1w", "weekly": "1w", "week": "1w",
               "1M": "1M", "M": "1M", "monthly": "1M", "month": "1M", "1m": "1M"}
TIMEFRAME = _TF_ALIASES.get(os.environ.get("TIMEFRAME", "1d").strip(), "1d")
TF_LABEL  = {"1d": "Daily", "1w": "Weekly", "1M": "Monthly"}[TIMEFRAME]

TOP_N             = _env_int("TOP_N", 500)             # CoinGecko coins by market cap to cover
MIN_QUOTE_VOLUME  = _env_float("MIN_QUOTE_VOLUME", 0)  # 24h USDT volume floor for Binance pairs (0 = every pair)
MAX_SYMBOLS       = _env_int("MAX_SYMBOLS", 0)         # smoke tests only: scan just the first N pairs (0 = all)
WORKERS           = max(1, _env_int("WORKERS", 4))     # parallel kline fetchers
REQUEST_PAUSE     = _env_float("REQUEST_PAUSE", 0.05)  # seconds after each exchange call (per worker)
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "").strip()
CG_PAUSE          = _env_float("CG_PAUSE", 2 if COINGECKO_API_KEY else 6)
DRY_RUN           = os.environ.get("DRY_RUN", "0").strip() == "1"

SIGNALS_LOG    = os.environ.get("SIGNALS_LOG", "signals.jsonl")
ALERTS_ARCHIVE = os.environ.get("ALERTS_ARCHIVE", "alerts_archive.txt")

CANDLES = 60                                       # candles fetched per pair (≥ VOL_LOOKBACK + forming + margin)

# Binance spot: the public mirror is not geo-blocked on GitHub's US runners. Some
# ISPs can't reach the mirror — set BINANCE_BASE=https://api.binance.com locally.
SPOT_BASE   = os.environ.get("BINANCE_BASE", "https://data-api.binance.vision").rstrip("/")
BITGET_BASE = "https://api.bitget.com"
MEXC_BASE   = "https://api.mexc.com"
KUCOIN_BASE = "https://api.kucoin.com"
CG_BASE     = "https://api.coingecko.com/api/v3"
QUOTE       = "USDT"

INTERVALS = {
    "BINANCE": {"1d": "1d",    "1w": "1w",    "1M": "1M"},
    "BITGET":  {"1d": "1Dutc", "1w": "1Wutc", "1M": "1Mutc"},   # UTC-aligned; plain 1day/1week/1M are UTC+8
    "MEXC":    {"1d": "1d",    "1w": "1W",    "1M": "1M"},
    "KUCOIN":  {"1d": "1day",  "1w": "1week", "1M": "1month"},
}
EXCHANGE_NAMES = {"BINANCE": "Binance", "BITGET": "Bitget", "MEXC": "MEXC", "KUCOIN": "KuCoin"}
BITGET_PLAIN = {"1d": "1day", "1w": "1week", "1M": "1M"}    # UTC+8 candles: fallback for new listings without *utc data
FULL_EXCHANGES = ["BINANCE", "BITGET"]            # every USDT spot pair of these is scanned; first listing wins
FALLBACK_ORDER = ["MEXC", "KUCOIN"]               # where top-500 coins go when no full exchange has a pair

LEV_SUFFIXES = ("3L", "3S", "5L", "5S", "UP", "DOWN")   # leveraged tokens (only when the plain base is listed too)
STABLE_BASES = {
    "USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "USDE", "USD1", "USDD", "PYUSD", "USDS",
    "RLUSD", "FRAX", "LUSD", "GUSD", "XUSD", "USDX", "BFUSD", "SUSD", "USDJ", "CUSD", "USDY", "USDTB",
    "USDL", "USDB", "USDR", "USD0", "EURC", "EUR", "AEUR", "EURI", "TRY", "BRL", "GBP", "JPY", "ARS",
    "PAXG", "XAUT", "WBTC", "WETH", "WBETH", "STETH", "WSTETH", "CBBTC", "BNSOL", "RETH", "WEETH",
    "EZETH", "RSETH", "METH", "CBETH", "SOLVBTC", "TBTC", "LBTC", "BTCB", "JITOSOL", "MSOL", "JUPSOL",
}
_EXCLUDE_NAME = re.compile(r"\b(wrapped|bridged|staked|restaked|liquid staking|tokeni[sz]ed|xstock|stablecoin)\b", re.I)
SYMBOL_ALIASES = {"beam": "BEAMX", "sats": "1000SATS", "1000sats": "1000SATS"}
# CoinGecko categories dropped from the top-N (one call each): stablecoins, tokenized treasuries / stocks /
# funds / private credit, yield-bearing stables, Bittensor subnet tokens and Binance bStocks. Their ids drop
# coins from the CoinGecko top-N and their tickers drop pairs from the Binance / Bitget listings (CRWDB, PLTRB).
EXCLUDE_CATEGORIES = tuple(c.strip() for c in os.environ.get(
    "EXCLUDE_CATEGORIES",
    "stablecoins,tokenized-products,tokenized-stock,tokenized-private-credit,yield-bearing-stablecoins,bittensor-subnets,bstocks-ecosystem",
).split(",") if c.strip())
# Real coins spelled <TICKER>B that must not be mistaken for Binance bStocks (see _looks_like_bstock)
KEEP_BASES = {b.strip().upper() for b in os.environ.get("KEEP_BASES", "SHIB").split(",") if b.strip()}

session = requests.Session()                      # exchanges
session.headers.update({"User-Agent": "spinning-top-scanner/1.0", "Accept": "application/json"})
cg_session = requests.Session()                   # CoinGecko only (keeps the API key off other hosts)
cg_session.headers.update({"User-Agent": "spinning-top-scanner/1.0", "Accept": "application/json"})
if COINGECKO_API_KEY:
    cg_session.headers["x-cg-demo-api-key"] = COINGECKO_API_KEY


# ── HTTP helpers ───────────────────────────────────────────────────
def get_json(url, params=None, timeout=25, retries=3):
    """GET an exchange endpoint with a short retry on network errors, 429 and 5xx."""
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=timeout)
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == retries - 1:
                r.raise_for_status()
            ra = r.headers.get("Retry-After", "")
            time.sleep(float(ra) if ra.isdigit() else 2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()


def cg_get(path, params=None, retries=5):
    """GET a CoinGecko endpoint with 429/5xx back-off and a pause after each call."""
    url = CG_BASE + path
    for attempt in range(retries):
        try:
            r = cg_session.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise
            print(f"  CoinGecko network error on {path}: {type(e).__name__} — retrying")
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            ra = r.headers.get("Retry-After", "")
            wait = float(ra) if ra.isdigit() else min(60.0, 10.0 * (attempt + 1))
            wait = max(wait, 5.0)
            print(f"  CoinGecko HTTP {r.status_code} on {path} — waiting {wait:.0f}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        time.sleep(CG_PAUSE)
        return r.json()
    raise RuntimeError(f"CoinGecko still failing after {retries} attempts: {path}")


# ── Universe ───────────────────────────────────────────────────────
def _is_stable_symbol(sym):
    """Stablecoin / wrapped-asset tickers, plus anything spelled USD-something or something-USD."""
    s = (sym or "").upper()
    return s in STABLE_BASES or s.startswith("USD") or s.endswith("USD")


def _looks_like_bstock(base, top_syms):
    """Binance lists tokenized US stocks / ETFs as <TICKER>B (CRWDB, TSLAB, SPYB). CoinGecko's
    bstocks-ecosystem category catches most of them; this catches the ones CoinGecko has not
    listed yet: a B-suffixed base (4+ chars) that the CoinGecko top-N does not know as a coin."""
    return len(base) >= 4 and base.endswith("B") and base not in top_syms and base not in KEEP_BASES


def _drop_leveraged(pairs):
    """pairs: base -> pair. Drops BTC3L / ETHDOWN style tokens when the plain base is listed too."""
    bases = set(pairs)
    out = {}
    for base, pair in pairs.items():
        if any(base.endswith(suf) and base[:-len(suf)] in bases for suf in LEV_SUFFIXES):
            continue
        out[base] = pair
    return out


def get_binance_pairs():
    """base asset -> pair for every active Binance USDT spot pair (minus stables / leveraged)."""
    info = get_json(f"{SPOT_BASE}/api/v3/exchangeInfo")
    vol = {}
    if MIN_QUOTE_VOLUME > 0:
        tickers = get_json(f"{SPOT_BASE}/api/v3/ticker/24hr")
        vol = {t["symbol"]: float(t.get("quoteVolume") or 0) for t in tickers}
    out = {}
    for s in info["symbols"]:
        if (s.get("status") == "TRADING" and s.get("quoteAsset") == QUOTE
                and s.get("isSpotTradingAllowed", True) and not _is_stable_symbol(s["baseAsset"])
                and (not MIN_QUOTE_VOLUME or vol.get(s["symbol"], 0) >= MIN_QUOTE_VOLUME)):
            out[s["baseAsset"]] = s["symbol"]
    return _drop_leveraged(out)


def get_bitget_pairs():
    """base asset -> pair for every online Bitget USDT spot COIN pair (minus stables / leveraged).
    Bitget also lists ~2,600 tokenized stocks / pre-IPO tokens with a lowercase prefix (rNKE,
    preOPAI); real coin tickers are all-uppercase, so anything with a lowercase letter is skipped.
    Pairs whose openTime is still in the future have no candles yet and are skipped too."""
    rows = get_json(f"{BITGET_BASE}/api/v2/spot/public/symbols").get("data") or []
    vol = {}
    if MIN_QUOTE_VOLUME > 0:
        tick = get_json(f"{BITGET_BASE}/api/v2/spot/market/tickers").get("data") or []
        vol = {t["symbol"]: float(t.get("usdtVolume") or t.get("quoteVolume") or 0) for t in tick}
    now_ms = time.time() * 1000
    out = {}
    for s in rows:
        base = s.get("baseCoin") or ""
        open_ms = float(s.get("openTime") or 0)
        if (s.get("status") == "online" and s.get("quoteCoin") == QUOTE and base.isupper()
                and open_ms <= now_ms and not _is_stable_symbol(base)
                and (not MIN_QUOTE_VOLUME or vol.get(s["symbol"], 0) >= MIN_QUOTE_VOLUME)):
            out[base] = s["symbol"]
    return _drop_leveraged(out)


def get_mexc_pairs():
    info = get_json(f"{MEXC_BASE}/api/v3/exchangeInfo")
    out = {}
    for s in info["symbols"]:
        if (s.get("status") == "1" and s.get("quoteAsset") == QUOTE          # MEXC: "1" == trading
                and s.get("isSpotTradingAllowed", False) and not _is_stable_symbol(s["baseAsset"])):
            out[s["baseAsset"]] = s["symbol"]
    return _drop_leveraged(out)


def get_kucoin_pairs():
    syms = get_json(f"{KUCOIN_BASE}/api/v1/symbols")["data"]
    out = {}
    for s in syms:
        if (s.get("quoteCurrency") == QUOTE and s.get("enableTrading")
                and not _is_stable_symbol(s.get("baseCurrency"))):
            out[s["baseCurrency"]] = s["symbol"]                           # e.g. "BTC-USDT"
    return _drop_leveraged(out)


def fetch_top_coins():
    """CoinGecko top TOP_N by market cap, minus stablecoins and wrapped/staked tokens.
    Returns (rows, dropped_count, excluded_symbols) - the tickers of every coin in EXCLUDE_CATEGORIES,
    used to drop tokenized stocks / stables from the exchange listings too."""
    rows, seen, page = [], set(), 1
    while len(rows) < TOP_N:
        batch = cg_get("/coins/markets", {"vs_currency": "usd", "order": "market_cap_desc",
                                          "per_page": 250, "page": page, "sparkline": "false"})
        if not batch:
            break
        for r in batch:
            if r.get("id") and r["id"] not in seen:                        # pages can shift → dedupe
                seen.add(r["id"])
                rows.append(r)
        if len(batch) < 250:
            break
        page += 1
    rows = rows[:TOP_N]
    excluded_ids, excluded_syms = set(), set()
    for cat in EXCLUDE_CATEGORIES:                                         # one call per category
        try:
            for r in cg_get("/coins/markets", {"vs_currency": "usd", "category": cat, "order": "market_cap_desc",
                                               "per_page": 250, "page": 1, "sparkline": "false"}):
                if r.get("id"):
                    excluded_ids.add(r["id"])
                    excluded_syms.add((r.get("symbol") or "").upper())
        except Exception as e:
            print(f"  category '{cat}' unavailable ({type(e).__name__}) — skipped")
    keep = [r for r in rows
            if r["id"] not in excluded_ids
            and not _is_stable_symbol(r.get("symbol"))
            and not _EXCLUDE_NAME.search(r.get("name") or "")]
    return keep, len(rows) - len(keep), excluded_syms


LISTERS = {"BINANCE": get_binance_pairs, "BITGET": get_bitget_pairs, "MEXC": get_mexc_pairs, "KUCOIN": get_kucoin_pairs}


def build_universe():
    """Returns (entries, notes, stats). entry = {key, base, exchange, pair, rank, name}.

    Every USDT pair of the FULL_EXCHANGES is scanned (Binance first; Bitget adds the bases
    Binance lacks). CoinGecko top-N coins on neither are looked up on the FALLBACK_ORDER exchanges."""
    notes, listings, unavailable = [], {}, set()
    for ex in FULL_EXCHANGES + FALLBACK_ORDER:
        try:
            listings[ex] = LISTERS[ex]()
        except Exception as e:
            if ex == "BINANCE":
                raise                                                      # Binance failing is fatal on purpose
            listings[ex] = {}
            unavailable.add(ex)
            what = "its pairs" if ex in FULL_EXCHANGES else f"its top-{TOP_N} fallbacks"
            notes.append(f"⚠️ {EXCHANGE_NAMES[ex]} listing unavailable ({type(e).__name__}) — {what} are skipped this run.")
    try:
        top, dropped, excluded_syms = fetch_top_coins()
    except Exception as e:
        top, dropped, excluded_syms = [], 0, set()
        notes.append(f"⚠️ CoinGecko unavailable ({type(e).__name__}) — exchange listings only this run "
                     "(tokenized-stock filter off).")

    top_syms = {SYMBOL_ALIASES.get((r.get("symbol") or "").lower(), (r.get("symbol") or "").upper()) for r in top}
    entries, by_base, dropped_pairs = {}, {}, 0
    for ex in FULL_EXCHANGES:                                              # first listing of a base wins
        for base, pair in listings[ex].items():
            if base in by_base:
                continue
            if base in excluded_syms or (ex == "BINANCE" and _looks_like_bstock(base, top_syms)):
                dropped_pairs += 1                                         # tokenized stocks (CRWDB, PLTRB), stables
                continue
            key = f"{ex}:{pair}"
            entries[key] = by_base[base] = {"key": key, "base": base, "exchange": ex, "pair": pair,
                                            "rank": None, "name": None}
    not_covered = []
    for r in top:
        sym = (r.get("symbol") or "").lower()
        base = SYMBOL_ALIASES.get(sym, sym.upper())
        rank = r.get("market_cap_rank")
        if base in by_base:
            by_base[base]["rank"], by_base[base]["name"] = rank, r.get("name")
            continue
        for ex in FALLBACK_ORDER:
            pair = listings[ex].get(base)
            if pair:
                key = f"{ex}:{pair}"
                entries[key] = by_base[base] = {"key": key, "base": base, "exchange": ex, "pair": pair,
                                                "rank": rank, "name": r.get("name")}
                break
        else:
            not_covered.append(f"{base}#{rank or '?'}")
    out = sorted(entries.values(), key=lambda e: (e["rank"] or 10 ** 6, e["base"]))
    stats = {ex: sum(e["exchange"] == ex for e in out) for ex in EXCHANGE_NAMES}
    stats.update({"top": len(top), "top_dropped": dropped, "not_covered": not_covered, "unavailable": unavailable,
                  "dropped_pairs": dropped_pairs})
    return out, notes, stats


# ── Candles ────────────────────────────────────────────────────────
# Candle = (open_ms, open, high, low, close, volume, is_closed)
def _binance_style(base_url, pair, interval, limit):
    """Binance and MEXC share this kline format."""
    data = get_json(f"{base_url}/api/v3/klines", {"symbol": pair, "interval": interval, "limit": limit})
    now_ms = time.time() * 1000
    return [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), int(k[6]) <= now_ms)
            for k in data]


def _start_closed(start_s, now_s, tf, tz_offset=0):
    """For feeds that give only a candle START time (KuCoin, Bitget): has the period ended?
    tz_offset (seconds) shifts the calendar for monthly candles that start at a non-UTC midnight."""
    if tf == "1M":
        st, nw = time.gmtime(start_s + tz_offset), time.gmtime(now_s + tz_offset)
        return (st.tm_year, st.tm_mon) < (nw.tm_year, nw.tm_mon)
    return start_s + (86400 if tf == "1d" else 604800) <= now_s


def _bitget(pair, tf, limit):
    """Bitget candles are ascending: [start_ms, open, high, low, close, baseVol, usdtVol, quoteVol].
    One call returns at most 300 daily / 100 weekly / all monthly candles. Newly listed pairs have
    no UTC-aligned (*utc) candles yet and answer HTTP 400 code 48001 -> fall back to the UTC+8 ones."""
    url = f"{BITGET_BASE}/api/v2/spot/market/candles"
    tz_offset = 0
    try:
        data = get_json(url, {"symbol": pair, "granularity": INTERVALS["BITGET"][tf],
                              "limit": min(limit, 1000)}).get("data") or []
    except requests.HTTPError as e:
        if e.response is None or e.response.status_code != 400:
            raise
        data = get_json(url, {"symbol": pair, "granularity": BITGET_PLAIN[tf],
                              "limit": min(limit, 1000)}).get("data") or []
        tz_offset = 8 * 3600
    now_s = time.time()
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]),
             _start_closed(int(r[0]) // 1000, now_s, tf, tz_offset)) for r in data[-limit:]]


def _kucoin(pair, tf, limit):
    """KuCoin candles are newest-first: [start_s, open, close, high, low, volume, turnover]."""
    data = get_json(f"{KUCOIN_BASE}/api/v1/market/candles",
                    {"type": INTERVALS["KUCOIN"][tf], "symbol": pair}).get("data") or []
    now_s = time.time()
    rows = list(reversed(data))[-limit:]
    return [(int(r[0]) * 1000, float(r[1]), float(r[3]), float(r[4]), float(r[2]), float(r[5]),
             _start_closed(int(r[0]), now_s, tf)) for r in rows]


def closed_klines(exchange, pair, tf=TIMEFRAME, limit=CANDLES):
    """Ascending CLOSED candles for one timeframe (the forming candle is always dropped)."""
    if exchange == "KUCOIN":
        rows = _kucoin(pair, tf, limit)
    elif exchange == "BITGET":
        rows = _bitget(pair, tf, limit)
    else:
        rows = _binance_style(SPOT_BASE if exchange == "BINANCE" else MEXC_BASE,
                              pair, INTERVALS[exchange][tf], limit)
    time.sleep(REQUEST_PAUSE)
    return [k for k in rows if k[6]]


def is_fresh(open_ms, tf, now=None):
    """True when a candle that opened at open_ms is the latest CLOSED one for tf.
    Guards against halted / delisted pairs whose 'last closed' candle is weeks old."""
    now = now or datetime.now(timezone.utc)
    opened = datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc)
    if tf == "1d":
        return now - opened <= timedelta(days=2)
    if tf == "1w":
        return now - opened <= timedelta(days=14)
    first_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    first_prev_month = (first_this_month - timedelta(days=1)).replace(day=1)
    return opened >= first_prev_month - timedelta(days=1)                  # 1 day slack: UTC+8 monthly candles


def describe_candle(now):
    """Human label of the candle checked this run — the last closed one of TIMEFRAME (UTC calendar)."""
    today = now.date()
    if TIMEFRAME == "1d":
        return f"daily candle of {(today - timedelta(days=1)).strftime('%a %d %b %Y')} UTC"
    if TIMEFRAME == "1w":
        monday = today - timedelta(days=today.weekday() + 7)
        return f"weekly candle that opened Mon {monday.strftime('%d %b %Y')} (KuCoin weeks open on Thursdays)"
    first_prev = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    return f"monthly candle of {first_prev.strftime('%B %Y')}"


# ── Pattern ────────────────────────────────────────────────────────
def detect_spinning_top(rows):
    """rows: ascending closed candles. Returns a signal dict for the LAST candle, or None."""
    if len(rows) < VOL_MIN_BARS + 1:
        return None
    ts, o, h, l, c, v = rows[-1][:6]
    rng = h - l
    if rng <= 0 or v <= 0:
        return None
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    body_pct, upper_pct, lower_pct = body / rng * 100, upper / rng * 100, lower / rng * 100
    if body_pct > BODY_MAX_PCT:
        return None
    if upper < WICK_MIN_BODY * body or lower < WICK_MIN_BODY * body:
        return None
    if upper_pct < WICK_MIN_PCT or lower_pct < WICK_MIN_PCT:
        return None
    prior = rows[-1 - VOL_LOOKBACK:-1]
    avg_vol = sum(r[5] for r in prior) / len(prior)
    if avg_vol <= 0:
        return None
    ratio = v / avg_vol
    if ratio < VOL_MULT:
        return None
    swing = None
    if SWING_BARS > 0:                                                     # must sit at a turning point
        prev = rows[-1 - SWING_BARS:-1]
        at_high = h >= max(r[2] for r in prev)
        at_low = l <= min(r[3] for r in prev)
        if not (at_high or at_low):
            return None
        swing = "both" if at_high and at_low else "high" if at_high else "low"
    move = None
    if len(rows) >= TREND_BARS + 2 and rows[-2 - TREND_BARS][4] > 0:
        move = (rows[-2][4] / rows[-2 - TREND_BARS][4] - 1) * 100         # trend INTO the signal candle
    return {"candle_ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": v,
            "body_pct": body_pct, "upper_pct": upper_pct, "lower_pct": lower_pct,
            "vol_ratio": ratio, "vol_bars": len(prior), "doji": body_pct < DOJI_PCT,
            "swing": swing, "move_pct": move, "color": "green" if c >= o else "red"}


def evaluate(entry):
    rows = closed_klines(entry["exchange"], entry["pair"])
    if len(rows) < VOL_MIN_BARS + 1:
        return {"entry": entry, "skip": "history"}
    if not is_fresh(rows[-1][0], TIMEFRAME):
        return {"entry": entry, "skip": "stale"}                           # halted / delisted pair
    sig = detect_spinning_top(rows)
    return {"entry": entry, "signal": sig}


def scan(universe):
    """Evaluate every entry with a small thread pool. Returns (signals, skipped, errors_by_exchange)."""
    signals, skipped, errors, err_types = [], {"history": 0, "stale": 0}, {}, {}

    def work(e):
        try:
            return evaluate(e)
        except Exception as ex:
            return {"entry": e, "error": type(ex).__name__}

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i, res in enumerate(pool.map(work, universe), 1):
            if "error" in res:
                ex = res["entry"]["exchange"]
                errors[ex] = errors.get(ex, 0) + 1
                err_types[res["error"]] = err_types.get(res["error"], 0) + 1
            elif "skip" in res:
                skipped[res["skip"]] = skipped.get(res["skip"], 0) + 1
            elif res["signal"]:
                signals.append({**res["signal"], "entry": res["entry"]})
            if i % 50 == 0 or i == len(universe):
                print(f"  {i}/{len(universe)} scanned, {len(signals)} hits, {skipped['history']} no-history, "
                      f"{skipped['stale']} stale, {sum(errors.values())} errors, {time.time() - t0:.0f}s")
    if err_types:
        print("  error types: " + ", ".join(f"{k} x{v}" for k, v in sorted(err_types.items())))
    return signals, skipped, errors


# ── Logs ───────────────────────────────────────────────────────────
def signal_id(s):
    e = s["entry"]
    return f"{TIMEFRAME}:{e['exchange']}:{e['pair']}:{s['candle_ts']}"


def load_signal_ids(path=SIGNALS_LOG):
    ids = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        ids.add(json.loads(line)["id"])
                    except (ValueError, KeyError):
                        pass
    return ids


def log_signals(signals, run_date, path=SIGNALS_LOG):
    """Append signals not already in the log (dedup by timeframe:exchange:pair:candle). Returns count."""
    seen = load_signal_ids(path)
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for s in signals:
            sid = signal_id(s)
            if sid in seen:
                continue
            e = s["entry"]
            f.write(json.dumps({
                "id": sid, "date": run_date.isoformat(), "timeframe": TIMEFRAME, "exchange": e["exchange"],
                "pair": e["pair"], "symbol": e["base"], "rank": e["rank"], "candle_ts": s["candle_ts"],
                "open": s["open"], "high": s["high"], "low": s["low"], "close": s["close"], "volume": s["volume"],
                "vol_ratio": round(s["vol_ratio"], 3), "body_pct": round(s["body_pct"], 2),
                "upper_pct": round(s["upper_pct"], 2), "lower_pct": round(s["lower_pct"], 2),
                "doji": s["doji"], "swing": s["swing"],
                "move_pct": None if s["move_pct"] is None else round(s["move_pct"], 2),
                "color": s["color"],
            }) + "\n")
            seen.add(sid)
            n += 1
    return n


def append_archive(text, path=ALERTS_ARCHIVE):
    divider = ("\n" + "=" * 50 + "\n\n") if os.path.exists(path) and os.path.getsize(path) else ""
    with open(path, "a", encoding="utf-8") as f:
        f.write(divider + text.rstrip() + "\n")


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


def send_telegram(text):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for part in _chunks(text):
        resp = session.post(url, json={"chat_id": chat_id, "text": part,
                                       "disable_web_page_preview": True}, timeout=20)
        resp.raise_for_status()


def fmt_price(p):
    return f"{p:.8f}".rstrip("0").rstrip(".") if p < 1 else f"{p:,.4f}".rstrip("0").rstrip(".")


def fmt_signal(s):
    e = s["entry"]
    tag = "" if e["exchange"] == "BINANCE" else f" [{EXCHANGE_NAMES[e['exchange']]}]"
    rank = f"  #{e['rank']}" if e["rank"] else ""
    doji = " (doji)" if s["doji"] else ""
    if s["move_pct"] is None:
        ctx = ""
    else:
        arrow = "↑" if s["move_pct"] > 0 else "↓"
        ctx = f"  {arrow} {s['move_pct']:+.1f}% into it"
    swing = {"high": f"  ⬆ {SWING_BARS}-bar high", "low": f"  ⬇ {SWING_BARS}-bar low",
             "both": f"  ↕ {SWING_BARS}-bar high & low"}.get(s.get("swing"), "")
    return (f"  • {e['base']}{tag}  vol {s['vol_ratio']:.1f}×  body {s['body_pct']:.0f}%{doji}{swing}{ctx}"
            f"  @ {fmt_price(s['close'])}{rank}")


def render_message(date_str, candle_str, signals, stats, notes, skipped, errors, scanned):
    head = f"🕯️ Spinning Top Scan — {TF_LABEL} — {date_str}"
    sub = f"Candle checked: {candle_str} — closed candles only, the forming candle is ignored"
    rules = (f"Rule: body ≤{BODY_MAX_PCT:g}% of range · wicks ≥{WICK_MIN_BODY:g}× body"
             + (f" & ≥{WICK_MIN_PCT:g}% of range" if WICK_MIN_PCT > 0 else "")
             + f" · volume ≥{VOL_MULT:g}× avg({VOL_LOOKBACK})"
             + (f" · at a {SWING_BARS}-candle high or low" if SWING_BARS > 0 else ""))
    nc = stats["not_covered"]
    cov = (f"Universe: {scanned} pairs (Binance {stats['BINANCE']} · Bitget {stats['BITGET']} · "
           f"MEXC {stats['MEXC']} · KuCoin {stats['KUCOIN']})"
           f" · top-{TOP_N} not covered: {len(nc)} · too little history: {skipped['history']}"
           f" · stale/halted: {skipped['stale']}")
    if errors:
        cov += " · errors: " + ", ".join(f"{EXCHANGE_NAMES[k]} {v}" for k, v in sorted(errors.items()))
    parts = [head, sub, rules, cov] + notes
    if signals:
        parts.append("\n".join([f"Spinning tops on high volume ({len(signals)}), biggest volume first:"]
                               + [fmt_signal(s) for s in signals]))
        parts.append(f"⬆/⬇ = the candle set the highest high / lowest low of the previous {SWING_BARS} candles"
                     " · ↑ = came after an up-move (watch for a bearish turn) · ↓ = after a down-move (watch for a bullish turn)"
                     f" · % is the close-to-close move over the previous {TREND_BARS} candles")
    else:
        parts.append("Spinning tops on high volume: none")
    if nc:
        shown = ", ".join(nc[:40]) + (f" … +{len(nc) - 40} more" if len(nc) > 40 else "")
        parts.append(f"Top-{TOP_N} coins without a USDT pair on Binance/Bitget/MEXC/KuCoin ({len(nc)}): {shown}")
    return "\n\n".join(parts)


# ── Main ───────────────────────────────────────────────────────────
def main():
    now = datetime.now(timezone.utc)
    today = now.date()
    date_str = today.strftime("%d %b %Y")
    print(f"Spinning Top Scan {today.isoformat()} | tf {TIMEFRAME} | body<={BODY_MAX_PCT:g}% wick>={WICK_MIN_BODY:g}x "
          f"& >={WICK_MIN_PCT:g}% | vol>={VOL_MULT:g}x avg{VOL_LOOKBACK} | swing {SWING_BARS} | top {TOP_N} | workers {WORKERS} | "
          f"binance={SPOT_BASE} | cg_key={'yes' if COINGECKO_API_KEY else 'no'} | dry_run={DRY_RUN}")

    print("Building universe (Binance + Bitget + CoinGecko top coins, MEXC/KuCoin fallback)...")
    universe, notes, stats = build_universe()
    print(f"  {len(universe)} pairs: Binance {stats['BINANCE']}, Bitget {stats['BITGET']}, "
          f"MEXC {stats['MEXC']}, KuCoin {stats['KUCOIN']} | "
          f"top coins {stats['top']} (+{stats['top_dropped']} stables/tokenized dropped) | "
          f"exchange pairs dropped as tokenized/stable: {stats['dropped_pairs']} | "
          f"not covered {len(stats['not_covered'])}")
    if MAX_SYMBOLS:
        universe = universe[:MAX_SYMBOLS]
        notes.append(f"⚠️ MAX_SYMBOLS={MAX_SYMBOLS}: partial scan (testing).")

    print(f"Scanning {len(universe)} pairs on {TIMEFRAME}...")
    signals, skipped, errors = scan(universe)
    signals.sort(key=lambda s: -s["vol_ratio"])

    candle_str = describe_candle(now)
    text = render_message(date_str, candle_str, signals, stats, notes, skipped, errors, len(universe))
    print(f"{len(signals)} signals")

    if DRY_RUN:
        print("\n----- DRY RUN: message that would be sent -----\n")
        print(text)
        print("\n----- (no Telegram, no files written) -----")
        return

    send_telegram(text)
    print("Telegram message sent.")
    append_archive(text)
    n = log_signals(signals, today)
    print(f"Wrote {ALERTS_ARCHIVE}; {n} new signals appended to {SIGNALS_LOG}.")


if __name__ == "__main__":
    main()
