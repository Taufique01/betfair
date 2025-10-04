from datetime import datetime, timedelta, time as dt_time, timezone
from betfairlightweight.filters import market_filter, price_projection
from safe_api import safe_api_call
from logger_factory import get_logger  # singleton logger

logger = get_logger()  # singleton logger
UTC = timezone.utc

def _ensure_aware(dt):
    """Ensure datetime object is timezone aware (UTC)."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt

def get_today_markets(betting, tz, now=None):
    """
    Fetch today's horse racing WIN markets for GB and IE.
    Returns sorted list of markets by start time.
    """
    now_utc = (now or datetime.now(UTC)) - timedelta(seconds=120)
    tomorrow_mid = (
        datetime.combine(datetime.now(tz).date() + timedelta(days=1), dt_time.min, tz)
        .astimezone(UTC)
    )

    mf = market_filter(
        event_type_ids=["7"],  # Horse Racing
        market_type_codes=["WIN"],
        market_countries=["GB", "IE"],
        market_start_time={
            "from": now_utc.isoformat(),
            "to": tomorrow_mid.isoformat()
        }
    )

    mkts = safe_api_call(
        betting.list_market_catalogue,
        filter=mf,
        market_projection=["EVENT", "MARKET_START_TIME", "RUNNER_METADATA"],
        max_results=1000,
        sort="FIRST_TO_START",
        locale="en"
    )

    if not mkts:
        logger.warning("No markets returned")
        return []

    for m in mkts:
        m.market_start_time = _ensure_aware(m.market_start_time)

    logger.info(f"Fetched {len(mkts)} markets for today")
    return sorted(mkts, key=lambda m: m.market_start_time)

def determine_fav_and_odds(betting, market):
    """
    Determine the favorite runner and its odds from a market book.
    Returns dict with selection_id and runner_name, and odds float.
    """
    books = safe_api_call(
        betting.list_market_book,
        market_ids=[market.market_id],
        price_projection=price_projection(price_data=["EX_BEST_OFFERS"])
    )

    if not books:
        logger.warning(f"No market book for {market.market_name}")
        return None, 0

    book = books[0]
    best = None
    for r in book.runners:
        if r.ex.available_to_back:
            p = r.ex.available_to_back[0].price
            if best is None or p < best[1]:
                best = (r.selection_id, p)

    if not best:
        logger.warning(f"No available odds for market {market.market_name}")
        return None, 0

    sel_id, odds = best
    name = next((r.runner_name for r in market.runners if r.selection_id == sel_id), "Unknown")
    return {"selection_id": sel_id, "runner_name": name}, odds
