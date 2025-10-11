# app.py
import os
import json
import contextlib
from pathlib import Path
from datetime import datetime
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
LOCAL_TZ = os.getenv("LOCAL_TZ", "Asia/Dhaka")
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "config.json"))
TRACK_GRADES_PATH = Path(os.getenv("TRACK_GRADES_PATH", "track_grades.json"))
LOW_WIN_RACES_PATH = Path(os.getenv("LOW_WIN_RACES_PATH", "low_win_races.json"))
STRAT_SETTINGS_PATH = Path(os.getenv("STRAT_SETTINGS_PATH", "strat_settings.json"))
BANK_BALANCE_PATH = Path(os.getenv("BANK_BALANCE_PATH", "bank_balance.json"))

# ───────────────────────── ORM → DataFrame ─────────────────────────
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
            "date": b.race_datetime,        # alias for readability
            "race_datetime": b.race_datetime,
            "leg": b.leg,
            "selection": b.selection,
            "odds": float(b.odds) if b.odds is not None else None,
            "stake": float(b.stake) if b.stake is not None else None,
            "result": (b.result or "").strip().upper() if b.result is not None else None,  # 'P'|'W'|'L'
            "profit": float(b.profit) if b.profit is not None else None,
            "balance": float(b.balance_after) if b.balance_after is not None else None,
        })
    return pd.DataFrame.from_records(recs)

