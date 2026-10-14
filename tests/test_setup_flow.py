import unittest
from unittest.mock import MagicMock, patch

from psa_car_controller.psa.setup.app_decoder import SignInRejected
from psa_car_controller.web.view.otp_step import (RESEND_LABEL, SEND_LABEL, clean_code, is_code_ready, sms_button_state,
                                                  sms_failed_message)
from psa_car_controller.web.view.setup_flow import (FAKE_SETUP_ENV, CodeRejected, FakeSetupFlow, ManualSignInNeeded,
                                                    NotSignedIn, RealSetupFlow, create_setup_flow)
from psa_car_controller.web.view.setup_steps import DONE, GET_CODE, SIGN_IN, step_states

VAUXHALL = "com.psa.mym.myvauxhall"


def fake_flow():
    flow = FakeSetupFlow()
    flow.DELAY_IN_S = 0
    return flow


class TestCodeStep(unittest.TestCase):

    def test_code_must_be_digits_and_pin_four_digits(self):
        self.assertTrue(is_code_ready(" 123456 ", "1234"))
        self.assertTrue(is_code_ready("123456789", "1234"))
        self.assertTrue(is_code_ready("123 456 789", "1234"))
        self.assertEqual(clean_code(" 123 456 789 "), "123456789")
        self.assertFalse(is_code_ready("12a456", "1234"))
        self.assertFalse(is_code_ready("123456", ""))
        self.assertFalse(is_code_ready("123456", "123"))
        self.assertFalse(is_code_ready("123456", "12345"))
        self.assertFalse(is_code_ready(None, "1234"))

    def test_send_button_before_any_code(self):
        self.assertEqual((False, SEND_LABEL, False), sms_button_state(None, 1000))

    def test_send_button_counts_down_after_a_code(self):
        self.assertEqual((True, "Send a new code in 45 s", True), sms_button_state(1000, 1015))
        self.assertEqual((True, "Send a new code in 1 s", True), sms_button_state(1000, 1059.5))

    def test_send_button_comes_back_after_the_wait(self):
        self.assertEqual((False, RESEND_LABEL, False), sms_button_state(1000, 1060))

    def test_sms_failure_messages(self):
        self.assertIn("Too many codes", sms_failed_message(429))
        self.assertIn("Sign in again", sms_failed_message(401))
        self.assertIn("Try again in a minute", sms_failed_message(500))


class TestSetupSteps(unittest.TestCase):

    def test_first_step_is_current(self):
        self.assertEqual([("Sign in", "current"), ("Get a code", "todo"), ("Done", "todo")], step_states(SIGN_IN))

    def test_signed_in_ticks_the_first_step(self):
        self.assertEqual([("Sign in", "done"), ("Get a code", "current"), ("Done", "todo")], step_states(GET_CODE))

    def test_finished_ticks_every_step(self):
        self.assertEqual([("Sign in", "done"), ("Get a code", "done"), ("Done", "done")], step_states(DONE))


class TestFakeSetupFlow(unittest.TestCase):

    def test_chosen_by_env(self):
        with patch.dict("os.environ", {FAKE_SETUP_ENV: "1"}):
            self.assertIsInstance(create_setup_flow(), FakeSetupFlow)
        with patch.dict("os.environ", {FAKE_SETUP_ENV: ""}):
            self.assertIsInstance(create_setup_flow(), RealSetupFlow)

    def test_happy_path(self):
        flow = fake_flow()
        flow.sign_in(VAUXHALL, "me@example.com", "password", "GB")
        self.assertTrue(flow.signed_in)
        flow.send_sms()
        flow.finish("123456", "1234")

    def test_current_step_follows_the_flow(self):
        flow = fake_flow()
        self.assertEqual(SIGN_IN, flow.current_step())
        flow.sign_in(VAUXHALL, "me@example.com", "password", "GB")
        self.assertEqual(GET_CODE, flow.current_step())
        with self.assertRaises(CodeRejected):
            flow.finish("000000", "1234")
        self.assertEqual(GET_CODE, flow.current_step())
        flow.finish("123456", "1234")
        self.assertEqual(DONE, flow.current_step())

    def test_code_step_needs_sign_in(self):
        flow = fake_flow()
        with self.assertRaises(NotSignedIn):
            flow.send_sms()
        with self.assertRaises(NotSignedIn):
            flow.finish("123456", "1234")

    def test_error_emails(self):
        flow = fake_flow()
        with self.assertRaises(SignInRejected):
            flow.sign_in(VAUXHALL, "noaccount@example.com", "password", "GB")
        with self.assertRaises(ManualSignInNeeded):
            flow.sign_in(VAUXHALL, "manual@example.com", "password", "GB")
        with self.assertRaises(ConnectionError):
            flow.sign_in(VAUXHALL, "error@example.com", "password", "GB")
        self.assertFalse(flow.signed_in)

    def test_manual_sign_in(self):
        flow = fake_flow()
        flow.finish_manual_sign_in("any-code")
        self.assertTrue(flow.signed_in)

    def test_rejected_code(self):
        flow = fake_flow()
        flow.sign_in(VAUXHALL, "me@example.com", "password", "GB")
        with self.assertRaises(CodeRejected):
            flow.finish("000000", "1234")


class TestRealSetupFlow(unittest.TestCase):

    def test_finish_drops_the_remote_token_from_the_old_session(self):
        # GIVEN a signed-in app that still holds a remote token made with the previous SMS code and PIN
        app = MagicMock()
        app.myp.remote_client.remoteCredentials.refresh_token = "old_remote_refresh_token"
        refresh_tokens_at_start = []
        app.start_remote_control.side_effect = lambda: refresh_tokens_at_start.append(
            app.myp.remote_client.remoteCredentials.refresh_token)

        # WHEN the new SMS code and PIN are accepted
        with patch("psa_car_controller.web.view.setup_flow.PSACarController", return_value=app), \
                patch("psa_car_controller.web.view.setup_flow.new_otp_session") as new_session:
            RealSetupFlow().finish("123456", "1234")

        # THEN remote control restarts without the old token, so it makes a new one from the new session
        self.assertEqual(new_session.return_value, app.myp.remote_client.otp)
        self.assertEqual([None], refresh_tokens_at_start)
        app.myp.save_config.assert_called_once()


if __name__ == '__main__':
    unittest.main()
