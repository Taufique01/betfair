#!/usr/bin/env python3
import json
import time
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo
from pathlib import Path

import betfairlightweight
from betfairlightweight.filters import market_filter, price_projection

# ───────────────────────────────────────────────
# CONFIG
CONFIG_FILE = "config.json"
OUTPUT_FILE = "horse_racing_full_dump.json"
TZ   = ZoneInfo("Europe/London")
UTC  = ZoneInfo("UTC")
# Set to an int to limit markets for a quick test
MAX_MARKETS = None
# ───────────────────────────────────────────────


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def create_client(cfg):
    trading = betfairlightweight.APIClient(
        username=cfg["betfair_username"],
        password=cfg["betfair_password"],
        app_key=cfg["betfair_app_key"],
        certs=cfg["certs"]
    )
    trading.login()
    return trading


def build_filter():
    now_utc = datetime.now(UTC).replace(microsecond=0)
    tomorrow_mid_bst = (
        datetime.combine(datetime.now(TZ).date() + timedelta(days=1), dt_time.min, TZ)
        .astimezone(UTC)
        .replace(microsecond=0)
    )
    return market_filter(
        event_type_ids=["7"],           # Horse racing
        market_type_codes=["WIN"],
        market_countries=["GB", "IE"],
        market_start_time={
            "from": now_utc.isoformat(),
            "to":   tomorrow_mid_bst.isoformat()
        }
    )


def fetch_catalogue(betting):
    print("Fetching market catalogue (minimal projection)…")
    mkts = betting.list_market_catalogue(
        filter=build_filter(),
        market_projection=["EVENT", "MARKET_START_TIME"],
        max_results=1000,
        sort="FIRST_TO_START",
        locale="en"
    )
    if MAX_MARKETS:
        mkts = mkts[:MAX_MARKETS]
    print(f" → Retrieved {len(mkts)} markets")
    return mkts


def fetch_market_books(betting, market_ids):
    print("Fetching market books for all markets…")
    pp = price_projection(price_data=["EX_BEST_OFFERS", "EX_ALL_OFFERS", "EX_TRADED"])
    books = betting.list_market_book(
        market_ids=market_ids,
        price_projection=pp,
        order_projection="ALL",
        match_projection="ROLLED_UP_BY_PRICE"
    )
    print(f" → Retrieved {len(books)} books")
    return books


def main():
    cfg     = load_config()
    trading = create_client(cfg)
    betting = trading.betting

    try:
        catalogue = fetch_catalogue(betting)
        market_ids = [m.market_id for m in catalogue]

        # Use the raw JSON data each object was created from
        cat_dump = {m.market_id: m._data for m in catalogue}

        time.sleep(1)  # respect rate limits

        books = fetch_market_books(betting, market_ids)
        book_dump = {b.market_id: b._data for b in books}

        full = []
        for mid in market_ids:
            full.append({
                "catalogue": cat_dump[mid],
                "book":      book_dump.get(mid)
            })

        Path(OUTPUT_FILE).write_text(json.dumps(full, indent=2))
        print(f"\n✅ All data dumped to {OUTPUT_FILE}")

    finally:
        trading.logout()


if __name__ == "__main__":
    main()
