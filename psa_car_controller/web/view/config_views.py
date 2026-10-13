import logging
import time
from urllib import parse

from dash import html, dcc, no_update
from dash.exceptions import PreventUpdate

from psa_car_controller.psa.RemoteClient import SmsRequestFailed
from psa_car_controller.psa.setup.app_decoder import SignInRejected
from psa_car_controller.common.mylogger import LOG_FILE
from psa_car_controller.web.app import dash_app
from psa_car_controller.web.view.otp_step import (CODE_REJECTED, FINISH_FAILED, NOT_SIGNED_IN, SEND_LABEL, SMS_HINT,
                                                  is_code_ready, sms_button_state, sms_failed_message)
from psa_car_controller.web.view.setup_flow import CodeRejected, ManualSignInNeeded, NotSignedIn, create_setup_flow
from psa_car_controller.web.view.sign_in import (BRANDS, COUNTRIES, SIGN_IN_FAILED, SIGN_IN_HINT, is_sign_in_ready,
                                                 sign_in_rejected_message)
import dash_bootstrap_components as dbc
from dash.dependencies import Output, Input, State

logger = logging.getLogger(__name__)

SETUP_FLOW = create_setup_flow()


def fake_mode_banner():
    if not SETUP_FLOW.fake:
        return None
    return dbc.Alert("Fake mode: nothing is sent to Stellantis and no SMS is used. "
                     "Try noaccount@, manual@ or error@ emails, or the code 000000, to see the errors.",
                     color="warning")


def button_content(prefix, label):
    """A button's label with a spinner in front that stays hidden until the action runs."""
    return [html.Span(id=f"{prefix}-spinner", className="d-none"), html.Span(label, id=f"{prefix}-label")]


setup_config_layout = html.Div(className="mokka-setup", children=[
    fake_mode_banner(),
    html.Div("Connect your car", className="mokka-title"),
    html.Div("Step 1 of 2 — sign in with your Vauxhall/Stellantis account. "
             "These details stay on your psacc server and are never sent anywhere else.",
             className="mokka-subtitle"),
    dbc.Form([
        html.Div(className="mb-3", children=[
            dbc.Label("Car brand", html_for="psa-app"),
            dcc.Dropdown(
                id="psa-app",
                options=[{"label": name, "value": package} for package, name in BRANDS],
                placeholder="Select your brand",
            )]),
        html.Div(className="mb-3", children=[
            dbc.Label("Email", html_for="psa-email"),
            dbc.Input(type="email", id="psa-email", placeholder="you@example.com"),
            dbc.FormText(
                "The email for your car account",
                color="secondary",
            )]),
        html.Div(className="mb-3", children=[
            dbc.Label("Password", html_for="psa-password"),
            dbc.Input(
                type="password",
                id="psa-password",
                placeholder="Account password",
            ),
            dbc.FormText(
                "The password for your car account",
                color="secondary",
            )]),
        html.Div(className="mb-3", children=[
            dbc.Label("Country", html_for="psa-countrycode"),
            dcc.Dropdown(
                id="psa-countrycode",
                options=[{"label": f"{name} ({code})", "value": code} for code, name in COUNTRIES],
                placeholder="Select your country",
            ),
            dbc.FormText(
                "The country your car account is registered in",
                color="secondary",
            )]),
        dbc.Button(button_content("submit", "Sign in"),
                   color="primary", id="submit-form", className="w-100 mt-2", disabled=True),
        dbc.FormText(
            SIGN_IN_HINT,
            id="submit-hint",
            color="secondary",
            className="d-block mt-2"),
        html.Div(id="form_result", className="mt-2"),
    ])])

