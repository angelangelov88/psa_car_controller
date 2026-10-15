"""The Home page, the only page besides settings and setup.

A single Dash screen that mirrors the Mokka Companion app inside psacc: car
status, a preconditioning on/off toggle, and the four weekly schedules. It is
wired to psacc's existing car functions (RemoteClient.preconditioning and
get/set_preconditioning_program) and reuses the shared Mokka theme in
assets/zz_mokka_theme.css. No new car logic lives here.
"""
import logging
import time

import dash_bootstrap_components as dbc
from dash import ALL, ctx, dcc, html, no_update
from dash.dependencies import Input, Output, State
from dash.exceptions import PreventUpdate

from psa_car_controller.common.utils import PRECOND_PROGRAM_KEYS, same_preconditioning_programs
from psa_car_controller.psa.constants import DEFAULT_PRECONDITIONING_PROGRAM
from psa_car_controller.psa.RemoteClient import SCHEDULE_CONFIRMED, SCHEDULE_FAILED
from psa_car_controller.psacc.application.car_controller import PSACarController
from psa_car_controller.web.app import dash_app
from psa_car_controller.web.view.schedule_form import HOUR_OPTIONS, UNSET_HOUR, minute_options, programs_from_form
from psa_car_controller.web.view.status_text import (MIN_PRECOND_BATTERY, battery_too_low_to_precondition,
                                                     car_state_text, charge_power_kw, charging_text, format_mileage,
                                                     format_power, format_rate, format_updated, map_embed_url,
                                                     map_link_url, position, precond_failure_text, precond_text)

logger = logging.getLogger(__name__)

APP = PSACarController()

DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SAVE_LABEL = "Save schedules"
SCHEDULE_POLL_MS = 3000
SCHEDULE_CONFIRM_TIMEOUT_S = 180


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


def _labelled(text, index, field):
    # The label wraps the dropdown, which names it for screen readers; dbc.Select takes no aria props.
    return html.Label(className="w-100", children=[
        html.Span(className="mk-mini-label d-block", children=[
            text, html.Span(f" for schedule {index + 1}", className="visually-hidden")]),
        field,
    ])


