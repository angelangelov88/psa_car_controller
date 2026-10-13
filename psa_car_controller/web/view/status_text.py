"""How the Home page shows car status values, kept free of Dash so it can be unit tested."""
KM_PER_MILE = 1.609344


def format_mileage(km):
    """The car reports km; show whole miles, e.g. "12,345 mi"."""
    return f"{round(km / KM_PER_MILE):,} mi"
