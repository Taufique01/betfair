import os
from datetime import datetime
import numpy as np
import pandas as pd
import streamlit as st
from zoneinfo import ZoneInfo

"""
Bet Results Dashboard (Streamlit)

- Reads a server-side CSV (no upload UI)
- Reloads on browser refresh and on "Refresh now" button
- Auto-invalidates cache when the CSV file changes (uses file modified time)
- KPIs, filters, downloadable filtered CSV
- "Today's Summary (till now)" in your local timezone
- "Day-wise Stats" table with running balance (ALL days)
- "Trades" shows only the latest 20 rows
"""

# ───────────────────────── Page setup ─────────────────────────
st.set_page_config(page_title="Bet Results Dashboard", page_icon="🎯", layout="wide")

# ───────────────────────── Config ─────────────────────────
CSV_PATH = os.getenv("CSV_PATH", "chase_results/chase_results.csv")
CURRENCY = os.getenv("CURRENCY", "$")
LOCAL_TZ = os.getenv("LOCAL_TZ", "Asia/Dhaka")

# ───────────────────────── Helpers ─────────────────────────
def file_mtime(path: str) -> float:
    """Return last modified time of a file. Used to invalidate cache when file changes."""
    try:
        return os.path.getmtime(path)
    except Exception:
        return 0.0

@st.cache_data(show_spinner=False)
def load_csv(path: str, mtime: float) -> pd.DataFrame:
    """
    Load CSV into DataFrame.
    NOTE: The 'mtime' argument is part of the cache key so when the file changes,
    the cache invalidates automatically. On browser refresh, Streamlit re-runs
    this script and evaluates the current mtime again.
    """
    df = pd.read_csv(path)
    return df

def coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    # Parse datetimes
    for col in ["timestamp", "date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")

    # Numeric columns
    for col in ["odds", "stake", "profit", "balance", "leg"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Normalize result values
    if "result" in df.columns:
        df["result"] = df["result"].astype(str).str.strip().str.upper()

    # Derive a 'day' column (UTC-based) useful for global daily filters
    if "date" in df.columns and pd.api.types.is_datetime64_any_dtype(df["date"]):
        df["day"] = df["date"].dt.tz_convert("UTC").dt.date if df["date"].dt.tz is not None else df["date"].dt.date
    elif "timestamp" in df.columns and pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        df["day"] = df["timestamp"].dt.tz_convert("UTC").dt.date if df["timestamp"].dt.tz is not None else df["timestamp"].dt.date
    else:
        df["day"] = pd.NaT
    return df

def compute_balance(df: pd.DataFrame, starting_balance: float = 0.0) -> pd.Series:
    """
    Use 'balance' column if present; otherwise compute running balance from profit.
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
st.sidebar.caption(f"Reading from: **{CSV_PATH}**")

# Read CSV (with mtime-based cache key)
starting_balance_default = 0.0
try:
    mtime = file_mtime(CSV_PATH)
    df_raw = load_csv(CSV_PATH, mtime)
    # Show file update time for visibility
    if mtime:
        last_update = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
        st.sidebar.caption(f"Last file update: **{last_update}**")
    if "balance" in df_raw.columns and df_raw["balance"].notna().any():
        starting_balance_default = float(df_raw["balance"].iloc[0])
except Exception as e:
    st.sidebar.error(f"Could not read CSV: {e}")
    st.stop()

starting_balance = st.sidebar.number_input(
    "Starting balance (only used if no 'balance' column)",
    value=starting_balance_default,
    step=10.0
)

# Manual refresh button: clears cache and reruns (forces disk read next run)
if st.sidebar.button("↻ Refresh now", use_container_width=True):
    st.cache_data.clear()
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()

# ───────────────────────── Load + prepare data ─────────────────────────
df = coerce_types(df_raw.copy())

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

# Result filter (W/L)
if "result" in df.columns:
    results = sorted([r for r in df["result"].dropna().unique().tolist()])
    selected_results = st.sidebar.multiselect("Result", results, default=results)
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
    mask = pd.Series([False] * len(df))
    for col in ["selection", "race_name", "track"]:
        if col in df.columns:
            mask |= df[col].astype(str).str.lower().str.contains(search_text, na=False)
    df = df[mask]

# ───────────────────────── KPIs ─────────────────────────
balance_series = compute_balance(df, starting_balance=starting_balance)

wins = int((df["result"] == "W").sum()) if "result" in df.columns else 0
losses = int((df["result"] == "L").sum()) if "result" in df.columns else 0
total_bets = int(len(df))
total_stake = float(df["stake"].sum()) if "stake" in df.columns else 0.0
total_profit = float(df["profit"].sum()) if "profit" in df.columns else (
    float(balance_series.iloc[-1] - balance_series.iloc[0]) if len(balance_series) > 1 else 0.0
)
win_rate = (wins / total_bets * 100.0) if total_bets else 0.0
roi = (total_profit / total_stake * 100.0) if total_stake else 0.0
dd = max_drawdown(balance_series)  # negative number

# ## --- TODAY'S SUMMARY (local timezone) ---
TZ = ZoneInfo(LOCAL_TZ)
now_local = datetime.now(TZ)

# Decide which datetime column to use
time_col = "timestamp" if "timestamp" in df.columns else ("date" if "date" in df.columns else None)

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
      /* Theme-aware cards */
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
      /* Make values pop (green) so they’re visible on dark theme */
      .value {
        font-size: 28px;
        font-weight: 700;
        margin-top: 4px;
        color: #10b981; /* emerald-500 */
      }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🎯 Bet Results Dashboard")
st.caption("Refresh the page or click **↻ Refresh now** to reload data. Any file change is detected automatically.")

c1, c2, c3, c4, c5, c6 = st.columns(6)
with c1:
    st.markdown(f'<div class="metric-card"><div class="muted">Total Bets</div><div class="value">{total_bets}</div></div>', unsafe_allow_html=True)
with c2:
    st.markdown(f'<div class="metric-card"><div class="muted">Wins</div><div class="value">{wins}</div></div>', unsafe_allow_html=True)
with c3:
    st.markdown(f'<div class="metric-card"><div class="muted">Win Rate</div><div class="value">{win_rate:.1f}%</div></div>', unsafe_allow_html=True)
with c4:
    st.markdown(f'<div class="metric-card"><div class="muted">Total Stake</div><div class="value">{CURRENCY}{total_stake:.2f}</div></div>', unsafe_allow_html=True)
with c5:
    st.markdown(f'<div class="metric-card"><div class="muted">P / L</div><div class="value">{CURRENCY}{total_profit:+.2f}</div></div>', unsafe_allow_html=True)
with c6:
    st.markdown(f'<div class="metric-card"><div class="muted">Max Drawdown</div><div class="value">{CURRENCY}{dd:.2f}</div></div>', unsafe_allow_html=True)

# Today's Summary (till now)
st.markdown("---")
st.subheader(f"Today's Summary · {LOCAL_TZ}")
if today_metrics is None:
    st.info("No dated rows found to compute today's stats.")
else:
    t_bets, t_wins, t_losses, t_stake, t_profit, t_win_rt, t_roi = today_metrics
    cA, cB, cC, cD, cE, cF, cG = st.columns(7)
    with cA: st.markdown(f'<div class="metric-card"><div class="muted">Bets Today</div><div class="value">{t_bets}</div></div>', unsafe_allow_html=True)
    with cB: st.markdown(f'<div class="metric-card"><div class="muted">Wins</div><div class="value">{t_wins}</div></div>', unsafe_allow_html=True)
    with cC: st.markdown(f'<div class="metric-card"><div class="muted">Losses</div><div class="value">{t_losses}</div></div>', unsafe_allow_html=True)
    with cD: st.markdown(f'<div class="metric-card"><div class="muted">Stake</div><div class="value">{CURRENCY}{t_stake:.2f}</div></div>', unsafe_allow_html=True)
    with cE: st.markdown(f'<div class="metric-card"><div class="muted">P / L</div><div class="value">{CURRENCY}{t_profit:+.2f}</div></div>', unsafe_allow_html=True)
    with cF: st.markdown(f'<div class="metric-card"><div class="muted">Win Rate</div><div class="value">{t_win_rt:.1f}%</div></div>', unsafe_allow_html=True)
    with cG: st.markdown(f'<div class="metric-card"><div class="muted">ROI</div><div class="value">{t_roi:.1f}%</div></div>', unsafe_allow_html=True)
    st.caption(f"Races remaining today: {today_remaining}")

# ───────────────────────── Day-wise Stats (local day) ─────────────────────────
st.markdown("---")
st.subheader(f"Day-wise Stats · {LOCAL_TZ}")

# Build a local-day column from the main datetime column
if time_col is not None and pd.api.types.is_datetime64_any_dtype(df[time_col]):
    if pd.api.types.is_datetime64tz_dtype(df[time_col]):
        dt_local_for_days = df[time_col].dt.tz_convert(ZoneInfo(LOCAL_TZ))
    else:
        dt_local_for_days = df[time_col].dt.tz_localize(ZoneInfo(LOCAL_TZ))
    df_day = df.assign(day_local=dt_local_for_days.dt.date)
else:
    # Fallback: use existing 'day' (UTC) if present
    if "day" in df.columns:
        df_day = df.assign(day_local=df["day"])
    else:
        df_day = df.copy()
        df_day["day_local"] = pd.NaT

if df_day["day_local"].notna().any():
    # Aggregate per local day (ALL rows shown)
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

    # Ensure numeric types
    for col in ["stake", "profit"]:
        if col in daily_stats.columns:
            daily_stats[col] = pd.to_numeric(daily_stats[col], errors="coerce").fillna(0.0)

    # Derived metrics
    daily_stats["win_rate_%"] = np.where(daily_stats["bets"] > 0, (daily_stats["wins"] / daily_stats["bets"]) * 100.0, 0.0)
    daily_stats["roi_%"] = np.where(
        daily_stats["stake"] > 0,
        (daily_stats["profit"] / daily_stats["stake"]) * 100.0,
        0.0
    )

    # Running balance over days (from starting_balance)
    if "profit" in daily_stats.columns:
        daily_stats["balance"] = starting_balance + daily_stats["profit"].cumsum()
    else:
        daily_stats["balance"] = np.nan  # can't compute without profit

    # Tidy display
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
    # Decide how to order newest first
    time_col_order = "timestamp" if "timestamp" in df.columns else ("date" if "date" in df.columns else None)
    if time_col_order is not None and pd.api.types.is_datetime64_any_dtype(df[time_col_order]):
        df_sorted = df.sort_values(time_col_order, ascending=False)
    else:
        # Fallback: reverse the current order (assumes newer appended at bottom)
        df_sorted = df.iloc[::-1].copy()

    # Round numerics for display and take only last 20
    df_last20 = df_sorted[show_cols].head(20).copy()
    for col in ["odds", "stake", "profit", "balance"]:
        if col in df_last20.columns:
            df_last20[col] = df_last20[col].map(lambda x: round(x, 3) if pd.notna(x) else x)

    st.dataframe(df_last20, use_container_width=True, hide_index=True)
else:
    st.info("No displayable columns found. Ensure your CSV has the expected headers.")

# Download filtered CSV (full filtered dataset)
st.download_button(
    "⬇️ Download filtered CSV",
    data=df.to_csv(index=False).encode("utf-8"),
    file_name="bet_results_filtered.csv",
    mime="text/csv",
)
