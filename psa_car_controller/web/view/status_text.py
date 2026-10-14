"""How the Home page shows car status values, kept free of Dash so it can be unit tested."""
KM_PER_MILE = 1.609344
# Below this battery level (%) the Home page won't start preconditioning, as the NFC tag automation won't.
MIN_PRECOND_BATTERY = 51


def format_mileage(km):
    """The car reports km; show whole miles, e.g. "12,345 mi"."""
    return f"{round(km / KM_PER_MILE):,} mi"


def battery_too_low_to_precondition(battery):
    """True when the battery level (%) is known and under MIN_PRECOND_BATTERY. An unknown level doesn't block."""
    return isinstance(battery, (int, float)) and battery < MIN_PRECOND_BATTERY
