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


def await_result(
    betting,
    market,
    race_label,
    bet,
    race_start,
    *,
    channel="watch",
    on_final=None
):
    event = getattr(market, "event", None)
    location = getattr(event, "venue", None) or getattr(event, "name", "Unknown")
    race_name = getattr(market, "market_name", str(race_label))

    if on_final is None:
        def on_final(b, loc, rn, rs):
            append_results(b, loc, rn, rs)

    likely_winner_id = None
    likely_winner_name = None
    likely_time_utc = None

    def infer_winner(book):
        """
        Implements the observed real-world pattern:

        Winner = first (and only) runner with LTP <= 1.10
        while all others are either:
        - 1000.0
        - >= 100
        - None
        """

        ltps = {
            r.selection_id: r.last_price_traded
            for r in getattr(book, "runners", [])
        }

        # Which runners have a real price?
        valid_ltps = {sid: p for sid, p in ltps.items() if p is not None}

        # Any <= 1.10?
        candidates = [sid for sid, p in valid_ltps.items() if p <= 1.10]

        if len(candidates) != 1:
            return None  # no clean pattern yet

        sel = candidates[0]
        winner_price = valid_ltps[sel]

        # Now check the others are "dead"
        for sid, p in valid_ltps.items():
            if sid == sel:
                continue
            if p < 100:  # means someone else is still competitive
                return None

        # Pattern matched → inferred winner
        return sel

    while True:
        books = safe_api_call(
            betting.list_market_book,
            market_ids=[market.market_id],
            price_projection=price_projection(price_data=["EX_BEST_OFFERS", "EX_LTP"])
        )
        if not books:
            logger.warning(f"{channel} - No MarketBook returned for {race_name}.")
            time.sleep(30)
            continue

        book = books[0]
        status = getattr(book, "status", "UNKNOWN")

        logger.debug(f"{channel} - Polling {location} — {race_name}: {status}")

        # ----------------------------------------------------------
        # 1. Attempt early winner inference (your new logic)
        # ----------------------------------------------------------
        if likely_winner_id is None and status != "CLOSED":
            sel = infer_winner(book)
            if sel is not None:
                # Lookup name
                runner = next((r for r in book.runners if r.selection_id == sel), None)
                name = getattr(runner, "runner_name", str(sel))
                likely_winner_id = sel
                likely_winner_name = name
                likely_time_utc = datetime.utcnow().isoformat(timespec="seconds")

                logger.info(
                    f"{channel} - Likely winner inferred: "
                    f"{name} (sel_id={sel}) at {likely_time_utc} UTC"
                )

        # ----------------------------------------------------------
        # 2. Market CLOSED → confirm official winner
        # ----------------------------------------------------------
        if status == "CLOSED":
            official = next(
                (r for r in getattr(book, "runners", []) if getattr(r, "status", "") == "WINNER"),
                None
            )

            if official:
                official_id = official.selection_id
                official_name = official.runner_name
            else:
                official_id = None
                official_name = "UNKNOWN"

            # Determine WON/LOST for the given bet
            bet["result"] = (
                "WON" if official_id == bet.get("selection_id") else "LOST"
            )

            # Print comparison if inferred winner exists
            if likely_winner_id:
                # Lag = official published time - likely inference time
                try:
                    t_likely = datetime.fromisoformat(likely_time_utc)
                    t_official = datetime.utcnow()
                    lag = (t_official - t_likely).total_seconds()
                except:
                    lag = None

                logger.info(
                    f"{channel} - Likely winner vs official winner: "
                    f"likely={likely_winner_name} (sel_id={likely_winner_id}) at {likely_time_utc}; "
                    f"official={official_name} (sel_id={official_id}); "
                    f"lag={lag} seconds"
                )

            if channel == "watch":
                on_final(bet, location, race_name, race_start)

            logger.info(f"{channel} - Market closed: {race_name}, result: {bet['result']}")
            return

        time.sleep(15)
