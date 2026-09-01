#!/usr/bin/env python3
"""
Trading School poster.

Posts a pinned-worthy curriculum of embeds to a Discord channel via webhook:
every term and setup the Market Trend Tracker uses, explained for new traders.

Run once (or re-run after edits - it just posts the series again, so clear the
channel first if re-posting). Uses LESSONS_WEBHOOK_URL so it can target a
separate #trading-school channel instead of the live alerts channel.
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

WEBHOOK = os.environ.get("LESSONS_WEBHOOK_URL", "")

BLUE, GREEN, RED, YELLOW, PURPLE, GREY = 0x3498DB, 0x2ECC71, 0xE74C3C, 0xF1C40F, 0x9B59B6, 0x95A5A6

LESSONS = [
    {
        "title": "\U0001F393 Welcome to Trading School",
        "color": BLUE,
        "body": (
            "This channel explains everything the bots in this server post - every term, "
            "every alert, every setup - in plain English.\n\n"
            "**The one rule before anything else:** nothing in this server is financial advice "
            "or a signal to trade. The bots describe what *is* happening. What you do with that "
            "is your decision and your risk. New traders should paper trade (practice with fake "
            "money) until the terms below feel boring."
        ),
    },
    {
        "title": "\U0001F4D6 Lesson 1 - Reading the Feed",
        "color": BLUE,
        "body": (
            "Every few minutes during market hours the tracker posts each ticker like this:\n"
            "**\u25b2 SPY BULLISH** - Price $650.20 | Open $648.10 | vs Open +0.32%\n\n"
            "**Price** - what it trades at right now.\n"
            "**Open** - the first price of the day (9:30 AM ET / 8:30 AM CT).\n"
            "**vs Open** - percent above or below the open. This is the day's scoreboard.\n\n"
            "**BULLISH** (green \u25b2) = trading meaningfully above the open - buyers in control so far.\n"
            "**BEARISH** (red \u25bc) = meaningfully below the open - sellers in control.\n"
            "**NEUTRAL** (grey \u2014) = hovering within 0.1% of the open - no real winner yet. "
            "That 0.1% buffer is called a *deadband*: it stops the label flipping on every tiny wiggle."
        ),
    },
    {
        "title": "\U0001F4D6 Lesson 2 - Core Terms",
        "color": BLUE,
        "body": (
            "**Ticker** - a stock or fund's short code (SPY, NVDA, TSLA).\n"
            "**ETF** - a fund that trades like a stock. SPY tracks the S&P 500, QQQ the Nasdaq 100, "
            "IWM small companies, SMH semiconductor stocks.\n"
            "**Prev close** - yesterday's final price. Today's action above or below it shows "
            "overnight sentiment.\n"
            "**Gap** - when today opens away from yesterday's close (gap up / gap down).\n"
            "**HOD / LOD** - high of day / low of day.\n"
            "**Range** - the space between a period's high and low.\n"
            "**Flip** - when a ticker's bias changes side (bullish \u2192 bearish or back). "
            "The tracker alerts every flip.\n"
            "**Intraday** - within a single trading day. Everything here is intraday."
        ),
    },
    {
        "title": "\U0001F3AF Lesson 3 - The Setups the Bot Flags",
        "color": GREEN,
        "body": (
            "When you see a \U0001F3AF SETUP alert, the bot spotted one of these classic patterns:\n\n"
            "**OPENING RANGE BREAKOUT / BREAKDOWN** - the first 30 minutes set a high and low "
            "(the *opening range*). Breaking above that high suggests buyers won the morning "
            "battle; breaking below the low suggests sellers did. One of the oldest day-trading "
            "triggers there is.\n\n"
            "**RECLAIMED / LOST PREV CLOSE** - crossing back above yesterday's close after being "
            "below it (or losing it from above). Traders watch this level because it separates "
            "\"recovering\" from \"still broken.\"\n\n"
            "**PUSHING NEW HIGHS / PRESSING NEW LOWS** - making fresh highs (or lows) of the day "
            "while the bias agrees. That's a trend *continuing*, not starting - strength begetting "
            "strength.\n\n"
            "\u26a0\ufe0f **Critical:** setups are patterns, not promises. Breakouts fail constantly - "
            "a break that immediately reverses is called a *fakeout* and traps people who chased it. "
            "A setup alert means \"pay attention,\" never \"buy now.\""
        ),
    },
    {
        "title": "\U0001F9ED Lesson 4 - Market Context Alerts",
        "color": PURPLE,
        "body": (
            "**\u25b2\u25b2 MARKET CONFLUENCE** - every tracked ticker points the same way. "
            "Indexes, semis, big names all agreeing is a strong tape: trend days often look like this.\n\n"
            "**\u26a0\ufe0f MARKET DIVERGENCE** - the tickers disagree (some green, some red). "
            "Mixed tape - choppy, harder conditions. Many experienced traders simply size down "
            "or sit out divergence.\n\n"
            "**Why these tickers?** SPY/QQQ/IWM = the broad market at three sizes. "
            "SMH = semiconductors, which often *lead* the market both directions. "
            "VXX = a fear gauge - it rises when markets panic, so the bot *flips* its signal: "
            "when you see VXX marked \"(inv)\" bullish, it means fear is falling, which is "
            "good for the market. Read the flipped label, not the raw price."
        ),
    },
    {
        "title": "\u2600\ufe0f Lesson 5 - The Daily Rhythm",
        "color": YELLOW,
        "body": (
            "**7:00 AM CT - Morning Brief**: pre-market read. Prices vs *yesterday's close* "
            "(there's no open yet). Pre-market is thin - moves can vanish at the bell.\n"
            "**8:30 AM CT - the open**: tracker starts posting. The first 30-60 min are the "
            "wildest of the day.\n"
            "**Through the day**: status posts, flips, setups, confluence changes.\n"
            "**3:00 PM CT - the close.**\n"
            "**3:10 PM CT - Close Recap**: how the day actually unfolded, narrated from the "
            "flip timeline.\n\n"
            "**Ask the bot anything**: type `/ask` for a live market read, or "
            "`/ticker NVDA` for a single-name deep dive with recent news."
        ),
    },
    {
        "title": "\U0001F6E1\uFE0F Lesson 6 - Risk, or How Not to Blow Up",
        "color": RED,
        "body": (
            "The uncomfortable truths every new trader needs before touching real money:\n\n"
            "\u2022 **Nobody knows what happens next** - not this bot, not anyone. Indicators "
            "and setups describe the present, not the future.\n"
            "\u2022 **Most day traders lose money.** The edge isn't finding magic signals; it's "
            "risk control - keeping losses small enough that being wrong doesn't end you.\n"
            "\u2022 **Position sizing beats prediction.** Risking a small fixed slice per idea "
            "means a losing streak is survivable.\n"
            "\u2022 **Options amplify everything** - gains, losses, and mistakes. They can go "
            "to zero fast. Understand them thoroughly before using them.\n"
            "\u2022 **Paper trade first.** Practice on a simulator until you're consistent "
            "there. The market will still exist when you're ready.\n\n"
            "*Everything in this server is informational only - not a trade recommendation.*"
        ),
    },
]


def post(embed):
    payload = {"embeds": [{
        "title": embed["title"],
        "description": embed["body"],
        "color": embed["color"],
        "timestamp": datetime.now(tz=ZoneInfo("UTC")).isoformat(),
    }]}
    req = urllib.request.Request(
        WEBHOOK,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "trading-school (github-actions, v1)"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=20).read()


def main():
    if not WEBHOOK:
        sys.exit("Missing LESSONS_WEBHOOK_URL environment variable.")
    for i, lesson in enumerate(LESSONS, 1):
        post(lesson)
        print(f"Posted {i}/{len(LESSONS)}: {lesson['title']}")
        time.sleep(1.5)  # keep Discord's webhook rate limiter happy
    print("Curriculum posted.")


if __name__ == "__main__":
    main()
