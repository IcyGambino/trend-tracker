#!/usr/bin/env python3
"""
Intraday bullish/bearish trend tracker.

Signal:  current price vs the day's OPEN (with a deadband to avoid whipsaw).
Alerts:  posts to Discord only when a ticker's bias FLIPS (bullish <-> bearish),
         plus one "opening bias" note per ticker on the first read of a new day.
Runtime: built for GitHub Actions on a schedule. State persists in state.json,
         which the workflow commits back to the repo between runs.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# CONFIG  —  edit these
# ---------------------------------------------------------------------------
TICKERS = ["SPY", "QQQ"]        # add / remove symbols here
DEADBAND_PCT = 0.001            # 0.1% neutral zone around the open (anti-whipsaw)
ALERT_ON_NEW_DAY = True         # announce each ticker's starting bias once per day
STATE_FILE = Path("state.json")
# ---------------------------------------------------------------------------

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
ET = ZoneInfo("America/New_York")


def market_is_open(now_et: datetime) -> bool:
    """Rough US equities regular-hours check: Mon-Fri, 9:30-16:00 ET.
    Does not account for market holidays."""
    if now_et.weekday() >= 5:  # Sat / Sun
        return False
    open_t = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_t <= now_et <= close_t


def fetch_quote(symbol: str) -> dict:
    url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_API_KEY}"
    req = urllib.request.Request(url, headers={"User-Agent": "trend-tracker"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def classify(price: float, open_: float, band: float) -> str:
    """bullish above open+band, bearish below open-band, else neutral."""
    if open_ <= 0:
        return "neutral"
    if price > open_ * (1 + band):
        return "bullish"
    if price < open_ * (1 - band):
        return "bearish"
    return "neutral"


def send_discord(symbol: str, bias: str, price: float, open_: float, prev_bias):
    pct = (price / open_ - 1) * 100 if open_ else 0
    color = {"bullish": 0x2ECC71, "bearish": 0xE74C3C}.get(bias, 0x95A5A6)
    arrow = {"bullish": "\u25b2", "bearish": "\u25bc"}.get(bias, "\u2014")
    if prev_bias:
        title = f"{arrow} {symbol} flipped {prev_bias.upper()} \u2192 {bias.upper()}"
    else:
        title = f"{arrow} {symbol} opening bias: {bias.upper()}"

    payload = {
        "embeds": [{
            "title": title,
            "color": color,
            "fields": [
                {"name": "Price", "value": f"${price:,.2f}", "inline": True},
                {"name": "Open", "value": f"${open_:,.2f}", "inline": True},
                {"name": "vs Open", "value": f"{pct:+.2f}%", "inline": True},
            ],
            "timestamp": datetime.now(tz=ZoneInfo("UTC")).isoformat(),
        }]
    }
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=15).read()


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def main():
    if not FINNHUB_API_KEY or not DISCORD_WEBHOOK_URL:
        sys.exit("Missing FINNHUB_API_KEY or DISCORD_WEBHOOK_URL environment variables.")

    now_et = datetime.now(tz=ET)
    if not market_is_open(now_et):
        print(f"Market closed at {now_et:%Y-%m-%d %H:%M %Z} - skipping.")
        return

    today = now_et.strftime("%Y-%m-%d")
    state = load_state()

    for symbol in TICKERS:
        try:
            q = fetch_quote(symbol)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            print(f"[{symbol}] quote fetch failed: {e}")
            continue

        price = q.get("c") or 0
        open_ = q.get("o") or 0
        if not price or not open_:
            print(f"[{symbol}] incomplete quote {q} - skipping.")
            continue

        rec = state.get(symbol, {})
        new_day = rec.get("day") != today
        prev_bias = None if new_day else rec.get("bias")   # fresh slate each day

        bias = classify(price, open_, DEADBAND_PCT)
        flipped = (
            bias != "neutral"
            and prev_bias not in (None, "neutral")
            and bias != prev_bias
        )

        should_alert, alert_prev = False, prev_bias
        if new_day and ALERT_ON_NEW_DAY and bias != "neutral":
            should_alert, alert_prev = True, None
        elif flipped:
            should_alert = True

        if should_alert:
            try:
                send_discord(symbol, bias, price, open_, alert_prev)
                print(f"[{symbol}] ALERT {alert_prev} -> {bias} @ {price}")
            except (urllib.error.URLError, TimeoutError) as e:
                print(f"[{symbol}] discord post failed: {e}")
        else:
            print(f"[{symbol}] {bias} (prev {prev_bias}) @ {price} vs open {open_}")

        # Keep the last DEFINITE bias so a drift into the deadband
        # doesn't erase the signal we're comparing flips against.
        stored_bias = bias if bias != "neutral" else prev_bias
        state[symbol] = {
            "bias": stored_bias,
            "day": today,
            "price": price,
            "open": open_,
            "updated": now_et.isoformat(),
        }

    save_state(state)


if __name__ == "__main__":
    main()
