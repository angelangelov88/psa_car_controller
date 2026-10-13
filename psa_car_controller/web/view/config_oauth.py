import logging

from dash import html, dcc, no_update
from dash.exceptions import PreventUpdate

from psa_car_controller.web.app import dash_app
import dash_bootstrap_components as dbc
from dash.dependencies import Output, Input, State

from psa_car_controller.web.view import config_views
from psa_car_controller.web.view.setup_flow import NotSignedIn

logger = logging.getLogger(__name__)


def get_oauth_config_layout(redirect_url):
    return html.Div(className="mokka-setup", children=[
        config_views.fake_mode_banner(),
        html.Div("Finish sign-in manually", className="mokka-title"),
        html.Div("Automatic sign-in didn't complete, so we'll do it by hand this once.",
                 className="mokka-subtitle"),
        html.Ol(className="mb-3", children=[
            html.Li(html.A("Open the Stellantis login page", href=redirect_url, target="_blank")),
            html.Li("Sign in there until you see 'LOGIN SUCCESSFUL'."),
            html.Li("Open your browser's DevTools (F12) and click the 'Network' tab."),
            html.Li("Hit the final 'OK' button under 'LOGIN SUCCESSFUL'."),
            html.Li("In the Network tab, find: xxxx://oauth2redirect…?code=<copy this part>&scope=openid…"),
        ]),
        html.P(html.A("More detail here",
                      href="https://github.com/flobz/psa_car_controller/discussions/779",
                      target="_blank")),
        dbc.Form([
            html.Div(className="mb-3", children=[
                dbc.Label("Login code", html_for="psa-oauth-code"),
                dbc.Input(type="text", id="psa-oauth-code", placeholder="Paste the code from step 5"),
                dbc.FormText(
                    "The code you copied above",
                    color="secondary",
                )]),
            dbc.Button(config_views.button_content("oauth", "Continue"),
                       color="primary", id="finish-oauth", className="w-100 mt-2", disabled=True),
            html.Div(id="oauth-result", className="mt-2"),
            dcc.Location(id="oauth-redirect", refresh=True),
        ])])


@dash_app.callback(
    Output("finish-oauth", "disabled"),
    Input("psa-oauth-code", "value"))
def toggle_finish_oauth(code):
    return not (code and code.strip())


@dash_app.callback(
    Output("oauth-result", "children"),
    Output("oauth-redirect", "href"),
    Input("finish-oauth", "n_clicks"),
    State("psa-oauth-code", "value"),
    running=config_views.running_state("oauth", "Continue", "Signing in…", ("psa-oauth-code", "finish-oauth")),
    prevent_initial_call=True)
def finish_oauth(n_clicks, code):  # pylint: disable=unused-argument
    if not code:
        raise PreventUpdate()
    try:
        config_views.SETUP_FLOW.finish_manual_sign_in(code.strip())
    except NotSignedIn:
        return dbc.Alert("Start again from step 1: sign in on the User config page.", color="danger"), no_update
    except Exception:
        logger.exception("finish_oauth:")
        return dbc.Alert("That login code wasn't accepted. Codes only work once and expire quickly, "
                         "so go through the login page again and copy the new code.", color="danger"), no_update
    # Signed in: move straight on to the code step.
    return dbc.Alert("Signed in.", color="success"), dash_app.get_relative_path("/config_otp")
