import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from unittest import mock

from psa_car_controller.psa.RemoteClient import KEEPALIVE_PERIOD, MAX_REFRESH_DELAY, RemoteClient
from psa_car_controller.psa.RemoteCredentials import RemoteCredentials
from psa_car_controller.psa.otp.otp import ConfigException

from tests.utils import get_rc


def get_remote_client(remote_refresh_token="remote_refresh_token"):
    remote_client: RemoteClient = get_rc()
    remote_client.manager = MagicMock()
    remote_client.manager.refresh_token_now.return_value = True
    res = MagicMock()
    res.json.return_value = {"access_token": "access_token"}
    remote_client.manager.post.return_value = res
    remote_client.remoteCredentials = RemoteCredentials(remote_refresh_token)
    remote_client.mqtt_client = MagicMock()
    return remote_client


class TestRefreshRemoteToken(unittest.TestCase):

    def test_forced_refresh_is_throttled(self):
        # GIVEN a remote client whose token was just refreshed
        remote_client = get_remote_client()
        remote_client.remote_token_last_update = datetime.now()

        # WHEN a forced refresh is requested (e.g. on mqtt disconnect rc 7)
        refreshed = remote_client._refresh_remote_token(force=True)

        # THEN the refresh is skipped: it happened less than MAX_REFRESH_DELAY ago
        self.assertTrue(refreshed)
        remote_client.manager.refresh_token_now.assert_not_called()
        remote_client.manager.post.assert_not_called()

    def test_forced_refresh_after_max_delay(self):
        # GIVEN a remote client whose token was refreshed MAX_REFRESH_DELAY ago
        remote_client = get_remote_client()
        remote_client.remote_token_last_update = datetime.now() - timedelta(seconds=MAX_REFRESH_DELAY)

        # WHEN a forced refresh is requested
        refreshed = remote_client._refresh_remote_token(force=True)

        # THEN the token is actually refreshed
        self.assertTrue(refreshed)
        remote_client.manager.refresh_token_now.assert_called_once()
        remote_client.manager.post.assert_called_once()
        self.assertEqual("access_token", remote_client.remoteCredentials.access_token)
        self.assertIsNotNone(remote_client.remote_token_last_update)

    def test_first_forced_refresh_is_not_throttled(self):
        # GIVEN a remote client which never refreshed its token
        remote_client = get_remote_client()
        self.assertIsNone(remote_client.remote_token_last_update)

        # WHEN a forced refresh is requested
        refreshed = remote_client._refresh_remote_token(force=True)

        # THEN the token is refreshed
        self.assertTrue(refreshed)
        remote_client.manager.refresh_token_now.assert_called_once()

    def test_unforced_refresh_respects_ttl(self):
        # GIVEN a remote client whose remote credentials were updated less than MQTT_TOKEN_TTL ago
        remote_client = get_remote_client()
        remote_client.remoteCredentials.refresh_token = "new_remote_refresh_token"

        # WHEN a refresh is requested without force
        refreshed = remote_client._refresh_remote_token()

        # THEN no refresh is performed
        self.assertTrue(refreshed)
        remote_client.manager.refresh_token_now.assert_not_called()

    def test_a_refreshed_token_is_reused_without_a_new_refresh_token(self):
        # GIVEN a remote client that just refreshed its token, with a reply that had no new refresh token
        remote_client = get_remote_client()
        remote_client._refresh_remote_token()
        remote_client.manager.refresh_token_now.reset_mock()

        # WHEN another message is published a moment later
        refreshed = remote_client._refresh_remote_token()

        # THEN the token is reused: refreshing before every publish took ~3 s and used up the rate limit
        self.assertTrue(refreshed)
        remote_client.manager.refresh_token_now.assert_not_called()

    def test_restart_soon_after_a_refresh_still_signs_in_to_mqtt(self):
        # GIVEN a remote client whose token was refreshed a moment ago (as when the setup screens
        # sign in and then restart remote control after the SMS step)
        remote_client = get_remote_client()
        remote_client.remoteCredentials.access_token = "access_token"
        remote_client.remoteCredentials.refresh_token = "new_remote_refresh_token"
        remote_client.load_otp = MagicMock(return_value=True)

        # WHEN remote control starts again, with a new mqtt client
        with mock.patch("psa_car_controller.psa.RemoteClient.mqtt.Client") as client_class, \
                mock.patch.object(RemoteClient, "_RemoteClient__keep_mqtt"):
            remote_client.start()

        # THEN the new client still gets the access token, without another refresh
        # (it used to connect without one and the broker refused it with rc 5 until the TTL ran out)
        client_class.return_value.username_pw_set.assert_called_once_with("IMA_OAUTH_ACCESS_TOKEN", "access_token")
        remote_client.manager.refresh_token_now.assert_not_called()

    def test_keepalive_period_is_well_under_token_lifetime(self):
        # Regression test for Sentry issue 150805222 / the daily invalid_grant: the remote
        # refresh token lives ~24h and __keep_mqtt is the only periodic refresh on an idle
        # car. The old 24h keepalive always fired a few seconds too late and killed the
        # chain daily.
        self.assertLess(KEEPALIVE_PERIOD, 24 * 3600 / 2,
                        "keepalive must run at least twice per remote token lifetime")

    def test_keep_mqtt_schedules_next_cycle(self):
        # GIVEN a remote client with at least one vehicle
        remote_client = get_remote_client()
        remote_client.vehicles_list = ["VR3UHZKX"]

        # WHEN the keepalive runs
        remote_client._RemoteClient__keep_mqtt()

        # THEN the next keepalive cycle is scheduled at KEEPALIVE_PERIOD
        self.assertIsNotNone(remote_client.update_thread)
        self.assertAlmostEqual(remote_client.update_thread.interval, KEEPALIVE_PERIOD)
        remote_client.update_thread.cancel()
        remote_client.update_thread = None

    def test_otp_failure_is_contained_in_refresh(self):
        # GIVEN a remote client whose remote refresh token is broken and whose otp
        # session fails to produce a code (e.g. NOK:ERR1 from the inwebo server)
        remote_client = get_remote_client()
        remote_client.manager.post.return_value.json.return_value = {"error": "invalid_grant"}
        remote_client.otp = MagicMock()
        remote_client.otp.get_otp_code.side_effect = ConfigException("NOK:ERR1")

        # WHEN the remote token is refreshed (no saved otp.bin on disk)
        with mock.patch("psa_car_controller.psa.RemoteClient.load_otp", return_value=None):
            refreshed = remote_client._refresh_remote_token()

        # THEN the ConfigException doesn't escape _refresh_remote_token (it used to
        # propagate up through publish/wakeup into unrelated exception handlers)
        self.assertFalse(refreshed)

    def test_get_otp_code_reload_recovers_from_config_exception(self):
        # GIVEN a remote client whose otp session is desynchronized from the server
        remote_client = get_remote_client()
        broken_session = MagicMock()
        broken_session.get_otp_code.side_effect = ConfigException("NOK:ERR1")
        remote_client.otp = broken_session

        # AND a saved session on disk that works
        saved_session = MagicMock()
        saved_session.get_otp_code.return_value = "otp_code"
        with mock.patch("psa_car_controller.psa.RemoteClient.load_otp", return_value=saved_session), \
                mock.patch("psa_car_controller.psa.RemoteClient.save_otp"):
            # WHEN get_otp_code is called
            otp_code = remote_client.get_otp_code()

        # THEN the session is reloaded from disk and the code is produced
        self.assertEqual("otp_code", otp_code)
        saved_session.get_otp_code.assert_called_once()

    def test_get_otp_code_raises_when_no_saved_session(self):
        # GIVEN a remote client whose otp session fails and no valid otp.bin on disk
        remote_client = get_remote_client()
        remote_client.otp = MagicMock()
        remote_client.otp.get_otp_code.side_effect = ConfigException("NOK:ERR1")

        # WHEN get_otp_code is called THEN the original ConfigException is re-raised
        # (the old code raised a misleading AttributeError on self.otp=None instead)
        with self.assertRaises(ConfigException):
            with mock.patch("psa_car_controller.psa.RemoteClient.load_otp", return_value=None):
                remote_client.get_otp_code()


if __name__ == '__main__':
    unittest.main()
