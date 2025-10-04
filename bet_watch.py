#!/usr/bin/env python3
import time
from datetime import datetime, timedelta, time as dt_time, timezone
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger

# Local imports
from config_utils import load_config, create_client
from markets import get_today_markets, determine_fav_and_odds
from results import await_result
from safe_api import safe_api_call
from logger_factory import get_logger  # singleton logger

# ───────────────────────────────────────────────────────
logger = get_logger()  # singleton logger
TZ = ZoneInfo("Europe/London")
UTC = timezone.utc
BET_LEAD_TIME = 90  # seconds before start to place bet


# ───────────────────────────────────────────────────────
def process_next_race(betting, market):
    full_event = getattr(market.event, "name", "Unknown")
    location = full_event.split()[0] if full_event else "Unknown"
    race_name = getattr(market, "market_name", "Unknown")
    race_start = getattr(market, "market_start_time", datetime.now(UTC))
    race_label = f"{location} {race_name}"

    fav, odds = determine_fav_and_odds(betting, market)
    if not fav:
        logger.warning(f"No odds for {race_label}, skipping")
        return

    bet = {
        "selection_id": fav["selection_id"],
        "runner_name": fav["runner_name"],
        "odds": odds,
        "result": "PENDING"
    }

    logger.info(f"Bet placed: {bet['runner_name']} x {bet['odds']} @ {race_label}")
    await_result(betting, market, race_label, bet, race_start)


# ───────────────────────────────────────────────────────
def schedule_today_races(scheduler):
    """Fetch today's markets and schedule bets."""
    client, _ = create_client(load_config())

    markets = get_today_markets(client, TZ)
    now_utc = datetime.now(UTC)

    for market in markets:
        bet_time = market.market_start_time - timedelta(seconds=BET_LEAD_TIME)
        if bet_time > now_utc:
            trigger = DateTrigger(run_date=bet_time)
            scheduler.add_job(
                func=process_next_race,
                trigger=trigger,
                args=[client, market],
                id=f"bet_{market.market_id}"
            )
            logger.info(f"Scheduled bet for {market.market_name} at {bet_time.astimezone(TZ).strftime('%-I:%M:%S%p')}")
        else:
            logger.info(f"Skipping {market.market_name}, bet time already passed")


# ───────────────────────────────────────────────────────

if __name__ == "__main__":
    # Initialize a single scheduler
    scheduler = BackgroundScheduler(timezone=TZ)
    scheduler.start()

    # Schedule daily reset + race scheduling at 5:00 AM
    scheduler.add_job(
        func=schedule_today_races,
        trigger=CronTrigger(hour=5, minute=0, timezone=TZ),
        args=[scheduler],
        id="daily_schedule"
    )

    # Check if we already passed 5 AM today → run immediately
    now_local = datetime.now(TZ)
    if now_local.time() >= dt_time(5, 0):
        logger.info("⏱ Already past 5:00 AM, fetching today's races immediately...")
        schedule_today_races(scheduler)

    else:
        logger.info("⌛ Waiting until 5:00 AM BST for first schedule...")

    try:
        # Keep main thread alive
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Bot stopped manually")
    finally:
        scheduler.shutdown()
        
        logger.info("Logged out from Betfair API")
