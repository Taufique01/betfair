import json
import time
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from betfairlightweight import APIClient
from betfairlightweight.filters import market_filter, price_projection

# Load config
with open("config.json") as f:
    config = json.load(f)

client = APIClient(
    username=config["betfair_username"],
    password=config["betfair_password"],
    app_key=config["betfair_app_key"],
    certs=config["certs"]
)
client.login()

# Load bankroll
cash_path = Path("cash.json")
if cash_path.exists():
    with open(cash_path) as f:
        bank = json.load(f)["bank"]
else:
    bank = 200.0

start_bank = bank
starting_percent = config.get("starting_bank_percent", 3.0) / 100
bet_multiplier = config.get("bet_multiplier", 1.5)
min_odds = config.get("min_odds", 2.7)
first_race_odds = config.get("first_race_min_odds", 2.0)

def log_email(subject, body):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = config["email"]
        msg["To"] = config["email_recipient"]

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(config["email"], config["email_pass"])
            smtp.send_message(msg)
        print("\n📧 Email sent.")
    except Exception as e:
        print(f"\n❌ Email failed: {e}")

# Get upcoming markets
markets = client.betting.list_market_catalogue(
    filter=market_filter(
        event_type_ids=["7"],
        market_type_codes=["WIN"],
        market_countries=["GB", "IE"],
        market_start_time={"from": datetime.utcnow().isoformat()}
    ),
    market_projection=["EVENT", "MARKET_START_TIME", "RUNNER_DESCRIPTION"],
    max_results=10,
    sort="FIRST_TO_START"
)

# Place simulated bets and wait for results
bet_log = []
current_bet = round(bank * starting_percent, 2)
total_losses = 0

for i, m in enumerate(markets):
    uk_time = m.market_start_time.astimezone(timezone(timedelta(hours=1)))
    odds_threshold = first_race_odds if i == 0 else min_odds
    market_id = m.market_id
    venue = m.event.venue or "Unknown"

    book = client.betting.list_market_book(
        market_ids=[market_id],
        price_projection=price_projection(price_data=["EX_BEST_OFFERS"])
    )[0]

    # Get favorite
    runners = sorted(book.runners, key=lambda r: r.last_price_traded or 999)
    fav = runners[0]
    fav_odds = fav.last_price_traded or 0

    if fav_odds < odds_threshold:
        print(f"Skipping {venue} ({uk_time.strftime('%H:%M')}): {fav_odds} < {odds_threshold}")
        continue

    print(f"\nPlacing simulated bet on {fav.selection_id} at {fav_odds:.2f} ({venue})")
    before = bank

    # Wait for market to close
    print("Waiting for market to close...")
    while True:
        book = client.betting.list_market_book(market_ids=[market_id])[0]
        if book.status == "CLOSED":
            break
        time.sleep(20)

    # Find winner
    book = client.betting.list_market_book(market_ids=[market_id])[0]
    result_runner = next((r for r in book.runners if r.status == "WINNER"), None)
    win = result_runner and result_runner.selection_id == fav.selection_id

    if win:
        profit = current_bet * (fav_odds - 1)
        bank += profit
        print(f"\033[92mWIN! +${profit:.2f} => ${bank:.2f}\033[0m")
    else:
        bank -= current_bet
        total_losses += current_bet
        print(f"\033[91mLOSS. -${current_bet:.2f} => ${bank:.2f}\033[0m")

    bet_log.append(f"{uk_time.strftime('%H:%M')} {venue} | Bet ${current_bet:.2f} | Odds {fav_odds:.2f} | {'WIN' if win else 'LOSS'}")

    if bank <= 0:
        print("\033[91m💀 Bankrupt\033[0m")
        break

    current_bet = round(current_bet * bet_multiplier, 2)

    # Wait a bit before next race
    time.sleep(5)

# Save bankroll
with open("cash.json", "w") as f:
    json.dump({"bank": round(bank, 2)}, f, indent=2)

# Send end-of-day email
net = round(bank - start_bank, 2)
subject = f"Autobet $$$ WIN $$$ +${net}" if net > 0 else (f"Autobet :( Account blown -${abs(net)}" if bank <= 0 else f"Autobet Final ${net}")
body = f"Start: ${start_bank}\nEnd: ${bank:.2f}\nProfit: ${net:.2f}\n\n" + "\n".join(bet_log)
log_email(subject, body)
