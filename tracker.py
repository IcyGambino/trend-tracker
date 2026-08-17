#!/usr/bin/env python3
"""
Intraday bullish/bearish trend tracker.

Signal:  current price vs the day's OPEN (with a deadband to avoid whipsaw).
Posting: two modes, set by POST_EVERY_RUN below.
           True  -> posts every ticker's current status on every run.
           False -> posts only on a FLIP (bullish <-> bearish), plus one
                    opening-bias note per ticker at the first read of the day.
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
TICKERS = ["SPY", "QQQ", "IWM", "SMH", "VXX", "MSFT", "NVDA", "TSLA", "PLTR", "SPCX"]  # add / remove symbols here
INVERSE_TICKERS = {"VXX", "TLT", "UVXY"}  # risk-off tells: price UP = market-bearish.
                                # Bias is flipped so confluence reads them correctly.
                                # Only applies if the symbol is also in TICKERS.
DEADBAND_PCT = 0.001            # 0.1% neutral zone around the open (anti-whipsaw)
POST_EVERY_RUN = True           # True: post status every run. False: flips only.
ALERT_ON_NEW_DAY = True         # (flip-only mode) announce starting bias each day
STATE_FILE = Path("state.json")
# ---------------------------------------------------------------------------

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
ET = ZoneInfo("America/New_York")   # market gate stays on ET - that's when the market trades
CT = ZoneInfo("America/Chicago")    # display/logged times use the user's local Central time


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


def send_discord(symbol, bias, price, open_, event, prev_bias=None):
    pct = (price / open_ - 1) * 100 if open_ else 0
    color = {"bullish": 0x2ECC71, "bearish": 0xE74C3C}.get(bias, 0x95A5A6)
    arrow = {"bullish": "\u25b2", "bearish": "\u25bc"}.get(bias, "\u2014")

    inv = " (inv)" if symbol in INVERSE_TICKERS else ""
    if event == "flip":
        title = f"{arrow} {symbol}{inv} flipped {prev_bias.upper()} \u2192 {bias.upper()}"
    elif event == "open":
        title = f"{arrow} {symbol}{inv} opening bias: {bias.upper()}"
    else:  # "status"
        title = f"{arrow} {symbol}{inv} {bias.upper()}"

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
        headers={
            "Content-Type": "application/json",
            "User-Agent": "trend-tracker (github-actions, v1)",
        },
        method="POST",
    )
    urllib.request.urlopen(req, timeout=15).read()


ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def claude_blurb(prompt: str) -> str:
    """One short Claude call for alert context. Returns '' on any failure."""
    if not ANTHROPIC_API_KEY:
        return ""
    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps({
                "model": "claude-sonnet-4-6",
                "max_tokens": 200,
                "system": ("You write one 2-sentence note for a trading alert. Concrete, plain, "
                           "no hype, no advice, no predictions - just what this configuration "
                           "means and one thing to watch."),
                "messages": [{"role": "user", "content": prompt}],
            }).encode(),
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "User-Agent": "trend-tracker (github-actions, v1)",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode())
        return " ".join(c.get("text", "") for c in data.get("content", [])).strip()[:900]
    except Exception:
        return ""


def send_discord_combined(kind: str, bias: str, details: list):
    """Post a combined market alert. kind: 'confluence' or 'divergence'."""
    if kind == "confluence":
        color = {"bullish": 0x2ECC71, "bearish": 0xE74C3C}.get(bias, 0x95A5A6)
        arrow = {"bullish": "\u25b2", "bearish": "\u25bc"}.get(bias, "\u2014")
        title = f"{arrow}{arrow} MARKET CONFLUENCE: ALL {bias.upper()}"
        desc = "Every tracked ticker is pointing the same way."
    else:
        color = 0xF1C40F  # yellow — mixed tape
        title = "\u26a0\ufe0f MARKET DIVERGENCE: MIXED SIGNALS"
        desc = "Tracked tickers disagree on direction."

    summary = ", ".join(f"{s} {b} {p:+.2f}%" for s, b, p in details)
    context = claude_blurb(f"Alert: {title}. Tickers vs open: {summary}.")
    if context:
        desc = f"{desc}\n\n{context}"

    payload = {
        "embeds": [{
            "title": title,
            "description": desc,
            "color": color,
            "fields": [
                {"name": sym, "value": f"{b.upper()} ({pct:+.2f}%)", "inline": True}
                for sym, b, pct in details
            ],
            "timestamp": datetime.now(tz=ZoneInfo("UTC")).isoformat(),
        }]
    }
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "trend-tracker (github-actions, v1)",
        },
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
        if symbol in INVERSE_TICKERS and bias != "neutral":
            bias = "bearish" if bias == "bullish" else "bullish"
        flipped = (
            bias != "neutral"
            and prev_bias not in (None, "neutral")
            and bias != prev_bias
        )

        # Classify the event for this read.
        if new_day and bias != "neutral":
            event = "open"
        elif flipped:
            event = "flip"
        else:
            event = "status"

        # Decide whether to post.
        if POST_EVERY_RUN:
            should_post = True
        else:
            should_post = event in ("open", "flip")

        if should_post:
            try:
                send_discord(symbol, bias, price, open_, event, prev_bias)
                print(f"[{symbol}] POST {event}: {bias} @ {price} (prev {prev_bias})")
            except (urllib.error.URLError, TimeoutError) as e:
                print(f"[{symbol}] discord post failed: {e}")
        else:
            print(f"[{symbol}] {bias} (prev {prev_bias}) @ {price} vs open {open_}")

        # Keep the last DEFINITE bias so a drift into the deadband
        # doesn't erase the signal we're comparing flips against.
        stored_bias = bias if bias != "neutral" else prev_bias
        rec_out = {
            "bias": stored_bias,
            "day": today,
            "price": price,
            "open": open_,
            "updated": now_et.isoformat(),
        }
        # Day-story memory: log opens and flips with timestamps so briefings
        # and future tools can narrate how the day developed.
        hist = rec.get("history", []) if rec.get("day") == today else []
        if event in ("open", "flip"):
            hist.append({"t": now_et.astimezone(CT).strftime("%H:%M") + " CT", "event": event, "bias": bias, "price": price})
        rec_out["history"] = hist[-40:]
        state[symbol] = rec_out

    # ------------------------------------------------------------------
    # Combined market read: confluence / divergence across all tickers.
    # Posts only when the combined state CHANGES, so it stays meaningful.
    # ------------------------------------------------------------------
    reads = []
    for symbol in TICKERS:
        rec = state.get(symbol, {})
        if rec.get("day") != today:
            continue
        b = rec.get("bias")
        if b in ("bullish", "bearish"):
            price, open_ = rec.get("price", 0), rec.get("open", 0)
            pct = (price / open_ - 1) * 100 if open_ else 0
            reads.append((symbol, b, pct))

    combined = "unknown"
    if len(reads) >= 2:
        biases = {b for _, b, _ in reads}
        if biases == {"bullish"}:
            combined = "confluence-bullish"
        elif biases == {"bearish"}:
            combined = "confluence-bearish"
        elif "bullish" in biases and "bearish" in biases:
            combined = "divergence"

    prev_combined = state.get("_combined", {})
    prev_val = prev_combined.get("state") if prev_combined.get("day") == today else None

    if combined != "unknown" and combined != prev_val:
        try:
            if combined.startswith("confluence"):
                send_discord_combined("confluence", combined.split("-")[1], reads)
            else:
                send_discord_combined("divergence", "", reads)
            print(f"[COMBINED] POST {prev_val} -> {combined}")
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"[COMBINED] discord post failed: {e}")
    else:
        print(f"[COMBINED] {combined} (prev {prev_val}) - no post")

    if combined != "unknown":
        state["_combined"] = {"state": combined, "day": today}

    save_state(state)


if __name__ == "__main__":
    main()