def _schedule_row(index, program):
    day = program.get("day", [0] * 7)
    hour = program.get("hour", 34)
    minute = program.get("minute", 0)
    on = program.get("on", 0)
    # hour 34 is the car's "unset" sentinel; show it as "Not set". Its minute means nothing, so show :00.
    hour_value = "" if hour == UNSET_HOUR else str(hour)
    if hour == UNSET_HOUR:
        minute = 0

    return html.Div(className="mk-sched-row", children=[
        html.Div(className="mk-sched-head", children=[
            html.Span(f"Schedule {index + 1}", className="mk-sched-title"),
            dbc.Switch(id={"type": "mk-sched-on", "index": index},
                       value=bool(on), label="Enabled", className="mk-sched-switch"),
        ]),
        dbc.Row(className="g-2 align-items-end", children=[
            dbc.Col(xs=6, md=3, children=[
                _labelled("Hour", index, dbc.Select(id={"type": "mk-sched-hour", "index": index},
                                                    options=HOUR_OPTIONS, value=hour_value)),
            ]),
            dbc.Col(xs=6, md=3, children=[
                _labelled("Minute", index, dbc.Select(id={"type": "mk-sched-minute", "index": index},
                                                      options=minute_options(minute), value=str(minute))),
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
    programs_known = False
    reported = None
    try:
        vin = car.vin if car else None
        programs = APP.myp.remote_client.get_preconditioning_program(vin) if vin \
            else DEFAULT_PRECONDITIONING_PROGRAM
        programs_known = bool(vin) and APP.myp.remote_client.has_preconditioning_program(vin)
        reported = APP.myp.remote_client.reported_preconditioning_program(vin) if vin else None
    except (AttributeError, TypeError):
        programs = DEFAULT_PRECONDITIONING_PROGRAM
    # What the car itself says beats what was last sent: a save it never confirmed would otherwise look applied.
    programs = reported or programs
    shown = {key: programs.get(key, DEFAULT_PRECONDITIONING_PROGRAM[key]) for key in PRECOND_PROGRAM_KEYS}

    schedule_rows = [_schedule_row(i, shown[key]) for i, key in enumerate(PRECOND_PROGRAM_KEYS)]

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
        html.Div("Saving sends all four to the car and waits for it to confirm. "
                 "Save turns on once something differs from what the car last reported.",
                 className="mokka-subtitle"),
        html.Div(
            "The car hasn't sent its schedules yet, so these may not match it. Saving replaces all four.",
            className="mk-err mb-2", role="note") if not programs_known else None,
        html.Div(schedule_rows),

        html.Div(className="mk-save-row", children=[
            html.Div(id="mokka-sched-status", className="mk-status", role="status"),
            dbc.Button(SAVE_LABEL, id="mokka-sched-save", color="primary",
                       disabled=same_preconditioning_programs(shown, reported)),
            dcc.Store(id="mokka-sched-pending"),
            dcc.Store(id="mokka-sched-car", data=reported),
            dcc.Interval(id="mokka-sched-poll", interval=SCHEDULE_POLL_MS, disabled=True),
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


def _sched_done(message, confirmed=None):
    """Outputs for a finished save: the message, the button label back and polling off. Once the car has confirmed,
    its programs are what it last reported, so the button stays off until the form changes again."""
    if confirmed is None:
        return html.Span(message, className="mk-err"), SAVE_LABEL, False, None, True, no_update
    return html.Span(message, className="mk-ok"), SAVE_LABEL, True, None, True, confirmed


def _check_delivery(pending):
    car = _get_car()
    if not pending or car is None:
        return _sched_done("No car found.")
    state = APP.myp.remote_client.schedule_delivery(car.vin, pending["programs"], pending["sent_at"])
    if state == SCHEDULE_CONFIRMED:
        return _sched_done("Done — the car has the new schedules.", pending["programs"])
    if state == SCHEDULE_FAILED:
        return _sched_done("The car couldn't apply the schedules. Try again in a moment.")
    if time.time() - pending["sent_at"] > SCHEDULE_CONFIRM_TIMEOUT_S:
        return _sched_done("No answer from the car after 3 minutes, so the change may not have reached it. "
                           "Try saving again later.")
    raise PreventUpdate()


@dash_app.callback(
    Output("mokka-sched-status", "children"),
    Output("mokka-sched-save", "children"),
    Output("mokka-sched-save", "disabled"),
    Output("mokka-sched-pending", "data"),
    Output("mokka-sched-poll", "disabled"),
    Output("mokka-sched-car", "data"),
    Input("mokka-sched-save", "n_clicks"),
    Input("mokka-sched-poll", "n_intervals"),
    State("mokka-sched-pending", "data"),
    State({"type": "mk-sched-on", "index": ALL}, "value"),
    State({"type": "mk-sched-hour", "index": ALL}, "value"),
    State({"type": "mk-sched-minute", "index": ALL}, "value"),
    State({"type": "mk-sched-days", "index": ALL}, "value"),
    prevent_initial_call=True)
def save_schedules(n_clicks, n_intervals, pending, ons, hours, minutes, days):  # pylint: disable=unused-argument
    """Sends the schedules, then checks every few seconds until the car reports them back, like the NFC tag's
    automation does after a preconditioning command."""
    # pylint: disable=too-many-arguments,too-many-positional-arguments  # Dash passes each input and state in turn
    if ctx.triggered_id == "mokka-sched-poll":
        return _check_delivery(pending)
    if not n_clicks:
        raise PreventUpdate()
    car = _get_car()
    if car is None:
        return _sched_done("No car found.")

    programs, problem = programs_from_form(ons, hours, minutes, days)
    if problem:
        return _sched_done(problem)

    sent_at = time.time()
    try:
        published = APP.myp.remote_client.set_preconditioning_program(car.vin, programs)
    except Exception:  # pylint: disable=broad-except
        logger.exception("save_schedules:")
        published = False

    if not published:
        return _sched_done("Couldn't reach the car — schedules not sent.")
    waiting_button = [html.Span(className="spinner-border spinner-border-sm me-2", **{"aria-hidden": "true"}),
                      "Waiting for the car…"]
    return (html.Span("Sent. Waiting for the car to confirm — usually under a minute."), waiting_button, True,
            {"sent_at": sent_at, "programs": programs}, False, no_update)


# Sending takes a few seconds, so the button changes in the browser straight away, which also stops a second click
# sending the schedules twice (the server rejects that as a duplicate). save_schedules' answer replaces it.
dash_app.clientside_callback(
    """function() {
        const spinner = {namespace: "dash_html_components", type: "Span",
                         props: {className: "spinner-border spinner-border-sm me-2", "aria-hidden": "true"}};
        return [true, [spinner, "Sending…"]];
    }""",
    Output("mokka-sched-save", "disabled", allow_duplicate=True),
    Output("mokka-sched-save", "children", allow_duplicate=True),
    Input("mokka-sched-save", "n_clicks"),
    prevent_initial_call=True)


@dash_app.callback(
    Output("mokka-sched-save", "disabled", allow_duplicate=True),
    Output("mokka-sched-status", "children", allow_duplicate=True),
    Input({"type": "mk-sched-on", "index": ALL}, "value"),
    Input({"type": "mk-sched-hour", "index": ALL}, "value"),
    Input({"type": "mk-sched-minute", "index": ALL}, "value"),
    Input({"type": "mk-sched-days", "index": ALL}, "value"),
    State("mokka-sched-car", "data"),
    State("mokka-sched-save", "children"),
    prevent_initial_call=True)
def update_save_button(ons, hours, minutes, days, reported, label):
    """Save is off while the form matches what the car last reported. A form that can't be sent stays clickable,
    so Save can say what to fix. The last save's message goes, as it's about the form before this change."""
    # pylint: disable=too-many-arguments,too-many-positional-arguments  # Dash passes each input and state in turn
    if label != SAVE_LABEL:
        raise PreventUpdate()  # the button shows "Sending…" or "Waiting for the car…" until the car answers
    programs, problem = programs_from_form(ons, hours, minutes, days)
    return not problem and same_preconditioning_programs(programs, reported), None
