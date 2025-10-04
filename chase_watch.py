
# ============================
# File: chase_bot.py
# Purpose: Main logic; imports DB helpers from db_layer.py
# ============================
import json, sys, csv, time
from datetime import datetime, timedelta, time as dt_time, timezone
from decimal import Decimal, ROUND_HALF_UP, ROUND_UP
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from logger_factory import get_logger
from config_utils import load_config, create_client
from markets import get_today_markets, determine_fav_and_odds
from results import await_result

from db_layer import (
    init_db, record_schedule, update_schedule_status,
    create_pending_bet, finalize_bet
)

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
BET_BUFFER_DEFAULT = 60

logger = get_logger("chase_logs")

def log_message(msg, level="INFO"):
    level = level.upper()
    if level == "ERROR":
        logger.error(msg)
    elif level in ("WARNING", "WARN"):
        logger.warning(msg)
    elif level == "EXCEPTION":
        logger.exception(msg)
    else:
        logger.info(msg)

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

def load_json(path: Path):
    with open(path, "r") as f:
        return json.load(f)

def load_initial_balance():
    if not BALANCE_FILE.exists():
        log_message(f"Missing {BALANCE_FILE}", "ERROR"); sys.exit(1)
    b = load_json(BALANCE_FILE)
    if "balance" not in b:
        log_message(f"{BALANCE_FILE} missing 'balance' key", "ERROR"); sys.exit(1)
    return float(b["balance"])

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
        "is_running_race": False,
    }

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# -----------------------------
# Stake calculation
# -----------------------------
MIN_STAKE = Decimal("0.01")
FIRST_BET_PERCENTAGE = Decimal("0.04")
ACCOUNT_CAP = Decimal("5000")
MULTIPLIER_HIGH = Decimal("1.50")
MULTIPLIER_MED = Decimal("1.65")
PROFIT_BUFFER = Decimal("1.20")

def ceil_penny(amount: Decimal) -> Decimal:
    return (amount * 100).to_integral_value(rounding=ROUND_UP) / Decimal("100")

def calculate_next_stake(prev_stake, leg, next_odds, acc_losses, balance):
    o = Decimal(str(next_odds))
    ps = Decimal(str(prev_stake)) if prev_stake else Decimal("0.01")
    losses = Decimal(str(acc_losses))
    bal = Decimal(str(balance))

    if leg <= 1:
        base = min(bal, ACCOUNT_CAP)
        stake = ceil_penny(base * FIRST_BET_PERCENTAGE)
        return float(max(stake, MIN_STAKE))

    if leg == 6:
        return float(min(bal, ACCOUNT_CAP))

    if leg in [2, 3, 4, 5]:
        if o >= 3:
            stake = ps * MULTIPLIER_HIGH
        elif o >= 2.25:
            stake = ps * MULTIPLIER_MED
        else:
            stake = (MIN_STAKE if (o - 1) <= 0 else (losses * PROFIT_BUFFER) / (o - 1))
        return float(max(ceil_penny(Decimal(stake)), MIN_STAKE))

    return float(max(ceil_penny(ps), MIN_STAKE))

# -----------------------------
# Skipping rules
# -----------------------------

def should_skip(event_name, track_name, low_win_list, track_grades):
    en = (event_name or "").strip()
    tn = (track_name or "").strip().lower()

    for row in low_win_list or []:
        if isinstance(row, dict) and row.get("skip", False):
            e = (row.get("event_name") or row.get("race_name") or "").strip()
            t = (row.get("track") or row.get("venue") or "").strip().lower()
            if (e and e == en) or (t and t in tn):
                log_message(f"Skipping {event_name}/{track_name} - low win list")
                return True

    if isinstance(track_grades, dict):
        for k, v in track_grades.items():
            if k and k.strip().lower() in tn:
                tg = v
                if (isinstance(tg, dict) and tg.get("skip", False)) or (isinstance(tg, bool) and tg):
                    log_message(f"Skipping {event_name}/{track_name} - track grade")
                    return True

    if isinstance(track_grades, list):
        for row in track_grades:
            t = (row.get("track") or row.get("venue") or "").strip().lower()
            if t and t in tn and row.get("skip", False):
                log_message(f"Skipping {event_name}/{track_name} - track grade list")
                return True

    return False

