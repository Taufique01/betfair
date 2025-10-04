#!/usr/bin/env python3
import json
import csv
from pathlib import Path

import betfairlightweight

# ───────────────────────────────────────────────────────
ROOT_DIR    = Path(__file__).parent.resolve()
CONFIG_FILE = ROOT_DIR / "config.json"
CSV_OUT     = ROOT_DIR / "sports.csv"
# ───────────────────────────────────────────────────────

def load_config():
    # identical to bet_watch.py
    return json.loads(CONFIG_FILE.read_text())

def create_client(cfg):
    # identical to bet_watch.py
    client = betfairlightweight.APIClient(
        username=cfg["betfair_username"],
        password=cfg["betfair_password"],
        app_key=cfg["betfair_app_key"],
        certs=cfg["certs"]
    )
    client.login()
    return client.betting, client.logout

def main():
    cfg = load_config()
    betting, logout = create_client(cfg)

    try:
        # fetch all event types (sports)
        res = betting.list_event_types()
        sports = res if isinstance(res, list) else res.event_types

        # print to console
        print(f"{'ID':<6}  Name")
        print("-" * 40)
        for sp in sports:
            eid  = sp.event_type.id
            name = sp.event_type.name
            print(f"{eid:<6}  {name}")

        # export to CSV
        with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["event_type_id", "event_type_name"])
            for sp in sports:
                writer.writerow([sp.event_type.id, sp.event_type.name])

        print(f"\nExported to {CSV_OUT}")

    finally:
        logout()

if __name__ == "__main__":
    main()
