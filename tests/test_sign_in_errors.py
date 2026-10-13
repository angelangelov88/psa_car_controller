import unittest
from unittest.mock import MagicMock, patch

from psa_car_controller.psa.setup.app_decoder import InitialSetup, SignInRejected
from psa_car_controller.web.view.sign_in import sign_in_rejected_message

VAUXHALL = "com.psa.mym.myvauxhall"


def start_setup(response_json):
    res = MagicMock()
    res.json.return_value = response_json
    apk_parser = MagicMock(site_code="site", host_brandid_prod="https://brandid.example")
    with patch("psa_car_controller.psa.setup.app_decoder.get_content_from_apk", return_value=apk_parser), \
            patch("psa_car_controller.psa.setup.app_decoder.requests.post", return_value=res):
        return InitialSetup(VAUXHALL, "someone@example.com", "password", "GB")


class TestSignInErrors(unittest.TestCase):

    def test_account_service_rejects_sign_in(self):
        # GIVEN the account service has no account for the email
        # WHEN the setup starts
        # THEN the return code is raised, not the raw reply
        with self.assertRaises(SignInRejected) as ctx:
            start_setup({"returnCode": "NEED_CREATION"})
        self.assertEqual("NEED_CREATION", ctx.exception.return_code)

    def test_no_account_message_names_brand_and_country(self):
        message = sign_in_rejected_message("NEED_CREATION", VAUXHALL, "GB")
        self.assertIn("No Vauxhall account found", message)
        self.assertIn("United Kingdom", message)

    def test_unknown_code_is_shown(self):
        message = sign_in_rejected_message("BAD_THING", VAUXHALL, "GB")
        self.assertIn("Vauxhall didn't accept this sign-in (code BAD_THING)", message)

    def test_unexpected_reply_is_not_shown(self):
        message = sign_in_rejected_message('{"oops": "<html>"}', VAUXHALL, "GB")
        self.assertNotIn("oops", message)
        self.assertIn("Check your email and password", message)


if __name__ == '__main__':
    unittest.main()
