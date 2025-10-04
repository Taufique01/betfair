#!/usr/bin/env python3
import json, sys, math
from datetime import datetime, timedelta
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from datetime import timezone

from logger_factory import get_logger  # singleton logger

# -----------------------------
# Logger
# -----------------------------
logger = get_logger("chase_logs")  # singleton logger


from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config_utils import load_config, create_client
from markets import get_today_markets, determine_fav_and_odds
from results import await_result
from safe_api import safe_api_call


# -----------------------------
# Constants / Paths
# -----------------------------
LONDON = ZoneInfo("Europe/London")
ROOT_DIR = Path(__file__).parent.resolve()
RESULTS_DIR = ROOT_DIR / "chase_results"
RESULTS_DIR.mkdir(exist_ok=True)


STATE_FILE = ROOT_DIR / "chase_state.json"
BALANCE_FILE = ROOT_DIR / "bank_balance.json"
STRAT_FILE = ROOT_DIR / "strat_settings.json"
LOW_WIN_FILE = ROOT_DIR / "low_win_races.json"
TRACK_GRADE_FILE = ROOT_DIR / "track_grades.json"

BET_BUFFER_DEFAULT = 60  # seconds before race


# -----------------------------
# Logging
# -----------------------------
def log_message(msg, level="INFO"):
    """
    Log a message using singleton logger.

    Args:
        msg (str): Message to log
        level (str): Log level. Options: INFO, WARNING, ERROR, EXCEPTION
    """
    level = level.upper()
    if level == "ERROR":
        logger.error(msg)
    elif level == "WARNING" or level == "WARN":
        logger.warning(msg)
    elif level == "EXCEPTION":
        logger.exception(msg)
    else:
        logger.info(msg)


# Constants
MIN_STAKE = Decimal("0.01")
FIRST_BET_PERCENTAGE = Decimal("0.04")
ACCOUNT_CAP = Decimal("5000")  # max cap for first bet / all-in
MULTIPLIER_HIGH = Decimal("1.50")  # odds >= 3
MULTIPLIER_MED = Decimal("1.65")  # 2.25 <= odds < 3
PROFIT_BUFFER = Decimal("1.20")  # odds < 2.25


