"""The three setup steps (sign in, text me a code, finish with code + PIN) behind one interface.

The web views talk to SETUP_FLOW and never to the PSA services directly, so the screens can be
tried out in fake mode: start psacc with PSACC_FAKE_SETUP=1 and nothing is sent to Stellantis,
no SMS is used up and no config is written. In fake mode:
- an email starting with "noaccount@" gets the "no account found" error,
- "manual@" falls back to the manual sign-in page (any login code works there),
- "error@" fails unexpectedly,
- the SMS code 000000 is rejected,
- anything else succeeds after a short wait.
"""
import logging
import os
import time

from psa_car_controller.psa.otp.otp import CONFIG_NAME as OTP_CONFIG_NAME, new_otp_session
from psa_car_controller.psa.setup.app_decoder import InitialSetup, SignInRejected
from psa_car_controller.psa.setup.headless_oauth import HeadlessOAuthError, get_oauth_code_headless
from psa_car_controller.psacc.application.car_controller import PSACarController
from psa_car_controller.web.view.setup_steps import DONE, GET_CODE, SIGN_IN

logger = logging.getLogger(__name__)

FAKE_SETUP_ENV = "PSACC_FAKE_SETUP"


class ManualSignInNeeded(Exception):
    """Automatic sign-in didn't finish; the user completes it at auth_url and pastes the code back.
    headless_error has the debug details when the headless browser itself gave up."""

    def __init__(self, auth_url, headless_error=None):
        super().__init__("Automatic sign-in failed")
        self.auth_url = auth_url
        self.headless_error = headless_error


class NotSignedIn(Exception):
    """A code step was tried before step 1 (sign in) succeeded."""


class CodeRejected(Exception):
    """The SMS code or PIN wasn't accepted."""


class RealSetupFlow:
    fake = False

    def __init__(self):
        self.initial_setup: InitialSetup = None

    @staticmethod
    def current_step():
        app = PSACarController()
        if not app.is_good or getattr(app, "myp", None) is None:
            return SIGN_IN
        # new_otp_session writes otp.bin once the code and PIN are accepted.
        return DONE if os.path.isfile(OTP_CONFIG_NAME) else GET_CODE

    def sign_in(self, app_name, email, password, countrycode):
        self.initial_setup = InitialSetup(app_name, email, password, countrycode)
        auth_url = self.initial_setup.psacc.manager.generate_redirect_url()
        scheme = self.initial_setup.psacc.manager.redirect_uri.split("://")[0]
        try:
            code = get_oauth_code_headless(auth_url, email, password, scheme)
            self.initial_setup.connect(code)
        except HeadlessOAuthError as e:
            raise ManualSignInNeeded(auth_url, e) from e
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Headless OAuth failed (%s), falling back to manual flow", e)
            raise ManualSignInNeeded(auth_url) from e

    def finish_manual_sign_in(self, code):
        if self.initial_setup is None:
            raise NotSignedIn()
        self.initial_setup.connect(code)

    @staticmethod
    def _signed_in_app():
        app = PSACarController()
        if getattr(app, "myp", None) is None:
            raise NotSignedIn()
        return app

    def send_sms(self):
        self._signed_in_app().myp.remote_client.get_sms_otp_code()

    def finish(self, sms_code, pin):
        app = self._signed_in_app()
        try:
            otp_session = new_otp_session(sms_code, pin, app.myp.remote_client.otp)
        except Exception as e:
            logger.exception("finishOtp:")
            raise CodeRejected() from e
        app.myp.remote_client.otp = otp_session
        # A remote token made with the previous code and PIN may no longer be accepted: drop it, so remote control
        # makes a new one from this session.
        app.myp.remote_client.remoteCredentials.refresh_token = None
        app.myp.save_config()
        app.start_remote_control()


class FakeSetupFlow:
    """Walks through the setup screens without contacting Stellantis. See the module docstring."""
    fake = True
    DELAY_IN_S = 2

    def __init__(self):
        self.signed_in = False
        self.finished = False

    def current_step(self):
        if not self.signed_in:
            return SIGN_IN
        return DONE if self.finished else GET_CODE

    def sign_in(self, app_name, email, password, countrycode):  # pylint: disable=unused-argument
        time.sleep(self.DELAY_IN_S)
        if email.startswith("noaccount@"):
            raise SignInRejected("NEED_CREATION")
        if email.startswith("manual@"):
            raise ManualSignInNeeded("https://example.invalid/fake-stellantis-login")
        if email.startswith("error@"):
            raise ConnectionError("Fake unexpected failure")
        self.signed_in = True

    def finish_manual_sign_in(self, code):  # pylint: disable=unused-argument
        time.sleep(self.DELAY_IN_S)
        self.signed_in = True

    def send_sms(self):
        if not self.signed_in:
            raise NotSignedIn()
        time.sleep(self.DELAY_IN_S)

    def finish(self, sms_code, pin):  # pylint: disable=unused-argument
        if not self.signed_in:
            raise NotSignedIn()
        time.sleep(self.DELAY_IN_S)
        if sms_code == "000000":
            raise CodeRejected()
        self.finished = True


def create_setup_flow():
    if os.environ.get(FAKE_SETUP_ENV) == "1":
        logger.warning("Fake setup mode: the setup screens won't contact Stellantis")
        return FakeSetupFlow()
    return RealSetupFlow()
