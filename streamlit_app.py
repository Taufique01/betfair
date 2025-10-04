# app.py
import os
import contextlib
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import streamlit as st

from sqlalchemy import select
from db_layer import SessionLocal, Bet, Schedule  # ← your ORM session & models

"""
Bet Results Dashboard (Streamlit) — ORM (SQLAlchemy) version

- Reads from DB (tables: bets, schedules) using SQLAlchemy ORM
- KPIs, filters, downloadable filtered CSV
- "Today's Summary (till now)" in your local timezone
- "Day-wise Stats" with running balance (ALL days)
- "Trades" table (last 20)
- "Today's Races" split into Upcoming / Completed with bet results when available
"""

# ───────────────────────── Page setup ─────────────────────────
st.set_page_config(page_title="Bet Results Dashboard", page_icon="🎯", layout="wide")

# ───────────────────────── Config ─────────────────────────
CURRENCY = os.getenv("CURRENCY", "$")
LOCAL_TZ = os.getenv("LOCAL_TZ", "Asia/Dhaka")

# ───────────────────────── ORM → DataFrame ─────────────────────────
@st.cache_data(show_spinner=False)
def load_bets_df() -> pd.DataFrame:
    """
    ORM-based load from bets table.
    Returns columns the UI expects:
    id, timestamp, market_id, race_name, track, date (alias of race_datetime),
    leg, selection, odds, stake, result, profit, balance (alias of balance_after)
    """
    with contextlib.closing(SessionLocal()) as s:
        rows = s.execute(
            select(Bet).order_by(Bet.timestamp, Bet.race_datetime, Bet.id)
        ).scalars().all()

    recs = []
    for b in rows:
        recs.append({
            "id": b.id,
            "timestamp": b.timestamp,                       # tz-aware datetime (from model)
            "market_id": b.market_id,
            "race_name": b.race_name,
            "track": b.track,
            "date": b.race_datetime,                        # rename for UI compatibility
            "leg": b.leg,
            "selection": b.selection,
            "odds": float(b.odds) if b.odds is not None else None,
            "stake": float(b.stake) if b.stake is not None else None,
            "result": (b.result or "").strip().upper() if b.result is not None else None,  # 'P' | 'W' | 'L'
            "profit": float(b.profit) if b.profit is not None else None,
            "balance": float(b.balance_after) if b.balance_after is not None else None,
        })
    return pd.DataFrame.from_records(recs)

