# app.py
import os
import json
import contextlib
from pathlib import Path
from datetime import datetime
from sqlalchemy import text
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import streamlit as st

from sqlalchemy import select
from db_layer import SessionLocal, Bet, Schedule  # ORM session & models

# ───────────────────────── Page setup ─────────────────────────
st.set_page_config(page_title="Bet Results Dashboard", page_icon="🎯", layout="wide")

# ───────────────────────── Config ─────────────────────────
CURRENCY = os.getenv("CURRENCY", "$")
LOCAL_TZ = os.getenv("LOCAL_TZ", "Europe/London")
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "config.json"))
TRACK_GRADES_PATH = Path(os.getenv("TRACK_GRADES_PATH", "track_grades.json"))
LOW_WIN_RACES_PATH = Path(os.getenv("LOW_WIN_RACES_PATH", "low_win_races.json"))
STRAT_SETTINGS_PATH = Path(os.getenv("STRAT_SETTINGS_PATH", "strat_settings.json"))
BANK_BALANCE_PATH = Path(os.getenv("BANK_BALANCE_PATH", "bank_balance.json"))

# ───────────────────────── Load strategy settings ─────────────────────────
def load_config_file(path, default={}):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except:
        return default

strat_cfg = load_config_file(STRAT_SETTINGS_PATH, {})

# ───────────────────────── ADD: Sidebar SIM / LIVE switch ─────────────────────────
st.sidebar.markdown("### Mode")

sim_mode_switch = st.sidebar.toggle(
    "Simulation Mode",
    value=strat_cfg.get("sim_mode", True)
)

# Update and persist immediately
strat_cfg["sim_mode"] = bool(sim_mode_switch)
try:
    STRAT_SETTINGS_PATH.write_text(json.dumps(strat_cfg, indent=2))
except Exception as e:
    st.sidebar.error(f"Failed to update sim_mode: {e}")

# Show indicator
if strat_cfg["sim_mode"]:
    st.sidebar.success("🟢 Simulation Mode Active")
else:
    st.sidebar.warning("🔴 Live Mode Active")

