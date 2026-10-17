import os
import tempfile
import threading
import time
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


class TestOneRefreshAtATime(unittest.TestCase):
    """On 2026-10-17 two refreshes at once spent the same refresh token, and the second got invalid_grant."""

    def setUp(self):
        self.manager = PSAClient("refresh_token", "client_id", "client_secret", None, "customer_id",
                                 "clientsB2CPeugeot", "GB").manager
        self.manager.refresh_callbacks = []

    def test_a_refresh_asked_for_during_another_uses_its_token(self):
        # GIVEN a refresh under way
        results = []
        second = threading.Thread(target=lambda: results.append(self.manager.refresh_token_now()))

        def slow_refresh():
            # WHEN another one is asked for before it ends
            second.start()
            time.sleep(0.2)

        with patch.object(self.manager, "_refresh_token", side_effect=slow_refresh) as refresh:
            self.assertTrue(self.manager.refresh_token_now())
            second.join(timeout=5)
        # THEN the token is refreshed once, and both get it
        self.assertEqual(refresh.call_count, 1)
        self.assertEqual(results, [True])

    def test_a_later_refresh_refreshes_again(self):
        with patch.object(self.manager, "_refresh_token") as refresh:
            self.manager.refresh_token_now()
            self.manager.refresh_token_now()
        self.assertEqual(refresh.call_count, 2)


if __name__ == '__main__':
    unittest.main()
