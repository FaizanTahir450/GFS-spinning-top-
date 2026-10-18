# CLAUDE.md — gfs-psx-scanner

Guidance for future Claude Code sessions. This is the **PSX** (Pakistan Stock
Exchange) edition of the GFS RSI screener next door in `../gfs-scanner/`
(crypto). Same owner, same rule, same message shape, same Telegram secrets;
self-contained by this repo's rule (copy, don't import). The owner's other PSX
scanner — `D:\projects\All time high strategy\psx_scanner.py` (ATH zone, SCS
Trade + TradingView) — is where the PSX data-source knowledge came from.

## What this project is

A single-file Python scanner that runs **Mon–Fri at 18:00 PKT on GitHub
Actions**, pulls one TradingView screener response covering every PSX common
stock (last bar + RSI 14 on D / W / M), keeps the ones matching the **GFS**
rules and sends one plain-text **Telegram** message. `state.json` remembers the
previous list for 🆕 / days-on-list / "left" diffs. No server, no UI.

Built 2026-10-18 after the owner asked "does GFS work for PSX?" and answered
the spec questions: **every listed equity** (no liquidity / index filter) and
**the same Telegram chat as the crypto GFS**.

## Strategy — DO NOT change unless the owner explicitly asks

| Rule | Value | Implementation |
|------|-------|----------------|
| Bullish GFS | monthly RSI **> 60** AND weekly RSI **> 60** AND daily RSI **≤ 40** | `gfs_side()`; env `GFS_BULL_M_MIN`, `GFS_BULL_W_MIN`, `GFS_BULL_D_MAX` (same names as the crypto scanner) |
| Bearish GFS | monthly RSI **< 40** AND weekly RSI **< 40** AND daily RSI **≥ 60** | `GFS_BEAR_M_MAX`, `GFS_BEAR_W_MAX`, `GFS_BEAR_D_MIN` |
| RSI | **Wilder 14**, TradingView's own values (`RSI`, `RSI\|1W`, `RSI\|1M` screener columns). The screener exposes RSI 14 only, so `RSI_LENGTH` is fixed | `tradingview_scan()` |
| RSI basis | **Monthly & weekly on the forming candle, daily on the closed session candle** — after the 18:00 PKT run all three end on the same session close, exactly what the chart shows | the screener's semantics; an intraday run flags "daily candle still forming" in the header |
| Universe | **Every PSX common stock in TradingView's Pakistan screener** (`type == stock`, `subtype == common`; 484 on 2026-10-18 — the ~700-symbol PSX universe minus dead listings) | `tradingview_scan()` |
| Exclusions | Preference / rights share classes of a listed company (`GCWLPRS`, `EFULR`, `CENIR`, `LSEFSLR` on 2026-10-18); stocks with no trade within `PSX_MAX_STALE_DAYS` (10) calendar days of the session; stocks whose D/W/M RSI is null (fewer than 15 candles on that timeframe — new listings); optional `PSX_MIN_AVG_VOLUME` floor (0 = off) | `is_share_class()`, `scan()` |
| Alerts | **Full list every run**, 🆕 for new entries, `(Nd)` calendar days on list, "↩️ Left since last run". Every line `SYM [PSX]  M · W · D  @ close  #rank`; rank = market-cap rank among PSX stocks (`market_cap_basic`; 127 of 484 have none → unranked). Skip counts go to the job log only (owner's message-style preference) | `render_message()`, `fmt_match()` |
| Schedule | **Mon–Fri 13:17 UTC ≈ 18:17 PKT** (PSX closes 15:30 Mon–Thu / 16:30 Fri; off the top of the hour as GitHub advises). GitHub's cron on the owner's repos has fired 5–8 h late and double-fired, so the run judges everything against `expected_session()` (the session closed by run time), never the run's calendar date, and `state.json["session"]` blocks a repeat alert for the same session (`PSX_RESEND=1` overrides) | `../.github/workflows/gfs_psx.yml` cron `17 13 * * 1-5`, checkout `ref: main` |

## Why TradingView's RSI and not our own

The ATH scanner computes from SCS Trade raw history because it needs the
all-time high. GFS only needs three RSI values, and the screener already has
them — adjusted for bonus shares / splits the way the chart is. Verified on
2026-10-18 with `verify_rsi.py` (SCS Trade history → gap-based corporate-action
back-adjustment → ISO-week / calendar-month resample → Wilder RSI 14):

| Symbol | Screener D / W / M | Own D / W / M | Note |
|--------|--------------------|---------------|------|
| OGDC | 33.41 / 41.83 / 59.13 | 33.41 / 41.83 / 59.13 | exact |
| HBL | 37.91 / 45.21 / 59.83 | 37.91 / 45.19 / 59.83 | |
| SYS | 48.07 / 34.22 / 46.59 | 48.07 / 34.22 / 46.56 | two splits (2022, 2025) adjusted |
| LUCK | 38.43 / 39.86 / 55.87 | 38.43 / 39.85 / 55.60 | 1:5 split 2025-04-28; **unadjusted** monthly would be 40.89 |
| 786 | 47.07 / 42.86 / 53.02 | 47.07 / 42.73 / 52.63 | SCS Trade history starts 2020, TradingView's earlier |

Full-history SCS Trade calls cost 3–16 s each (≈2 h for the universe), so a
home-grown version would need a committed price cache; the screener is one
1–2 s request. Trade-off: a single unofficial data source. If the screener ever
changes, `verify_rsi.py` holds the independent computation to fall back on.

## Files

| File | Purpose |
|------|---------|
| `scanner.py` | Everything: config, screener client, session resolution (SCS Trade bellwether probe), rule, state, Telegram, archive, matches log. |
| `verify_rsi.py` | Dev tool, not run by the workflow: recomputes RSI from SCS Trade history and prints it next to the screener's. |
| `../.github/workflows/gfs_psx.yml` (repo root) | Cron + `workflow_dispatch` (`dry_run`), `working-directory: gfs-psx-scanner`. Commits `state.json`, `alerts_archive.txt`, `matches.jsonl` back (skipped on dry run). Concurrency group `gfs-spinning-top-scan` shared with the other two workflows. |
| `state.json` | Created on first real run; committed. `{"bull": {SYM: {first_seen}}, "bear": {…}, "session", "updated"}`. |
| `alerts_archive.txt` | Append-only copy of every Telegram message (`=====` divider), holiday / outage one-liners included. |
| `matches.jsonl` | One line per (session, side, symbol): rsi_m/rsi_w/rsi_d, close, volume, rank, first_seen, new. |
| `README.md` | End-user guide. |

## Data flow (`main()`)

1. `tradingview_scan()` — POST `https://scanner.tradingview.com/pakistan/scan`
   with `TV_COLUMNS`; retries network / 429 / 5xx three times. Fewer than
   `MIN_ROWS` (100) rows ⇒ ⚠️ "screener unavailable" one-liner, state kept.
   `rank_by_mcap()` numbers the rows.
2. `expected_session()` = the latest weekday whose session closed by run time
   (today after `SESSION_CUTOFF` 16:45 PKT, else the previous weekday).
   `resolve_session(rows, expected)` — session = most common bar date (`time`
   → PKT date). If it is older than `expected`, `scs_traded(expected)` asks SCS
   Trade (14-day span of a bellwether, ~0.2 s): no bar there either ⇒
   **holiday** one-liner; a bar there ⇒ the screener is late ⇒ re-fetch every
   `PSX_SESSION_POLL` (300 s) up to `PSX_SESSION_WAIT` (1800 s), then ⚠️
   "stale" one-liner, state kept; SCS Trade unreachable ⇒ same wait, then the
   holiday one-liner marked "could not confirm". A session newer than
   `expected` (run before the cutoff) is an **intraday** run: header says
   "daily candle still forming", message sent, state / log not written.
3. `scan()` — share-class, stale, history and volume skips (counted, job log
   only), then `gfs_side()` per stock.
4. `state.json["session"] == session` ⇒ already alerted (cron double-fire):
   print and exit without sending (`PSX_RESEND=1` overrides; dry runs ignore
   the guard). Otherwise diff against `state.json` (keys = symbols): new-first
   sorting, then by daily RSI (bulls ascending, bears descending).
   `render_message()` → Telegram (`send_telegram()` retries each ≤4000-char
   part on dropped connections, 429 and 5xx) → `append_archive()` →
   `log_matches()` → `save_state()`. `DRY_RUN=1` prints the message and writes
   nothing.

## Data sources

- **TradingView screener** `POST https://scanner.tradingview.com/pakistan/scan`
  — unofficial but stable; the ATH scanner has used it daily since 2026-10-01
  for the PSX universe and fallback bars. Column notes: `time` = last bar open
  (UTC, 09:15 PKT on the session date); `RSI|1W` / `RSI|1M` use TradingView's
  weekly (Monday-start) and calendar-month candles; nulls on 2026-10-18: RSI 3,
  `RSI|1W` 10, `RSI|1M` 26 (new listings); `market_cap_basic` null for 127.
  Other useful columns if ever needed: `open`, `high`, `low`, `change`,
  `Value.Traded`, `time|1W`, `time|1M`, `close|1W`, `close|1M`, `sector`.
- **SCS Trade** `POST https://www.scstrade.com/stockscreening/SS_CompanySnapShotHP.aspx/chart`
  `{"par": SYM, "date1": "MM/DD/YYYY", "date2": …}` → `{"d": [{trading_Date:
  "/Date(ms)/", trading_open, trading_high, trading_low, trading_close,
  trading_vol}]}` newest first. Used only by `scs_traded()` (holiday check) and
  `verify_rsi.py`. Raw exchange prints, history mostly from 2006, no bars on
  no-trade days.
- The official PSX portal (`dps.psx.com.pk`) data routes have answered HTML 404
  since ~2026-09-15 and are not used. The owner rejects Yahoo for PSX.

## Config knobs (env, top of `scanner.py`)

`GFS_BULL_M_MIN` 60 · `GFS_BULL_W_MIN` 60 · `GFS_BULL_D_MAX` 40 · `GFS_BEAR_M_MAX` 40 ·
`GFS_BEAR_W_MAX` 40 · `GFS_BEAR_D_MIN` 60 · `PSX_MAX_STALE_DAYS` 10 · `PSX_MIN_AVG_VOLUME` 0 ·
`PSX_SESSION_WAIT` 1800 · `PSX_SESSION_POLL` 300 · `PSX_RESEND` 0 · `DRY_RUN` 0 · `MAX_SYMBOLS` 0 ·
`STATE_FILE` state.json · `ALERTS_ARCHIVE` alerts_archive.txt · `MATCHES_LOG` matches.jsonl.

## Secrets — SECURITY

`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` come **only** from environment
variables / GitHub Actions secrets (the repo-level ones shared by all three
scanners). Never hardcode them in code, commits, logs or docs.

## Running locally

```bash
pip install -r requirements.txt
DRY_RUN=1 python scanner.py
# PowerShell: $env:DRY_RUN="1"; python scanner.py
PSX_SESSION_WAIT=0 DRY_RUN=1 python scanner.py   # weekday-evening test without the 30-min wait path
python verify_rsi.py OGDC LUCK SYS
```

Both endpoints are reachable from the owner's machine (unlike Binance's
mirror). Pure Python, `requests` only — Windows Smart App Control here blocks
unsigned compiled extensions.

## Deployment

Same repo as the other scanners: `https://github.com/FaizanTahir450/GFS-spinning-top-`
(trailing hyphen), branch `main`. The weekday commit-back keeps the repo active
so GitHub's 60-day scheduled-workflow pause never triggers.