# -----------------------------
# Optional CSV audit
# -----------------------------

def append_result_csv(record):
    today_str = datetime.now(LONDON).strftime("%Y-%m-%d")
    folder = RESULTS_DIR / today_str
    folder.mkdir(exist_ok=True)

    csv_file = folder / f"chase_bets_{today_str}.csv"
    csv_file_all = RESULTS_DIR / "chase_results.csv"

    headers = [
        "timestamp","market_id","race_name","track","date","leg",
        "selection","odds","stake","result","profit","balance",
    ]

    def write_row(path):
        write_header = not path.exists()
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(headers)
            w.writerow([record.get(k) for k in headers])

    write_row(csv_file)
    write_row(csv_file_all)

# -----------------------------
# Bet placement
# -----------------------------

def place_chase_bet(client, market, fav, stake, odds):
    bet = {
        "selection_id": fav.get("selection_id"),
        "runner_name": fav.get("runner_name"),
        "odds": odds,
        "placed_odds": float(odds),
        "stake": stake,
        "market_id": getattr(market, "market_id", None),
        "race_name": getattr(market, "market_name", None),
        "result": "PENDING",
    }
    log_message(f"Bid placed: {bet}")

    race_start = to_datetime(getattr(market, "market_start_time", None))
    await_result(client, market, market.market_name, bet, race_start, channel="chase")

    win = bet.get("result") == "WON"
    profit = (odds - 1) * stake if win else -stake
    return {"win": win, "profit": float(profit), "selection": fav.get("runner_name")}

# -----------------------------
# Config helpers
# -----------------------------

def get_cutoff():
    settings = load_json(STRAT_FILE)
    ct = datetime.strptime(settings["cutoff_time"], "%H:%M").time()
    now = datetime.now(LONDON)
    return datetime(now.year, now.month, now.day, ct.hour, ct.minute, tzinfo=LONDON)

# -----------------------------
# Job (runs at scheduled time)
# -----------------------------

def place_bet_job(client, market, job_id: str):
    try:
        update_schedule_status(job_id, "running")
        state = load_state()
        leg = state.get("leg", 1)

        now = datetime.now(LONDON)
        cutoff = get_cutoff()
        if now > cutoff and leg == 1:
            msg = f"Skipping {market.market_name} - after cutoff {cutoff}, leg {leg}"
            log_message(msg)
            update_schedule_status(job_id, "skipped", error=msg)
            return

        bal = state.get("balance", 0.0)
        if bal < float(MIN_STAKE):
            msg = f"Skipping bet for {market.market_name} - balance too low ({bal} < {MIN_STAKE})"
            log_message(msg, "WARN")
            update_schedule_status(job_id, "skipped", error=msg)
            return

        if state.get("is_running_race", False):
            msg = f"Skipping bet for {market.market_name} - race already running"
            log_message(msg, "WARN")
            update_schedule_status(job_id, "skipped", error=msg)
            return

        ps = state.get("prev_stake")
        acc_losses = state.get("accumulated_losses", 0.0)

        fav, odds = determine_fav_and_odds(client, market)
        if not fav or not odds:
            msg = f"Missing fav/odds for {market.market_name}"
            log_message(msg, "WARN")
            update_schedule_status(job_id, "skipped", error=msg)
            return

        stake = calculate_next_stake(ps, leg, odds, acc_losses, bal)

        # DB: create pending bet row BEFORE placing the bet
        bet_id = create_pending_bet(
            job_id=job_id,
            market=market,
            leg=leg,
            selection=fav.get("runner_name"),
            odds=float(odds),
            stake=float(stake),
        )

        state["is_running_race"] = True
        save_state(state)

        try:
            outcome = place_chase_bet(client, market, fav, stake, float(odds))
        except Exception as e:
            err = f"Error placing bet {market.market_name}: {e}"
            log_message(err, "ERROR")
            update_schedule_status(job_id, "error", error=err)
            state["is_running_race"] = False
            save_state(state)
            return

        win, profit = outcome["win"], float(outcome["profit"])

        if win:
            state.update({
                "accumulated_losses": 0.0,
                "prev_stake": None,
                "chase_active": False,
                "leg": 1,
            })
        else:
            state["accumulated_losses"] = acc_losses + stake
            state["prev_stake"] = stake
            state["leg"] = leg + 1

        bal += profit
        state["balance"] = bal
        state["is_running_race"] = False
        save_state(state)

        # DB: finalize bet + schedule
        finalize_bet(bet_id, result_code=("W" if win else "L"), profit=profit, balance_after=bal)
        update_schedule_status(job_id, "done")

        # Optional CSV
        rec = {
            "timestamp": datetime.now(LONDON).isoformat(),
            "market_id": getattr(market, "market_id", None),
            "race_name": getattr(market, "market_name", None),
            "track": getattr(getattr(market, "event", None), "name", None),
            "date": getattr(market, "market_start_time", None),
            "leg": leg,
            "selection": outcome["selection"],
            "odds": float(odds),
            "stake": float(stake),
            "result": "W" if win else "L",
            "profit": float(profit),
            "balance": float(bal),
        }
        append_result_csv(rec)

        log_message(
            f"Bet {rec['race_name']}: {rec['selection']} | stake {stake} | result {rec['result']} | balance {bal}"
        )

    except Exception as e:
        import traceback
        err = f"Exception in place_bet_job for {getattr(market, 'market_name', 'unknown')}: {e}"
        log_message(err, "ERROR")
        log_message(traceback.format_exc(), "ERROR")
        try:
            update_schedule_status(job_id, "error", error=str(e))
        except Exception:
            pass

