def calculate_first_stake(balance, settings):
    """
    Determine the first stake of a new chase.

    Normal mode: uses a fraction of the current bankroll (e.g., 3%).
    Sim/light mode: if settings['use_penny_first'] is truthy, returns 0.01.

    Always:
      - Enforces a minimum of 0.01
      - Does not exceed the current balance
      - Applies an optional max cap if provided in settings
    """
    # Force minimal live/sim-lite probe if requested
    if settings.get("use_penny_first"):
        stake = 0.01
    else:
        pct = settings.get("first_stake_pct", 0.03)
        stake = balance * pct

    # Enforce minimum stake
    stake = max(stake, 0.01)

    # Apply optional maximum cap for first stake
    max_first = settings.get("max_first_stake")
    if max_first is not None:
        stake = min(stake, max_first)

    # Never bet more than the available balance
    stake = min(stake, balance)

    # Optional rounding logic if you have tick size in settings
    tick = settings.get("tick_size")
    if tick:
        # round up to nearest tick
        stake = ((stake + tick - 1e-12) // tick) * tick
        # ensure at least one tick
        stake = max(stake, tick)

    return round(stake, 2)  # round to cents/pence; adjust precision if needed

