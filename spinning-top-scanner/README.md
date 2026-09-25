# Spinning Top Scanner

Telegram alerts for **spinning-top candles on high volume**, on daily, weekly
and monthly charts, run for free on GitHub Actions. No server, no UI.

A spinning top is a small-bodied candle with a real shadow on both sides —
indecision. On high volume after a move it often marks exhaustion.

| Rule | Value |
|------|-------|
| Body | at most 30 % of the candle's high-low range |
| Wicks | each at least as long as the body, and each at least 20 % of the range |
| Volume | at least 1.5× the average of the previous 20 candles |
| Swing extreme | the candle's high is the highest high, or its low the lowest low, of the previous 10 candles |
| Candle | the last **closed** candle of the timeframe |

Dojis (body under 10 %) count and are tagged `(doji)`. Each hit shows the
close-to-close move over the 5 candles before it: `↑` after an up-move (watch
for a bearish turn), `↓` after a down-move (watch for a bullish turn).

**Coverage:** every Binance USDT spot pair, every Bitget USDT spot pair (for the
coins Binance does not list), plus the CoinGecko top-500 coins by market cap.
Top-500 coins on neither exchange are scanned on MEXC, then KuCoin. Stablecoins,
wrapped/staked tokens and leveraged tokens are skipped.

**Schedule:** daily at 00:15 UTC (05:15 PKT) after the daily close; Mondays for
the weekly candle; the 1st of the month for the monthly candle.

## Setup (one time)

1. **Telegram bot** — talk to `@BotFather`, `/newbot`, copy the token. Start a
   chat with the bot (or add it to a group) and get the chat id, e.g. via
   `https://api.telegram.org/bot<TOKEN>/getUpdates` after sending it a message.
2. This scanner lives in the **GFS-spinning-top** repo next to the GFS scanner.
   The secrets below are set once on that repo and serve both.
3. **Secrets** — repo → Settings → Secrets and variables → Actions → New repository secret:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `COINGECKO_API_KEY` (optional; a free Demo key from coingecko.com raises the rate limit)
4. Actions → **Spinning Top Scan** → Run workflow → pick the timeframe (tick
   *dry run* first to see the message in the job log without sending anything).

The workflow then runs by itself and commits `signals.jsonl` and
`alerts_archive.txt` back to the repo.

## What the message looks like

```
🕯️ Spinning Top Scan — Daily — 21 Sep 2026

Candle checked: daily candle of Sun 20 Sep 2026 UTC — closed candles only, the forming candle is ignored

Rule: body ≤30% of range · wicks ≥1× body & ≥20% of range · volume ≥1.5× avg(20) · at a 10-candle high or low

Universe: 740 pairs (Binance 480 · Bitget 215 · MEXC 40 · KuCoin 5) · top-500 not covered: 50 · too little history: 4 · stale/halted: 3

Spinning tops on high volume (3), biggest volume first:
  • DOGE  vol 3.4×  body 4% (doji)  ⬇ 10-bar low  ↓ -12.1% into it  @ 0.0912  #8
  • BTC  vol 2.1×  body 12%  ⬆ 10-bar high  ↑ +8.4% into it  @ 61,234  #1
  • ABC [Bitget]  vol 1.7×  body 22%  ⬆ 10-bar high  ↑ +3.0% into it  @ 0.0123  #412

⬆/⬇ = the candle set the highest high / lowest low of the previous 10 candles · ↑ = came after an up-move (watch for a bearish turn) · ↓ = after a down-move (watch for a bullish turn) · % is the close-to-close move over the previous 5 candles
```

`#8` = CoinGecko market-cap rank; `[Bitget]` / `[MEXC]` / `[KuCoin]` = scanned
there because Binance has no USDT pair for it.

## Running locally

```bash
pip install -r requirements.txt
DRY_RUN=1 python scanner.py                              # daily
DRY_RUN=1 TIMEFRAME=1w python scanner.py                 # weekly   (PowerShell: $env:TIMEFRAME="1w")
DRY_RUN=1 TIMEFRAME=1M MAX_SYMBOLS=30 python scanner.py  # monthly, quick smoke test
```

If your ISP cannot reach `data-api.binance.vision` (the geo-unrestricted mirror
GitHub's runners use), set `BINANCE_BASE=https://api.binance.com` for local runs.

## Tuning (environment variables)

| Variable | Default | Meaning |
|----------|---------|---------|
| `TIMEFRAME` | 1d | `1d` / `1w` / `1M` (or daily / weekly / monthly) |
| `BODY_MAX_PCT` | 30 | Max body as % of range |
| `WICK_MIN_BODY` | 1.0 | Each wick must be at least this × body |
| `WICK_MIN_PCT` | 20 | Each wick must be at least this % of range (0 = off) |
| `VOL_MULT` | 1.5 | Volume vs average of the previous `VOL_LOOKBACK` candles |
| `VOL_LOOKBACK` | 20 | Candles in the volume average (uses what exists, min `VOL_MIN_BARS`=10) |
| `SWING_BARS` | 10 | Candle must set the highest high or lowest low of the previous N candles (0 = off) |
| `TOP_N` | 500 | CoinGecko coins by market cap to cover |
| `EXCLUDE_CATEGORIES` | stablecoins, tokenized-products, tokenized-stock, tokenized-private-credit, yield-bearing-stablecoins, bittensor-subnets, bstocks-ecosystem | CoinGecko categories dropped from the top list (comma-separated) |
| `KEEP_BASES` | SHIB | Real coins spelled `<TICKER>B` that must not be treated as Binance tokenized stocks (comma-separated) |
| `MIN_QUOTE_VOLUME` | 0 | Skip Binance pairs under this 24h USDT volume (0 = keep all) |
| `WORKERS` | 4 | Parallel kline fetchers |
| `BINANCE_BASE` | data-api.binance.vision | Binance host (use api.binance.com locally if the mirror is blocked) |
| `DRY_RUN` | 0 | `1` = print the message, send nothing, write nothing |
| `MAX_SYMBOLS` | 0 | Testing: scan only the first N pairs |
