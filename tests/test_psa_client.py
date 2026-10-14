import os
import tempfile
import unittest
from unittest.mock import patch

from psa_car_controller.psacc.application.psa_client import PSAClient


class TestPSAClient(unittest.TestCase):

    def test_refreshed_token_is_saved_straight_away(self):
        # GIVEN a client whose config file holds a refresh token that the next refresh will spend
        with tempfile.TemporaryDirectory() as tmp:
            config_file = os.path.join(tmp, "config.json")
            client = PSAClient("old_refresh_token", "client_id", "client_secret", None, "customer_id",
                               "clientsB2CPeugeot", "GB")
            client.config_file = config_file
            client.save_config()

            # WHEN the access token is refreshed and the server hands back a new refresh token
            def refresh():
                client.manager.refresh_token = "new_refresh_token"

            with patch.object(client.manager, "_refresh_token", side_effect=refresh):
                self.assertTrue(client.manager.refresh_token_now())

            # THEN the new refresh token is on disk at once, without waiting for a timed save
            with open(config_file, encoding="utf-8") as f:
                saved = f.read()
            self.assertIn("new_refresh_token", saved)
            self.assertNotIn("old_refresh_token", saved)


if __name__ == '__main__':
    unittest.main()