config_otp_layout = html.Div(className="mokka-setup", children=[
    fake_mode_banner(),
    html.Div("Verify it's you", className="mokka-title"),
    html.Div("Step 2 of 2 — we'll text a code to the phone on your account. "
             "Enter it with your account PIN to finish.",
             className="mokka-subtitle"),
    dbc.Form([
        html.Div(className="mb-3", children=[
            dbc.Button(button_content("sms", SEND_LABEL), color="info", id="ask-sms", className="w-100"),
            dbc.FormText(SMS_HINT, color="secondary", className="d-block mt-2"),
            html.Div(id="sms-demand-result", className="mt-2"),
            dcc.Store(id="sms-sent-at"),
            dcc.Interval(id="sms-cooldown", interval=1000, disabled=True),
        ]),
        html.Div(className="mb-3", children=[
            dbc.Label("SMS code", html_for="psa-code"),
            dbc.Input(type="text", id="psa-code", placeholder="Code from the text message",
                      inputmode="numeric", autocomplete="one-time-code"),
            dbc.FormText(
                "Usually 6 digits",
                color="secondary",
            )]),
        html.Div(className="mb-3", children=[
            dbc.Label("Account PIN", html_for="psa-pin"),
            dbc.Input(
                type="password",
                id="psa-pin",
                placeholder="Your numeric PIN",
                inputmode="numeric",
                maxLength=4,
            ),
            dbc.FormText(
                "The 4-digit PIN you set in your car brand's app",
                color="secondary",
            )]),
        dbc.Button(button_content("finish", "Finish setup"),
                   color="primary", id="finish-otp", className="w-100 mt-2", disabled=True),
        html.Div(id="opt-result", className="mt-2"),
        dcc.Location(id="otp-redirect-url", refresh=True),
        dcc.Interval(id="otp-redirect-interval", interval=5000, n_intervals=0, disabled=True),
    ])])


def log_layout():
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            log_text = f.read()
    except Exception as e:
        log_text = f"Can't read log file: {e}"
    return html.H3(className="m-2", children=["Log:", dbc.Container(
        fluid=True,
        style={"height": "80vh",
               "overflow": "auto",
               "display": "flex",
               "flex-direction": "column-reverse",
               "white-space": "pre-line"},
        children=log_text,
        className="m-3 bg-light h5"),
        html.Div(id="empty-div")])


def config_layout(activeTabs="log"):
    return dbc.Tabs(id="config-tabs", active_tab=activeTabs, children=[
        dbc.Tab([log_layout()], label="Log", tab_id="log"),
        dbc.Tab([setup_config_layout], label="User config", tab_id="login"),
        dbc.Tab([config_otp_layout], label="OTP config", tab_id="otp")])


@dash_app.callback(
    Output("submit-form", "disabled"),
    Input("psa-app", "value"),
    Input("psa-email", "value"),
    Input("psa-password", "value"),
    Input("psa-countrycode", "value"))
def toggle_sign_in(app_name, email, password, countrycode):
    return not is_sign_in_ready(app_name, email, password, countrycode)


def running_state(prefix, label, running_label, fields):
    """While an action runs, lock its fields and button and show a spinner on the button."""
    return [(Output(field, "disabled"), True, False) for field in fields] + [
        (Output(f"{prefix}-spinner", "className"), "spinner-border spinner-border-sm me-2", "d-none"),
        (Output(f"{prefix}-label", "children"), running_label, label),
    ]


# While signing in, lock the whole form so it can't be changed or sent twice.
SIGN_IN_RUNNING = running_state(
    "submit", "Sign in", "Signing in…",
    ("psa-app", "psa-email", "psa-password", "psa-countrycode", "submit-form")) + [
    (Output("submit-hint", "children"), "Signing in to your car account. This can take up to a minute, "
                                        "please keep this page open.", SIGN_IN_HINT),
]


def manual_sign_in_alert(error: ManualSignInNeeded):
    login_link = html.A(
        "Go to login",
        href=f"{dash_app.config.requests_pathname_prefix}config_connect?url={parse.quote(error.auth_url)}")
    debug = error.headless_error
    if debug is None:
        return dbc.Alert(["Automatic login failed. Please complete manually: ", login_link], color="warning")
    debug_style = {"font-family": "monospace", "font-size": "12px", "margin-bottom": "10px"}
    return dbc.Alert(
        [
            html.P("Automatic login failed. Please complete manually: "),
            login_link,
            html.Hr(),
            html.P("Debug information (please include in GitHub issue):"),
            dbc.Label("Last URL:"),
            dbc.Input(value=debug.url, readonly=True, style={"margin-bottom": "10px"}),
            dbc.Label("Console Logs:"),
            dbc.Textarea(value="\n".join(debug.logs), style={"height": "100px", **debug_style}, readonly=True),
            dbc.Label("HTML Content:"),
            dbc.Textarea(value=debug.html, style={"height": "200px", **debug_style}, readonly=True),
        ],
        color="warning")


