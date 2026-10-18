# GFS RSI Scanner — PSX

Telegram alert for the **GFS** multi-timeframe RSI setup on every Pakistan
Stock Exchange equity, after each trading session, run for free on GitHub
Actions. Same rule as the crypto [gfs-scanner](../gfs-scanner/), different market.

| Side | Monthly RSI | Weekly RSI | Daily RSI |
|------|-------------|------------|-----------|
| 🟢 Bullish | above 60 | above 60 | 40 or below |
| 🔴 Bearish | below 40 | below 40 | 60 or above |

RSI is the standard 14-period Wilder RSI, taken straight from TradingView's PSX
screener — the same bonus/split-adjusted numbers a TradingView chart shows after
the close. Monthly and weekly values are the live ones; the daily value is from
the closed session candle.

**Coverage:** every PSX common stock TradingView lists (about 480). Preference /
rights share classes are dropped, stocks with no trade in the last 10 days and
stocks with under 15 candles on any timeframe are held out. No market-cap or
liquidity filter by default.

## Setup (one time)

1. This scanner lives in the **GFS-spinning-top** repo next to the crypto GFS and
   spinning-top scanners and uses the same `TELEGRAM_BOT_TOKEN` and
   `TELEGRAM_CHAT_ID` secrets — nothing new to add if those are set.
2. Actions → **GFS PSX RSI Scan** → Run workflow (tick *dry run* first to see the
   message in the job log without sending anything).

The workflow then runs by itself Monday–Friday at about 18:17 PKT (13:17 UTC),
after the PSX close, and commits `state.json`, `alerts_archive.txt` and
`matches.jsonl` back to the repo. On a PSX holiday it sends a one-line
"market closed" note and leaves the list untouched. GitHub's schedule can run
hours late; the scanner always reports the session that had closed by run
time, and a second run for the same session sends nothing.

## What the message looks like

```
📊 GFS RSI Scan — PSX — 16 Oct 2026 (after the close)

Rules: 🟢 M>60 W>60 D≤40 · 🔴 M<40 W<40 D≥60 · RSI 14 · M/W live, D closed

Universe: 480 PSX equities · 455 scanned

🟢 Bullish GFS (M/W strong, D pulled back) (2):
  • 🆕 MEBL [PSX]  M 77 · W 61 · D 39  @ 545.82  #3
  • FFC [PSX]  M 65 · W 62 · D 36  @ 537.87  #4  (3d)

🔴 Bearish GFS (M/W weak, D bounced): none

↩️ Left since last run: SYS (bear)
```

🆕 = entered the list this session; `(3d)` = days it has been on the list;
`#3` = market-cap rank among PSX stocks (TradingView's figure; stocks it has no
market cap for carry no rank). Skip counts (not traded lately, too little
history, share classes) are printed in the Actions job log, not in the message.

## Running locally

```bash
pip install -r requirements.txt
DRY_RUN=1 python scanner.py                  # PowerShell: $env:DRY_RUN="1"; python scanner.py
DRY_RUN=1 MAX_SYMBOLS=50 python scanner.py   # the 50 largest stocks only
python verify_rsi.py OGDC LUCK SYS           # compare the screener's RSI with RSI computed from SCS Trade history
```

A run is one screener request and takes a few seconds. Run before 16:45 PKT on
a trading day and the daily candle is still forming — the header says so, and
such a run leaves `state.json` alone so the after-close run stays authoritative.

## Tuning (environment variables)

| Variable | Default | Meaning |
|----------|---------|---------|
| `GFS_BULL_M_MIN` / `GFS_BULL_W_MIN` / `GFS_BULL_D_MAX` | 60 / 60 / 40 | Bullish thresholds |
| `GFS_BEAR_M_MAX` / `GFS_BEAR_W_MAX` / `GFS_BEAR_D_MIN` | 40 / 40 / 60 | Bearish thresholds |
| `PSX_MAX_STALE_DAYS` | 10 | Hold out stocks with no trade within this many days of the session |
| `PSX_MIN_AVG_VOLUME` | 0 | Hold out stocks whose 10-day average volume (shares/day) is below this (0 = keep all) |
| `PSX_SESSION_WAIT` | 1800 | Seconds to wait for the screener to show today's bar when SCS Trade says PSX traded |
| `PSX_RESEND` | 0 | `1` = send even if `state.json` says this session was already alerted |
| `DRY_RUN` | 0 | `1` = print the message, send nothing, write nothing |
| `MAX_SYMBOLS` | 0 | Testing: scan only the N largest stocks |
