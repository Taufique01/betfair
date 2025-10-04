#!/usr/bin/env python3
import csv
import os
import json
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

# ───────────────────────────────────────────────────────
# Directories & filenames
today_dt = datetime.now() 
today_str = today_dt.strftime("%Y-%m-%d")
today_fmt = today_dt.strftime("%A, %B %d, %Y")  # Sunday, August 31, 2025

RESULTS_DIR = Path("results")       # daily CSVs
LOG_DIR     = Path("email_logs")    # logs & email content
LOG_DIR.mkdir(exist_ok=True)

CSV_LOG = RESULTS_DIR / f"ghost_bets_{today_str}.csv"
EMAIL_FILE_TXT = LOG_DIR / f"{today_str}_email.txt"
EMAIL_FILE_HTML = LOG_DIR / f"{today_str}_email.html"

# ───────────────────────────────────────────────────────
# Logging helper
def log_message(msg, level="INFO"):
    log_file = LOG_DIR / f"{today_str}.log"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a") as f:
        f.write(f"{ts} | {level} | {msg}\n")
    print(f"{ts} | {level} | {msg}")

# ───────────────────────────────────────────────────────
# Load email config
try:
    with open("config.json") as f:
        cfg = json.load(f)
except Exception as e:
    log_message(f"Failed to load config.json: {e}", "ERROR")
    raise

# ───────────────────────────────────────────────────────
# Read today's CSV
bets = []
if CSV_LOG.exists():
    try:
        with open(CSV_LOG, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                result = row.get("result", "").upper()
                if result == "PENDING":
                    continue

                try:
                    ts = datetime.fromisoformat(row.get("timestamp", "").split("+")[0])
                    time_str = ts.strftime("%b %d, %Y %I:%M %p")  # Sep 02, 2025 01:41 PM
                except Exception:
                    time_str = row.get("timestamp", "")

                try:
                    odds = float(row.get("odds", 0))
                except ValueError:
                    odds = 0.0

                bets.append({
                    "time": time_str,
                    "venue": row.get("location", ""),
                    "race": row.get("race_name", ""),
                    "runner": row.get("runner_name", ""),
                    "odds": odds,
                    "result": result
                })
    except Exception as e:
        log_message(f"Failed to read CSV {CSV_LOG}: {e}", "ERROR")
else:
    log_message(f"CSV {CSV_LOG} not found. Will send info accordingly.", "WARNING")

# ───────────────────────────────────────────────────────
# Build HTML email
if not CSV_LOG.exists():
    html_body = f"""
    <h2>🐎 Ghost Bet Report — {today_fmt}</h2>
    <p>⚠️ CSV log not found. Bets will be updated later when available.</p>
    """
elif not bets:
    html_body = f"""
    <h2>🐎 Ghost Bet Report — {today_fmt}</h2>
    <p>ℹ️ All bets for today are still pending. No results yet.</p>
    """
else:
    total  = len(bets)
    wins   = sum(1 for b in bets if b["result"] == "WON")
    losses = sum(1 for b in bets if b["result"] == "LOST")
    net    = sum((b["odds"] - 1) if b["result"] == "WON" else -1 for b in bets)

    # color code net result
    if net > 0:
        net_color = "#28a745"  # green
    elif net < 0:
        net_color = "#dc3545"  # red
    else:
        net_color = "#6c757d"  # gray

    table_rows = ""
    for b in bets:
        color = "#28a745" if b["result"] == "WON" else ("#dc3545" if b["result"] == "LOST" else "#6c757d")
        table_rows += f"""
        <tr style="color:{color}">
            <td>{b['time']}</td>
            <td>{b['venue']}</td>
            <td>{b['race']}</td>
            <td>{b['runner']}</td>
            <td>{b['odds']:.2f}</td>
            <td>{b['result']}</td>
        </tr>
        """

    html_body = f"""
    <h2>🐎 Ghost Bet Report — {today_fmt}</h2>
    <table border="1" cellpadding="5" cellspacing="0" style="border-collapse:collapse;">
        <thead style="background-color:#f2f2f2;">
            <tr>
                <th>Time</th>
                <th>Venue</th>
                <th>Race</th>
                <th>Runner</th>
                <th>Odds</th>
                <th>Result</th>
            </tr>
        </thead>
        <tbody>
            {table_rows}
        </tbody>
    </table>
    <p>
        <strong>Total Bets:</strong> {total} &nbsp; | &nbsp;
        <strong>Wins:</strong> {wins} &nbsp; | &nbsp;
        <strong>Losses:</strong> {losses} <br>
        <strong>Net Profit/Loss:</strong> <span style="color:{net_color};">${net:.2f}</span>
    </p>
    """

# ───────────────────────────────────────────────────────
# Save email content
try:
    with open(EMAIL_FILE_TXT, "w") as f:
        f.write(html_body)
    with open(EMAIL_FILE_HTML, "w") as f:
        f.write(html_body)
    log_message(f"✅ Email content saved to {EMAIL_FILE_TXT} & {EMAIL_FILE_HTML}", "INFO")
except Exception as e:
    log_message(f"❌ Failed to save email content: {e}", "ERROR")

# ───────────────────────────────────────────────────────
# Send HTML email
try:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🐎 End-of-Day Ghost Bet Report — {today_fmt}"
    msg["From"]    = cfg["email"]
    msg["To"]      = cfg["email_recipient"]
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(cfg.get("smtp_host", "smtp.gmail.com"), cfg.get("smtp_port", 587)) as smtp:
        smtp.starttls()
        smtp.login(cfg["email"], cfg["email_pass"])
        smtp.send_message(msg)

    log_message("✅ Summary email sent successfully.", "INFO")
except Exception as e:
    log_message(f"❌ Failed to send email: {e}", "ERROR")

