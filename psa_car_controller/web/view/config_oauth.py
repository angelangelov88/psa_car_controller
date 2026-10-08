import logging

from dash import callback_context, html, dcc
from dash.exceptions import PreventUpdate

from psa_car_controller.web.app import dash_app
import dash_bootstrap_components as dbc
from dash.dependencies import Output, Input, State

from psa_car_controller.web.view import config_views

logger = logging.getLogger(__name__)


def get_oauth_config_layout(redirect_url):
    return html.Div(className="mokka-setup", children=[
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
            dbc.Button("Continue", color="primary", id="finish-oauth", className="w-100 mt-2"),
            dcc.Loading(
                id="loading-2",
                children=[html.Div([html.Div(id="oauth-result")])],
                type="circle",
            ),
        ])])


@dash_app.callback(
    Output("oauth-result", "children"),
    Input("finish-oauth", "n_clicks"),
    State("psa-oauth-code", "value"))
def finish_oauth(n_clicks, code):  # pylint: disable=unused-argument
    ctx = callback_context
    if ctx.triggered:
        try:
            config_views.INITIAL_SETUP.connect(code)
            return dbc.Alert(["PSA login finish !", html.A(" Go to otp config",
                             href=dash_app.config.requests_pathname_prefix + "config_otp")], color="success")
        except Exception as e:
            logger.exception("finish_oauth:")
            return dbc.Alert(str(e), color="danger")
    raise PreventUpdate()