# -----------------------------
# Daily 5 AM reset
# -----------------------------

def daily_reset_job():
    log_message("Running daily 5 AM reset")
    save_state({
        "balance": load_initial_balance(),
        "leg": 1,
        "accumulated_losses": 0.0,
        "prev_stake": None,
        "chase_active": False,
        "is_running_race": False,
    })

# -----------------------------
# Scheduling (creates rows in DB)
# -----------------------------

def schedule_races(scheduler):
    client, _ = create_client(load_config())

    mkts = get_today_markets(client, LONDON)
    settings = load_json(STRAT_FILE)
    low_win_list = load_json(LOW_WIN_FILE)
    track_grades = load_json(TRACK_GRADE_FILE)

    daily_reset_job()

    for m in mkts:
        race_start = to_datetime(getattr(m, "market_start_time", None))
        track_name = getattr(getattr(m, "event", None), "name", None)

        if should_skip(m.market_name, track_name, low_win_list, track_grades):
            log_message(f"Skipping {m.market_name}/{track_name}")
            continue

        bet_time = race_start - timedelta(seconds=settings.get("bet_buffer_seconds", BET_BUFFER_DEFAULT))
        if bet_time <= datetime.now(LONDON):
            log_message(f"Race {m.market_name} already passed or too close")
            continue

        job_id = f"{getattr(m, 'market_id', 'm')}-{int(bet_time.timestamp())}"

        # DB row CREATED here for the schedule
        record_schedule(job_id, m, bet_time, status="scheduled")

        try:
            scheduler.add_job(
                place_bet_job,
                "date",
                id=job_id,
                run_date=bet_time,
                args=[client, m, job_id],
                misfire_grace_time=30,
                coalesce=True,
                replace_existing=True,
            )
            log_message(f"Scheduled bet for {m.market_name} at {bet_time} (job_id={job_id})")
        except Exception as e:
            err = f"Failed to schedule {m.market_name} at {bet_time}: {e}"
            log_message(err, "ERROR")
            update_schedule_status(job_id, "error", error=str(e))

    log_message("All races scheduled. Scheduler running...")

# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    from datetime import time as dt_time
    init_db()

    TZ = LONDON
    scheduler = BackgroundScheduler(timezone=TZ)
    scheduler.start()

    scheduler.add_job(
        func=schedule_races,
        trigger=CronTrigger(hour=5, minute=0, timezone=TZ),
        args=[scheduler],
        id="daily_schedule",
        replace_existing=True,
    )

    now_local = datetime.now(TZ)
    if now_local.time() >= dt_time(5, 0):
        log_message("⏱ Past 5:00 AM, scheduling today's races now...")
        schedule_races(scheduler)
    else:
        log_message("⌛ Waiting until 5:00 AM for first schedule...")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log_message("Bot stopped manually")
    finally:
        scheduler.shutdown()
        log_message("Logged out from Betfair API")
