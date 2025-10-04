#!/usr/bin/env python3
"""
Utility module for Original Chase daemon, providing:
- Initialization of log files
- Appending chase-specific logs to TXT and CSV
- Formatting summary email subject and body
"""
import csv
from datetime import datetime
from zoneinfo import ZoneInfo

def init_chase_logs(txt_path: str, csv_path: str, tz: ZoneInfo):
    """
    Ensure TXT and CSV log files exist and write headers if new.
    :param txt_path: Path to the text log file
    :param csv_path: Path to the CSV log file
    :param tz: Timezone for timestamps
    """
    # TXT file
    try:
        with open(txt_path, 'x') as f:
            f.write("timestamp | leg | track | runner | odds | result | stake | bal_before | bal_after\n")
    except FileExistsError:
        pass
    # CSV file
    try:
        with open(csv_path, 'x', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp","leg","track","runner","odds","result",
                "stake","bal_before","bal_after"
            ])
    except FileExistsError:
        pass


def append_chase_log(
    leg: int,
    track: str,
    runner: str,
    odds: float,
    result: str,
    stake: float,
    bal_before: float,
    bal_after: float,
    txt_path: str,
    csv_path: str,
    tz: ZoneInfo
):
    """
    Append a single chase bet entry to both TXT and CSV logs.
    """
    timestamp = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    # Write to TXT
    line = (
        f"{timestamp} | {leg} | {track} | {runner} | {odds} | {result} | "
        f"{stake} | {bal_before} | {bal_after}\n"
    )
    with open(txt_path, 'a') as f:
        f.write(line)
    # Write to CSV
    with open(csv_path, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            timestamp, leg, track, runner, odds, result,
            stake, bal_before, bal_after
        ])


def format_chase_summary(
    summary_rows: list,
    start_balance: float,
    ending_balance: float
) -> (str, str):
    """
    Build email subject and body from summary rows.
    :param summary_rows: List of tuples (leg, time, track, runner, odds, result)
    :return: (subject, body)
    """
    total_legs = len(summary_rows)
    profit = round(ending_balance - start_balance, 2)
    subject = f"Original Chase: {total_legs} legs, P/L £{profit:.2f}"

    lines = [
        f"Original Chase Results",
        f"Start Balance: £{start_balance:.2f}",
        f"End Balance: £{ending_balance:.2f}",
        f"Profit/Loss: £{profit:.2f}",
        "",
        "Leg  Time   Track   Runner   Odds   Result"
    ]
    for leg, time_str, track, runner, odds, result in summary_rows:
        lines.append(f"{leg:>2}   {time_str:>5}   {track:>6}   {runner:>10}   {odds:<4}   {result}")
    body = "\n".join(lines)
    return subject, body