@dash_app.callback(
    Output("form_result", "children"),
    Output("config-tabs", "active_tab"),
    Input("submit-form", "n_clicks"),
    State("psa-app", "value"),
    State("psa-email", "value"),
    State("psa-password", "value"),
    State("psa-countrycode", "value"),
    running=SIGN_IN_RUNNING,
    prevent_initial_call=True)
def connectPSA(n_clicks, app_name, email, password, countrycode):  # pylint: disable=unused-argument
    logger.info("Initial setup...")
    try:
        SETUP_FLOW.sign_in(app_name, email.strip(), password, countrycode)
    except SignInRejected as e:
        logger.warning("Sign-in rejected by the account service: %s", e.return_code)
        return dbc.Alert(sign_in_rejected_message(e.return_code, app_name, countrycode), color="danger"), no_update
    except ManualSignInNeeded as e:
        return manual_sign_in_alert(e), no_update
    except Exception as e:
        logger.exception(e)
        return dbc.Alert(SIGN_IN_FAILED, color="danger"), no_update
    # Signed in: move straight on to the code step.
    return dbc.Alert("Signed in.", color="success"), "otp"


@dash_app.callback(
    Output("sms-demand-result", "children"),
    Output("sms-sent-at", "data"),
    Input("ask-sms", "n_clicks"),
    running=running_state("sms", SEND_LABEL, "Sending…", ("ask-sms",)),
    prevent_initial_call=True)
def askCode(n_clicks):  # pylint: disable=unused-argument
    try:
        SETUP_FLOW.send_sms()
    except NotSignedIn:
        return dbc.Alert(NOT_SIGNED_IN, color="danger"), no_update
    except SmsRequestFailed as e:
        logger.warning("SMS code request failed: HTTP %s", e.status_code)
        return dbc.Alert(sms_failed_message(e.status_code), color="danger"), no_update
    except Exception:
        logger.exception("askCode:")
        return dbc.Alert(sms_failed_message(None), color="danger"), no_update
    sent_at = time.time()
    return dbc.Alert(f"Code sent at {time.strftime('%H:%M', time.localtime(sent_at))}. "
                     "It can take a minute to arrive.", color="success"), sent_at


# After a code is sent, count down before another can be asked for.
@dash_app.callback(
    Output("ask-sms", "disabled"),
    Output("sms-label", "children"),
    Output("sms-cooldown", "disabled"),
    Input("sms-cooldown", "n_intervals"),
    Input("sms-sent-at", "data"))
def sms_cooldown(n_intervals, sent_at):  # pylint: disable=unused-argument
    disabled, label, counting = sms_button_state(sent_at, time.time())
    return disabled, label, not counting


@dash_app.callback(
    Output("finish-otp", "disabled"),
    Input("psa-code", "value"),
    Input("psa-pin", "value"))
def toggle_finish(sms_code, code_pin):
    return not is_code_ready(sms_code, code_pin)


@dash_app.callback(
    [Output("opt-result", "children"),
     Output("otp-redirect-interval", "disabled")],
    Input("finish-otp", "n_clicks"),
    State("psa-pin", "value"),
    State("psa-code", "value"),
    running=running_state("finish", "Finish setup", "Finishing setup…", ("psa-code", "psa-pin", "finish-otp")),
    prevent_initial_call=True)
def finishOtp(n_clicks, code_pin, sms_code):  # pylint: disable=unused-argument
    try:
        SETUP_FLOW.finish(sms_code.strip(), code_pin.strip())
    except NotSignedIn:
        return dbc.Alert(NOT_SIGNED_IN, color="danger"), True
    except CodeRejected:
        return dbc.Alert(CODE_REJECTED, color="danger"), True
    except Exception:
        logger.exception("finishOtp:")
        return dbc.Alert(FINISH_FAILED, color="danger"), True
    home = dash_app.get_relative_path("/")
    saved = " (Fake mode: nothing was saved.)" if SETUP_FLOW.fake else ""
    alert = dbc.Alert(
        [f"Setup finished!{saved} Redirecting to home in 5 seconds… ",
         html.A("Go to home now", href=home)], color="success")
    return alert, False  # enable the interval -> auto-redirect


@dash_app.callback(
    Output("otp-redirect-url", "href"),
    Input("otp-redirect-interval", "n_intervals"))
def otp_redirect_home(n_intervals):
    if n_intervals and n_intervals > 0:
        return dash_app.get_relative_path("/")
    raise PreventUpdate()