@st.cache_data(show_spinner=False)
def load_todays_schedules_with_latest_bet(local_tz: str = "Asia/Dhaka") -> pd.DataFrame:
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
            "run_time_local": run_local,
            "job_id": sc.job_id,
            "status": sc.status,  # scheduled|running|done|skipped|error
            "race_name": sc.race_name,
            "track": sc.track,
            "market_id": sc.market_id,

            # Bet details (if any)
            "selection": getattr(b, "selection", None),
            "odds": float(getattr(b, "odds", np.nan)) if getattr(b, "odds", None) is not None else None,
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
    # Parse datetimes
    for col in ["timestamp", "date", "race_datetime"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")

    # Numeric columns
    for col in ["odds", "stake", "profit", "balance", "leg"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Result normalization
    if "result" in df.columns:
        df["result"] = df["result"].astype(str).str.strip().str.upper()

    # Derive UTC day (available if you need grouping)
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

# ───────────────────────── Config I/O helpers ─────────────────────────
def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        backup = path.with_suffix(".corrupt.backup.json")
        try:
            path.replace(backup)
        except Exception:
            pass
        return {}

def save_config(path: Path, cfg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

def load_json_file(path: Path, fallback):
    if not path.exists():
        return fallback
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback

# ───────────────────────── Sidebar ─────────────────────────
if st.sidebar.button("↻ Refresh", use_container_width=True):
    st.cache_data.clear()
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()

page = st.sidebar.radio("Pages", ["Today’s Races", "Stats", "History", "Settings"], index=0)

# ───────────────────────── Load + prepare base data ─────────────────────────
try:
    df_all = load_bets_df()
    if df_all.empty:
        st.sidebar.warning("No rows in `bets` table yet.")
except Exception as e:
    st.sidebar.error(f"DB error: {e}")
    st.stop()

df_all = coerce_types(df_all.copy())

# Global aggregates (no filters)
balance_series_global = compute_balance(df_all, starting_balance=0.0)
wins_global     = int((df_all.get("result", pd.Series([])) == "W").sum())
losses_global   = int((df_all.get("result", pd.Series([])) == "L").sum())
pending_global  = int((df_all.get("result", pd.Series([])) == "P").sum())
total_bets_glob = int(len(df_all))
total_stake_glb = float(df_all["stake"].sum()) if "stake" in df_all.columns else 0.0
total_profit_glb = float(df_all["profit"].sum()) if "profit" in df_all.columns else (
    float(balance_series_global.iloc[-1] - balance_series_global.iloc[-2]) if len(balance_series_global) > 1 else 0.0
) if len(balance_series_global) > 0 else 0.0
dd_global = max_drawdown(balance_series_global)

time_col = "timestamp" if "timestamp" in df_all.columns else ("date" if "date" in df_all.columns else None)

# ───────────────────────── Shared UI bits (styles) ─────────────────────────
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
        color: #10b981;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

# ───────────────────────── Page: Today’s Races ─────────────────────────
if page == "Today’s Races":
    st.title("🏇 Today’s Races")

    TZ = ZoneInfo(LOCAL_TZ)
    now_local = datetime.now(TZ)

    today_metrics = None
    current_balance = float(balance_series_global.iloc[-1]) if len(balance_series_global) else 0.0

    if time_col is not None and pd.api.types.is_datetime64_any_dtype(df_all[time_col]):
        dt_local = df_all[time_col].dt.tz_convert(TZ) if pd.api.types.is_datetime64tz_dtype(df_all[time_col]) else df_all[time_col].dt.tz_localize(TZ)
        df_today = df_all.assign(local_day=dt_local.dt.date)
        today = now_local.date()
        mask_today_so_far = (df_today["local_day"] == today) & (dt_local <= now_local)

        df_today_so_far = df_today.loc[mask_today_so_far].copy()
        if not df_today_so_far.empty:
            t_bets   = int(len(df_today_so_far))
            t_wins   = int((df_today_so_far["result"] == "W").sum()) if "result" in df_today_so_far.columns else 0
            t_losses = int((df_today_so_far["result"] == "L").sum()) if "result" in df_today_so_far.columns else 0
            t_stake  = float(df_today_so_far["stake"].sum()) if "stake" in df_today_so_far.columns else 0.0
            t_profit = float(df_today_so_far["profit"].sum()) if "profit" in df_today_so_far.columns else 0.0
            t_win_rt = (t_wins / t_bets * 100.0) if t_bets else 0.0
            t_roi    = (t_profit / t_stake * 100.0) if t_stake else 0.0
            today_metrics = (t_bets, t_wins, t_losses, t_stake, t_profit, t_win_rt, t_roi)

    st.subheader(f"Today’s Summary · {LOCAL_TZ}")
    if today_metrics is None:
        st.info("No dated rows found to compute today's stats yet.")
    else:
        t_bets, t_wins, t_losses, t_stake, t_profit, t_win_rt, t_roi = today_metrics
        cA, cB, cC, cD, cE, cF, cG, cH = st.columns(8)
        for (label, val), c in zip(
            [
                ("Bets Today", t_bets),
                ("Wins", t_wins),
                ("Losses", t_losses),
                ("Stake", f"{CURRENCY}{t_stake:.2f}"),
                ("P / L", f"{CURRENCY}{t_profit:+.2f}"),
                ("Win Rate", f"{t_win_rt:.1f}%"),
                ("ROI", f"{t_roi:.1f}%"),
                ("Current Balance", f"{CURRENCY}{current_balance:.2f}"),
            ],
            [cA, cB, cC, cD, cE, cF, cG, cH],
        ):
            c.markdown(
                f'<div class="metric-card"><div class="muted">{label}</div><div class="value">{val}</div></div>',
                unsafe_allow_html=True
            )

    st.markdown("---")
    st.subheader(f"Races Today · {LOCAL_TZ}")

    df_races = load_todays_schedules_with_latest_bet(LOCAL_TZ)
    if df_races.empty:
        st.info("No schedules found for today.")
    else:
        statuses = ["All", "scheduled", "running", "done", "skipped", "error"]
        tabs = st.tabs([s.capitalize() for s in statuses])

        base_cols = ["run_time_local", "track", "race_name", "status"]
        bet_cols  = ["selection", "odds", "stake", "result", "profit", "balance"]
        display_cols = [c for c in base_cols + bet_cols if c in df_races.columns]

        for s, tab in zip(statuses, tabs):
            with tab:
                if s == "All":
                    df_view = df_races.copy()
                else:
                    df_view = df_races[df_races["status"] == s].copy()

                if df_view.empty:
                    st.info(f"No {s.lower()} races." if s != "All" else "No races today.")
                else:
                    df_view["run_time_local"] = df_view["run_time_local"].dt.strftime("%H:%M")
                    st.dataframe(df_view[display_cols], use_container_width=True, hide_index=True)

        remaining = (df_races["status"].isin(["scheduled", "running"])).sum()
        st.caption(f"Races remaining today (by status): {remaining}")

# ───────────────────────── Page: Stats (Day-wise) ─────────────────────────
elif page == "Stats":
    st.title("📊 Day-wise Stats")

    TZ = ZoneInfo(LOCAL_TZ)
    if time_col is not None and pd.api.types.is_datetime64_any_dtype(df_all[time_col]):
        dt_local_for_days = df_all[time_col].dt.tz_convert(TZ) if pd.api.types.is_datetime64tz_dtype(df_all[time_col]) else df_all[time_col].dt.tz_localize(TZ)
        df_day = df_all.assign(day_local=dt_local_for_days.dt.date)
    else:
        df_day = df_all.copy()
        df_day["day_local"] = df_all["day"] if "day" in df_all.columns else pd.NaT

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
            daily_stats["balance"] = float(0.0) + daily_stats["profit"].cumsum()
        else:
            daily_stats["balance"] = np.nan

        total_days = int(daily_stats["day_local"].nunique())
        total_bets = int(daily_stats["bets"].sum())
        total_wins = int(daily_stats["wins"].sum())
        total_losses = int(daily_stats["losses"].sum())
        total_stake = float(daily_stats["stake"].sum()) if "stake" in daily_stats.columns else 0.0
        total_profit = float(daily_stats["profit"].sum()) if "profit" in daily_stats.columns else 0.0
        last_balance = float(daily_stats["balance"].iloc[-1]) if "balance" in daily_stats.columns and len(daily_stats) else 0.0

        c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
        for (label, val), c in zip(
            [
                ("Days", total_days),
                ("Total Bets", total_bets),
                ("Wins", total_wins),
                ("Losses", total_losses),
                ("Total Stake", f"{CURRENCY}{total_stake:.2f}"),
                ("P / L", f"{CURRENCY}{total_profit:+.2f}"),
                ("Last Balance", f"{CURRENCY}{last_balance:.2f}"),
            ],
            [c1, c2, c3, c4, c5, c6, c7],
        ):
            c.markdown(f'<div class="metric-card"><div class="muted">{label}</div><div class="value">{val}</div></div>', unsafe_allow_html=True)

        st.markdown("---")
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

# ───────────────────────── Page: History (Summary + Trades) ─────────────────────────
elif page == "History":
    st.title("📜 History")

    cols = st.columns(7)
    labels = [
        ("Total Bets", total_bets_glob),
        ("Wins", wins_global),
        ("Losses", losses_global),
        ("Pending", pending_global),
        ("Total Stake", f"{CURRENCY}{total_stake_glb:.2f}"),
        ("P / L", f"{CURRENCY}{total_profit_glb:+.2f}"),
        ("Max Drawdown", f"{CURRENCY}{dd_global:.2f}"),
    ]
    for (label, value), c in zip(labels, cols):
        c.markdown(
            f'<div class="metric-card"><div class="muted">{label}</div><div class="value">{value}</div></div>',
            unsafe_allow_html=True
        )

    st.markdown("---")
    st.subheader("Bet Results (Trades) — full history")

    show_cols = [c for c in ["timestamp", "track", "race_name", "selection", "odds", "stake", "result", "profit", "balance"] if c in df_all.columns]
    if show_cols:
        time_col_order = "timestamp" if "timestamp" in df_all.columns else ("date" if "date" in df_all.columns else None)
        if time_col_order is not None and pd.api.types.is_datetime64_any_dtype(df_all[time_col_order]):
            df_sorted = df_all.sort_values(time_col_order, ascending=True)
        else:
            df_sorted = df_all.iloc[::-1].copy()

        df_trades = df_sorted[show_cols].copy()
        for col in ["odds", "stake", "profit", "balance"]:
            if col in df_trades.columns:
                df_trades[col] = df_trades[col].map(lambda x: round(x, 3) if pd.notna(x) else x)

        st.dataframe(df_trades, use_container_width=True, hide_index=True)
    else:
        st.info("No displayable columns found. Check your `bets` schema.")

    st.download_button(
        "⬇️ Download full bets CSV",
        data=df_all.to_csv(index=False).encode("utf-8"),
        file_name="bet_results_full.csv",
        mime="text/csv",
    )

# ───────────────────────── Page: Settings (email_recipient + JSON editors + bank balance) ─────────────────────────
elif page == "Settings":
    st.title("⚙️ Settings")

    # ---------- Email Recipient ----------
    st.caption(f"`{CONFIG_PATH.name}`")
    cfg = load_config(CONFIG_PATH)

    with st.form("recipient_settings_form", clear_on_submit=False):
        st.subheader("Notification Recipient")
        email_recipient = st.text_input(
            "Email Recipient",
            value=cfg.get("email_recipient", ""),
            placeholder="recipient@example.com",
        )
        submitted = st.form_submit_button("💾 Save Recipient", use_container_width=True)
        if submitted:
            cfg["email_recipient"] = email_recipient.strip()
            try:
                save_config(CONFIG_PATH, cfg)
                st.success("Recipient saved to config.json.")
            except Exception as e:
                st.error(f"Failed to save config: {e}")

    st.markdown("---")

    # ---------- Track Grades Editor ----------
    st.subheader("Track Grades")
    st.caption(f"`{TRACK_GRADES_PATH.name}`")

    default_tracks = {
        "Ascot": {"skip": False, "grade": "A"},
        "Brighton": {"skip": True, "grade": "C"},
        "York": {"skip": False, "grade": "A"},
    }
    tracks_obj = load_json_file(TRACK_GRADES_PATH, default_tracks)

    rows = []
    if isinstance(tracks_obj, dict):
        for k, v in tracks_obj.items():
            rows.append({"track": k, "skip": bool(v.get("skip", False)), "grade": str(v.get("grade", ""))})
    else:
        for k, v in default_tracks.items():
            rows.append({"track": k, "skip": v["skip"], "grade": v["grade"]})

    df_tracks = pd.DataFrame(rows).sort_values("track") if rows else pd.DataFrame(columns=["track", "skip", "grade"])

    edited_tracks = st.data_editor(
        df_tracks,
        num_rows="dynamic",
        use_container_width=True,
        key="tracks_editor",
        column_config={
            "track": st.column_config.TextColumn("Track", required=True),
            "skip": st.column_config.CheckboxColumn("Skip", default=False),
            "grade": st.column_config.TextColumn("Grade", help="Optional note/grade"),
        }
    )

    if st.button("💾 Save low grades races", use_container_width=True):
        try:
            cleaned = {}
            for _, r in edited_tracks.dropna(subset=["track"]).iterrows():
                name = str(r["track"]).strip()
                if not name:
                    continue
                cleaned[name] = {
                    "skip": bool(r.get("skip", False)),
                    "grade": str(r.get("grade", "")),
                }
            save_config(TRACK_GRADES_PATH, cleaned)
            st.success("Saved track_grades.json")
        except Exception as e:
            st.error(f"Failed to save track_grades.json: {e}")

    st.markdown("---")

    # ---------- Low Win Races Editor ----------
    st.subheader("Low Win Races")
    st.caption(f"`{LOW_WIN_RACES_PATH.name}`")

    default_low_win = [
        {"event_name": "5f Hcap", "skip": True},
        {"event_name": "6f Hcap", "skip": True},
    ]
    low_win_list = load_json_file(LOW_WIN_RACES_PATH, default_low_win)

    if not isinstance(low_win_list, list):
        low_win_list = default_low_win

    df_low = pd.DataFrame(low_win_list)
    if "event_name" not in df_low.columns:
        df_low["event_name"] = ""
    if "skip" not in df_low.columns:
        df_low["skip"] = False
    df_low = df_low[["event_name", "skip"]]

    edited_low = st.data_editor(
        df_low,
        num_rows="dynamic",
        use_container_width=True,
        key="low_win_editor",
        column_config={
            "event_name": st.column_config.TextColumn("Event Name", required=True),
            "skip": st.column_config.CheckboxColumn("Skip", default=False),
        }
    )

    if st.button("💾 Save low win races", use_container_width=True):
        try:
            out = []
            for _, r in edited_low.dropna(subset=["event_name"]).iterrows():
                name = str(r["event_name"]).strip()
                if not name:
                    continue
                out.append({"event_name": name, "skip": bool(r.get("skip", False))})
            save_config(LOW_WIN_RACES_PATH, out)
            st.success("Saved low_win_races.json")
        except Exception as e:
            st.error(f"Failed to save low_win_races.json: {e}")

    st.markdown("---")

    # ---------- Strategy Settings Editor ----------
    st.subheader("Strategy Settings")
    st.caption(f"`{STRAT_SETTINGS_PATH.name}`")

    default_strat = {"cutoff_time": "23:00", "bet_buffer_seconds": 300}
    strat_cfg = load_json_file(STRAT_SETTINGS_PATH, default_strat)

    with st.form("strat_settings_form", clear_on_submit=False):
        cutoff_time = st.text_input("Cutoff Time (HH:MM, 24h)", value=str(strat_cfg.get("cutoff_time", "23:00")))
        bet_buffer_seconds = st.number_input("Bet Buffer Seconds", min_value=0, step=5, value=int(strat_cfg.get("bet_buffer_seconds", 300)))
        submitted_strat = st.form_submit_button("💾 Save start setting", use_container_width=True)
        if submitted_strat:
            try:
                datetime.strptime(cutoff_time.strip(), "%H:%M")
                save_config(STRAT_SETTINGS_PATH, {
                    "cutoff_time": cutoff_time.strip(),
                    "bet_buffer_seconds": int(bet_buffer_seconds),
                })
                st.success("Saved strat_settings.json")
            except ValueError:
                st.error("Invalid time format for Cutoff Time. Use HH:MM (24h).")
            except Exception as e:
                st.error(f"Failed to save strat_settings.json: {e}")

    st.markdown("---")

    # ---------- Bank Balance Editor ----------
    st.subheader("Bank Balance")
    st.caption(f"`{BANK_BALANCE_PATH.name}`")

    bank_default = {"balance": 220.00}
    bank_cfg = load_json_file(BANK_BALANCE_PATH, bank_default)
    try:
        current_balance_val = float(bank_cfg.get("balance", bank_default["balance"]))
    except Exception:
        current_balance_val = bank_default["balance"]

    with st.form("bank_balance_form", clear_on_submit=False):
        new_balance = st.number_input(
            "Balance",
            min_value=0.0,
            step=1.0,
            value=round(current_balance_val, 2),
            format="%.2f"
        )
        submitted_bank = st.form_submit_button("💾 Save bank balance", use_container_width=True)
        if submitted_bank:
            try:
                save_config(BANK_BALANCE_PATH, {"balance": float(new_balance)})
                st.success("Saved bank_balance.json")
            except Exception as e:
                st.error(f"Failed to save bank_balance.json: {e}")
