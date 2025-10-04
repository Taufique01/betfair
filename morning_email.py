#!/usr/bin/env python3
import json, smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo
import betfairlightweight
from betfairlightweight.filters import market_filter, price_projection

# Constants
TZ = ZoneInfo("Europe/London")

# Load config
with open("config.json") as f:
    cfg = json.load(f)

# Betfair client
client = betfairlightweight.APIClient(
    cfg["betfair_username"],
    cfg["betfair_password"],
    app_key=cfg["betfair_app_key"],
    certs=cfg["certs"]
)
client.login()

def get_races():
    now = datetime.now(TZ)
    start = now.replace(hour=3, minute=0, second=0, microsecond=0).astimezone(ZoneInfo("UTC"))
    end   = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(ZoneInfo("UTC"))
    mf = market_filter(
        event_type_ids=["7"], market_type_codes=["WIN"],
        market_countries=["GB","IE"],
        market_start_time={"from": start.isoformat(), "to": end.isoformat()}
    )
    mkts = client.betting.list_market_catalogue(
        filter=mf, max_results=1000,
        market_projection=["RUNNER_METADATA","MARKET_START_TIME","EVENT"]
    )
    return sorted(mkts, key=lambda m: m.market_start_time)

def determine_fav(mkt):
    # fetch live book
    book = client.betting.list_market_book(
        market_ids=[mkt.market_id],
        price_projection=price_projection(price_data=["EX_BEST_OFFERS"])
    )[0]
    best = None
    for r in book.runners:
        if r.ex.available_to_back:
            p = r.ex.available_to_back[0].price
            if best is None or p < best[1]:
                best = (r.selection_id, p)
    if not best:
        return None, 0
    sel, od = best
    name = next((r.runner_name for r in mkt.runners if r.selection_id == sel), "Unknown")
    return name, od

# Build the email body
lines = [f"🐎 Morning Schedule — {datetime.now(TZ).date()}", "-"*40]
for m in get_races():
    t = m.market_start_time.astimezone(TZ).strftime("%H:%M")
    name, od = determine_fav(m)
    if name:
        lines.append(f"{t} — {m.event.name} — {name} @ {od}")

body = "\n".join(lines)

# Send it
msg = MIMEText(body)
msg["Subject"] = "🐎 Today's Races & Favorites"
msg["From"]    = cfg["email"]
msg["To"]      = cfg["email_recipient"]

with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
    smtp.starttls()
    smtp.login(cfg["email"], cfg["email_pass"])
    smtp.send_message(msg)

print("✅ Morning email sent.")
