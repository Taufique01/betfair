#!/usr/bin/env python3
"""
chase_email.py

Reads 'ghost_chase.txt', computes Original Chase summary (legs, wins, losses, P/L), and sends an email.
Usage: send_summary_email(recipient, logfile_path)
"""
import os
import smtplib
from email.message import EmailMessage

def send_summary_email(recipient: str, logfile_path: str):
    """
    Read chase log, calculate summary, and send email.
    :param recipient: email address
    :param logfile_path: path to ghost_chase.txt
    """
    # Parse log
    if not os.path.exists(logfile_path):
        raise FileNotFoundError(f"Log file not found: {logfile_path}")

    legs = []  # list of dicts per line
    with open(logfile_path) as f:
        header = f.readline().strip().split(' | ')
        for line in f:
            parts = [p.strip() for p in line.strip().split('|')]
            if len(parts) != len(header):
                continue
            entry = dict(zip([h.replace(' ', '_') for h in header], parts))
            # Convert numeric fields
            entry['odds'] = float(entry['odds'])
            entry['stake'] = float(entry['stake'])
            entry['bal_before'] = float(entry['bal_before'])
            entry['bal_after'] = float(entry['bal_after'])
            legs.append(entry)

    if not legs:
        subject = "Original Chase: No legs placed"
        body = "No bets were logged today."
    else:
        # Compute stats
        total_legs = len(legs)
        wins = sum(1 for e in legs if e['result'] == 'WON')
        losses = sum(1 for e in legs if e['result'] == 'LOST')
        start_balance = legs[0]['bal_before']
        end_balance = legs[-1]['bal_after']
        profit = round(end_balance - start_balance, 2)

        subject = f"Original Chase: {wins}W/{losses}L in {total_legs} legs, P/L £{profit:.2f}"

        # Build body
        lines = [
            f"Original Chase Results ({total_legs} legs, {wins} wins, {losses} losses)",
            f"Start Balance: £{start_balance:.2f}",
            f"End Balance:   £{end_balance:.2f}",
            f"Profit/Loss:   £{profit:.2f}",
            "",
            "Leg | Time       | Track | Runner       | Odds | Result | Stake  | BalBefore→After"
        ]
        for e in legs:
            time = e['timestamp']
            leg = e['leg']
            track = e['track']
            runner = e['runner']
            odds = e['odds']
            result = e['result']
            stake = e['stake']
            bb = e['bal_before']
            ba = e['bal_after']
            lines.append(
                f"{leg:>3} | {time} | {track:>6} | {runner:>12} | {odds:<4.2f} | {result:^5} | £{stake:<6.2f} | £{bb:.2f}→£{ba:.2f}"
            )
        body = "\n".join(lines)

    # Prepare email
    msg = EmailMessage()
    msg["From"] = "no-reply@yourdomain.com"
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)

    # SMTP settings (adjust as needed)
    SMTP_HOST = "smtp.yourprovider.com"
    SMTP_PORT = 587
    SMTP_USER = "username"
    SMTP_PASS = "password"

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg)
