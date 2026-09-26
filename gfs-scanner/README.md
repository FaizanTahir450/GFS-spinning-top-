# GFS RSI Scanner

Daily Telegram alert for the **GFS** multi-timeframe RSI setup, run for free on
GitHub Actions. No server, no UI.

| Side | Monthly RSI | Weekly RSI | Daily RSI |
|------|-------------|------------|-----------|
| 🟢 Bullish | above 60 | above 60 | 40 or below |
| 🔴 Bearish | below 40 | below 40 | 60 or above |

RSI is the standard 14-period Wilder RSI (same as TradingView). Monthly and
weekly values are the live ones a chart shows at the daily close; the daily
value is from the last closed daily candle.

**Coverage:** every Binance USDT spot pair, every Bitget USDT spot pair (for the
coins Binance does not list), plus the CoinGecko top-500 coins by market cap.
Top-500 coins on neither exchange are scanned on MEXC, then KuCoin. Stablecoins,
wrapped/staked tokens and leveraged tokens are skipped.

## Setup (one time)

1. **Telegram bot** — talk to `@BotFather`, `/newbot`, copy the token. Start a
   chat with the bot (or add it to a group) and get the chat id, e.g. via
   `https://api.telegram.org/bot<TOKEN>/getUpdates` after sending it a message.
2. This scanner lives in the **GFS-spinning-top** repo next to the spinning-top
   scanner. The secrets below are set once on that repo and serve both.
3. **Secrets** — repo → Settings → Secrets and variables → Actions → New repository secret:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `COINGECKO_API_KEY` (optional; a free Demo key from coingecko.com raises the rate limit)
4. Actions → **GFS RSI Scan** → Run workflow (tick *dry run* first to see the
   message in the job log without sending anything).

The workflow then runs by itself every day at 00:15 UTC (05:15 PKT) and commits
`state.json`, `alerts_archive.txt` and `matches.jsonl` back to the repo.

## What the message looks like

```
📊 GFS RSI Scan — 21 Sep 2026 (after the daily close)

Rules: 🟢 M>60 W>60 D≤40 · 🔴 M<40 W<40 D≥60 · RSI 14 · M/W live, D closed

Universe: 670 pairs · Binance 396 · Bitget 213 · MEXC 56 · KuCoin 5

🟢 Bullish GFS (M/W strong, D pulled back) (3):
  • 🆕 SOL [Binance]  M 68 · W 63 · D 35  @ 142.5  #6
  • ETH [Binance]  M 71 · W 66 · D 38  @ 3,905  #2  (4d)
  • ABC [Bitget]  M 62 · W 61 · D 31  @ 0.0123  #412

🔴 Bearish GFS (M/W weak, D bounced): none

↩️ Left since last run: XYZ (bull)
```

🆕 = entered the list this run; `(4d)` = days it has been on the list;
`#6` = CoinGecko market-cap rank; `[Binance]` / `[Bitget]` / `[MEXC]` / `[KuCoin]`
= the exchange the coin was scanned on (Binance first, the others only when
Binance has no USDT pair). Coins without any USDT pair and the "too little
history" / "stale" counts are printed in the Actions job log, not in the message.

## Running locally

```bash
pip install -r requirements.txt
DRY_RUN=1 python scanner.py                  # PowerShell: $env:DRY_RUN="1"; python scanner.py
DRY_RUN=1 MAX_SYMBOLS=20 python scanner.py   # quick smoke test on the 20 largest coins
```

If your ISP cannot reach `data-api.binance.vision` (the geo-unrestricted mirror
GitHub's runners use), set `BINANCE_BASE=https://api.binance.com` for local runs.

A full run scans ~740 pairs on three timeframes and takes a few minutes.
Bitget's ~2,600 tokenized stocks and pre-IPO tokens are not coins and are skipped.

## Tuning (environment variables)

| Variable | Default | Meaning |
|----------|---------|---------|
| `GFS_BULL_M_MIN` / `GFS_BULL_W_MIN` / `GFS_BULL_D_MAX` | 60 / 60 / 40 | Bullish thresholds |
| `GFS_BEAR_M_MAX` / `GFS_BEAR_W_MAX` / `GFS_BEAR_D_MIN` | 40 / 40 / 60 | Bearish thresholds |
| `RSI_LENGTH` | 14 | RSI period |
| `TOP_N` | 500 | CoinGecko coins by market cap to cover |
| `EXCLUDE_CATEGORIES` | stablecoins, tokenized-products, tokenized-stock, tokenized-private-credit, yield-bearing-stablecoins, bittensor-subnets, bstocks-ecosystem | CoinGecko categories dropped from the top list (comma-separated) |
| `KEEP_BASES` | SHIB | Real coins spelled `<TICKER>B` that must not be treated as Binance tokenized stocks (comma-separated) |
| `MIN_QUOTE_VOLUME` | 0 | Skip Binance pairs under this 24h USDT volume (0 = keep all) |
| `WORKERS` | 4 | Parallel kline fetchers |
| `BINANCE_BASE` | data-api.binance.vision | Binance host (use api.binance.com locally if the mirror is blocked) |
| `DRY_RUN` | 0 | `1` = print the message, send nothing, write nothing |
| `MAX_SYMBOLS` | 0 | Testing: scan only the first N pairs |
