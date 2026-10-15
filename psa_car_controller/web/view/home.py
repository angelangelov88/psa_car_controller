"""The Home page, the only page besides settings and setup.

A single Dash screen that mirrors the Mokka Companion app inside psacc: car
status, a preconditioning on/off toggle, and the four weekly schedules. It is
wired to psacc's existing car functions (RemoteClient.preconditioning and
get/set_preconditioning_program) and reuses the shared Mokka theme in
assets/zz_mokka_theme.css. No new car logic lives here.
"""
import logging

import dash_bootstrap_components as dbc
from dash import dcc, html, ALL
from dash.dependencies import Input, Output, State
from dash.exceptions import PreventUpdate

from psa_car_controller.common.utils import PRECOND_PROGRAM_KEYS, validate_preconditioning_programs
from psa_car_controller.psa.constants import DEFAULT_PRECONDITIONING_PROGRAM
from psa_car_controller.psacc.application.car_controller import PSACarController
from psa_car_controller.web.app import dash_app
from psa_car_controller.web.view.status_text import (MIN_PRECOND_BATTERY, battery_too_low_to_precondition,
                                                     car_state_text, charge_power_kw, charging_text, format_mileage,
                                                     format_power, format_rate, format_updated, map_embed_url,
                                                     map_link_url, position, precond_failure_text, precond_text)

logger = logging.getLogger(__name__)

APP = PSACarController()

DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _get_car():
    vehicles = APP.myp.vehicles_list
    return vehicles[0] if vehicles else None


def _read(getter):
    """Read a car-status field; None on any gap, as the car leaves many fields out."""
    try:
        return getter()
    except (AttributeError, TypeError, KeyError, IndexError):
        return None


def _stat_tile(label, value, sub=None):
    return dbc.Col(html.Div(className="mk-tile", children=[
        html.Div(label, className="mk-tile-label"),
        html.Div("—" if value is None else value, className="mk-tile-value"),
        html.Div(sub, className="mk-tile-sub") if sub else None,
    ]), xs=6, md=3)


def _when_known(value, text):
    """text(value), or None when the car didn't send the value."""
    return text(value) if value is not None else None


def _charging_rate_tile(car, energy):
    rate = _read(lambda: energy.charging.charging_rate)
    if _read(lambda: energy.charging.status) != "InProgress" or not rate:
        return _stat_tile("Charging rate", None)
    power = charge_power_kw(rate, car.battery_power, energy.level, energy.autonomy)
    return _stat_tile("Charging rate", format_power(power) if power else format_rate(rate),
                      format_rate(rate) if power else None)


def _precond_tile(status):
    ac = _read(lambda: status.preconditionning.air_conditioning)
    state = _read(lambda: ac.status)
    failure = _read(lambda: ac.failure_cause) if state == "Failure" else None
    return _stat_tile("Preconditioning", precond_text(state), _when_known(failure, precond_failure_text))


def _car_position(car):
    return position(_read(lambda: car.status.last_position.geometry.coordinates))


def _location_section(car):
    latlon = _car_position(car)
    if latlon is None:
        return None
    seen_at = format_updated(_read(lambda: car.status.last_position.properties.updated_at))
    return html.Details(id="mokka-location", className="mk-details", children=[
        html.Summary("Location"),
        # Filled in when the section is first opened, so Google only gets the car's position on request.
        html.Div(id="mokka-map"),
        html.Div(className="mk-map-foot", children=[
            html.Span(f"Position from {seen_at}" if seen_at else None),
            html.A("Open in Google Maps", href=map_link_url(*latlon), target="_blank", rel="noopener noreferrer"),
        ]),
    ])


