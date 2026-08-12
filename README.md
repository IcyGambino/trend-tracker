# Market Trend Tracker

Automated intraday market monitoring that posts to Discord.

## What it does
- **Trend tracking** — reads 10 tickers (SPY, QQQ, IWM, SMH, VXX, MSFT, NVDA, TSLA, PLTR, SPCX) every 5 minutes during US market hours and posts each ticker's bias (bullish / bearish / neutral vs the day's open, with a 0.1% deadband).
- **Confluence & divergence alerts** — a combined alert fires when every tracked ticker points the same way, or when they split, with a short AI-written context note.
- **Inverse instruments** — risk-off tells (VXX) are flipped so their signal reads as market direction.
- **Daily briefings** — a pre-market Morning Brief (~9:00 ET) and an end-of-day Close Recap (~4:10 ET) written by Claude from live quotes, headlines, and the day's flip timeline.
- **Day-story memory** — every open and flip is logged with a timestamp in `state.json`, so the close recap can narrate how the session developed.

## How it runs
- `market-tracker.yml` — self-looping GitHub Actions job: a daily kicker starts it near the open, then it re-runs `tracker.py` every 5 minutes until the close.
- `briefing.yml` — scheduled workflow that runs `briefing.py` for the morning and close briefings.
- `register-command.yml` — one-time helper that registers Discord slash commands.
- A companion Cloudflare Worker (separate) powers the `/ask` and `/ticker` Discord commands.

## Configuration
All credentials live in GitHub Actions **secrets** (never in code):
`FINNHUB_API_KEY`, `DISCORD_WEBHOOK_URL`, `ANTHROPIC_API_KEY`, `DISCORD_APP_ID`, `DISCORD_BOT_TOKEN`.

Tickers and the deadband are set at the top of `tracker.py` / `briefing.py`.

*All output is informational only — not financial advice.*
