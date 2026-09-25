# CLAUDE.md — GFS-spinning-top (monorepo)

Guidance for future Claude Code sessions. This repo holds **two independent
scanners** that share one GitHub repo, one Telegram bot and one pair of
secrets, but **no code** (each `scanner.py` is self-contained by design — copy,
don't import). Sibling projects by the same owner with the same shape:
`D:\projects\sweep-scanner`, `D:\projects\All time high strategy`.

## Layout

| Path | Purpose |
|------|---------|
| `gfs-scanner/` | Multi-timeframe RSI screener ("GFS"). Its own `CLAUDE.md` holds the strategy table — the source of truth for its rules. |
| `spinning-top-scanner/` | High-volume spinning-top candle scanner on daily / weekly / monthly. Its own `CLAUDE.md` holds the strategy table. |
| `.github/workflows/gfs.yml` | Daily 00:15 UTC + manual (`dry_run`). `defaults.run.working-directory: gfs-scanner`; commits `gfs-scanner/{state.json, alerts_archive.txt, matches.jsonl}` back. |
| `.github/workflows/spinning_top.yml` | Daily / Monday / 1st 00:15 UTC + manual (timeframe dropdown, `dry_run`). `working-directory: spinning-top-scanner`; commits `spinning-top-scanner/{signals.jsonl, alerts_archive.txt}` back. |
| `README.md` | End-user guide for both scanners (setup, secrets, manual runs). |

Workflows must live at the repo root (`.github/workflows/`) — GitHub ignores
workflow files inside subfolders. Both workflows share the concurrency group
`gfs-spinning-top-scan`, so the two commit-backs (and the overlapping Monday /
1st schedules) never race. State files stay inside each scanner's folder.

## Shared universe (identical code in both scanners)

Every Binance USDT spot pair → every Bitget USDT spot pair whose base Binance
doesn't list (owner asked for Bitget as full coverage after Binance on
2026-09-25) → CoinGecko top-500 coins on neither exchange are looked up on MEXC,
then KuCoin. Exclusions: CoinGecko categories `stablecoins, tokenized-products,
tokenized-stock, tokenized-private-credit, yield-bearing-stablecoins,
bittensor-subnets, bstocks-ecosystem` (their tickers are also removed from the
Binance/Bitget listings, which drops Binance's bStocks such as CRWDB and PLTRB; B-suffixed Binance bases
unknown to the CoinGecko top-500 are dropped too, with `KEEP_BASES`, default `SHIB`,
whitelisting real coins), `USD*`/`*USD` tickers, wrapped/staked names, leveraged
`3L/3S/5L/5S/UP/DOWN` tokens. A freshness guard skips halted pairs. Telegram
messages show the per-exchange counts and tag non-Binance hits `[Bitget]`,
`[MEXC]`, `[KuCoin]`. ~740 pairs on 2026-09-25 (Binance ≈ 480, Bitget ≈ 215, the
rest MEXC/KuCoin fallbacks). Bitget also lists ~2,600 tokenized stocks / pre-IPO
tokens with lowercase prefixes (`rNKE`, `preOPAI`); those are not coins and are
skipped by an all-uppercase ticker check.

## Secrets — SECURITY

`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (and optional `COINGECKO_API_KEY`) are
GitHub Actions repository secrets on this repo, read only from env vars. Never
hardcode them in code, commits, logs or docs.

## Running locally (this machine)

```bash
cd gfs-scanner           # or spinning-top-scanner
DRY_RUN=1 MAX_SYMBOLS=20 BINANCE_BASE=https://api.binance.com python scanner.py
```

`BINANCE_BASE` is needed locally because the owner's ISP cannot reach
`data-api.binance.vision` (the mirror GitHub's US runners must use). Windows
Smart App Control blocks unsigned compiled Python extensions here, which is why
both scanners are pure Python with `requests` as the only dependency.

## Deployment

`https://github.com/FaizanTahir450/GFS-spinning-top-` (note the trailing hyphen),
branch `main`. Actions runs the crons; the commit-backs keep the repo active so
GitHub's 60-day scheduled-workflow pause never triggers.
