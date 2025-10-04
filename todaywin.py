#!/usr/bin/env python3
import json
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

import betfairlightweight
from betfairlightweight.filters import market_filter

# ───────────────────────────────────────────────────────
# Load config
cfg = json.load(open("config.json"))

# Timezones
TZ  = ZoneInfo("Europe/London")
UTC = ZoneInfo("UTC")

# Login
client = betfairlightweight.APIClient(
    username=cfg["betfair_username"],
    password=cfg["betfair_password"],
    app_key=cfg["betfair_app_key"],
    certs=cfg["certs"]
)
client.login()
betting = client.betting

# 1) Fetch all today’s WIN markets (midnight BST → now)
today_mid_local = datetime.combine(datetime.now(TZ).date(), dt_time.min, TZ)
start_utc       = today_mid_local.astimezone(UTC)
now_utc         = datetime.now(UTC)

mf = market_filter(
    event_type_ids=["7"],
    market_type_codes=["WIN"],
    market_countries=["GB","IE"],
    market_start_time={
        "from": start_utc.isoformat(),
        "to":   now_utc.isoformat()
    }
)

catalogue = betting.list_market_catalogue(
    filter=mf,
    market_projection=["EVENT","MARKET_START_TIME","RUNNER_DESCRIPTION"],
    max_results=1000,
    sort="FIRST_TO_START",
    locale="en"
)

if not catalogue:
    print("No markets found for today.")
    client.logout()
    exit()

# Build a lookup for event name and start time
market_info = {
    m.market_id: (
        m.event.name,
        m.market_start_time.astimezone(TZ)
    )
    for m in catalogue
}
market_ids = list(market_info.keys())

# 2) Pull profit-and-loss for all these markets
# Note: may need to chunk if >50 or API limits, but for <1000 usually fine
pnl_results = betting.list_market_profit_and_loss(
    market_ids=market_ids,
    include_settled_bets=True
)

# 3) For each result, find the runner with positive profit
count = 0
for res in pnl_results:
    mid = res.market_id
    info = market_info.get(mid)
    if not info:
        continue
    event_name, start_local = info

    # Each runner has pnl.ant and pnl.lay
    # Positive pnl on back bets indicates the winner
    winner = None
    for r in res.runner_pnl:
        if r.pnl > 0:
            winner = r
            break

    if not winner:
        continue

    # Implied odds = pnl + stake (assume stake=1)
    odds = winner.pnl + 1.0

    ts = start_local.strftime("%Y-%m-%d %-I:%M%p")
    print(f"{ts} | {event_name} | {winner.selection_id} @ {odds:.2f}")
    count += 1

if count == 0:
    print("No settled markets with positive P&L found yet.")

client.logout()
