import csv
import json
import random
import time
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path

# Load config
with open("config.json") as f:
    config = json.load(f)

# Load persistent bankroll
cash_path = Path("cash.json")
if cash_path.exists():
    with open(cash_path) as f:
        cash_data = json.load(f)
else:
    cash_data = {"bank": 200.00}  # fallback

bank = cash_data["bank"]
double_run = config.get("double_run", "n").lower() == "y"
starting_percent = config.get("starting_bank_percent", 3.0) / 100
bet_multiplier = config.get("bet_multiplier", 1.5)
min_odds = config.get("min_odds", 2.7)
first_race_odds = config.get("first_race_min_odds", 2.0)
sim_mode = config.get("mode", "sim") == "sim"

# Track bets and states
bet_history = []
total_losses = 0.0
win_found = False
has_doubled = False
current_bet = round(bank * starting_percent, 2)
initial_bank = bank

# Load race data
with open("sim_races.csv", newline='') as f:
    races = list(csv.DictReader(f))

# Pick race order randomly
random.shuffle(races)

for idx, race in enumerate(races):
    try:
        race_time = race["Race"]
        venue = race["Venue"]
        fav = race["Favorite"]
        odds = float(race["Odds"])
        result = race.get("Result", "L").strip().upper()

        odds_threshold = first_race_odds if not bet_history else min_odds
        if odds < odds_threshold:
            print(f"Skipping: {race_time} @ {venue} | {fav} | {odds:.2f} (Below threshold)")
            continue

        print("\n=== RACE ===")
        print(f"{race_time} | {venue} | {fav} | Odds: {odds:.2f}")
        print(f"Starting bank: ${bank:.2f} - Total losses so far: ${total_losses:.2f}")
        print(f"Next bet: ${current_bet:.2f} Total possible winnings: ${round(current_bet * odds, 2)}")
        print(f"Net profit: ${round(bank - initial_bank, 2)} Possible total exposure: ${round(total_losses + current_bet, 2)}")

        time.sleep(1.5)  # simulate passage of time

        win = result == "W"
        before = bank

        if win:
            bank += current_bet * (odds - 1)
            win_found = True
            print(f"\033[92m✅ WIN! New bank: ${bank:.2f}\033[0m")
        else:
            bank -= current_bet
            total_losses += current_bet
            print(f"\033[91m❌ LOSS. New bank: ${bank:.2f}\033[0m")

        bet_history.append({
            "Race": race_time,
            "Venue": venue,
            "Favorite": fav,
            "Odds": odds,
            "Result": "W" if win else "L",
            "Bet": round(current_bet, 2),
            "Bank Before": round(before, 2)
        })

        if bank <= 0:
            print("\n\033[91m💀 Bankrupt. Cannot place next bet.\033[0m")
            break

        if win:
            if double_run and not has_doubled and len(bet_history) <= 2:
                print("\033[94m🔁 Double run triggered! Restarting cycle...\033[0m")
                current_bet = round(bank * starting_percent, 2)
                total_losses = 0.0
                has_doubled = True
                continue  # start again in next iteration
            else:
                break  # stop on win
        else:
            current_bet = round(current_bet * bet_multiplier, 2)

    except Exception as e:
        print(f"Error: {e}")
        continue

# Save final bank
with open("cash.json", "w") as f:
    json.dump({"bank": round(bank, 2)}, f, indent=2)

# Compose email summary
total_bets = len(bet_history)
total_wins = sum(1 for b in bet_history if b["Result"] == "W")
total_losses_count = total_bets - total_wins
net_profit = round(bank - initial_bank, 2)

lines = [
    f"Race: {b['Race']} | {b['Venue']} | {b['Favorite']} | Odds: {b['Odds']} | {b['Result']} | Bet: ${b['Bet']} | Bank Before: ${b['Bank Before']}"
    for b in bet_history
]

body = f"Final bankroll: ${bank:.2f}\nNet Profit: ${net_profit:.2f}\n\n" + "\n".join(lines) + f"\n\nTotal Bets: {total_bets} | Wins: {total_wins} | Losses: {total_losses_count}"

if net_profit > 0:
    subject = f"Autobet $$$ WIN $$$ +${net_profit:.2f}"
elif bank <= 0:
    subject = f"Autobet :( Account blown -${abs(net_profit):.2f}"
else:
    subject = f"End of Simulation: +${net_profit:.2f}"

# Send email
try:
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = config["email"]
    msg["To"] = config["email_recipient"]

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(config["email"], config["email_pass"])
        smtp.send_message(msg)

    print("\n📧 Summary email sent.")
except Exception as e:
    print(f"\n❌ Failed to send summary email: {e}")
