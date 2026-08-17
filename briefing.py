#!/usr/bin/env python3
"""
Daily market briefings for Discord.

Two modes, chosen automatically by ET clock (or forced with BRIEF_MODE env):
  morning -> pre-market snapshot before the open (reads vs prev close)
  close   -> end-of-day recap (reads vs open + the day's flip history)

Runs in GitHub Actions. Uses the same secrets as the tracker plus
ANTHROPIC_API_KEY, and reads state.json for the day-story memory.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TICKERS = ["SPY", "QQQ", "IWM", "SMH", "VXX", "MSFT", "NVDA", "TSLA", "PLTR", "SPCX"]
INVERSE_TICKERS = {"VXX", "TLT", "UVXY"}
STATE_FILE = Path("state.json")

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")  # user's local time - briefing schedule anchors here


def http_json(url, payload=None, headers=None, timeout=25):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_quote(symbol):
    return http_json(
        f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_API_KEY}",
        headers={"User-Agent": "market-briefing"},
    )


def fetch_headlines(n=6):
    try:
        news = http_json(
            f"https://finnhub.io/api/v1/news?category=general&token={FINNHUB_API_KEY}",
            headers={"User-Agent": "market-briefing"},
        )
        lines = []
        for item in news[:n]:
            h = (item.get("headline") or "").strip()
            if h:
                lines.append(f"- {h}")
        return "\n".join(lines)
    except Exception:
        return ""


def build_market_lines(mode):
    import time as _t
    lines = []
    for sym in TICKERS:
        try:
            q = fetch_quote(sym)
        except Exception:
            lines.append(f"{sym}: fetch failed")
            _t.sleep(0.15)
            continue
        price, open_, pc = q.get("c") or 0, q.get("o") or 0, q.get("pc") or 0
        if not price:
            lines.append(f"{sym}: no data")
            _t.sleep(0.15)
            continue
        if mode == "morning" or not open_:
            ref, refname = (pc or open_), "prev close"
        else:
            ref, refname = open_, "open"
        pct = (price / ref - 1) * 100 if ref else 0
        inv = " [inverse instrument: strength = market-bearish]" if sym in INVERSE_TICKERS else ""
        lines.append(f"{sym}: ${price:.2f}, {pct:+.2f}% vs {refname}{inv}")
        _t.sleep(0.15)
    return "\n".join(lines)


def build_day_story():
    if not STATE_FILE.exists():
        return ""
    try:
        state = json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError:
        return ""
    today = datetime.now(tz=ET).strftime("%Y-%m-%d")
    parts = []
    for sym in TICKERS:
        rec = state.get(sym, {})
        if rec.get("day") != today:
            continue
        hist = rec.get("history", [])
        if hist:
            moves = "; ".join(f"{h['t']} {h['event']} {h['bias']} @ {h['price']}" for h in hist)
            parts.append(f"{sym}: {moves}")
    return "\n".join(parts)


def ask_claude(prompt, system):
    data = http_json(
        "https://api.anthropic.com/v1/messages",
        payload={
            "model": "claude-sonnet-4-6",
            "max_tokens": 700,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        },
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "User-Agent": "market-briefing",
        },
    )
    if data.get("error"):
        raise RuntimeError(data["error"].get("message", "Claude API error"))
    return " ".join(c.get("text", "") for c in data.get("content", [])).strip()


def post_discord(title, body, color):
    if len(body) > 3800:
        body = body[:3795] + "\n[...]"
    payload = {"embeds": [{
        "title": title,
        "description": body,
        "color": color,
        "timestamp": datetime.now(tz=ZoneInfo("UTC")).isoformat(),
    }]}
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "market-briefing (github-actions, v1)"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=25).read()


def main():
    if not (FINNHUB_API_KEY and DISCORD_WEBHOOK_URL and ANTHROPIC_API_KEY):
        sys.exit("Missing FINNHUB_API_KEY, DISCORD_WEBHOOK_URL, or ANTHROPIC_API_KEY.")

    now_ct = datetime.now(tz=CT)
    if now_ct.weekday() >= 5 and not os.environ.get("BRIEF_MODE"):
        print("Weekend - no briefing.")
        return

    mode = os.environ.get("BRIEF_MODE")
    if not mode:
        # Central Time windows: morning 6:30-7:45 AM CT (pre-open; market
        # opens 8:30 CT), close 3:00-3:59 PM CT (market closes 3:00 CT).
        # Windows are sized so exactly ONE cron of each DST pair lands
        # inside - the other skips harmlessly. No double posts, no misses.
        hm = now_ct.hour * 60 + now_ct.minute
        if 6 * 60 + 30 <= hm <= 7 * 60 + 45:
            mode = "morning"
        elif now_ct.hour == 15:
            mode = "close"
        else:
            print(f"No briefing window at {now_ct:%H:%M} CT - skipping.")
            return

    market = build_market_lines(mode)
    headlines = fetch_headlines()
    story = build_day_story() if mode == "close" else ""

    guardrails = ("You describe what IS happening; no predictions, no advice, no guarantees. "
                  "Under 1400 characters. Discord markdown: bold and bullets, no headers. "
                  "End with: Informational only - not a trade recommendation.")

    if mode == "morning":
        system = ("You write a pre-market briefing for a trader's Discord. Readings are vs previous "
                  "close and pre-market is thin - frame accordingly. Cover: index tone (SPY/QQQ/IWM), "
                  "semis (SMH), fear read (VXX, already flipped to market meaning), notable single "
                  "names, and one thing to watch at the open. " + guardrails)
        prompt = f"PRE-MARKET DATA:\n{market}\n\nHEADLINES:\n{headlines or 'none available'}"
        title, color = f"\u2600\ufe0f Morning Brief - {now_ct.strftime('%b %d')}", 0x3498DB
    else:
        system = ("You write an end-of-day recap for a trader's Discord. You get closing reads vs "
                  "open AND a timeline of intraday bias flips (times are Central Time) - use it to "
                  "narrate how the day "
                  "developed (early tone, reversals, where it settled). Note confluence/divergence "
                  "across indexes, semis leadership, fear read (VXX, already flipped), standout "
                  "names, and what carried into the close. " + guardrails)
        prompt = (f"CLOSING DATA (vs open):\n{market}\n\nINTRADAY FLIP TIMELINE:\n{story or 'no flips logged'}"
                  f"\n\nHEADLINES:\n{headlines or 'none available'}")
        title, color = f"\U0001F514 Close Recap - {now_ct.strftime('%b %d')}", 0x9B59B6

    body = ask_claude(prompt, system)
    post_discord(title, body, color)
    print(f"Posted {mode} briefing.")


if __name__ == "__main__":
    main()
