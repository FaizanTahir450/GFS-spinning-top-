# CLAUDE.md — spinning-top-scanner

Guidance for future Claude Code sessions working in this repo. Sibling projects
by the same owner, same GitHub-Actions → Telegram shape, different strategies:
`D:\projects\sweep-scanner`, `D:\projects\All time high strategy`,
`D:\projects\gfs-scanner` (shares this repo's universe/exchange code by copy, not
import). Reuse their conventions; do not import their code.

## What this project is

A single-file Python scanner that runs **on GitHub Actions** after each
daily / weekly / monthly close, fetches the closed candles of every coin in the
universe on the selected timeframe, keeps the ones whose last closed candle is a
**spinning top on high volume**, and sends one plain-text **Telegram** message.
Signals are appended to `signals.jsonl` (dedup per candle). No server, no UI.

The **timeframe** is selected at runtime via the `TIMEFRAME` env var
(`1d`/`1w`/`1M`, default daily). The workflow runs three schedules — daily
(00:15 UTC), weekly (Mondays 00:15 UTC), monthly (1st 00:15 UTC) — each setting
`TIMEFRAME`; a manual `workflow_dispatch` has a timeframe dropdown.

## Strategy — DO NOT change unless the owner explicitly asks

Owner's brief (2026-09-20): "spinning top with high volume for daily weekly
monthly". The owner picked the **Standard** shape and the **1.5× / 20-candle**
volume rule from the options offered:

