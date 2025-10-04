import os
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from betfairlightweight import APIClient
from betfairlightweight.filters import market_filter, price_projection

# Load config
with open("config.json") as f:
    config = json.load(f)

USERNAME = config["betfair_username"]
PASSWORD = config["betfair_password"]
APP_KEY = config["betfair_app_key"]
CERTS_DIR = config["certs"]

# Validate cert directory
if not os.path.isdir(CERTS_DIR):
    raise FileNotFoundError(f"Cert directory not found: {CERTS_DIR}")

# Setup Betfair client
client = APIClient(USERNAME, PASSWORD, app_key=APP_KEY, certs=CERTS_DIR)

# Login
try:
    client.login()
except Exception as e:
    raise RuntimeError(f"Betfair login failed: {e}")

# ✅ Show account balance
funds = client.account.get_account_funds()
print(f"💰 Account Balance: £{funds.available_to_bet_balance:.2f}")

# ✅ Use UK timezone for today's date
uk_tz = ZoneInfo("Europe/London")
uk_now = datetime.now(uk_tz)
today = (uk_now - timedelta(days=1)).date()

from_dt = datetime.combine(today, datetime.min.time(), tzinfo=uk_tz).astimezone(ZoneInfo("UTC"))
to_dt = datetime.combine(today, datetime.max.time(), tzinfo=uk_tz).astimezone(ZoneInfo("UTC"))

from_time = from_dt.isoformat().replace("+00:00", "Z")
to_time = to_dt.isoformat().replace("+00:00", "Z")

# ✅ Fetch UK WIN markets
markets = client.betting.list_market_catalogue(
    filter=market_filter(
        event_type_ids=["7"],  # Horse racing
        market_type_codes=["WIN"],
        market_countries=["GB"],
        market_start_time={"from": from_time, "to": to_time}
    ),
    market_projection=["MARKET_START_TIME", "RUNNER_METADATA"],
    max_results=200
)

if not markets:
    print("❌ No UK WIN markets found today.")
    exit()

# ✅ Fetch live market book data (corrected price_projection)
market_ids = [m.market_id for m in markets]
books = client.betting.list_market_book(
    market_ids=market_ids,
    price_projection=price_projection(price_data=["EX_BEST_OFFERS"])
)

# ✅ Process race results
results = []
for m, b in zip(markets, books):
    if not b.runners:
        continue

    winner = next((r for r in b.runners if r.status == "WINNER"), None)
    if not winner:
        continue

    meta = next((r for r in m.runners if r.selection_id == winner.selection_id), None)
    if not meta:
        continue

    odds = winner.last_price_traded or "?"
    time = m.market_start_time.astimezone(uk_tz).strftime("%H:%M")
    event = m.event.name
    name = meta.runner_name

    try:
        sorted_bsp = sorted(m.runners, key=lambda r: float(r.metadata.get("BSP", 999)))
        fav = "Y" if sorted_bsp[0].selection_id == winner.selection_id else "N"
    except:
        fav = "?"

    results.append((time, event, name, fav, odds))

# ✅ Output results
results.sort(key=lambda r: r[0])
print("\n🏇 UK Horse Racing Winners (Today):\n")
for r in results:
    print(f"{r[0]} - {r[1]} - {r[2]} - Fav: {r[3]} - Odds: {r[4]}")
