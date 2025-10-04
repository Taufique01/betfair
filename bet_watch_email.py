import os
import json
import smtplib
from datetime import datetime
from email.mime.text import MIMEText

# === Load config ===
with open("config.json") as f:
    config = json.load(f)

email_sender = config["email"]
email_pass = config["email_pass"]
email_recipient = config["email_recipient"]

TXT_LOG_PATH = "ghost_bets.txt"  # adjust if path changes
STAKE = 1.5
START_BANK = 50.00

# === Send Email ===
def send_email(subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = email_sender
    msg["To"] = email_recipient

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login(email_sender, email_pass)
        smtp.send_message(msg)

# === Parse .txt file into structured entries ===
def parse_txt_bets(filepath):
    bets = []
    if not os.path.exists(filepath):
        print(f"❌ No log file found at {filepath}")
        return bets

    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line or "@" not in line or "—" not in line:
                continue
            try:
                # Example: [18:15] Ballinrobe — Magnolia Drive @ 3.5 — WON
                parts = line.split("]")
                time_str = parts[0].replace("[", "").strip()
                rest = parts[1].strip()
                venue, after = rest.split(" — ", 1)
                selection, odds_result = after.split("@")
                odds_str, result = odds_result.split("—")

                bets.append({
                    "Time": time_str.strip(),
                    "Venue": venue.strip(),
                    "Selection": selection.strip(),
                    "Odds": float(odds_str.strip()),
                    "Result": result.strip(),
                })
            except Exception as e:
                print(f"⚠️ Failed to parse line: {line} — {e}")
    return bets

# === Build Report ===
def build_report(bets):
    total = len(bets)
    wins = sum(1 for b in bets if b["Result"].upper() == "WON")
    losses = total - wins
    win_rate = (wins / total) * 100 if total else 0.0
    bank = START_BANK

    lines = [f"🐎 Ghost Bet Daily Report ({total} Races)", "-" * 40]
    for b in bets:
        result = b['Result'].upper()
        line = f"[{b['Time']}] {b['Venue']} — {b['Selection']} @ {b['Odds']} — {result}"
        lines.append(line)
        if result == "WON":
            bank += (b["Odds"] - 1) * STAKE
        else:
            bank -= STAKE

    summary = f"""
📊 Summary:
Total Races: {total}
Wins: {wins}
Losses: {losses}
Win Rate: {win_rate:.1f}%
Starting Bank: ${START_BANK:.2f}
Ending Bank: ${bank:.2f}
Net Profit: ${bank - START_BANK:.2f}
""".strip()

    lines.append("")
    lines.append(summary)
    return "\n".join(lines)

# === Main ===
def main():
    bets = parse_txt_bets(TXT_LOG_PATH)
    if not bets:
        print("❌ No bets to report.")
        return
    body = build_report(bets)
    send_email("🐎 Ghost Bet Report", body)
    print("📧 Email sent!")

if __name__ == "__main__":
    main()