def _status_cards(car):
    if car is None or car.status is None:
        return html.Div("Car status isn't available yet.", className="mokka-subtitle")

    status = car.status
    energy = _read(lambda: status.get_energy('Electric'))
    updated = format_updated(_read(lambda: energy.updated_at))

    return html.Div([
        html.Div(f"Updated {updated}" if updated else None, className="mokka-subtitle mk-updated"),
        dbc.Row(className="g-2 mb-3", children=[
            _stat_tile("Battery", _when_known(_read(lambda: energy.level), lambda v: f"{v:g}%"),
                       _when_known(_read(lambda: energy.autonomy), lambda v: f"{format_mileage(v)} range")),
            _stat_tile("Charging", charging_text(_read(lambda: energy.charging.plugged),
                                                 _read(lambda: energy.charging.status),
                                                 _read(lambda: energy.charging.remaining_time))),
            _charging_rate_tile(car, energy),
            _precond_tile(status),
            _stat_tile("Outside", _when_known(_read(lambda: status.environment.air.temp), lambda v: f"{v:g} °C")),
            _stat_tile("Mileage", _when_known(_read(lambda: status.timed_odometer.mileage), format_mileage)),
            _stat_tile("Car", car_state_text(_read(lambda: status.ignition.type), _read(lambda: status.kinetic.moving),
                                             _read(lambda: status.kinetic.speed))),
            _stat_tile("Battery health", _when_known(_read(lambda: energy.battery.health.resistance),
                                                     lambda v: f"{v:g}%")),
        ]),
        _location_section(car),
    ])


def _precond_on_state(car):
    try:
        return car.status.preconditionning.air_conditioning.status != "Disabled"
    except (AttributeError, TypeError):
        return False


def _battery_level(car):
    return _read(lambda: car.status.get_energy('Electric').level)


def _too_low_text(battery):
    return f"Battery is {battery}%. Preconditioning needs at least {MIN_PRECOND_BATTERY}%."


def _schedule_row(index, program):
    day = program.get("day", [0] * 7)
    hour = program.get("hour", 34)
    minute = program.get("minute", 0)
    on = program.get("on", 0)
    # hour 34 is the car's "unset" sentinel; show it as an empty field.
    hour_value = None if hour == 34 else hour

    return html.Div(className="mk-sched-row", children=[
        html.Div(className="mk-sched-head", children=[
            html.Span(f"Schedule {index + 1}", className="mk-sched-title"),
            dbc.Switch(id={"type": "mk-sched-on", "index": index},
                       value=bool(on), label="Enabled", className="mk-sched-switch"),
        ]),
        dbc.Row(className="g-2 align-items-end", children=[
            dbc.Col(xs=6, md=3, children=[
                dbc.Label("Hour", className="mk-mini-label"),
                dbc.Input(id={"type": "mk-sched-hour", "index": index}, type="number",
                          min=0, max=23, step=1, value=hour_value, placeholder="0-23"),
            ]),
            dbc.Col(xs=6, md=3, children=[
                dbc.Label("Minute", className="mk-mini-label"),
                dbc.Input(id={"type": "mk-sched-minute", "index": index}, type="number",
                          min=0, max=59, step=1, value=minute, placeholder="0-59"),
            ]),
            dbc.Col(xs=12, md=6, children=[
                dbc.Label("Days", className="mk-mini-label"),
                dcc.Checklist(
                    id={"type": "mk-sched-days", "index": index},
                    options=[{"label": name, "value": i} for i, name in enumerate(DAY_LABELS)],
                    value=[i for i, flag in enumerate(day) if flag == 1],
                    inline=True,
                    className="mk-days",
                    inputClassName="mk-day-input",
                    labelClassName="mk-day-label",
                ),
            ]),
        ]),
    ])


def get_home_layout():
    car = _get_car()
    try:
        vin = car.vin if car else None
        programs = APP.myp.remote_client.get_preconditioning_program(vin) if vin \
            else DEFAULT_PRECONDITIONING_PROGRAM
    except (AttributeError, TypeError):
        programs = DEFAULT_PRECONDITIONING_PROGRAM

    schedule_rows = [
        _schedule_row(i, programs.get(key, DEFAULT_PRECONDITIONING_PROGRAM[key]))
        for i, key in enumerate(PRECOND_PROGRAM_KEYS)
    ]

    precond_on = _precond_on_state(car)
    battery = _battery_level(car)
    # Turning it off always stays possible.
    too_low = not precond_on and battery_too_low_to_precondition(battery)

    return html.Div(className="mokka-panel", children=[
        html.H1("Home", className="visually-hidden"),  # for screen readers; on screen it's obvious

        html.H2("Car status", className="mk-section-title"),
        _status_cards(car),

        html.H2("Preconditioning", className="mk-section-title"),
        html.Div("Warm or cool the car, and set when it should do it automatically.",
                 className="mokka-subtitle"),
        html.Div(className="mk-toggle-card", children=[
            dbc.Switch(id="mokka-precond-toggle", value=precond_on, disabled=too_low,
                       label="Precondition now", className="mk-big-switch"),
            html.Div(
                "Turning this on wakes the car and starts climate control straight away.",
                className="mokka-subtitle mb-0"),
            html.Div(id="mokka-precond-status", className="mk-status",
                     children=html.Span(_too_low_text(battery), className="mk-err") if too_low else None),
        ]),

        html.H3("Weekly schedules", className="mk-section-title"),
        html.Div("Changes apply the next time the car is awake — not always straight away.",
                 className="mokka-subtitle"),
        html.Div(schedule_rows),

        html.Div(className="mk-save-row", children=[
            html.Div(id="mokka-sched-status", className="mk-status"),
            dbc.Button("Save schedules", id="mokka-sched-save", color="primary"),
        ]),
    ])


