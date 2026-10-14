import unittest
from unittest.mock import patch

from psa_car_controller.psa.setup.headless_oauth import get_code

REDIRECT = "mymvxsdk://oauth2redirect/gb?code=the-code&scope=openid%20profile&state=s"


class FakeRequest:
    def __init__(self, url):
        self.url = url


class FakeConsentPage:
    """The "Log in complete, ready to explore?" page: one CONTINUE button that leads to the app redirect."""

    def __init__(self, button_selector):
        self.button_selector = button_selector
        self.on_request = None
        self.clicked = []

    def on(self, event, handler):
        if event == "request":
            self.on_request = handler

    def is_visible(self, selector):
        return selector == self.button_selector

    def click(self, selector):
        self.clicked.append(selector)
        self.on_request(FakeRequest(REDIRECT))


@patch("psa_car_controller.psa.setup.headless_oauth.time.sleep")
class TestHeadlessOAuth(unittest.TestCase):

    def test_clicks_continue_on_the_log_in_complete_page(self, _sleep):
        page = FakeConsentPage("#consentbutton")
        self.assertEqual(get_code(page, "mymvxsdk", "user@example.com", "pw"), "the-code")
        self.assertEqual(page.clicked, ["#consentbutton"])

    def test_gives_up_when_no_known_button_appears(self, _sleep):
        page = FakeConsentPage("#some-new-button")
        with patch("psa_car_controller.psa.setup.headless_oauth.time.time", side_effect=[0, 0, 1000]):
            with self.assertRaises(RuntimeError):
                get_code(page, "mymvxsdk", "user@example.com", "pw")
        self.assertEqual(page.clicked, [])
