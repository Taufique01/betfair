#!/usr/bin/env python3
import json
import time
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo
import betfairlightweight
from betfairlightweight.filters import market_filter

# === CONFIG & LOGIN ===
CONFIG = json.load(open("config.json"))
client = betfairlightweight.APIClient(
    username=CONFIG["betfair_username"],
    password=CONFIG["betfair_password"],
    app_key=CONFIG["betfair_app_key"],
    certs=CONFIG["certs"]
)
client.login()
betting = client.betting

# === TIME WINDOWS ===
TZ = ZoneInfo("Europe/London")
UTC = ZoneInfo("UTC")
now_utc = datetime.now(UTC).replace(microsecond=0)
tomorrow_mid_bst = (
    datetime.combine(
        datetime.now(TZ).date() + timedelta(days=1),
        dt_time.min,
        TZ
    )
    .astimezone(UTC)
    .replace(microsecond=0)
)
# static window for comparison
static_from = "2025-06-28T14:00:00Z"
static_to   = "2025-06-28T23:00:00Z"

# === PARAMETER OPTIONS ===
event_id_opts = [["7"], [7]]
time_opts = [
    ("dynamic", {"from": now_utc.isoformat(), "to": tomorrow_mid_bst.isoformat()}),
    ("static",  {"from": static_from,      "to": static_to})
]
proj_opts = [
    ("minimal", ["EVENT", "MARKET_START_TIME"]),
    ("full",    ["RUNNER_METADATA","MARKET_START_TIME","EVENT","MARKET_NAME"])
]
sort_opts = [None, "FIRST_TO_START"]
locale_opts = [None, "en"]

# === DEBUG LOOP ===
print("\n=== Starting catalogue debug ===\n")
for evt_ids in event_id_opts:
    for tname, time_window in time_opts:
        mf = market_filter(
            event_type_ids=evt_ids,
            market_type_codes=["WIN"],
            market_countries=["GB","IE"],
            market_start_time=time_window
        )
        for pname, projection in proj_opts:
            for sort in sort_opts:
                for locale in locale_opts:
                    params = {
                        "filter": mf,
                        "market_projection": projection,
                        "max_results": 10
                    }
                    if sort is not None:
                        params["sort"] = sort
                    if locale is not None:
                        params["locale"] = locale

                    # Print header
                    print(">>> TEST:", 
                          f"eventTypeIds={evt_ids!r},",
                          f"time={tname},",
                          f"proj={pname},",
                          f"sort={sort!r},",
                          f"locale={locale!r}")
                    print("    time_window:", time_window)
                    print("    projection:", projection)
                    try:
                        markets = betting.list_market_catalogue(**params)
                        print(f"    ✅ Success: got {len(markets)} markets\n")
                    except Exception as e:
                        # Betfair error payload is on e.args or repr(e)
                        print(f"    ❌ Exception: {e!r}\n")
                    # slight pause to avoid rate limits
                    time.sleep(0.2)

# cleanup
client.logout()
print("\n=== Debug complete ===")
