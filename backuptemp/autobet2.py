
import csv
import json
import random
import time
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path
from colorama import init, Fore, Style

# Initialize colorama
init(autoreset=True)

# Load config
with open("config.json") as f:
    config = json.load(f)

# Load persistent bankroll
cash_path = Path("cash.json")
if cash_path.exists():
    with open(cash_path) as f:
        cash_data = json.load(f)
else:
    cash_data = {"bank": 200.00}

bank = cash_data["bank"]
double_run = config.get("double_run", "n").lower() == "y"
starting_percent = config.get("starting_bank_percent", 3.0) / 100
bet_multiplier = config.get("bet_multiplier", 1.5)
min_odds = config.get("min_odds", 2.7)
first_race_odds = config.get("first_race_min_odds", 2.0)
sim_mode = config.get("mode", "sim") == "sim"

bet_history = []
total_losses = 0.0
has_doubled = False
current_bet = round(bank * starting_percent, 2)
initial_bank = bank

# Load race data
with open("sim_races.csv", newline='') as f:
    races = list(csv.DictReader(f))

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
            print(f"{Fore.YELLOW}Skipping: {race_time} @ {venue} | {fav} | {odds:.2f} (Below threshold){Style.RESET_ALL}")
            continue

        print(f"\n{Fore.CYAN}=== RACE {idx + 1} ==={Style.RESET_ALL}")
        print(f"{Fore.BLUE}{race_time} | {venue} | Favorite: {fav} | Odds: {odds:.2f}{Style.RESET_ALL}")
        print(f"Bank: ${bank:.2f} | Total Losses: ${total_losses:.2f}")
        print(f"Bet: ${current_bet:.2f} | Potential Win: ${round(current_bet * odds, 2)}")
        print(f"Net Profit: ${round(bank - initial_bank, 2)} | Exposure: ${round(total_losses + current_bet, 2)}")

        time.sleep(1.5)

        win = result == "W"
        before = bank

        if win:
            bank += current_bet * (odds - 1)
            print(f"{Fore.GREEN}✅ WIN! New bank: ${bank:.2f}{Style.RESET_ALL}")
        else:
            bank -= current_bet
            total_losses += current_bet
            print(f"{Fore.RED}❌ LOSS. New bank: ${bank:.2f}{Style.RESET_ALL}")

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
            print(f"\n{Fore.RED}💀 Bankrupt. Cannot place next bet.{Style.RESET_ALL}")
            break

        if win:
            if double_run and not has_doubled and len(bet_history) <= 2:
                print(f"{Fore.BLUE}🔁 Double run triggered! Restarting cycle...{Style.RESET_ALL}")
                current_bet = round(bank * starting_percent, 2)
                total_losses = 0.0
                has_doubled = True
                continue
            else:
                break
        else:
            current_bet = round(current_bet * bet_multiplier, 2)

    except Exception as e:
        print(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
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

body = f"Final bankroll: ${bank:.2f}\nNet Profit: ${net_profit:.2f}\n\n" + "\n".join(lines)
body += f"\n\nTotal Bets: {total_bets} | Wins: {total_wins} | Losses: {total_losses_count}"

if net_profit > 0:
    subject = f"Autobet $$$ WIN $$$ +${net_profit:.2f}"
elif bank <= 0:
    subject = f"Autobet :( Account blown -${abs(net_profit):.2f}"
else:
    subject = f"End of Simulation: +${net_profit:.2f}"

try:
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = config["email"]
    msg["To"] = config["email_recipient"]

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(config["email"], config["email_pass"])
        smtp.send_message(msg)

    print(f"\n{Fore.CYAN}📧 Summary email sent.{Style.RESET_ALL}")
except Exception as e:
    print(f"\n{Fore.RED}❌ Failed to send summary email: {e}{Style.RESET_ALL}")
