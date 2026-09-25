# CLAUDE.md — gfs-scanner

Guidance for future Claude Code sessions working in this repo. Sibling projects
by the same owner, same GitHub-Actions → Telegram shape, different strategies:
`D:\projects\sweep-scanner`, `D:\projects\All time high strategy`,
`D:\projects\spinning-top-scanner` (shares this repo's universe/exchange code by
copy, not import). Reuse their conventions; do not import their code.

## What this project is

A single-file Python scanner that runs **daily on GitHub Actions**, computes the
14-period RSI on the monthly, weekly and daily timeframes for every coin in the
universe, keeps the ones matching the owner's **GFS** rules, and sends one
plain-text **Telegram** message. `state.json` remembers the previous list so the
message can flag 🆕 entries, show days on the list, and name coins that left.
No server, no UI.

## Strategy — DO NOT change unless the owner explicitly asks

Agreed with the owner on 2026-09-20 (the brief said "monthly RSI above 60, weekly
above 60, daily below or equal to 40 for bullish and 40 40 60 for bearish"):

| Rule | Value | Implementation |
|------|-------|----------------|
| Bullish GFS | monthly RSI **> 60** AND weekly RSI **> 60** AND daily RSI **≤ 40** | `gfs_side()`; thresholds `GFS_BULL_M_MIN`, `GFS_BULL_W_MIN`, `GFS_BULL_D_MAX` |
| Bearish GFS | monthly RSI **< 40** AND weekly RSI **< 40** AND daily RSI **≥ 60** (mirror of bullish; the owner's "40 40 60") | `GFS_BEAR_M_MAX`, `GFS_BEAR_W_MAX`, `GFS_BEAR_D_MIN` |
| RSI | **Wilder 14** (TradingView `ta.rsi`: SMA seed, then alpha = 1/14 smoothing) | `rsi()` — pure Python, no pandas |
| RSI basis | **Monthly & weekly on the current (forming) candle**, daily on the **last closed** candle — the owner chose "live monthly/weekly, closed daily" so signals can fire any day, matching what the chart shows at the daily close | `evaluate()`: daily filtered on `is_closed`, weekly/monthly not |
| Universe | **All Binance USDT spot pairs ∪ all Bitget USDT spot pairs ∪ CoinGecko top 500 by market cap**. Binance has priority: a coin on both is scanned on Binance only (Bitget added as full coverage on 2026-09-25 at the owner's request). Top-500 coins on neither → **MEXC, then KuCoin** (the owner chose the fallback over Binance-only) | `build_universe()`, `FULL_EXCHANGES`, `FALLBACK_ORDER` |
| Exclusions | CoinGecko categories in `EXCLUDE_CATEGORIES` (default `stablecoins, tokenized-products, tokenized-stock, tokenized-private-credit, yield-bearing-stablecoins, bittensor-subnets, bstocks-ecosystem`; the category tickers are also removed from the Binance/Bitget listings, which drops Binance's bStocks tokenized stocks (CRWDB, PLTRB, ... on 2026-09-25); bStocks CoinGecko has not listed yet are caught by `_looks_like_bstock()`: a Binance base spelled `<TICKER>B` (4+ chars) that the CoinGecko top-N does not know as a coin. `KEEP_BASES` (env, default `SHIB`) whitelists real coins ending in B — none trade as USDT pairs; without them the 2026-09-20 dry run's "not covered" list was ~90 tokenized treasuries / private-credit tokens), tickers in `STABLE_BASES` or spelled `USD*`/`*USD`, names matching wrapped/bridged/staked/tokenized, leveraged tokens (`3L/3S/5L/5S/UP/DOWN` when the plain base is also listed) | `fetch_top_coins()`, `_is_stable_symbol()`, `_drop_leveraged()` |
| Freshness | The last closed daily candle must have opened within 2 days, otherwise the pair is skipped as "stale/halted" (delisted or suspended pairs would otherwise yield an RSI on old data) | `is_fresh()` in `evaluate()` |
| Alerts | **Full list every run**, 🆕 for new entries, `(Nd)` days on list, "↩️ Left since last run" | `state.json` per side: `key → {symbol, exchange, first_seen}` |
| Schedule | **Daily 00:15 UTC** (after the 1D close) | `.github/workflows/scan.yml` cron `15 0 * * *` |

Minimum history: a pair needs at least `RSI_LENGTH + 1` candles on **each**
timeframe (15 months for the monthly RSI), otherwise it is counted under "too
little history" — the same moment TradingView starts plotting RSI.

## Files

| File | Purpose |
|------|---------|
| `scanner.py` | Everything: config, CoinGecko client with back-off, exchange listings, kline fetchers, RSI, rules, state, Telegram, archive, matches log. |
| `../.github/workflows/gfs.yml` (repo root) | Daily cron + `workflow_dispatch` (`dry_run`), `working-directory: gfs-scanner`. Commits `state.json`, `alerts_archive.txt`, `matches.jsonl` back (skipped on dry run). `permissions: contents: write`, concurrency group `gfs-spinning-top-scan` shared with the spinning-top workflow. |
| `state.json` | Created on first real run; committed. `{"bull": {key: {symbol, exchange, first_seen}}, "bear": {…}, "updated"}`; key = `EXCHANGE:PAIR`. |
| `alerts_archive.txt` | Append-only copy of every Telegram message (`=====` divider). |
| `matches.jsonl` | One line per (run, side, pair): rsi_m/rsi_w/rsi_d, close, rank, first_seen, new. |
| `README.md` | End-user setup guide (Telegram bot, secrets, manual run, tuning). |

## Data flow (`main()`)

1. `build_universe()` — Binance `exchangeInfo` (fatal if it fails) → Bitget `/api/v2/spot/public/symbols` (every online USDT pair whose base Binance lacks) → MEXC + KuCoin listings (each optional, ⚠️ note on failure; an exchange whose listing failed keeps its previous state entries) → CoinGecko `/coins/markets` 2 pages of 250 + one call per `EXCLUDE_CATEGORIES` entry (7 by default; a failing category is skipped; CoinGecko as a whole failing → ⚠️ note and a Binance-only run). Top-500 coins are matched to exchange base assets **by ticker symbol** (`SYMBOL_ALIASES` for the few that differ, e.g. `beam → BEAMX`). Entries sorted by market-cap rank, Binance-only pairs after the ranked ones.
2. `scan()` — `ThreadPoolExecutor(WORKERS=4)`, `evaluate()` per pair = 3 kline requests (1d/1w/1M) — roughly 2,200 requests for ~740 pairs, 3–5 min on a runner (workflow timeout 60 min). Per-pair exceptions are counted, never fatal; error types are printed in the job log.
3. Matches are diffed against `state.json`: new-first sorting, then by daily RSI (bulls ascending, bears descending). An exchange whose every pair errored keeps its previous state entries instead of reporting them as "left".
4. `render_message()` → Telegram (plain text, no parse_mode, split on line boundaries under 4000 chars) → `append_archive()` → `log_matches()` → `save_state()`. `DRY_RUN=1` prints the message and writes nothing.

## Data sources

- **Binance spot** `https://data-api.binance.vision` (`BINANCE_BASE`) — public mirror; GitHub runners are US-based and the main API blocks them. The owner's ISP cannot reach the mirror, so local runs need `BINANCE_BASE=https://api.binance.com` (verified 2026-09-20).
- **Bitget** `https://api.bitget.com` — `/api/v2/spot/public/symbols` (`status == "online"`, `openTime` not in the future). Of its ~3,100 online USDT pairs, ~2,590 are tokenized stocks / pre-IPO tokens with a lowercase prefix (`rNKE`, `rCVCO`, `preOPAI`); real coin tickers are all-uppercase, so `baseCoin.isupper()` is the filter — leaving ~510 coins, ~215 of them not on Binance (2026-09-25). `/api/v2/spot/market/candles` with the UTC granularities `1Dutc/1Wutc/1Mutc` (plain `1day/1week/1M` are UTC+8 and would misalign with Binance — verified 2026-09-25). Rows are ascending `[start_ms, open, high, low, close, baseVol, usdtVol, quoteVol]` with a start time only, so `_start_closed()` drops the forming candle; one call returns at most 300 daily / 100 weekly rows. Newly listed pairs have no `*utc` candles for a while and answer HTTP 400 code `48001` (RAIN, RLB on 2026-09-25, both listed within two weeks); `_bitget()` then falls back to that pair's UTC+8 `1day/1week/1M` candles (monthly closure shifted by 8 h via `_start_closed(tz_offset)`; `is_fresh()` allows one day of slack for the monthly check). `MIN_QUOTE_VOLUME` (24h USDT, via `/api/v2/spot/market/tickers`) applies to Bitget too.
- **MEXC** `https://api.mexc.com` — Binance-compatible klines (weekly interval is `1W`); `status == "1"` = trading.
- **KuCoin** `https://api.kucoin.com` — candles newest-first `[start, open, close, high, low, vol, turnover]`, start time only, so `_kucoin_closed()` decides the forming candle. KuCoin weeks start Thursday (Binance/MEXC: Monday) — the weekly RSI differs slightly for KuCoin-sourced coins; accepted.
- **CoinGecko** `https://api.coingecko.com/api/v3` — 9 calls per run (2 market pages + 7 categories). Keyless ≈ 10 req/min (`CG_PAUSE` 6 s); with a free Demo key (`COINGECKO_API_KEY`, sent only on the CoinGecko session) 2 s. 429/5xx honour `Retry-After`.

Known limitation: symbol-based matching can pair a CoinGecko coin with a
same-ticker but different asset on an exchange. Rare inside the top 500; add
to `SYMBOL_ALIASES` (or exclude) if one shows up.

## Config knobs (env, top of `scanner.py`)

`GFS_BULL_M_MIN` 60 · `GFS_BULL_W_MIN` 60 · `GFS_BULL_D_MAX` 40 · `GFS_BEAR_M_MAX` 40 ·
`GFS_BEAR_W_MAX` 40 · `GFS_BEAR_D_MIN` 60 · `RSI_LENGTH` 14 · `TOP_N` 500 · `EXCLUDE_CATEGORIES` (see table) ·
`MIN_QUOTE_VOLUME` 0 · `WORKERS` 4 · `REQUEST_PAUSE` 0.05 · `BINANCE_BASE` data-api.binance.vision ·
`COINGECKO_API_KEY` "" · `CG_PAUSE` 6 keyless / 2 with key · `DRY_RUN` 0 · `MAX_SYMBOLS` 0 (testing) ·
`STATE_FILE` state.json · `ALERTS_ARCHIVE` alerts_archive.txt · `MATCHES_LOG` matches.jsonl.
`CANDLES` = 400 daily / 300 weekly / 200 monthly (RSI warm-up; more history = closer to the chart).

## Secrets — SECURITY

`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (and optional `COINGECKO_API_KEY`) come
**only** from environment variables / GitHub Actions secrets. Never hardcode
them in code, commits, logs or docs.

## Running locally

```bash
pip install -r requirements.txt
DRY_RUN=1 MAX_SYMBOLS=20 BINANCE_BASE=https://api.binance.com python scanner.py
# PowerShell: $env:DRY_RUN="1"; $env:MAX_SYMBOLS="20"; $env:BINANCE_BASE="https://api.binance.com"; python scanner.py
```

`DRY_RUN=1` never sends Telegram or writes files. For a real local run set the
two Telegram env vars and point `STATE_FILE` / `ALERTS_ARCHIVE` / `MATCHES_LOG`
at scratch paths if the committed files should stay untouched.

On this machine Windows Smart App Control blocks unsigned compiled Python
extensions on first load, which is one reason this scanner is pure Python
(`requests` only, no pandas).

## Deployment

Push to a GitHub repo; Actions runs the cron. The daily commit-back keeps the
repo active so GitHub's 60-day scheduled-workflow pause never triggers.
