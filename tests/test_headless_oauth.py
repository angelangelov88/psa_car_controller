import unittest
from unittest.mock import patch

from psa_car_controller.psa.setup.headless_oauth import IDLE_LIMIT_S, MAX_WAIT_S, get_code

REDIRECT = "mymvxsdk://oauth2redirect/gb?code=the-code&scope=openid%20profile&state=s"


class FakeRequest:
    def __init__(self, url):
        self.url = url


class FakeClock:
    """Stands in for time.time and time.sleep: each sleep moves the clock on by that many seconds."""

    def __init__(self):
        self.now = 0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeConsentPage:
    """The "Log in complete, ready to explore?" page.

    It keeps loading scripts (one response per second) for `load_time` seconds, then shows its CONTINUE button,
    which leads to the app redirect.
    """

    def __init__(self, clock, button_selector="#consentbutton", load_time=0, loading_for=None):
        self.clock = clock
        self.button_selector = button_selector
        self.load_time = load_time
        self.loading_for = load_time if loading_for is None else loading_for
        self.handlers = {}
        self.clicked = []

    def on(self, event, handler):
        self.handlers[event] = handler

    def is_visible(self, selector):
        if self.clock.now < self.loading_for:
            self.handlers["response"](None)
        return selector == self.button_selector and self.clock.now >= self.load_time

    def click(self, selector):
        self.clicked.append(selector)
        self.handlers["request"](FakeRequest(REDIRECT))


class TestHeadlessOAuth(unittest.TestCase):

    def setUp(self):
        self.clock = FakeClock()
        for name in ("time", "sleep"):
            patcher = patch(f"psa_car_controller.psa.setup.headless_oauth.time.{name}", getattr(self.clock, name))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_clicks_continue_on_the_log_in_complete_page(self):
        page = FakeConsentPage(self.clock)
        self.assertEqual(get_code(page, "mymvxsdk", "user@example.com", "pw"), "the-code")
        self.assertEqual(page.clicked, ["#consentbutton"])

    def test_waits_for_a_slow_page_while_it_is_still_loading(self):
        page = FakeConsentPage(self.clock, load_time=IDLE_LIMIT_S * 2)
        self.assertEqual(get_code(page, "mymvxsdk", "user@example.com", "pw"), "the-code")

    def test_gives_up_when_the_page_goes_quiet(self):
        page = FakeConsentPage(self.clock, load_time=MAX_WAIT_S, loading_for=10)
        with self.assertRaises(RuntimeError):
            get_code(page, "mymvxsdk", "user@example.com", "pw")
        self.assertEqual(page.clicked, [])
        self.assertLess(self.clock.now, 10 + IDLE_LIMIT_S + 2)

    def test_gives_up_after_the_overall_limit_even_if_still_loading(self):
        page = FakeConsentPage(self.clock, load_time=MAX_WAIT_S * 2)
        with self.assertRaises(RuntimeError):
            get_code(page, "mymvxsdk", "user@example.com", "pw")
        self.assertLess(self.clock.now, MAX_WAIT_S + 2)
