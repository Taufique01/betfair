import json
import argparse
import time
from datetime import datetime, timedelta
from pytz import timezone as pytz_timezone
from colorama import Fore, Style, init
from betfairlightweight import APIClient
from betfairlightweight.filters import market_filter, price_projection

init(autoreset=True)

# --- Load Config ---
with open("config.json") as f:
    config = json.load(f)

# --- Parse Arguments ---
parser = argparse.ArgumentParser()
parser.add_argument("--5", dest="interval", action="store_const", const=5)
parser.add_argument("--30", dest="interval", action="store_const", const=30)
parser.add_argument("--60", dest="interval", action="store_const", const=60)
args = parser.parse_args()
interval_minutes = args.interval or 15

# --- Initialize Client ---
client = APIClient(
    username=config["betfair_username"],
    password=config["betfair_password"],
    app_key=config["betfair_app_key"],
    certs=config["certs"]
)
client.login()

# --- Timezone ---
uk_tz = pytz_timezone("Europe/London")
today = datetime.now(uk_tz).date()

# --- Get Today's Markets ---
def get_today_markets():
    start_time = datetime.combine(today, datetime.min.time()).replace(hour=3, tzinfo=uk_tz)
    end_time = datetime.combine(today + timedelta(days=1), datetime.min.time()).replace(hour=0, tzinfo=uk_tz)

    mf = market_filter(
        event_type_ids=["7"],
        market_countries=["GB", "IE"],
        market_type_codes=["WIN"],
        market_start_time={
            "from": start_time.isoformat(),
            "to": end_time.isoformat()
        }
    )
    return client.betting.list_market_catalogue(
        filter=mf,
        market_projection=["EVENT", "MARKET_START_TIME", "RUNNER_METADATA"],
        sort="FIRST_TO_START",
        max_results=1000
    )

# --- Track Favorites ---
last_seen = {}  # market_id -> (fav_name, odds, start_time)

print(f"\n🎯 Watching races for {today.strftime('%A %d %B')} (UK)...\n")

while True:
    now = datetime.now(uk_tz)
    markets = get_today_markets()
    updated_any = False

    for m in markets:
        uk_time = m.market_start_time.astimezone(uk_tz)
        if uk_time.date() != today:
            continue

        market_id = m.market_id
        venue = m.event.venue or "Unknown"

        book = client.betting.list_market_book(
            market_ids=[market_id],
            price_projection=price_projection(price_data=["EX_BEST_OFFERS"])
        )[0]

        runners = book.runners
        if not runners:
            continue

        sorted_runners = sorted(runners, key=lambda r: r.last_price_traded or 9999)
        fav = sorted_runners[0]
        fav_name = next((r.runner_name for r in m.runners if r.selection_id == fav.selection_id), "TBD")
        odds_val = fav.last_price_traded
        odds = f"{odds_val:.2f}" if odds_val else "TBD"
        status = book.status

        key = market_id
        if last_seen.get(key, (None, None))[0:2] != (fav_name, odds):
            updated_any = True
            last_seen[key] = (fav_name, odds, uk_time)
            color = Fore.GREEN if odds_val and odds_val >= 2.75 else Fore.YELLOW
            print(f"{color}{uk_time.strftime('%H:%M')} | {odds} | {fav_name} | {venue} | {status}{Style.RESET_ALL}")

    if not updated_any:
        print(f"Checked at {now.strftime('%H:%M:%S')} - no changes")

    # Determine if any races are within the next 10 minutes
    in_next_10 = any((uk_time - now).total_seconds() <= 600 for (_, _, uk_time) in last_seen.values())
    time.sleep(30 if in_next_10 else interval_minutes * 60)
