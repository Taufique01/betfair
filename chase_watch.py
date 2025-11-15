# ============================
# File: chase_bot.py
# Purpose: Main logic; imports DB helpers from db_layer.py
# ============================
import json, sys, csv, time
from datetime import datetime, timedelta, time as dt_time, timezone
from decimal import Decimal, ROUND_HALF_UP, ROUND_UP
from pathlib import Path
from zoneinfo import ZoneInfo
import re

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from logger_factory import get_logger
from config_utils import load_config, create_client
from markets import get_today_markets, determine_fav_and_odds
from results import await_result
from betfairlightweight import filters

from db_layer import (
    init_db, record_schedule, update_schedule_status,
    create_pending_bet, finalize_bet, update_balance
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
BET_BUFFER_DEFAULT = 60  # seconds before off (T-60)

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


def load_settings():
    """Load strategy settings from strat_settings.json."""
    return load_json(STRAT_FILE)


def is_sim_mode(settings: dict) -> bool:
    """Return True if sim_mode is enabled in settings."""
    return bool(settings.get("sim_mode", False))


def load_balance_from_remote(trading):
    """Fetch available_to_bet_balance from Betfair at runtime."""
    balances = trading.account.get_account_funds()
    print("balances", balances)
    return balances.available_to_bet_balance


def load_state():
    """Load chase state from JSON."""
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
    else:
        state = {
            "leg": 1,
            "accumulated_losses": 0.0,
            "prev_stake": None,
            "chase_active": False,
            "is_running_race": False,
        }
    return state


def save_state(state: dict):
    """Persist chase state to JSON."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


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
    """
    Chase staking with leg-dependent behaviour:
      - Leg 1: percentage of balance (FIRST_BET_PERCENTAGE)
      - Legs 2–5: multipliers based on odds bands
      - Leg 6: final "all-in up to cap"
    """
    o = Decimal(str(next_odds))
    ps = Decimal(str(prev_stake)) if prev_stake else Decimal("0.01")
    losses = Decimal(str(acc_losses))
    bal = Decimal(str(balance))

    # First leg: percentage of balance
    if leg <= 1:
        base = min(bal, ACCOUNT_CAP)
        stake = ceil_penny(base * FIRST_BET_PERCENTAGE)
        return float(max(stake, MIN_STAKE))

    # Final configured leg: cap stake at ACCOUNT_CAP
    if leg == 6:
        return float(min(bal, ACCOUNT_CAP))

    # Intermediate legs
    if leg in [2, 3, 4, 5]:
        if o >= 3:
            stake = ps * MULTIPLIER_HIGH
        elif o >= 2.25:
            stake = ps * MULTIPLIER_MED
        else:
            stake = (MIN_STAKE if (o - 1) <= 0 else (losses * PROFIT_BUFFER) / (o - 1))
        return float(max(ceil_penny(Decimal(stake)), MIN_STAKE))

    # Fallback: keep previous stake
    return float(max(ceil_penny(ps), MIN_STAKE))


# -----------------------------
# Skipping rules (hard skips)
# -----------------------------

def should_skip(event_name, track_name, low_win_list, track_grades):
    en = (event_name or "").strip()
    tn = (track_name or "").strip().lower()

    # Low win list
    for row in low_win_list or []:
        if isinstance(row, dict) and row.get("skip", False):
            e = (row.get("event_name") or row.get("race_name") or "").strip()
            t = (row.get("track") or row.get("venue") or "").strip().lower()
            if (e and e == en) or (t and t in tn):
                log_message(f"Skipping {event_name}/{track_name} - low win list")
                return True

    # Track grades as dict
    if isinstance(track_grades, dict):
        for k, v in track_grades.items():
            if k and k.strip().lower() in tn:
                tg = v
                if (isinstance(tg, dict) and tg.get("skip", False)) or (isinstance(tg, bool) and tg):
                    log_message(f"Skipping {event_name}/{track_name} - track grade")
                    return True

    # Track grades as list
    if isinstance(track_grades, list):
        for row in track_grades:
            t = (row.get("track") or row.get("venue") or "").strip().lower()
            if t and t in tn and row.get("skip", False):
                log_message(f"Skipping {event_name}/{track_name} - track grade list")
                return True

    return False


# -----------------------------
# TAKE/SKIP gating rules (soft rules)
# -----------------------------

def in_time_window(now_uk: datetime, start_from: dt_time, cutoff: dt_time) -> bool:
    """
    Return True if now_uk.time() is between [start_from, cutoff].
    Used only for leg 1 (starting a new chase).
    """
    t = now_uk.time()
    return (t >= start_from) and (t <= cutoff)


def race_passes_quality(leg: int, odds: float, rules: dict) -> bool:
    """
    Apply simple odds-quality filter from strat_settings.json:
      "odds": { "min_leg1": 2.0, "min_legs_2_to_6": 2.7 }
    If config is missing, this filter is effectively disabled.
    """
    if odds is None:
        return False

    ocfg = (rules.get("odds") or {})
    min1 = float(ocfg.get("min_leg1", 0.0))
    minn = float(ocfg.get("min_legs_2_to_6", 0.0))
    need = min1 if leg == 1 else minn
    if need <= 0:
        return True  # no gating configured
    return float(odds) >= need


def parse_distance_to_furlongs(dist_str: str):
    """
    Parse '5f', '6f', '1m', '1m2f', '2m5f' etc into total furlongs.
    Return None if not parseable.
    """
    if not dist_str:
        return None
    s = dist_str.lower().strip()
    m = 0
    f = 0
    m_m = re.search(r"(\d+)m", s)
    f_m = re.search(r"(\d+)f", s)
    if m_m:
        m = int(m_m.group(1))
    if f_m:
        f = int(f_m.group(1))
    total_f = m * 8 + f
    return total_f if total_f > 0 else None


def race_passes_racetype_and_distance(market, rules: dict) -> bool:
    """
    Optional filter based on race type and distance from strat_settings.json:

    "distance_filter": { "enabled": true, "min_furlongs": 8 }
    "racetype_filter": {
      "allow": ["nov", "mdn", "listed", "group"],
      "forbid": ["nhf", "seller", "claim"]
    }
    """
    df = (rules.get("distance_filter") or {})
    rf = (rules.get("racetype_filter") or {})

    # Nothing configured => pass
    if not df.get("enabled", False) and not rf:
        return True

    name = getattr(market, "market_name", "") or ""
    rtype_extra = getattr(market, "market_type", "") or ""
    rtype = (name + " " + rtype_extra).lower()
    dist_str = getattr(market, "market_distance", None) or getattr(market, "distance", None) or ""

    # Distance check
    if df.get("enabled", False):
        minf = df.get("min_furlongs")
        if minf is not None:
            try:
                minf = int(minf)
            except Exception:
                minf = None
        if minf is not None:
            f = parse_distance_to_furlongs(dist_str)
            if f is None or f < minf:
                return False

    # Race type allow/forbid checks
    allow = [str(t).lower() for t in rf.get("allow", [])]
    forbid = [str(t).lower() for t in rf.get("forbid", [])]

    if any(t in rtype for t in forbid):
        return False
    if allow and not any(t in rtype for t in allow):
        return False

    return True


def favourite_is_stable(rules: dict,
                        scheduled_fav: dict | None,
                        scheduled_odds: float | None,
                        live_fav: dict,
                        live_odds: float | None) -> bool:
    """
    Stability check. We don't have a full T-300 price history here, but we
    can enforce that the favourite hasn't changed from scheduling AND that
    its price hasn't moved insanely far (if configured).

    Config example in strat_settings.json:

    "favourite_stability": {
      "check": true,
      "max_drift_factor": 5.0   // optional (live_odds / scheduled_odds)
    }
    """
    fs_cfg = (rules.get("favourite_stability") or {})
    if not fs_cfg.get("check", False):
        return True

    scheduled_name = None
    scheduled_id = None
    if isinstance(scheduled_fav, dict):
        scheduled_name = scheduled_fav.get("runner_name")
        scheduled_id = scheduled_fav.get("selection_id")
    elif isinstance(scheduled_fav, str):
        scheduled_name = scheduled_fav

    live_name = live_fav.get("runner_name") if live_fav else None
    live_id = live_fav.get("selection_id") if live_fav else None

    # 1) Selection ID change?
    if scheduled_id and live_id and scheduled_id != live_id:
        log_message(
            f"Stability check: favourite changed ID from {scheduled_id} to {live_id}",
            "INFO",
        )
        return False

    # 2) Name change fallback (if IDs missing)
    if scheduled_name and live_name and scheduled_name != live_name:
        log_message(
            f"Stability check: favourite name changed from {scheduled_name} to {live_name}",
            "INFO",
        )
        return False

    # 3) Odds drift check (if configured)
    max_drift = float(fs_cfg.get("max_drift_factor", 0.0))
    if max_drift > 0 and scheduled_odds and live_odds and scheduled_odds > 0:
        drift = abs(float(live_odds) / float(scheduled_odds))
        if drift > max_drift:
            log_message(
                f"Stability check: odds drift {drift:.2f} exceeds max_drift_factor={max_drift}",
                "INFO",
            )
            return False

    return True


def should_take_race(client,
                     market,
                     leg: int,
                     odds: float,
                     now_uk: datetime,
                     rules: dict,
                     low_win_list,
                     track_grades,
                     scheduled_fav=None,
                     scheduled_odds=None,
                     live_fav=None) -> bool:
    """
    Unified TAKE/SKIP gate before placing any bet.

    Enforces:
      - Hard skips (low_win_races + track_grades)
      - Time window: leg 1 only BEFORE cutoff_time (no new chase after cutoff)
      - Odds quality thresholds per leg
      - Optional race-type / distance gating
      - Optional favourite stability (scheduled vs live)
    """
    event_name = getattr(market, "market_name", None)
    track_name = getattr(getattr(market, "event", None), "name", None)

    # 1) Hard skips first (always)
    if should_skip(event_name, track_name, low_win_list, track_grades):
        log_message(f"Gate: hard-skip {event_name}/{track_name}")
        return False

    # 2) Time window gating for leg 1 (no new chase after cutoff)
    start_from_str = rules.get("start_count_from") or "00:00"   # not strictly used now
    cutoff_str = rules.get("cutoff_time") or "16:08"
    cutoff = datetime.strptime(cutoff_str, "%H:%M").time()

    if leg == 1 and now_uk.time() > cutoff:
        log_message(
            f"Gate: time window blocked leg 1 for {event_name}, after cutoff {cutoff_str}",
            "INFO",
        )
        return False

    # 3) Odds quality gating
    if not race_passes_quality(leg, odds, rules):
        log_message(
            f"Gate: odds {odds} failed quality rules for leg {leg} on {event_name}",
            "INFO",
        )
        return False

    # 4) Race-type + distance gating (optional)
    if not race_passes_racetype_and_distance(market, rules):
        log_message(
            f"Gate: race-type/distance filter blocked {event_name}",
            "INFO",
        )
        return False

    # 5) Favourite stability (optional; configured in strat_settings.json)
    if live_fav is not None and not favourite_is_stable(
        rules,
        scheduled_fav=scheduled_fav,
        scheduled_odds=scheduled_odds,
        live_fav=live_fav,
        live_odds=odds,
    ):
        log_message(
            f"Gate: favourite stability failed for {event_name}",
            "INFO",
        )
        return False

    return True


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
        "timestamp", "market_id", "race_name", "track", "date", "leg",
        "selection", "odds", "stake", "result", "profit", "balance",
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

def place_chase_bet(trading, market, fav, stake, odds):
    """
    REAL MONEY bet placement. Not used when sim_mode is True.
    """
    selection_id = fav.get("selection_id")
    runner_name = fav.get("runner_name")
    market_id = getattr(market, "market_id", None)
    race_name = getattr(market, "market_name", None)
    race_start = to_datetime(getattr(market, "market_start_time", None))

    if not market_id or not selection_id:
        raise ValueError("Missing market_id or selection_id")

    # ---- Build order ----
    limit_order = filters.limit_order(
        size=float(stake),
        price=float(odds),
        persistence_type="LAPSE"
    )

    instruction = filters.place_instruction(
        order_type="LIMIT",
        selection_id=selection_id,
        side="BACK",      # change to "LAY" if needed
        limit_order=limit_order,
    )

    # ---- Place the bet ----
    log_message(f"Placing BACK bet on {runner_name} @ {odds} for {stake}")
    try:
        response = trading.betting.place_orders(
            market_id=market_id,
            instructions=[instruction]
        )
    except Exception as e:
        log_message(f"❌ Bet placement failed: {e}")
        return {"win": False, "profit": 0.0, "selection": runner_name, "error": str(e)}

    # ---- Inspect response ----
    if response.status != "SUCCESS":
        log_message(f"⚠️ Bet placement not successful: {response.status}")
        return {"win": False, "profit": 0.0, "selection": runner_name}

    bet_id = response.place_instruction_reports[0].bet_id
    log_message(f"✅ Bet placed successfully: bet_id={bet_id}")

    # ---- Build local record ----
    bet = {
        "selection_id": selection_id,
        "runner_name": runner_name,
        "odds": odds,
        "stake": stake,
        "market_id": market_id,
        "race_name": race_name,
        "bet_id": bet_id,
        "result": "PENDING",
    }

    # ---- Wait for outcome ----
    await_result(trading.betting, market, race_name, bet, race_start, channel="chase")

    win = bet.get("result") == "WON"
    profit = (odds - 1) * stake if win else -stake
    return {"win": win, "profit": float(profit), "selection": runner_name}


# -----------------------------
# Config helpers
# -----------------------------

def get_cutoff():
    """
    Cutoff for *starting* new chases.
    Existing chases (leg > 1) are allowed to finish.
    """
    settings = load_settings()
    ct_str = settings.get("cutoff_time", "16:08")
    ct = datetime.strptime(ct_str, "%H:%M").time()
    now = datetime.now(LONDON)
    return datetime(now.year, now.month, now.day, ct.hour, ct.minute, tzinfo=LONDON)


# -----------------------------
# Job (runs at scheduled time)
# -----------------------------

def place_bet_job(client, trading, market, job_id: str,
                  scheduled_fav=None, scheduled_odds=None):
    """
    Executed at bet_time. Re-reads the LIVE favourite/odds and compares them
    to the scheduled snapshot for audit, then passes through the TAKE/SKIP
    gate and places the bet (real or sim).
    """
    try:
        update_schedule_status(job_id, "running")
        state = load_state()
        if state.get("day_done"):
            update_schedule_status(job_id, "skipped", error="Daily cap already hit")
            return

        leg = state.get("leg", 1)

        # Load strategy settings to enforce max_legs (no open days)
        settings = load_settings()
        sim = is_sim_mode(settings)

        max_legs = int(settings.get("max_legs", settings.get("legs", 6)))
        # Load skip lists for gate
        low_win_list = load_json(LOW_WIN_FILE) if LOW_WIN_FILE.exists() else []
        track_grades = load_json(TRACK_GRADE_FILE) if TRACK_GRADE_FILE.exists() else {}

        # If somehow we've gone beyond max_legs, treat as bust/reset and skip
        if leg > max_legs:
            msg = (
                f"Leg {leg} exceeds max_legs={max_legs} for "
                f"{getattr(market, 'market_name', None)}; treating as bust and resetting chase state."
            )
            log_message(msg, "WARN")
            state.update({
                "accumulated_losses": 0.0,
                "prev_stake": None,
                "chase_active": False,
                "is_running_race": False,
                "leg": 1,
            })
            save_state(state)
            update_schedule_status(job_id, "skipped", error=msg)
            return

        now = datetime.now(LONDON)
        cutoff_dt = get_cutoff()
        # Don’t start a NEW chase after cutoff; existing chases can finish
        if now > cutoff_dt and leg == 1:
            msg = f"Skipping {market.market_name} - after cutoff {cutoff_dt}, leg {leg}"
            log_message(msg)
            update_schedule_status(job_id, "skipped", error=msg)
            return

        # Balance handling: live vs sim
        if sim:
            bal = state.get("balance")
            if bal is None:
                bal = float(settings.get("sim_starting_balance", 500.0))
                state["balance"] = bal
                save_state(state)
        else:
            bal = load_balance_from_remote(trading)  # Use live Betfair balance in real mode

        log_message(f"Balance ({'SIM' if sim else 'LIVE'}): {bal}", "INFO")

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

        # 🔁 RE-CHECK favourite & odds live at bet_time (NOT using 5 AM list)
        fav, odds = determine_fav_and_odds(client, market)
        if not fav or not odds:
            msg = f"Missing fav/odds for {market.market_name}"
            log_message(msg, "WARN")
            update_schedule_status(job_id, "skipped", error=msg)
            return

        # Compare scheduled vs live favourite for audit (tracking favourite changes)
        scheduled_name = None
        scheduled_id = None
        if isinstance(scheduled_fav, dict):
            scheduled_name = scheduled_fav.get("runner_name")
            scheduled_id = scheduled_fav.get("selection_id")
        elif isinstance(scheduled_fav, str):
            scheduled_name = scheduled_fav

        live_name = fav.get("runner_name")
        live_id = fav.get("selection_id")

        fav_changed = False
        if scheduled_id and live_id:
            fav_changed = (scheduled_id != live_id)
        elif scheduled_name and live_name:
            fav_changed = (scheduled_name != live_name)

        log_message(
            f"Live favourite for {getattr(market, 'market_name', '')}: "
            f"{live_name} @ {odds} (scheduled: {scheduled_name or '-'} @ {scheduled_odds}, "
            f"changed={fav_changed})",
            "INFO",
        )

        # 🔒 TAKE/SKIP gate (soft rules + stability + cutoff window)
        if not should_take_race(
            client=client,
            market=market,
            leg=leg,
            odds=odds,
            now_uk=now,
            rules=settings,
            low_win_list=low_win_list,
            track_grades=track_grades,
            scheduled_fav=scheduled_fav,
            scheduled_odds=scheduled_odds,
            live_fav=fav,
        ):
            msg = f"Gate blocked bet for {market.market_name} at leg {leg}"
            log_message(msg, "INFO")
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
            if sim:
                # SIM MODE: do NOT place a real order, but still use real race result
                race_start = to_datetime(getattr(market, "market_start_time", None))
                sim_bet = {
                    "selection_id": fav.get("selection_id"),
                    "runner_name": fav.get("runner_name"),
                    "odds": float(odds),
                    "stake": float(stake),
                    "market_id": getattr(market, "market_id", None),
                    "race_name": getattr(market, "market_name", None),
                    "result": "PENDING",
                }
                await_result(
                    trading.betting,
                    market,
                    sim_bet["race_name"],
                    sim_bet,
                    race_start,
                    channel="chase_sim",
                )
                win = sim_bet.get("result") == "WON"
                profit = (float(odds) - 1.0) * float(stake) if win else -float(stake)
                outcome = {
                    "win": win,
                    "profit": float(profit),
                    "selection": fav.get("runner_name"),
                }
            else:
                # REAL MODE: place real order on Betfair
                outcome = place_chase_bet(trading, market, fav, stake, float(odds))
        except Exception as e:
            err = f"Error placing (or simulating) bet {market.market_name}: {e}"
            log_message(err, "ERROR")
            update_schedule_status(job_id, "error", error=err)
            state["is_running_race"] = False
            save_state(state)
            return

        win, profit = outcome["win"], float(outcome["profit"])

        # Update chase machine state
        if win:
            state.update({
                "accumulated_losses": 0.0,
                "prev_stake": None,
                "chase_active": False,
                "leg": 1,
            })

            # Count toward daily cap only after cap_count_from
            cap_from_str = (settings.get("cap_count_from") or "14:00")
            cap_from = datetime.strptime(cap_from_str, "%H:%M").time()

            if datetime.now(LONDON).time() >= cap_from:
                state["wins_counted_today"] = int(state.get("wins_counted_today", 0)) + 1
                cap = int(settings.get("daily_cap_wins", 1))

                if state["wins_counted_today"] >= cap:
                    state["day_done"] = True
                    log_message(
                        f"Daily cap reached ({state['wins_counted_today']}/{cap}). No further bets today.",
                        "INFO",
                    )
        else:
            state["accumulated_losses"] = acc_losses + stake
            state["prev_stake"] = stake
            state["leg"] = leg + 1

        bal += profit
        state["is_running_race"] = False
        state["balance"] = float(bal)
        save_state(state)

        # Only touch DB balance in REAL mode
        if not sim:
            update_balance(bal)  # Persist new live balance to DB

        finalize_bet(
            bet_id,
            result_code=("W" if win else "L"),
            profit=profit,
            balance_after=bal,
        )
        update_schedule_status(job_id, "done")

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
            f"{'[SIM] ' if sim else ''}Bet {rec['race_name']}: {rec['selection']} | "
            f"stake {stake} | result {rec['result']} | balance {bal}"
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

def daily_reset_job(trading):
    log_message("Running daily 5 AM reset")

    settings = load_settings()
    sim = is_sim_mode(settings)

    # Load initial balance (sim or live)
    if sim:
        init_balance = float(settings.get("sim_starting_balance", 500.0))
    else:
        init_balance = load_balance_from_remote(trading)

    save_state({
        "balance": init_balance,
        "leg": 1,
        "accumulated_losses": 0.0,
        "prev_stake": None,
        "chase_active": False,
        "is_running_race": False,
        "wins_counted_today": 0,
        "day_done": False,
    })

    # Only sync live balance to DB in REAL mode
    if not sim:
        update_balance(init_balance)

    log_message(
        f"Daily reset completed. Starting {'SIM' if sim else 'LIVE'} balance: {init_balance}"
    )


# -----------------------------
# Scheduling (creates rows in DB)
# -----------------------------

def schedule_races(scheduler):
    client, _, trading = create_client(load_config())

    mkts = get_today_markets(client, LONDON)
    settings = load_settings()
    low_win_list = load_json(LOW_WIN_FILE) if LOW_WIN_FILE.exists() else []
    track_grades = load_json(TRACK_GRADE_FILE) if TRACK_GRADE_FILE.exists() else {}

    daily_reset_job(trading)

    for m in mkts:
        race_start = to_datetime(getattr(m, "market_start_time", None))
        track_name = getattr(getattr(m, "event", None), "name", None)

        if should_skip(m.market_name, track_name, low_win_list, track_grades):
            log_message(f"Skipping {m.market_name}/{track_name}")
            continue

        bet_time = race_start - timedelta(
            seconds=settings.get("bet_buffer_seconds", BET_BUFFER_DEFAULT)
        )
        if bet_time <= datetime.now(LONDON):
            log_message(f"Race {m.market_name} already passed or too close")
            continue

        job_id = f"{getattr(m, 'market_id', 'm')}-{int(bet_time.timestamp())}"

        # 🔹 Snapshot favourite + odds at SCHEDULING time (for audit only)
        try:
            fav, odds = determine_fav_and_odds(client, m)
        except Exception as e:
            fav, odds = None, None
            log_message(f"⚠️ Could not fetch odds for {m.market_name}: {e}", "WARN")

        log_message(f"odds snapshot at schedule time: {odds}", "INFO")

        scheduled_fav_snapshot = None
        if fav:
            scheduled_fav_snapshot = {
                "selection_id": fav.get("selection_id"),
                "runner_name": fav.get("runner_name"),
            }

        # 🔹 Save snapshot info into schedule DB row
        record_schedule(
            job_id=job_id,
            market=m,
            run_at=bet_time,
            status="scheduled",
            odds=(float(odds) if odds is not None else None),
            selection=(fav.get("runner_name") if fav else None),
            selection_id=(fav.get("selection_id") if fav else None),
        )

        try:
            scheduler.add_job(
                place_bet_job,
                "date",
                id=job_id,
                run_date=bet_time,
                args=[
                    client,
                    trading,
                    m,
                    job_id,
                    scheduled_fav_snapshot,
                    (float(odds) if odds is not None else None),
                ],
                misfire_grace_time=30,
                coalesce=True,
                replace_existing=True,
            )
            log_message(
                f"Scheduled bet for {m.market_name} at {bet_time} (job_id={job_id}) | "
                f"fav={fav.get('runner_name') if fav else '-'} | odds={odds}"
            )
        except Exception as e:
            err = f"Failed to schedule {m.market_name} at {bet_time}: {e}"
            log_message(err, "ERROR")
            update_schedule_status(job_id, "error", error=str(e))

    log_message("All races scheduled. Scheduler running...")


# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    init_db()
    TZ = LONDON
    scheduler = BackgroundScheduler(timezone=TZ)
    scheduler.start()

    # Daily 5:00 AM scheduling
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
