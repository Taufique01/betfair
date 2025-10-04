#!/usr/bin/env python3
import sys
from datetime import datetime, timezone
from bet_watch import (
    create_client,
    load_config,
    get_today_markets,
    determine_fav_and_odds
)


def main():
    # Initialize Betfair client
    client, logout = create_client(load_config())
    try:
        # Fetch today's markets
        markets = get_today_markets(client)
        now_utc = datetime.now(timezone.utc)

        # Separate upcoming and completed markets
        upcoming = [m for m in markets if getattr(m, 'market_start_time', now_utc) > now_utc]
        completed = [m for m in markets if getattr(m, 'market_start_time', now_utc) <= now_utc]

        # Print upcoming races and favorites
        print("\n=== Upcoming Races & Favorites ===")
        for m in sorted(upcoming, key=lambda x: x.market_start_time):
            fav, odds = determine_fav_and_odds(client, m)
            fav_name = fav.get('runner_name') or fav.get('selection_name') or 'N/A'
            start = m.market_start_time.strftime('%Y-%m-%d %H:%M:%S %Z')
            print(f"{m.market_name} at {start} --> Favorite: {fav_name} @ {odds}")

        # Print completed races and results
        print("\n=== Completed Races & Results ===")
        for m in sorted(completed, key=lambda x: x.market_start_time):
            # Fetch the market book to check status and winner
            books = client.betting.list_market_book(market_ids=[m.market_id])
            if not books:
                print(f"{m.market_name}: No market book data")
                continue
            book = books[0]
            status = book.status
            if status == 'CLOSED':
                winner = next((r for r in book.runners if r.status == 'WINNER'), None)
                winner_name = getattr(winner, 'runner_name', 'Unknown') if winner else 'No winner'
                print(f"{m.market_name}: CLOSED - Winner: {winner_name}")
            else:
                print(f"{m.market_name}: Status: {status}")
    finally:
        # Clean up session
        try:
            logout()
        except Exception:
            pass

if __name__ == '__main__':
    main()
#!/usr/bin/env python3
import sys
from datetime import datetime, timezone
from bet_watch import (
    create_client,
    load_config,
    get_today_markets,
    determine_fav_and_odds
)


def main():
    # Initialize Betfair client
    client, logout = create_client(load_config())
    try:
        # Fetch today's markets
        markets = get_today_markets(client)
        now_utc = datetime.now(timezone.utc)

        # Separate upcoming and completed markets
        upcoming = [m for m in markets if getattr(m, 'market_start_time', now_utc) > now_utc]
        completed = [m for m in markets if getattr(m, 'market_start_time', now_utc) <= now_utc]

        # Print upcoming races and favorites
        print("\n=== Upcoming Races & Favorites ===")
        for m in sorted(upcoming, key=lambda x: x.market_start_time):
            fav, odds = determine_fav_and_odds(client, m)
            fav_name = fav.get('runner_name') or fav.get('selection_name') or 'N/A'
            start = m.market_start_time.strftime('%Y-%m-%d %H:%M:%S %Z')
            print(f"{m.market_name} at {start} --> Favorite: {fav_name} @ {odds}")

        # Print completed races and results
        print("\n=== Completed Races & Results ===")
        for m in sorted(completed, key=lambda x: x.market_start_time):
            # Fetch the market book to check status and winner
            books = client.betting.list_market_book(market_ids=[m.market_id])
            if not books:
                print(f"{m.market_name}: No market book data")
                continue
            book = books[0]
            status = book.status
            if status == 'CLOSED':
                winner = next((r for r in book.runners if r.status == 'WINNER'), None)
                winner_name = getattr(winner, 'runner_name', 'Unknown') if winner else 'No winner'
                print(f"{m.market_name}: CLOSED - Winner: {winner_name}")
            else:
                print(f"{m.market_name}: Status: {status}")
    finally:
        # Clean up session
        try:
            logout()
        except Exception:
            pass

if __name__ == '__main__':
    main()