# -----------------------------
# Helpers
# -----------------------------
def to_datetime(dt):
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def money(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {
        "balance": load_initial_balance(),
        "leg": 1,
        "accumulated_losses": 0.0,
        "prev_stake": None,
        "chase_active": False,
    }


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def load_initial_balance():
    if not BALANCE_FILE.exists():
        log_message(f"Missing {BALANCE_FILE}", "ERROR")
        sys.exit(1)
    b = load_json(BALANCE_FILE)
    if "balance" not in b:
        log_message(f"{BALANCE_FILE} missing 'balance' key", "ERROR")
        sys.exit(1)
    return float(b["balance"])


# -----------------------------
# Stake calculation
# -----------------------------

from decimal import Decimal, ROUND_UP


def ceil_penny(amount: Decimal) -> Decimal:
    """Round up to the next penny."""
    return (amount * 100).to_integral_value(rounding=ROUND_UP) / Decimal("100")


def calculate_next_stake(prev_stake, leg, next_odds, acc_losses, balance):
    """
    Compute next stake for CHASE strategy:
    - Leg 1: 4% of balance, capped at 5000, rounded penny-first
    - Legs 2-5: multipliers or short-odds recovery
    - Leg 6: all-in (up to 5000)
    - Minimum stake enforced: 0.01
    """
    o = Decimal(str(next_odds))
    ps = Decimal(str(prev_stake)) if prev_stake else Decimal("0.01")
    losses = Decimal(str(acc_losses))
    bal = Decimal(str(balance))

    # --- Leg 1: first bet ---
    if leg <= 1:
        base = min(bal, ACCOUNT_CAP)
        stake = base * FIRST_BET_PERCENTAGE
        stake = ceil_penny(stake)
        return float(max(stake, MIN_STAKE))

    # --- Leg 6: all-in ---
    if leg == 6:
        stake = min(bal, ACCOUNT_CAP)
        return float(stake)

    # --- Legs 2-5: multipliers or recovery ---
    if leg in [2, 3, 4, 5]:
        if o >= 3:
            stake = ps * MULTIPLIER_HIGH
        elif o >= 2.25:
            stake = ps * MULTIPLIER_MED
        else:
            # short-odds recovery
            if (o - 1) <= 0:
                stake = MIN_STAKE
            else:
                stake = (losses * PROFIT_BUFFER) / (o - 1)
        stake = ceil_penny(stake)
        return float(max(stake, MIN_STAKE))

    # fallback (should rarely reach here)
    return float(max(ceil_penny(ps), MIN_STAKE))


# satke over balances
# -----------------------------
# Race skipping
# -----------------------------
def should_skip(event_name, track_name, low_win_list, track_grades):
    en = (event_name or "").strip()
    tn = (track_name or "").strip().lower()  # normalize track name

    # low-win list
    for row in low_win_list or []:
        if isinstance(row, dict) and row.get("skip", False):
            e = (row.get("event_name") or row.get("race_name") or "").strip()
            t = (row.get("track") or row.get("venue") or "").strip().lower()
            if (e and e == en) or (
                t and t in tn
            ):  # substring match, case-insensitive for track
                log_message(
                    f"Skipping {event_name}/{track_name} - reason: low win list"
                )
                return True

    # track grades (dict form)
    if isinstance(track_grades, dict):
        for k, v in track_grades.items():
            if k and k.strip().lower() in tn:  # fuzzy match
                tg = v
                if isinstance(tg, dict) and tg.get("skip", False):
                    log_message(
                        f"Skipping {event_name}/{track_name} - reason: track_grades dict"
                    )
                    return True
                if isinstance(tg, bool) and tg:
                    log_message(
                        f"Skipping {event_name}/{track_name} - reason: track_grades bool"
                    )
                    return True

    # track grades (list form)
    if isinstance(track_grades, list):
        for row in track_grades:
            t = (row.get("track") or row.get("venue") or "").strip().lower()
            if t and t in tn and row.get("skip", False):  # substring match
                log_message(
                    f"Skipping {event_name}/{track_name} - reason: track_grades list"
                )
                return True

    return False


# -----------------------------
# Results logging
# -----------------------------
def append_result(record):
    today_str = datetime.now(LONDON).strftime("%Y-%m-%d")
    folder = RESULTS_DIR / today_str
    folder.mkdir(exist_ok=True)

    # Daily file
    csv_file = folder / f"chase_bets_{today_str}.csv"
    # Global file (stored directly in RESULTS_DIR)
    csv_file_all_results = RESULTS_DIR / "chase_results.csv"

    import csv

    CSV_HEADERS = [
        "timestamp",
        "market_id",
        "race_name",
        "track",
        "date",
        "leg",
        "selection",
        "odds",
        "stake",
        "result",
        "profit",
        "balance",
    ]

    def write_csv_row(file_path: Path):
        write_header = not file_path.exists()
        with open(file_path, "a", newline="") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(CSV_HEADERS)
            w.writerow([record.get(k) for k in CSV_HEADERS])

    # Write to daily file
    write_csv_row(csv_file)

    # Write to global file
    write_csv_row(csv_file_all_results)

# -----------------------------
# Bet placement
# -----------------------------
def place_chase_bet(client, market, fav, stake, odds):
    sel_id = fav.get("selection_id")
    runner_name = fav.get("runner_name")
    bet = {
        "selection_id": sel_id,
        "runner_name": runner_name,
        "odds": odds,
        "placed_odds": float(odds),
        "stake": stake,
        "market_id": getattr(market, "market_id", None),
        "race_name": getattr(market, "market_name", None),
        "result": "PENDING",
    }
    log_message(f"Bid placed: {str(bet)}", "INFO")

    race_start = to_datetime(getattr(market, "market_start_time", None))

    await_result(client, market, market.market_name, bet, race_start, channel="chase")
    win = bet.get("result") == "WON"
    profit = (odds - 1) * stake if win else -stake
    return {"win": win, "profit": profit, "selection": runner_name}


def get_cutoff():
    settings = load_json(STRAT_FILE)

    # Load cutoff
    ct = datetime.strptime(settings["cutoff_time"], "%H:%M").time()
    now = datetime.now(LONDON)
    cutoff = datetime(now.year, now.month, now.day, ct.hour, ct.minute, tzinfo=LONDON)
    return cutoff

# -----------------------------
# Bet placement
# -----------------------------
def place_bet_job(client, market):
    try:
        state = load_state()
        log_message(f"State: {state}", "INFO")
        leg = state.get("leg", 1)   # Default leg = 1

        now = datetime.now(LONDON)  # Current time in London
        cutoff = get_cutoff()       # Configured cutoff time

        if now > cutoff and leg==1:
            log_message(f"Skipping {market.market_name} - after cutoff {cutoff}, leg {leg}")
            return
        
        # Current balance from state or initial load
        bal = state.get("balance", 0.0)
        # 🛑 Skip if balance is less than minimum stake
        if bal < MIN_STAKE:
            log_message(
                f"Skipping bet for {market.market_name} - balance too low ({bal} < {MIN_STAKE})",
                "WARN",
            )
            return

        # 🛑 Skip if a race is already flagged as running
        if state.get("is_running_race", False):
            log_message(
                f"Skipping bet for {market.market_name} - race already running (via state flag)",
                "WARN",
            )
            return
                
        ps = state.get("prev_stake")  # previous stake (None for leg 1)
        acc_losses = state.get("accumulated_losses", 0.0)

        # Determine favourite and odds
        fav, odds = determine_fav_and_odds(client, market)
        if not fav or not odds:
            log_message(f"Missing fav/odds for {market.market_name}", "WARN")
            return

        # Calculate stake using the strategy functions

        stake = calculate_next_stake(ps, leg, odds, acc_losses, bal)

        # 🆕 Mark race as running
        state["is_running_race"] = True
        save_state(state)

        # Place bet
        try:
            outcome = place_chase_bet(client, market, fav, stake, odds)
        except Exception as e:
            log_message(f"Error placing bet {market.market_name}: {e}", "ERROR")
            return

        win, profit = outcome["win"], outcome["profit"]

        # Update state
        if win:
            state.update(
                {
                    "accumulated_losses": 0.0,
                    "prev_stake": None,
                    "chase_active": False,
                    "leg": 1,
                }
            )
        else:
            state["accumulated_losses"] = acc_losses + stake
            state["prev_stake"] = stake
            state["leg"] = leg + 1

        # Update balance
        bal += profit
        state["balance"] = bal

        # ✅ Race finished, reset flag
        state["is_running_race"] = False
        save_state(state)

        # Record result
        rec = {
            "timestamp": datetime.now(LONDON).isoformat(),
            "market_id": getattr(market, "market_id", None),
            "race_name": getattr(market, "market_name", None),
            "track": getattr(getattr(market, "event", None), "name", None),
            "date": getattr(market, "market_start_time", None),
            "leg": leg,
            "selection": outcome["selection"],
            "odds": odds,
            "stake": stake,
            "result": "W" if win else "L",
            "profit": profit,
            "balance": bal,
        }
        append_result(rec)
        log_message(
            f"Bet placed for {rec['race_name']}: {rec['selection']} | stake {stake} | "
            f"result {rec['result']} | balance {bal}"
        )

    except Exception as e:
        import traceback

        log_message(
            f"Exception in place_bet_job for {market.market_name}: {e}", "ERROR"
        )
        log_message(traceback.format_exc(), "ERROR")


# -----------------------------
# Daily 5 AM reset
# -----------------------------
def daily_reset_job():
    log_message("Running daily 5 AM reset")
    # Reset chase state
    save_state(
        {
            "balance": load_initial_balance(),
            "leg": 1,
            "accumulated_losses": 0.0,
            "prev_stake": None,
            "chase_active": False,
        }
    )



# -----------------------------
# Schedule all races
# -----------------------------
# -----------------------------
# Schedule all races (safe version)
# -----------------------------
import time



def schedule_races(scheduler):
    client, _ = create_client(load_config())

    mkts = get_today_markets(client, LONDON)
    settings = load_json(STRAT_FILE)
    low_win_list = load_json(LOW_WIN_FILE)
    track_grades = load_json(TRACK_GRADE_FILE)

    # Daily 5 AM reset
    daily_reset_job()

    # Schedule bets
    for m in mkts:
        race_start = to_datetime(getattr(m, "market_start_time", None))
        track_name = getattr(getattr(m, "event", None), "name", None)
        
        # # Check cutoff
        # if race_start >= cutoff:
        #     log_message(f"Skipping {m.market_name}/{track_name} - after cutoff {cutoff}")
        #     continue

        
        if should_skip(m.market_name, track_name, low_win_list, track_grades):
            log_message(f"Skipping {m.market_name}/{track_name}")
            continue

        bet_time = race_start - timedelta(
            seconds=settings.get("bet_buffer_seconds", BET_BUFFER_DEFAULT)
        )
        if bet_time <= datetime.now(LONDON):
            log_message(f"Race {m.market_name} already passed or too close")
            continue

        scheduler.add_job(place_bet_job, "date", run_date=bet_time, args=[client, m])
        log_message(f"Scheduled bet for {m.market_name} at {bet_time}")

    log_message("All races scheduled. Scheduler running...")
    

# -----------------------------
# Main entry
# -----------------------------
from datetime import time as dt_time

if __name__ == "__main__":
    # Use the same timezone as your code
    TZ = LONDON

    # Initialize a single scheduler
    scheduler = BackgroundScheduler(timezone=TZ)
    scheduler.start()

    # Schedule daily reset + race scheduling at 5:00 AM
    scheduler.add_job(
        func=schedule_races,
        trigger=CronTrigger(hour=5, minute=0, timezone=TZ),
        args=[scheduler],
        id="daily_schedule"
    )

    # Check if we already passed 5 AM today → run immediately
    now_local = datetime.now(TZ)
    if now_local.time() >= dt_time(5, 0):
        log_message("⏱ Already past 5:00 AM, fetching today's races immediately...")
        schedule_races(scheduler)

    else:
        log_message("⌛ Waiting until 5:00 AM BST for first schedule...")

    try:
        # Keep main thread alive
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log_message("Bot stopped manually")
    finally:
        scheduler.shutdown()
        
        log_message("Logged out from Betfair API")
