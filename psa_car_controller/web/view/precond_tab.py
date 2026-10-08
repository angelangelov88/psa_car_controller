"""Mokka-style preconditioning tab.

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

logger = logging.getLogger(__name__)

APP = PSACarController()

DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _get_car():
    vehicles = APP.myp.vehicles_list
    return vehicles[0] if vehicles else None


def _status_value(getter, default="—"):
    """Read a car-status field, returning a dash placeholder on any gap."""
    try:
        value = getter()
        return "—" if value is None else value
    except (AttributeError, TypeError, KeyError, IndexError):
        return default


def _stat_tile(label, value):
    return dbc.Col(html.Div(className="mk-tile", children=[
        html.Div(label, className="mk-tile-label"),
        html.Div(value, className="mk-tile-value"),
    ]), xs=6, md=3)


def _status_cards(car):
    if car is None or car.status is None:
        return html.Div("Car status isn't available yet.", className="mokka-subtitle")

    battery = _status_value(lambda: car.status.get_energy('Electric').level)
    mileage = _status_value(lambda: round(car.status.timed_odometer.mileage))
    precond = _status_value(
        lambda: "On" if car.status.preconditionning.air_conditioning.status != "Disabled" else "Off")
    charging = _status_value(
        lambda: "Charging" if car.status.get_energy('Electric').charging.status == "InProgress" else "Not charging")

    return dbc.Row(className="g-2 mb-3", children=[
        _stat_tile("Battery", f"{battery}%" if battery != "—" else "—"),
        _stat_tile("Mileage", f"{mileage} km" if mileage != "—" else "—"),
        _stat_tile("Preconditioning", precond),
        _stat_tile("Charging", charging),
    ])


def _precond_on_state(car):
    try:
        return car.status.preconditionning.air_conditioning.status != "Disabled"
    except (AttributeError, TypeError):
        return False


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


def get_precond_layout():
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

    return html.Div(className="mokka-panel", children=[
        html.Div("Preconditioning", className="mokka-title"),
        html.Div("Warm or cool the car, and set when it should do it automatically.",
                 className="mokka-subtitle"),

        _status_cards(car),

        html.Div(className="mk-toggle-card", children=[
            dbc.Switch(id="mokka-precond-toggle", value=_precond_on_state(car),
                       label="Precondition now", className="mk-big-switch"),
            html.Div(
                "Turning this on wakes the car and starts climate control straight away.",
                className="mokka-subtitle mb-0"),
            html.Div(id="mokka-precond-status", className="mk-status"),
        ]),

        html.Div(className="mk-section-title", children="Weekly schedules"),
        html.Div("Changes apply the next time the car is awake — not always straight away.",
                 className="mokka-subtitle"),
        html.Div(schedule_rows),

        html.Div(className="mk-save-row", children=[
            html.Div(id="mokka-sched-status", className="mk-status"),
            dbc.Button("Save schedules", id="mokka-sched-save", color="primary"),
        ]),
    ])


@dash_app.callback(
    Output("mokka-precond-status", "children"),
    Input("mokka-precond-toggle", "value"),
    prevent_initial_call=True)
def toggle_precond(value):
    car = _get_car()
    if car is None:
        return html.Span("No car found.", className="mk-err")
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
