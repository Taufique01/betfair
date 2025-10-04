import json
import csv
from pathlib import Path
from datetime import datetime

import betfairlightweight

# ───────────────────────────────────────────────────────
# Utilities for Betfair client setup and logging
# ───────────────────────────────────────────────────────

# Directory references
ROOT = Path(__file__).parent.resolve()
CONFIG_FILE = ROOT / "config.json"
TXT_LOG     = ROOT / "ghost_bets.txt"
CSV_LOG     = ROOT / "ghost_bets.csv"


def load_config() -> dict:
    """
    Load the bot configuration from config.json.
    """
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Missing config file: {CONFIG_FILE}")
    return json.loads(CONFIG_FILE.read_text())


def create_client(cfg: dict):
    api = betfairlightweight.APIClient(
        cfg["betfair_username"],
        cfg["betfair_password"],
        app_key=cfg["betfair_app_key"],
        certs=tuple(cfg["certs"])
    )
    api.login()
    return api.betting, api.logout


def append_logs(bet: dict, location: str, race_name: str, start_time: datetime) -> None:
    """
    Append a single bet record to both text and CSV logs.

    bet: {
      timestamp, location, race_name, runner_name, odds, stake, result, chase, leg
    }
    """
    now_ts = datetime.now().isoformat()

    # --- Text Summary ---
    summary = (
        f"{now_ts} | {location} | {race_name} | "
        f"{bet['runner_name']} @ {bet['odds']} | "
        f"Stake: {bet.get('stake','')} | Result: {bet['result']} | "
        f"Chase: {bet.get('chase','')} | Leg: {bet.get('leg','')}\n"
    )
    with TXT_LOG.open('a') as f:
        f.write(summary)

    # --- CSV Logging ---
    header = [
        'timestamp', 'location', 'race_name', 'runner_name',
        'odds', 'stake', 'result', 'chase', 'leg'
    ]
    # Write header if new
    if not CSV_LOG.exists():
        with CSV_LOG.open('w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(header)

    # Append the row
    row = [
        now_ts,
        location,
        race_name,
        bet.get('runner_name',''),
        bet.get('odds',''),
        bet.get('stake',''),
        bet.get('result',''),
        bet.get('chase',''),
        bet.get('leg',''),
    ]
    with CSV_LOG.open('a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(row)

