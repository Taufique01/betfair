#!/usr/bin/env python3
import os, re, json, smtplib, sys, traceback
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

# -----------------------------
# CONFIG & PATHS
# -----------------------------
RESULTS_DIR = Path("chase_results")
LOGS_DIR    = Path("chase_logs")
RESULTS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

CFG_PATH = os.getenv("CHASE_CFG", "config.json")

# Log function
def log(msg, level="INFO"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{now} | {level} | {msg}", flush=True)
    with open(LOGS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}_process.log", "a") as f:
        f.write(f"{now} | {level} | {msg}\n")

try:
    with open(CFG_PATH, "r") as f:
        cfg = json.load(f)
except Exception as e:
    log(f"Failed to load config {CFG_PATH}: {e}", "ERROR")
    sys.exit(1)

FROM = cfg["email"]
TO   = cfg.get("chase_email_recipient", cfg.get("email_recipient", cfg["email"]))
SMTP_HOST = cfg.get("smtp_host", "smtp.gmail.com")
SMTP_PORT = int(cfg.get("smtp_port", 587))
SMTP_USER = cfg.get("smtp_user", cfg["email"])
SMTP_PASS = cfg.get("smtp_pass", cfg.get("email_pass"))

# -----------------------------
# HELPERS
# -----------------------------
def money(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

LINE_RE = re.compile(
    r"^(?P<ts>[^|]+)\s*\|\s*(?P<track>[^|]+)\s*\|\s*(?P<race>[^|]+)\s*\|\s*leg\s*(?P<leg>\S+)\s*\|\s*"
    r"(?P<runner>.*?)\s*@\s*(?P<odds>[\d\.]+)\s*\|\s*stake\s*(?P<stake>[\d\.]+)\s*\|\s*"
    r"result\s*(?P<result>\w+)\s*\|\s*profit\s*(?P<profit>-?[\d\.]+|None)\s*\|\s*balance\s*(?P<balance>-?[\d\.]+)"
)

# -----------------------------
# DATE FORMATTING
# -----------------------------
today_dt = datetime.now()
today_str = today_dt.strftime("%Y-%m-%d")
today_fmt = today_dt.strftime("%A, %B %d, %Y")  # Sunday, August 31, 2025

# -----------------------------
# LOAD TODAY'S RESULTS
# -----------------------------
subdir = RESULTS_DIR / today_str
log_file = subdir / f"chase_bets_{today_str}.csv"

bets = []
if log_file.exists():
    with open(log_file, "r") as fh:
        for line in fh:
            line = line.strip()
            if not line or "END_OF_DAY" in line:
                continue
            m = LINE_RE.match(line)
            if not m:
                log(f"Skipping unparsable line: {line}", "WARNING")
                continue
            d = m.groupdict()
            res = d["result"].upper()
            if res == "PENDING":
                continue
            bets.append({
                "time": d["ts"].strip(),
                "track": d["track"].strip(),
                "race": d["race"].strip(),
                "leg": d["leg"],
                "runner": d["runner"].strip(),
                "odds": float(d["odds"]),
                "stake": float(d["stake"]),
                "result": res,
                "profit": None if d["profit"]=="None" else float(d["profit"]),
                "balance": float(d["balance"]),
            })
else:
    log(f"No results file for today ({log_file}). Will send email with no bets.", "INFO")

# -----------------------------
# SUMMARY STATS
# -----------------------------
total   = len(bets)
wins    = sum(1 for b in bets if b["result"]=="W")
losses  = sum(1 for b in bets if b["result"]=="L")
skipped = sum(1 for b in bets if b["result"] not in ("W","L"))

net = Decimal("0.00")
for b in bets:
    if b["result"]=="W":
        net += money(b["profit"]) if b["profit"] is not None else money(Decimal(str(b["stake"]))*(Decimal(str(b["odds"]))-1))
    elif b["result"]=="L":
        net += money(b["profit"]) if b["profit"] is not None else -money(Decimal(str(b["stake"])))

# -----------------------------
# BUILD HTML EMAIL BODY
# -----------------------------
if total == 0:
    html_lines = f"""
    <h2>🐎 CHASE Report — {today_fmt}</h2>
    <p>No bets were placed today.</p>
    """
else:
    table_rows = ""
    for b in bets:
        result_color = "#28a745" if b["result"]=="W" else ("#dc3545" if b["result"]=="L" else "#6c757d")
        profit_str = "" if b["profit"] is None else f"£{money(b['profit']):.2f}"
        leg_str = "" if (b['leg'] in (None,'None')) else f"Leg {b['leg']}"
        table_rows += f"""
        <tr style="color:{result_color}">
            <td>{b['time']}</td>
            <td>{b['track']}</td>
            <td>{b['runner']}</td>
            <td>{b['odds']:.2f}</td>
            <td>£{money(b['stake']):.2f}</td>
            <td>{'WON' if b['result']=='W' else ('LOST' if b['result']=='L' else b['result'])}</td>
            <td>{profit_str}</td>
            <td>{leg_str}</td>
        </tr>
        """

    html_lines = f"""
    <h2>🐎 CHASE Report — {today_fmt}</h2>
    <table border="1" cellpadding="5" cellspacing="0" style="border-collapse:collapse">
        <thead style="background-color:#f2f2f2">
            <tr>
                <th>Time</th>
                <th>Track</th>
                <th>Runner</th>
                <th>Odds</th>
                <th>Stake</th>
                <th>Result</th>
                <th>Profit</th>
                <th>Leg</th>
            </tr>
        </thead>
        <tbody>
            {table_rows}
        </tbody>
    </table>
    <p>
        <strong>Total Bets:</strong> {total} &nbsp; | &nbsp;
        <strong>Wins:</strong> {wins} &nbsp; | &nbsp;
        <strong>Losses:</strong> {losses} &nbsp; | &nbsp;
        <strong>Skipped:</strong> {skipped} <br>
        <strong>Net P/L:</strong> £{net:.2f}
    </p>
    """

# -----------------------------
# SAVE EMAIL CONTENT
# -----------------------------
email_txt_file = LOGS_DIR / f"{today_str}_email.txt"
with open(email_txt_file, "w") as f:
    f.write(html_lines)
log(f"Saved email content to {email_txt_file}")

# -----------------------------
# SEND HTML EMAIL
# -----------------------------
try:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🐎 End-of-Day CHASE Report — {today_fmt}"
    msg["From"]    = FROM
    msg["To"]      = TO
    msg.attach(MIMEText(html_lines, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg)

    log(f"✅ CHASE summary email sent to {TO}")
except Exception as e:
    log(f"❌ Failed to send email: {e}\n{traceback.format_exc()}", "ERROR")
