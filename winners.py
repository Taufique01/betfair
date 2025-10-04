import json
from datetime import datetime, timedelta
from pytz import timezone
from betfairlightweight import APIClient
from betfairlightweight.filters import market_filter, price_projection

# --- Load Config ---
with open("config.json") as f:
    config = json.load(f)

# --- Time Zone Setup ---
uk_tz = timezone("Europe/London")
now_uk = datetime.now(uk_tz)
start_bst = uk_tz.localize(datetime(now_uk.year, now_uk.month, now_uk.day, 3, 0))
end_bst = uk_tz.localize(datetime(now_uk.year, now_uk.month, now_uk.day, 23, 59, 59))
start_utc = start_bst.astimezone(timezone("UTC"))
end_utc = end_bst.astimezone(timezone("UTC"))

# --- Initialize Betfair API ---
client = APIClient(
    username=config["betfair_username"],
    password=config["betfair_password"],
    app_key=config["betfair_app_key"],
    certs=config["certs"]
)
client.login()

# --- Fetch Market Catalogue ---
mf = market_filter(
    event_type_ids=["7"],  # Horse racing
    market_countries=["GB", "IE"],
    market_type_codes=["WIN"],
    market_start_time={"from": start_utc.isoformat(), "to": end_utc.isoformat()}
)
catalogue = client.betting.list_market_catalogue(
    filter=mf,
    market_projection=["RUNNER_METADATA", "MARKET_START_TIME", "EVENT"],
    max_results=100,
    sort="FIRST_TO_START"
)

if not catalogue:
    print("❌ No markets found in the specified time window.")
    exit()

market_ids = [m.market_id for m in catalogue]

# --- Fetch Market Books ---
winners = []
for i in range(0, len(market_ids), 10):  # Fetch in chunks
    chunk = market_ids[i:i+10]
    books = client.betting.list_market_book(
        market_ids=chunk,
        price_projection=price_projection(price_data=["EX_BEST_OFFERS"])
    )
    for book, market in zip(books, catalogue[i:i+10]):
        if book.status != "CLOSED":
            continue

        # Assume winner is the runner with lowest traded price
        settled = [r for r in book.runners if r.status == "WINNER"]
        if not settled:
            continue

        winner = settled[0]
        winner_name = next((r.runner_name for r in market.runners if r.selection_id == winner.selection_id), "TBD")

        uk_time = market.market_start_time.astimezone(uk_tz)
        winners.append(f"{uk_time.strftime('%H:%M')} | {winner_name} | {market.event.venue} | {book.status}")

# --- Display Winners ---
if winners:
    print("\n🏁 Winners for today:\n")
    for line in winners:
        print(line)
else:
    print("⚠️  No resulted (CLOSED) races with winners found yet.")
