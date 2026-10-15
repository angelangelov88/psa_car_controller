"""Turns the Home page's weekly schedule form into the car's four programs, kept free of Dash for unit tests."""
from psa_car_controller.common.utils import PRECOND_PROGRAM_KEYS, validate_preconditioning_programs

# The car's "no time set" hour.
UNSET_HOUR = 34
MINUTE_STEP = 10
HOUR_OPTIONS = [{"label": "Not set", "value": ""}] + [{"label": f"{h:02d}", "value": str(h)} for h in range(24)]


def minute_options(minute):
    """Every MINUTE_STEP minutes, plus the car's own minute when it's in between, so loading doesn't change it."""
    minutes = sorted(set(range(0, 60, MINUTE_STEP)) | {minute})
    return [{"label": f"{m:02d}", "value": str(m)} for m in minutes]


def _whole_number(value, default):
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def programs_from_form(ons, hours, minutes, days):
    """(programs, None) ready to send, or (None, message) saying what to fix. A blank hour is the car's unset hour
    and a blank minute is 0. An enabled schedule needs an hour and at least one day, or it would never run."""
    programs = {}
    for i, key in enumerate(PRECOND_PROGRAM_KEYS):
        selected = days[i] or []
        program = {
            "day": [1 if d in selected else 0 for d in range(7)],
            "hour": _whole_number(hours[i], UNSET_HOUR),
            "minute": _whole_number(minutes[i], 0),
            "on": 1 if ons[i] else 0,
        }
        if program["on"] and program["hour"] == UNSET_HOUR:
            return None, f"Schedule {i + 1} is enabled but has no hour."
        if program["on"] and not any(program["day"]):
            return None, f"Schedule {i + 1} is enabled but has no days."
        programs[key] = program
    try:
        return validate_preconditioning_programs(programs), None
    except (ValueError, TypeError):
        return None, "Hours must be 0–23 and minutes 0–59."
