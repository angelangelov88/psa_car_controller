from urllib.parse import parse_qs, urlparse

import dash_bootstrap_components as dbc
from dash import dcc, html
from dash.dependencies import Output, Input

from psa_car_controller.common.mylogger import CustomLogger
from psa_car_controller.psacc.application.car_controller import PSACarController

from psa_car_controller.psacc.application.charging import Charging

from psa_car_controller.web.app import dash_app
from psa_car_controller.web.view.config_oauth import get_oauth_config_layout
from psa_car_controller.web.view.config_views import log_layout, config_layout, setup_links, SETUP_FLOW
from psa_car_controller.web.view.setup_steps import DONE, GET_CODE, SIGN_IN

# pylint: disable=invalid-name
from psa_car_controller.web.view.home import get_home_layout

from psa_car_controller.web.view import api  # pylint: disable=unused-import

logger = CustomLogger.getLogger(__name__)

APP = PSACarController()
GITHUB_URL = "https://github.com/angelangelov88/psa_car_controller"


def icon_with_label(icon, label):
    """A Bootstrap icon with its label kept for screen readers only."""
    return [html.I(className=f"bi {icon}", **{"aria-hidden": "true"}), html.Span(label, className="visually-hidden")]


def add_header(el, pathname):
    """GitHub and settings icons on the right; on every page but home, a link back to it on the left."""
    back = None
    if pathname != "/":
        back = dcc.Link([html.I(className="bi bi-arrow-left me-2", **{"aria-hidden": "true"}), "Home"],
                        href=dash_app.get_relative_path("/"), className="mk-back")
    github = html.A(icon_with_label("bi-github", "Source code on GitHub (opens in a new tab)"),
                    href=GITHUB_URL, target="_blank", rel="noopener noreferrer", className="mk-icon-link")
    settings = dcc.Link(icon_with_label("bi-gear-fill", "Settings"),
                        href=dash_app.get_relative_path("/config"), className="mk-icon-link")
    return dbc.Row([dbc.Col(back),
                    dbc.Col(html.Div([github, settings],
                                     className="d-flex align-items-center justify-content-end gap-2"))],
                   className="align-items-center pt-2"), el


@dash_app.callback(Output('page-content', 'children'),
                   [Input('url', 'pathname'),
                    Input('url', 'search')])
def display_page(pathname, search):
    prefix = dash_app.config.requests_pathname_prefix or "/"
    pathname = pathname[len(prefix) - 1:]
    query_params = parse_qs(urlparse(search).query)
    no_header = query_params.get("header", None) == ["false"]
    setup_step = SETUP_FLOW.current_step()
    if pathname == "/config_connect":
        page = get_oauth_config_layout(query_params["url"][0])
    elif pathname == "/log":
        page = log_layout()  # also before sign-in, to see why it failed
        if setup_step != DONE or not APP.is_good:
            page = html.Div([page, setup_links(("Back to setup", "/config"))])
    elif setup_step == SIGN_IN or pathname == "/config_login":
        page = config_layout(SIGN_IN)
    elif setup_step == GET_CODE or pathname == "/config_otp":
        page = config_layout(GET_CODE)  # setup isn't finished: every page opens at the code step
    elif not APP.is_good:
        # Only in fake mode: the setup screens say done, but the app has no real config.
        page = config_layout(SIGN_IN)
    elif pathname == "/config":
        page = config_layout()
    else:
        page = get_home_layout()
    # Until setup is finished there's no home or settings to link to, so the setup pages go without the header.
    if no_header or setup_step != DONE or not APP.is_good:
        return page
    return add_header(page, pathname)


try:
    if APP.is_good:
        Charging.set_default_price(APP.myp.vehicles_list)
except (IndexError, TypeError, AttributeError):
    logger.error("Failed to set the default charge prices:", exc_info=True)

dash_app.layout = dbc.Container(fluid=True, children=[dcc.Location(id='url', refresh=False),
                                                      html.Div(id='page-content')],
                                style={"height": "100vh"})