| Rule | Value | Implementation |
|------|-------|----------------|
| Body | **≤ 30 %** of the high–low range | `BODY_MAX_PCT` |
| Wicks | **each ≥ 1.0 × body** | `WICK_MIN_BODY` |
| Wicks (shape guard) | each **≥ 20 % of range** — added so near-zero-body candles with one long shadow (dragonfly / gravestone dojis, hammers) don't pass as spinning tops. Not in the brief; `WICK_MIN_PCT=0` turns it off | `WICK_MIN_PCT` |
| Volume | **≥ 1.5 ×** the average of the **previous 20** candles (same timeframe; uses what exists down to `VOL_MIN_BARS`=10 so young coins still qualify on weekly/monthly) | `VOL_MULT`, `VOL_LOOKBACK`, `VOL_MIN_BARS` |
| Swing extreme | The candle's **high ≥ the highest high OR low ≤ the lowest low of the previous 10 candles** — the owner accepted this suggestion on 2026-09-25 so only spinning tops at a turning point alert (mid-range ones are noise). `SWING_BARS=0` turns it off. Hits are tagged ⬆ (at the high), ⬇ (at the low) or ↕ (both) | `SWING_BARS` |
| Candle | the **last CLOSED** candle of `TIMEFRAME`; the forming candle is always dropped | `closed_klines()` |
| Tags | `(doji)` when body < 10 % (`DOJI_PCT`); context arrow + close-to-close % over the previous 5 candles (`TREND_BARS`) — informational, never a filter | `detect_spinning_top()` |
| Universe | **All Binance USDT spot pairs ∪ all Bitget USDT spot pairs ∪ CoinGecko top 500 by market cap**. Binance has priority: a coin on both is scanned on Binance only (Bitget added as full coverage on 2026-09-25 at the owner's request). Top-500 coins on neither → **MEXC, then KuCoin** | `build_universe()`, `FULL_EXCHANGES`, `FALLBACK_ORDER` |
| Exclusions | CoinGecko categories in `EXCLUDE_CATEGORIES` (default `stablecoins, tokenized-products, tokenized-stock, tokenized-private-credit, yield-bearing-stablecoins, bittensor-subnets, bstocks-ecosystem`; the category tickers are also removed from the Binance/Bitget listings, which drops Binance's bStocks tokenized stocks (CRWDB, PLTRB, ... on 2026-09-25); bStocks CoinGecko has not listed yet are caught by `_looks_like_bstock()`: a Binance base spelled `<TICKER>B` (4+ chars) that the CoinGecko top-N does not know as a coin. `KEEP_BASES` (env, default `SHIB`) whitelists real coins ending in B — none trade as USDT pairs; without them the 2026-09-20 dry run's "not covered" list was ~90 tokenized treasuries / private-credit tokens), tickers in `STABLE_BASES` or spelled `USD*`/`*USD`, names matching wrapped/bridged/staked/tokenized, leveraged tokens (`3L/3S/5L/5S/UP/DOWN` when the plain base is also listed) | `fetch_top_coins()`, `_is_stable_symbol()`, `_drop_leveraged()` |
| Freshness | The last closed candle must be the current one for `TIMEFRAME` (daily: opened ≤ 2 days ago; weekly: ≤ 14 days; monthly: opened in the previous calendar month or later), otherwise the pair is skipped as "stale/halted" | `is_fresh()` in `evaluate()` |
| Header | "Candle checked" is derived from the UTC calendar (`describe_candle()`): yesterday / the Monday one week back / the previous month — not from the hits | `describe_candle()` |
| Alerts | Every hit on the just-closed candle, split into 🔴 **bearish** (swing `high` → up-move stalling) and 🟢 **bullish** (swing `low` → sell-off stalling) sections; with the swing filter off, the sign of the prior move decides. Each Telegram line is only `COIN [Exchange] @ price #rank`, ranked coins first — the owner asked on 2026-09-26 for the body %, swing tag and move % to go (they stay in the job log via `fmt_signal_detail()` and in `signals.jsonl`, which also stores `direction`). The "coins without a USDT pair" list is job-log only too. No 🆕/left logic — each candle is a new event. `signals.jsonl` dedups on `tf:exchange:pair:candle_ts` so a manual re-run doesn't double-log | `signal_direction()`, `fmt_signal()`, `render_message()`, `log_signals()` |

## Files

| File | Purpose |
|------|---------|
| `scanner.py` | Everything: config, CoinGecko client with back-off, exchange listings, kline fetchers, pattern detection, Telegram, archive, signal log. |
| `../.github/workflows/spinning_top.yml` (repo root) | Three crons + `workflow_dispatch` (timeframe dropdown, `dry_run`), `working-directory: spinning-top-scanner`. Resolves `TIMEFRAME` from the cron string, runs the scanner, commits `signals.jsonl` + `alerts_archive.txt` back (skipped on dry run). `permissions: contents: write`, concurrency group `gfs-spinning-top-scan` shared with the GFS workflow. |
| `signals.jsonl` | Append-only, created on first real run, committed. One line per signal: id, date, timeframe, exchange, pair, symbol, rank, candle_ts, OHLCV, vol_ratio, body/upper/lower %, doji, move_pct, color. |
| `alerts_archive.txt` | Append-only copy of every Telegram message (`=====` divider). |
| `README.md` | End-user setup guide (Telegram bot, secrets, manual run, tuning). |

## Data flow (`main()`)

1. `build_universe()` — Binance `exchangeInfo` (fatal if it fails) → Bitget `/api/v2/spot/public/symbols` (every online USDT pair whose base Binance lacks) → MEXC + KuCoin listings (each optional, ⚠️ note on failure) → CoinGecko `/coins/markets` 2 pages of 250 + one call per `EXCLUDE_CATEGORIES` entry (7 by default; a failing category is skipped; CoinGecko as a whole failing → ⚠️ note and a Binance-only run). Top-500 coins are matched to exchange base assets **by ticker symbol** (`SYMBOL_ALIASES` for the few that differ). Entries sorted by market-cap rank, Binance-only pairs after the ranked ones.
2. `scan()` — `ThreadPoolExecutor(WORKERS=4)`, `evaluate()` per pair = 1 kline request (60 candles) — ~740 requests, about a minute on a runner (workflow timeout 45 min). Per-pair exceptions are counted, never fatal; error types are printed in the job log.
3. `render_message()` → Telegram (plain text, no parse_mode, split on line boundaries under 4000 chars) → `append_archive()` → `log_signals()`. `DRY_RUN=1` prints the message and writes nothing.

## Data sources

- **Binance spot** `https://data-api.binance.vision` (`BINANCE_BASE`) — public mirror; GitHub runners are US-based and the main API blocks them. The owner's ISP cannot reach the mirror, so local runs need `BINANCE_BASE=https://api.binance.com` (verified 2026-09-20).
- **Bitget** `https://api.bitget.com` — `/api/v2/spot/public/symbols` (`status == "online"`, `openTime` not in the future). Of its ~3,100 online USDT pairs, ~2,590 are tokenized stocks / pre-IPO tokens with a lowercase prefix (`rNKE`, `rCVCO`, `preOPAI`); real coin tickers are all-uppercase, so `baseCoin.isupper()` is the filter — leaving ~510 coins, ~215 of them not on Binance (2026-09-25). `/api/v2/spot/market/candles` with the UTC granularities `1Dutc/1Wutc/1Mutc` (plain `1day/1week/1M` are UTC+8 and would misalign with Binance — verified 2026-09-25). Rows are ascending `[start_ms, open, high, low, close, baseVol, usdtVol, quoteVol]` with a start time only, so `_start_closed()` drops the forming candle; one call returns at most 300 daily / 100 weekly rows. Newly listed pairs have no `*utc` candles for a while and answer HTTP 400 code `48001` (RAIN, RLB on 2026-09-25, both listed within two weeks); `_bitget()` then falls back to that pair's UTC+8 `1day/1week/1M` candles (monthly closure shifted by 8 h via `_start_closed(tz_offset)`; `is_fresh()` allows one day of slack for the monthly check). `MIN_QUOTE_VOLUME` (24h USDT, via `/api/v2/spot/market/tickers`) applies to Bitget too.
- **MEXC** `https://api.mexc.com` — Binance-compatible klines (weekly interval is `1W`); `status == "1"` = trading.
- **KuCoin** `https://api.kucoin.com` — candles newest-first `[start, open, close, high, low, vol, turnover]`, start time only, so `_kucoin_closed()` decides the forming candle. KuCoin weeks start Thursday (Binance/MEXC: Monday), so on the Monday run KuCoin's "last closed week" is the Thu–Wed one — accepted.
- **CoinGecko** `https://api.coingecko.com/api/v3` — 9 calls per run (2 market pages + 7 categories). Keyless ≈ 10 req/min (`CG_PAUSE` 6 s); with a free Demo key (`COINGECKO_API_KEY`, sent only on the CoinGecko session) 2 s. 429/5xx honour `Retry-After`.

Known limitation: symbol-based matching can pair a CoinGecko coin with a
same-ticker but different asset on an exchange. Rare inside the top 500; add
to `SYMBOL_ALIASES` (or exclude) if one shows up.

## Config knobs (env, top of `scanner.py`)

`TIMEFRAME` 1d · `BODY_MAX_PCT` 30 · `WICK_MIN_BODY` 1.0 · `WICK_MIN_PCT` 20 · `VOL_MULT` 1.5 ·
`VOL_LOOKBACK` 20 · `VOL_MIN_BARS` 10 · `SWING_BARS` 10 · `DOJI_PCT` 10 · `TREND_BARS` 5 · `TOP_N` 500 · `EXCLUDE_CATEGORIES` (see table) ·
`MIN_QUOTE_VOLUME` 0 · `WORKERS` 4 · `REQUEST_PAUSE` 0.05 · `BINANCE_BASE` data-api.binance.vision ·
`COINGECKO_API_KEY` "" · `CG_PAUSE` 6 keyless / 2 with key · `DRY_RUN` 0 · `MAX_SYMBOLS` 0 (testing) ·
`SIGNALS_LOG` signals.jsonl · `ALERTS_ARCHIVE` alerts_archive.txt · `CANDLES` 60.

## Secrets — SECURITY

`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (and optional `COINGECKO_API_KEY`) come
**only** from environment variables / GitHub Actions secrets. Never hardcode
them in code, commits, logs or docs.

## Running locally

```bash
pip install -r requirements.txt
DRY_RUN=1 TIMEFRAME=1w MAX_SYMBOLS=30 BINANCE_BASE=https://api.binance.com python scanner.py
# PowerShell: $env:DRY_RUN="1"; $env:TIMEFRAME="1w"; $env:MAX_SYMBOLS="30"; $env:BINANCE_BASE="https://api.binance.com"; python scanner.py
```

`DRY_RUN=1` never sends Telegram or writes files. For a real local run set the
two Telegram env vars and point `SIGNALS_LOG` / `ALERTS_ARCHIVE` at scratch
paths if the committed files should stay untouched.

On this machine Windows Smart App Control blocks unsigned compiled Python
extensions on first load, which is one reason this scanner is pure Python
(`requests` only, no pandas).

## Deployment

Push to a GitHub repo; Actions runs the crons. The commit-back keeps the repo
active so GitHub's 60-day scheduled-workflow pause never triggers.