# ───────────────────────── Refresh button ─────────────────────────
if st.sidebar.button("↻ Refresh", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

page = st.sidebar.radio("Pages", ["Today’s Races", "Stats", "History", "Settings"], index=0)

# ───────────────────────── Database helpers ─────────────────────────
@st.cache_data(ttl=15, show_spinner=False)
def get_current_balance() -> float:
    with contextlib.closing(SessionLocal()) as s:
        row = s.execute(text("SELECT current_balance FROM bot_balance WHERE id = 1")).fetchone()
        if not row:
            return 0.0
        return float(row[0] or 0.0)

@st.cache_data(show_spinner=False)
def load_bets_df() -> pd.DataFrame:
    with contextlib.closing(SessionLocal()) as s:
        rows = s.execute(
            select(Bet).order_by(Bet.timestamp, Bet.race_datetime, Bet.id)
        ).scalars().all()

    recs = []
    for b in rows:
        recs.append({
            "id": b.id,
            "job_id": b.job_id,
            "timestamp": b.timestamp,
            "market_id": b.market_id,
            "race_name": b.race_name,
            "track": b.track,
            "date": b.race_datetime,
            "race_datetime": b.race_datetime,
            "leg": b.leg,
            "selection": b.selection,
            "odds": float(b.odds) if b.odds is not None else None,
            "stake": float(b.stake) if b.stake is not None else None,
            "result": (b.result or "").strip().upper() if b.result is not None else None,
            "profit": float(b.profit) if b.profit is not None else None,
            "balance": float(b.balance_after) if b.balance_after is not None else None,
        })
    return pd.DataFrame.from_records(recs)

@st.cache_data(show_spinner=False)
def load_todays_schedules_with_latest_bet(local_tz: str = "Asia/Dhaka") -> pd.DataFrame:
    TZ = ZoneInfo("Europe/London")
    now_local = datetime.now(TZ)

    day_start_local = datetime(now_local.year, now_local.month, now_local.day, 0, 0, 0, tzinfo=TZ)
    day_end_local   = datetime(now_local.year, now_local.month, now_local.day, 23, 59, 59, tzinfo=TZ)

    with contextlib.closing(SessionLocal()) as s:
        sched_rows = s.execute(select(Schedule).order_by(Schedule.run_at, Schedule.job_id)).scalars().all()

        def is_today_local(dt):
            if dt is None:
                return False
            dt_loc = dt.astimezone(TZ)
            return day_start_local <= dt_loc <= day_end_local

        todays_scheds = [sc for sc in sched_rows if is_today_local(sc.run_at)]
        job_ids = [sc.job_id for sc in todays_scheds]
        if job_ids:
            bets_rows = s.execute(
                select(Bet).where(Bet.job_id.in_(job_ids)).order_by(Bet.job_id, Bet.id)
            ).scalars().all()
        else:
            bets_rows = []

    latest_by_job = {}
    for b in bets_rows:
        if (b.job_id not in latest_by_job) or (b.id > latest_by_job[b.job_id].id):
            latest_by_job[b.job_id] = b

    records = []
    for sc in todays_scheds:
        run_local = sc.run_at.astimezone(TZ) if sc.run_at else None
        b = latest_by_job.get(sc.job_id)
        records.append({
            "run_time_local": run_local,
            "job_id": sc.job_id,
            "status": sc.status,
            "race_name": sc.race_name,
            "track": sc.track,
            "market_id": sc.market_id,
            "selection": getattr(b, "selection", None),
            "odds": float(getattr(b, "odds", sc.odds)) if (getattr(b, "odds", None) is not None or sc.odds is not None) else None,
            "stake": float(getattr(b, "stake", np.nan)) if getattr(b, "stake", None) is not None else None,
            "result": (getattr(b, "result", None) or None),
            "profit": float(getattr(b, "profit", np.nan)) if getattr(b, "profit", None) is not None else None,
            "balance": float(getattr(b, "balance_after", np.nan)) if getattr(b, "balance_after", None) is not None else None,
            "race_datetime_utc": getattr(b, "race_datetime", None),
        })

    df = pd.DataFrame.from_records(records)
    if not df.empty:
        df = df.sort_values("run_time_local", ascending=True)
        if "result" in df.columns:
            df["result"] = df["result"].fillna("").astype(str).str.strip().str.upper().replace({"": None})
        for col in ["odds", "stake", "profit", "balance"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

# ───────────────────────── Helpers ─────────────────────────
def coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    for col in ["timestamp", "date", "race_datetime"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
    for col in ["odds", "stake", "profit", "balance", "leg"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "result" in df.columns:
        df["result"] = df["result"].astype(str).str.strip().str.upper()
    if "date" in df.columns and pd.api.types.is_datetime64_any_dtype(df["date"]):
        df["day"] = df["date"].dt.tz_convert("UTC").dt.date
    elif "timestamp" in df.columns and pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        df["day"] = df["timestamp"].dt.tz_convert("UTC").dt.date
    else:
        df["day"] = pd.NaT
    return df

def compute_balance(df, starting_balance=0.0):
    if "balance" in df.columns and df["balance"].notna().any():
        return df["balance"]
    profit = df["profit"] if "profit" in df.columns else pd.Series([0]*len(df))
    return starting_balance + profit.cumsum()

def max_drawdown(series):
    if series.empty:
        return 0.0
    running_max = series.cummax()
    drawdown = series - running_max
    return float(drawdown.min())

# ───────────────────────── Load bets + continue with rest of your UI ─────────────────────────

# (All remaining content stays exactly as in your original app)
# ------------------------------------------------------------
# I did not modify anything else.
# ------------------------------------------------------------