def load_todays_races_df(local_tz: str = "Asia/Dhaka") -> pd.DataFrame:
    """
    Build a table of today's scheduled races (from `schedules`) and join the latest bet (from `bets`),
    showing status and results if completed.
    """
    TZ = ZoneInfo(local_tz)
    now_local = datetime.now(TZ)

    # Local day bounds
    day_start_local = datetime(now_local.year, now_local.month, now_local.day, 0, 0, 0, tzinfo=TZ)
    day_end_local   = datetime(now_local.year, now_local.month, now_local.day, 23, 59, 59, tzinfo=TZ)

    with contextlib.closing(SessionLocal()) as s:
        sched_rows = s.execute(
            select(Schedule).order_by(Schedule.run_at, Schedule.job_id)
        ).scalars().all()

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

    # Latest bet per job_id
    latest_by_job = {}
    for b in bets_rows:
        if (b.job_id not in latest_by_job) or (b.id > latest_by_job[b.job_id].id):
            latest_by_job[b.job_id] = b

    records = []
    for sc in todays_scheds:
        run_local = sc.run_at.astimezone(TZ) if sc.run_at else None
        b = latest_by_job.get(sc.job_id)
        records.append({
            "run_time_local": run_local,               # local display
            "job_id": sc.job_id,
            "status": sc.status,                       # scheduled|running|done|skipped|error
            "race_name": sc.race_name,
            "track": sc.track,
            "market_id": sc.market_id,

            # Bet details (if any)
            "selection": getattr(b, "selection", None),
            "odds": float(getattr(b, "odds", np.nan)) if getattr(b, "odds", None) is not None else None,
            "stake": float(getattr(b, "stake", np.nan)) if getattr(b, "stake", None) is not None else None,
            "result": getattr(b, "result", None),      # 'P' | 'W' | 'L' or None
            "profit": float(getattr(b, "profit", np.nan)) if getattr(b, "profit", None) is not None else None,
            "balance": float(getattr(b, "balance_after", np.nan)) if getattr(b, "balance_after", None) is not None else None,

            # Race datetime (UTC in DB) — useful for ordering/fallbacks
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
    # Parse datetimes
    for col in ["timestamp", "date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")

    # Numeric columns
    for col in ["odds", "stake", "profit", "balance", "leg"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Result normalization
    if "result" in df.columns:
        df["result"] = df["result"].astype(str).str.strip().str.upper()

    # Derive UTC day for global filters
    if "date" in df.columns and pd.api.types.is_datetime64_any_dtype(df["date"]):
        df["day"] = (df["date"].dt.tz_convert("UTC") if pd.api.types.is_datetime64tz_dtype(df["date"]) else df["date"]).dt.date
    elif "timestamp" in df.columns and pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        df["day"] = (df["timestamp"].dt.tz_convert("UTC") if pd.api.types.is_datetime64tz_dtype(df["timestamp"]) else df["timestamp"]).dt.date
    else:
        df["day"] = pd.NaT
    return df

def compute_balance(df: pd.DataFrame, starting_balance: float = 0.0) -> pd.Series:
    """
    Prefer the balance column (balance_after) if present; else compute from profit.
    """
    if "balance" in df.columns and df["balance"].notna().any():
        return df["balance"]
    profit = df["profit"] if "profit" in df.columns else pd.Series([0]*len(df), index=df.index, dtype=float)
    return starting_balance + profit.cumsum()

def max_drawdown(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    running_max = series.cummax()
    drawdown = series - running_max
    return float(drawdown.min()) if len(series) else 0.0  # negative number

# ───────────────────────── Sidebar ─────────────────────────
st.sidebar.header("⚙️ Settings")

# Manual refresh button (clear cached queries)
if st.sidebar.button("↻ Refresh now", use_container_width=True):
    st.cache_data.clear()
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()

# ───────────────────────── Load + prepare data ─────────────────────────
try:
    df_raw = load_bets_df()
    if df_raw.empty:
        st.sidebar.warning("No rows in `bets` table yet.")
except Exception as e:
    st.sidebar.error(f"DB error: {e}")
    st.stop()

df = coerce_types(df_raw.copy())

# Starting balance (if not in table, let user provide)
starting_balance_default = 0.0
if "balance" in df.columns and df["balance"].notna().any():
    try:
        starting_balance_default = float(df["balance"].dropna().iloc[0])
    except Exception:
        starting_balance_default = 0.0

starting_balance = st.sidebar.number_input(
    "Starting balance (only used if no 'balance' column)",
    value=starting_balance_default,
    step=10.0
)

# Include Pending (P) bets?
include_pending = st.sidebar.checkbox("Include pending bets (P)", value=False)
if not include_pending and "result" in df.columns:
    df = df[df["result"].isin(["W", "L"])]

# Filters
st.sidebar.subheader("Filters")

# Date range (UTC-based)
if "day" in df.columns and df["day"].notna().any():
    min_day = df["day"].min()
    max_day = df["day"].max()
    date_range = st.sidebar.date_input("Date range", value=(min_day, max_day))
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_day, end_day = date_range
        df = df[(df["day"] >= start_day) & (df["day"] <= end_day)]

# Race Name filter (optional)
if "race_name" in df.columns:
    race_names = sorted(df["race_name"].dropna().unique().tolist())
    selected_races = st.sidebar.multiselect("Race Name (optional)", race_names, default=[])
    if selected_races:
        df = df[df["race_name"].isin(selected_races)]

# Result filter (W/L/P)
if "result" in df.columns:
    results = sorted([r for r in df["result"].dropna().unique().tolist()])
    default_results = results if include_pending else [r for r in results if r in ("W", "L")]
    selected_results = st.sidebar.multiselect("Result", results, default=default_results)
    if selected_results:
        df = df[df["result"].isin(selected_results)]

# Odds range
if "odds" in df.columns and df["odds"].notna().any():
    min_odds = float(df["odds"].min())
    max_odds = float(df["odds"].max())
    odds_min, odds_max = st.sidebar.slider(
        "Odds range",
        min_value=float(round(min_odds, 2)),
        max_value=float(round(max_odds + 0.01, 2)),
        value=(float(round(min_odds, 2)), float(round(max_odds, 2)))
    )
    df = df[(df["odds"] >= odds_min) & (df["odds"] <= odds_max)]

# Text search
search_text = st.sidebar.text_input("Search (selection / race / track)").strip().lower()
if search_text:
    mask = pd.Series([False] * len(df), index=df.index)
    for col in ["selection", "race_name", "track"]:
        if col in df.columns:
            mask |= df[col].astype(str).str.lower().str.contains(search_text, na=False)
    df = df[mask]

# ───────────────────────── KPIs ─────────────────────────
balance_series = compute_balance(df, starting_balance=starting_balance)

wins = int((df["result"] == "W").sum()) if "result" in df.columns else 0
losses = int((df["result"] == "L").sum()) if "result" in df.columns else 0
pendings = int((df["result"] == "P").sum()) if "result" in df.columns else 0
total_bets = int(len(df))
total_stake = float(df["stake"].sum()) if "stake" in df.columns else 0.0
total_profit = float(df["profit"].sum()) if "profit" in df.columns else (
    float(balance_series.iloc[-1] - balance_series.iloc[0]) if len(balance_series) > 1 else 0.0
)
win_rate = (wins / total_bets * 100.0) if total_bets else 0.0
roi = (total_profit / total_stake * 100.0) if total_stake else 0.0
dd = max_drawdown(balance_series)  # negative number

# Pick a datetime column for time-based UI
time_col = "timestamp" if "timestamp" in df.columns else ("date" if "date" in df.columns else None)

# ## --- TODAY'S SUMMARY (local timezone) ---
TZ = ZoneInfo(LOCAL_TZ)
now_local = datetime.now(TZ)

today_metrics = None
today_remaining = 0
if time_col is not None and pd.api.types.is_datetime64_any_dtype(df[time_col]):
    if pd.api.types.is_datetime64tz_dtype(df[time_col]):
        dt_local = df[time_col].dt.tz_convert(TZ)
    else:
        dt_local = df[time_col].dt.tz_localize(TZ)

    df = df.assign(local_day=dt_local.dt.date)
    today = now_local.date()
    mask_today_so_far = (df["local_day"] == today) & (dt_local <= now_local)
    mask_today_later  = (df["local_day"] == today) & (dt_local > now_local)

    df_today = df.loc[mask_today_so_far].copy()
    today_remaining = int(mask_today_later.sum())

    if not df_today.empty:
        t_bets   = int(len(df_today))
        t_wins   = int((df_today["result"] == "W").sum()) if "result" in df_today.columns else 0
        t_losses = int((df_today["result"] == "L").sum()) if "result" in df_today.columns else 0
        t_stake  = float(df_today["stake"].sum()) if "stake" in df_today.columns else 0.0
        t_profit = float(df_today["profit"].sum()) if "profit" in df_today.columns else 0.0
        t_win_rt = (t_wins / t_bets * 100.0) if t_bets else 0.0
        t_roi    = (t_profit / t_stake * 100.0) if t_stake else 0.0
        today_metrics = (t_bets, t_wins, t_losses, t_stake, t_profit, t_win_rt, t_roi)

# ───────────────────────── UI ─────────────────────────
st.markdown(
    """
    <style>
      .metric-card {
        border: 1px solid rgba(140,140,160,.35);
        padding: 16px;
        border-radius: 16px;
        background: var(--secondary-background-color);
        color: var(--text-color);
      }
      .muted {
        color: var(--text-color);
        opacity: .75;
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: .06em;
      }
      .value {
        font-size: 28px;
        font-weight: 700;
        margin-top: 4px;
        color: #10b981; /* emerald-500 for visibility in dark theme */
      }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🎯 Bet Results Dashboard")
st.caption("Data source: database tables **bets** and **schedules** (via SQLAlchemy ORM). Click **↻ Refresh now** to clear cache and requery DB.")

# KPI row
cols = st.columns(7)
labels = [
    ("Total Bets", total_bets),
    ("Wins", wins),
    ("Losses", losses),
    ("Pending", pendings if include_pending else 0),
    ("Total Stake", f"{CURRENCY}{total_stake:.2f}"),
    ("P / L", f"{CURRENCY}{total_profit:+.2f}"),
    ("Max Drawdown", f"{CURRENCY}{dd:.2f}"),
]
for (label, value), c in zip(labels, cols):
    c.markdown(f'<div class="metric-card"><div class="muted">{label}</div><div class="value">{value}</div></div>', unsafe_allow_html=True)

# ───────────────────────── Today's Races (Upcoming / Completed) ─────────────────────────
st.markdown("---")
st.subheader(f"Today's Races · {LOCAL_TZ}")

df_races = load_todays_races_df(LOCAL_TZ)

if df_races.empty:
    st.info("No schedules found for today.")
else:
    now_local = datetime.now(ZoneInfo(LOCAL_TZ))
    upcoming_mask = df_races["run_time_local"] >= now_local
    df_upcoming = df_races.loc[upcoming_mask].copy()
    df_completed = df_races.loc[~upcoming_mask].copy()

    cols_common = ["run_time_local", "track", "race_name", "status"]
    cols_bet = ["selection", "odds", "stake", "result", "profit", "balance"]
    display_upcoming = [c for c in cols_common + cols_bet if c in df_upcoming.columns]
    display_completed = [c for c in cols_common + cols_bet if c in df_completed.columns]

    tab1, tab2 = st.tabs(["⏳ Upcoming", "✅ Completed"])
    with tab1:
        if df_upcoming.empty:
            st.info("No upcoming races today.")
        else:
            df_upcoming["run_time_local"] = df_upcoming["run_time_local"].dt.strftime("%H:%M")
            st.dataframe(df_upcoming[display_upcoming], use_container_width=True, hide_index=True)
    with tab2:
        if df_completed.empty:
            st.info("No completed races yet today.")
        else:
            df_completed["run_time_local"] = df_completed["run_time_local"].dt.strftime("%H:%M")
            df_completed = df_completed.sort_values("run_time_local", ascending=False)
            st.dataframe(df_completed[display_completed], use_container_width=True, hide_index=True)

# ───────────────────────── Today's Summary (till now) ─────────────────────────
st.markdown("---")
st.subheader(f"Today's Summary · {LOCAL_TZ}")
if today_metrics is None:
    st.info("No dated rows found to compute today's stats.")
else:
    t_bets, t_wins, t_losses, t_stake, t_profit, t_win_rt, t_roi = today_metrics
    cA, cB, cC, cD, cE, cF, cG = st.columns(7)
    for (label, val), c in zip(
        [
            ("Bets Today", t_bets),
            ("Wins", t_wins),
            ("Losses", t_losses),
            ("Stake", f"{CURRENCY}{t_stake:.2f}"),
            ("P / L", f"{CURRENCY}{t_profit:+.2f}"),
            ("Win Rate", f"{t_win_rt:.1f}%"),
            ("ROI", f"{t_roi:.1f}%"),
        ],
        [cA, cB, cC, cD, cE, cF, cG],
    ):
        c.markdown(f'<div class="metric-card"><div class="muted">{label}</div><div class="value">{val}</div></div>', unsafe_allow_html=True)
    st.caption(f"Races remaining today: {(df_races['run_time_local'] >= now_local).sum() if not df_races.empty else 0}")

# ───────────────────────── Day-wise Stats (local day) ─────────────────────────
st.markdown("---")
st.subheader(f"Day-wise Stats · {LOCAL_TZ}")

# Build a local-day column
if time_col is not None and pd.api.types.is_datetime64_any_dtype(df[time_col]):
    if pd.api.types.is_datetime64tz_dtype(df[time_col]):
        dt_local_for_days = df[time_col].dt.tz_convert(ZoneInfo(LOCAL_TZ))
    else:
        dt_local_for_days = df[time_col].dt.tz_localize(ZoneInfo(LOCAL_TZ))
    df_day = df.assign(day_local=dt_local_for_days.dt.date)
else:
    df_day = df.copy()
    df_day["day_local"] = df["day"] if "day" in df.columns else pd.NaT

if df_day["day_local"].notna().any():
    daily_stats = (
        df_day.groupby("day_local", dropna=True)
              .agg(
                  bets=("result", "size"),
                  wins=("result", lambda s: (s == "W").sum()),
                  losses=("result", lambda s: (s == "L").sum()),
                  stake=("stake", "sum") if "stake" in df_day.columns else ("result", "size"),
                  profit=("profit", "sum") if "profit" in df_day.columns else ("result", "size"),
              )
              .reset_index()
              .sort_values("day_local")
    )
    for col in ["stake", "profit"]:
        if col in daily_stats.columns:
            daily_stats[col] = pd.to_numeric(daily_stats[col], errors="coerce").fillna(0.0)

    daily_stats["win_rate_%"] = np.where(daily_stats["bets"] > 0, (daily_stats["wins"] / daily_stats["bets"]) * 100.0, 0.0)
    daily_stats["roi_%"] = np.where(
        daily_stats["stake"] > 0,
        (daily_stats["profit"] / daily_stats["stake"]) * 100.0,
        0.0
    )
    if "profit" in daily_stats.columns:
        daily_stats["balance"] = float(starting_balance) + daily_stats["profit"].cumsum()
    else:
        daily_stats["balance"] = np.nan

    display_cols = ["day_local", "bets", "wins", "losses"]
    if "stake" in daily_stats.columns: display_cols.append("stake")
    if "profit" in daily_stats.columns: display_cols.append("profit")
    display_cols += ["win_rate_%", "roi_%", "balance"]

    daily_display = daily_stats[display_cols].copy()
    for c in ["stake", "profit", "win_rate_%", "roi_%", "balance"]:
        if c in daily_display.columns:
            daily_display[c] = daily_display[c].astype(float).round(2)

    st.dataframe(daily_display, use_container_width=True, hide_index=True)
else:
    st.info("No date/time column found to compute day-wise stats.")

# ───────────────────────── Trades (LAST 20) ─────────────────────────
st.markdown("---")
st.subheader("Trades (last 20)")
show_cols = [c for c in ["timestamp", "track", "race_name", "selection", "odds", "stake", "result", "profit", "balance"] if c in df.columns]

if show_cols:
    time_col_order = "timestamp" if "timestamp" in df.columns else ("date" if "date" in df.columns else None)
    if time_col_order is not None and pd.api.types.is_datetime64_any_dtype(df[time_col_order]):
        df_sorted = df.sort_values(time_col_order, ascending=False)
    else:
        df_sorted = df.iloc[::-1].copy()

    df_last20 = df_sorted[show_cols].head(20).copy()
    for col in ["odds", "stake", "profit", "balance"]:
        if col in df_last20.columns:
            df_last20[col] = df_last20[col].map(lambda x: round(x, 3) if pd.notna(x) else x)

    st.dataframe(df_last20, use_container_width=True, hide_index=True)
else:
    st.info("No displayable columns found. Check your `bets` schema.")

# Download filtered CSV (from DB rows after filters)
st.download_button(
    "⬇️ Download filtered CSV",
    data=df.to_csv(index=False).encode("utf-8"),
    file_name="bet_results_filtered.csv",
    mime="text/csv",
)
