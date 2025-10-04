
import json
from datetime import datetime, timedelta
from pytz import timezone as pytz_timezone
from betfairlightweight import APIClient
from betfairlightweight.filters import market_filter, price_projection
from more_itertools import chunked

# Load config
with open("config.json") as f:
    config = json.load(f)

uk_tz = pytz_timezone("Europe/London")
now_uk = datetime.now(uk_tz)
today = now_uk.date()
tomorrow = today + timedelta(days=1)

def get_day_bounds(day):
    return {
        "from": datetime.combine(day, datetime.min.time()).astimezone(uk_tz).isoformat(),
        "to": datetime.combine(day, datetime.max.time()).astimezone(uk_tz).isoformat()
    }

client = APIClient(
    username=config["betfair_username"],
    password=config["betfair_password"],
    app_key=config["betfair_app_key"],
    certs=config["certs"]
)
client.login()

def fetch_favorites_for_day(day):
    time_range = get_day_bounds(day)

    mf = market_filter(
        event_type_ids=["7"],
        market_countries=["GB", "IE"],
        market_type_codes=["WIN"],
        market_start_time=time_range
    )

    markets = client.betting.list_market_catalogue(
        filter=mf,
        market_projection=["EVENT", "MARKET_START_TIME", "RUNNER_METADATA"],
        max_results=200,
        sort="FIRST_TO_START"
    )

    market_map = {
        m.market_id: (m, m.market_start_time.astimezone(uk_tz))
        for m in markets if m.market_start_time
    }

    results = []
    for chunk in chunked(list(market_map.keys()), 40):
        books = client.betting.list_market_book(
            market_ids=chunk,
            price_projection=price_projection(price_data=["EX_BEST_OFFERS"])
        )
        for book in books:
            m, local_time = market_map[book.market_id]
            venue = m.event.venue or "Unknown"
            try:
                sorted_runners = sorted(book.runners, key=lambda r: r.last_price_traded or 9999)
                fav = sorted_runners[0]
                fav_name = next((r.runner_name for r in m.runners if r.selection_id == fav.selection_id), "TBD")
                odds = fav.last_price_traded if fav.last_price_traded else "TBD"
            except:
                fav_name = "TBD"
                odds = "TBD"

            results.append({
                "time": local_time.strftime('%H:%M'),
                "venue": venue,
                "fav": fav_name,
                "odds": f"{odds:.2f}" if isinstance(odds, float) else odds,
                "day": day
            })

    return results

summary = {
    today.strftime("%A %d %B"): fetch_favorites_for_day(today),
    tomorrow.strftime("%A %d %B"): fetch_favorites_for_day(tomorrow)
}

# Print summary to terminal
print("\n📅 Daily Autobet Race Summary (GB + IE)\n")
for day, races in summary.items():
    print(f"===== {day} =====")
    for r in races:
        print(f"{r['time']} | {r['venue']} | Favorite: {r['fav']} | Odds: {r['odds']}")
    print()
