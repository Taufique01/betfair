import time
from datetime import datetime, timezone
from pathlib import Path
import csv
from zoneinfo import ZoneInfo
from betfairlightweight.filters import price_projection
from safe_api import safe_api_call
from logger_factory import get_logger

logger = get_logger()  # singleton logger

TZ = ZoneInfo("Europe/London")
RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

def append_results(bet, location, race_name, race_start):
    now_ts = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    start_ts = race_start.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")
    line = (
        f"{now_ts} | {location} | {race_name} | {start_ts} | "
        f"{bet['selection_id']} | {bet['runner_name']} | {bet.get('placed_odds', bet['odds'])} | {bet['result']}"
    )

    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    CSV_LOG = RESULTS_DIR / f"ghost_bets_{today_str}.csv"

    try:
        new_file = not CSV_LOG.exists()
        with CSV_LOG.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if new_file:
                writer.writerow([
                    "timestamp", "location", "race_name", "race_start",
                    "selection_id", "runner_name", "odds", "result"
                ])
            writer.writerow([
                now_ts, location, race_name, start_ts,
                bet["selection_id"], bet["runner_name"], bet["odds"], bet["result"]
            ])
    except Exception:
        logger.exception("Failed writing CSV log")

    logger.info(f"Logged bet: {line}")


def await_result(betting, market, race_label, bet, race_start, *, channel="watch", on_final=None):
    event = getattr(market, "event", None)
    location = getattr(event, "venue", None) or getattr(event, "name", "Unknown")
    race_name = getattr(market, "market_name", str(race_label))

    if on_final is None:
        def on_final(b, loc, rn, rs):
            append_results(b, loc, rn, rs)

    while True:
        books = safe_api_call(
            betting.list_market_book,
            market_ids=[market.market_id],
            price_projection=price_projection(price_data=["EX_BEST_OFFERS"])
        )
        book = books[0] if books else None
        status = getattr(book, "status", "UNKNOWN") if book else "UNKNOWN"

        logger.debug(f"{channel} - Polling {location} — {race_name}: {status}")

        if status == "CLOSED":
            winner = next(
                (r for r in getattr(book, "runners", []) if getattr(r, "status", "") == "WINNER"),
                None
            )
            bet["result"] = "WON" if (winner and getattr(winner, "selection_id", None) == bet.get("selection_id")) else "LOST"
            if channel == "watch":
                on_final(bet, location, race_name, race_start)
            logger.info(f"{channel} - Market closed: {race_name}, result: {bet['result']}")
            return

        time.sleep(30)
