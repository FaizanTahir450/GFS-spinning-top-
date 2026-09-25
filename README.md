# GFS-spinning-top

Two crypto scanners that run for free on GitHub Actions and post to one
Telegram bot. No server, no UI.

| Scanner | What it alerts on | When it runs |
|---------|-------------------|--------------|
| [gfs-scanner](gfs-scanner/) | **GFS** multi-timeframe RSI: 🟢 monthly RSI > 60, weekly > 60, daily ≤ 40 · 🔴 monthly < 40, weekly < 40, daily ≥ 60 | daily 00:15 UTC |
| [spinning-top-scanner](spinning-top-scanner/) | **Spinning top on high volume** at a 10-candle high or low: body ≤ 30 % of range, both wicks ≥ body, volume ≥ 1.5× the 20-candle average | daily, Mondays (weekly), the 1st (monthly), 00:15 UTC |

**Coverage (both):** every Binance USDT spot pair, every Bitget USDT spot pair
for coins Binance does not list, plus the CoinGecko top-500 coins by market cap
(those on neither exchange are scanned on MEXC, then KuCoin). About 740 pairs.
Stablecoins, tokenized stocks and treasuries, wrapped/staked tokens and
leveraged tokens are skipped. Every message shows the per-exchange counts and tags non-Binance hits
`[Bitget]`, `[MEXC]` or `[KuCoin]`.

Each scanner's folder has its own README with the exact rules, message format
and tuning knobs.

## Setup (one time)

1. **Telegram bot** — talk to `@BotFather`, `/newbot`, copy the token. Start a
   chat with the bot (or add it to a group) and get the chat id, e.g. via
   `https://api.telegram.org/bot<TOKEN>/getUpdates` after sending it a message.
2. **Secrets** — this repo → Settings → Secrets and variables → Actions → New repository secret:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `COINGECKO_API_KEY` (optional; a free Demo key from coingecko.com triples the rate limit and avoids 60-second waits)
3. **First run** — Actions → *GFS RSI Scan* or *Spinning Top Scan* → Run workflow.
   Tick **dry run** to see the message in the job log without sending anything.

After that the crons run by themselves. Each run commits its state / archive /
log files back into its scanner's folder, which also keeps the repo active so
GitHub never pauses the schedules.

## Running locally

```bash
cd gfs-scanner                       # or spinning-top-scanner
pip install -r requirements.txt
DRY_RUN=1 MAX_SYMBOLS=30 python scanner.py
```

If your ISP cannot reach `data-api.binance.vision` (the mirror GitHub's runners
use), add `BINANCE_BASE=https://api.binance.com`.