@dash_app.callback(
    Output("mokka-map", "children"),
    Input("mokka-location", "n_clicks"),
    State("mokka-map", "children"),
    prevent_initial_call=True)
def show_map(n_clicks, shown):
    if not n_clicks or shown:
        raise PreventUpdate()
    latlon = _car_position(_get_car())
    if latlon is None:
        return html.Span("Location isn't available.", className="mk-err")
    return html.Iframe(src=map_embed_url(*latlon), title="Map of the car's location", className="mk-map",
                       referrerPolicy="no-referrer")


@dash_app.callback(
    Output("mokka-precond-status", "children"),
    Input("mokka-precond-toggle", "value"),
    prevent_initial_call=True)
def toggle_precond(value):
    car = _get_car()
    if car is None:
        return html.Span("No car found.", className="mk-err")
    # The switch is disabled when the page loads with a low battery; this covers a page left open while it drains.
    if value and battery_too_low_to_precondition(_battery_level(car)):
        return html.Span(_too_low_text(_battery_level(car)) + " Nothing sent.", className="mk-err")
    try:
        APP.myp.remote_client.preconditioning(car.vin, bool(value))
    except Exception:  # pylint: disable=broad-except
        logger.exception("toggle_precond:")
        return html.Span("Couldn't reach the car. Try again in a moment.", className="mk-err")
    if value:
        return html.Span("Sent — the car should start preconditioning shortly.", className="mk-ok")
    return html.Span("Sent — preconditioning turned off.", className="mk-ok")


@dash_app.callback(
    Output("mokka-sched-status", "children"),
    Input("mokka-sched-save", "n_clicks"),
    State({"type": "mk-sched-on", "index": ALL}, "value"),
    State({"type": "mk-sched-hour", "index": ALL}, "value"),
    State({"type": "mk-sched-minute", "index": ALL}, "value"),
    State({"type": "mk-sched-days", "index": ALL}, "value"),
    prevent_initial_call=True)
def save_schedules(n_clicks, ons, hours, minutes, days):  # pylint: disable=unused-argument
    if not n_clicks:
        raise PreventUpdate()
    car = _get_car()
    if car is None:
        return html.Span("No car found.", className="mk-err")

    programs = {}
    for i, key in enumerate(PRECOND_PROGRAM_KEYS):
        selected = days[i] or []
        day = [1 if d in selected else 0 for d in range(7)]
        try:
            hour = int(hours[i]) if hours[i] not in (None, "") else 34
        except (ValueError, TypeError):
            hour = 34
        try:
            minute = int(minutes[i]) if minutes[i] not in (None, "") else 0
        except (ValueError, TypeError):
            minute = 0
        programs[key] = {"day": day, "hour": hour, "minute": minute, "on": 1 if ons[i] else 0}

    try:
        programs = validate_preconditioning_programs(programs)
    except (ValueError, TypeError) as error:
        return html.Span(str(error), className="mk-err")

    try:
        published = APP.myp.remote_client.set_preconditioning_program(car.vin, programs)
    except Exception:  # pylint: disable=broad-except
        logger.exception("save_schedules:")
        published = False

    if not published:
        return html.Span("Couldn't reach the car — schedules not sent.", className="mk-err")
    return html.Span("Update sent — applies next time the car is awake.", className="mk-ok")
