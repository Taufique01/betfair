#!/usr/bin/env python3
"""
Interactive CLI that:
 - Loads sports (ID + name) from sports_competitions.json
 - Dynamically fetches competitions, markets, and runners via Betfair API
 - Displays live odds for selected market
"""
import json
from pathlib import Path

import betfairlightweight

# Paths
script_dir = Path(__file__).parent.resolve()
CONFIG_FILE = script_dir / "config.json"
SPORTS_FILE = script_dir / "sports_competitions.json"

# --- Load config and create client ---
def load_config():
    return json.loads(CONFIG_FILE.read_text())

def create_client(cfg):
    client = betfairlightweight.APIClient(
        username=cfg['betfair_username'],
        password=cfg['betfair_password'],
        app_key=cfg['betfair_app_key'],
        certs=cfg['certs']
    )
    client.login()
    return client.betting, client.logout

# --- Utility for menus ---
def prompt_menu(options, title):
    print(f"\n{title}\n")
    for idx, (key, name) in enumerate(options, start=1):
        print(f"{idx}. {name} (ID {key})")
    print("0. Exit")
    choice = input("Select number: ").strip()
    if choice == '0':
        return None
    try:
        idx = int(choice)
        if 1 <= idx <= len(options):
            return options[idx-1][0]
    except ValueError:
        pass
    print("Invalid choice, try again.")
    return prompt_menu(options, title)

# --- Main navigation ---
def main():
    cfg = load_config()
    betting, logout = create_client(cfg)
    try:
        # Load sports from file
        sports_data = json.loads(SPORTS_FILE.read_text())
        sports = [(int(sid), data['sport_name']) for sid, data in sports_data.items()]
        while True:
            sport_id = prompt_menu(sports, 'Select a Sport:')
            if sport_id is None:
                print("Goodbye!")
                break

            # Fetch competitions live
            comps_resp = betting.list_competitions(filter={'eventTypeIds': [sport_id]})
            comps = comps_resp if isinstance(comps_resp, list) else comps_resp.competitions
            comp_list = [(c.competition.id, c.competition.name) for c in comps]
            comp_id = prompt_menu(comp_list, f"Competitions for Sport ID {sport_id}:")
            if comp_id is None:
                continue  # back to sports

            # Fetch markets live
            mkt_resp = betting.list_market_catalogue(
                filter={'eventTypeIds': [sport_id], 'competitionIds': [comp_id]},
                max_results=100,
                sort='FIRST_TO_START',
                market_projection=['RUNNER_DESCRIPTION']
            )
            mkts = mkt_resp if isinstance(mkt_resp, list) else mkt_resp.market_catalogue
            mkt_list = [(m.market_id, m.market_name) for m in mkts]
            mkt_id = prompt_menu(mkt_list, f"Markets for Competition ID {comp_id}:")
            if mkt_id is None:
                continue  # back to competitions

            # Find selected market from catalogue
            market = next((m for m in mkts if m.market_id == mkt_id), None)
            if not market:
                print("Market not found, try again.")
                continue

            # Fetch live odds via market book
            print(f"\nFetching odds for '{market.market_name}' (ID {mkt_id})...")
            mb_resp = betting.list_market_book(
                market_ids=[mkt_id],
                price_projection={'priceData': ['EX_BEST_OFFERS']}
            )
            mb = mb_resp[0] if isinstance(mb_resp, list) else mb_resp.market_books[0]

            # Display runners with odds
            print(f"\nRunners with Odds in '{market.market_name}':\n")
            for runner_book in mb.runners:
                sel_id = runner_book.selection_id
                # get runner name from catalogue
                runner_name = next(
                    (r.runner_name for r in market.runners if r.selection_id == sel_id),
                    'N/A'
                )
                # best back and lay
                if runner_book.ex.available_to_back:
                    best_back = runner_book.ex.available_to_back[0]
                    back_price, back_size = best_back.price, best_back.size
                else:
                    back_price = back_size = None
                if runner_book.ex.available_to_lay:
                    best_lay = runner_book.ex.available_to_lay[0]
                    lay_price, lay_size = best_lay.price, best_lay.size
                else:
                    lay_price = lay_size = None

                print(f"- {runner_name} (ID {sel_id})")
                print(f"    Best Back: {back_price} @ {back_size}")
                print(f"    Best Lay : {lay_price} @ {lay_size}\n")

            input("Press Enter to continue...")

    finally:
        logout()

if __name__ == '__main__':
    main()
