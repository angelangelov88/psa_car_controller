import unittest
from datetime import datetime, timedelta, timezone

from psa_car_controller.web.view.status_text import (battery_too_low_to_precondition, car_state_text, charge_power_kw,
                                                     charging_text, format_duration, format_mileage, format_power,
                                                     format_rate, format_updated, map_embed_url, map_link_url,
                                                     position, precond_failure_text, precond_text, signal_text)


class TestStatusText(unittest.TestCase):

    def test_mileage_is_shown_in_whole_miles(self):
        self.assertEqual(format_mileage(16093.44), "10,000 mi")
        self.assertEqual(format_mileage(0), "0 mi")
        self.assertEqual(format_mileage(2.5), "2 mi")

    def test_range_is_shown_in_miles(self):
        self.assertEqual(format_mileage(202), "126 mi")

    def test_preconditioning_needs_at_least_51_percent(self):
        self.assertTrue(battery_too_low_to_precondition(50))
        self.assertTrue(battery_too_low_to_precondition(50.9))
        self.assertFalse(battery_too_low_to_precondition(51))
        self.assertFalse(battery_too_low_to_precondition(100))

    def test_unknown_battery_does_not_block_preconditioning(self):
        self.assertFalse(battery_too_low_to_precondition(None))
        self.assertFalse(battery_too_low_to_precondition("—"))


class TestChargingText(unittest.TestCase):

    def test_plugged_in_but_not_charging(self):
        # GIVEN the car reports the cable in and "Disconnected", which only means it isn't charging
        # WHEN it is shown THEN it says plugged in, not unplugged
        self.assertEqual(charging_text(True, "Disconnected", "PT0S"), "Plugged in, not charging")

    def test_unplugged(self):
        self.assertEqual(charging_text(False, "Disconnected", "PT0S"), "Unplugged")

    def test_charging_shows_time_to_full(self):
        self.assertEqual(charging_text(True, "InProgress", "PT2H10M"), "Charging · full in 2 h 10 min")
        self.assertEqual(charging_text(True, "InProgress", "PT0S"), "Charging")

    def test_finished_and_failed(self):
        self.assertEqual(charging_text(True, "Finished", "PT0S"), "Charged")
        self.assertEqual(charging_text(True, "Failure", "PT0S"), "Charging failed")

    def test_nothing_known(self):
        self.assertIsNone(charging_text(None, None, None))

    def test_durations(self):
        self.assertEqual(format_duration("PT2H"), "2 h")
        self.assertEqual(format_duration("PT45M"), "45 min")
        self.assertIsNone(format_duration("PT0S"))
        self.assertIsNone(format_duration(None))


class TestChargingRate(unittest.TestCase):

    def test_power_is_charge_left_over_time_to_full(self):
        # GIVEN 46 kWh at 23% (35.4 kWh to add) and full in 4 h 50 min, as on 2026-11-01 on the 7.4 kW zappi, when
        # the car's range was 16 mi and the old estimate from it said 17.1 kW
        # WHEN the power is estimated THEN it is about 7.3 kW
        power = charge_power_kw(46, 23, "PT4H50M")
        self.assertAlmostEqual(power, 7.33, places=2)
        self.assertEqual(format_power(power), "≈7.3 kW")

    def test_power_with_hours_or_minutes_only(self):
        self.assertAlmostEqual(charge_power_kw(46, 70, "PT2H"), 6.9)
        self.assertAlmostEqual(charge_power_kw(46, 90, "PT45M"), 6.13, places=2)

    def test_no_estimate_without_time_or_level(self):
        self.assertIsNone(charge_power_kw(46, 75, "PT0S"))
        self.assertIsNone(charge_power_kw(46, 75, None))
        self.assertIsNone(charge_power_kw(46, None, "PT2H"))
        self.assertIsNone(charge_power_kw(46, 100, "PT2H"))
        self.assertIsNone(charge_power_kw(None, 75, "PT2H"))

    def test_rate_in_miles(self):
        self.assertEqual(format_rate(40), "+25 mi range per hour")


class TestPreconditioningText(unittest.TestCase):

    def test_states(self):
        self.assertEqual(precond_text("Enabled"), "On")
        self.assertEqual(precond_text("Disabled"), "Off")
        self.assertEqual(precond_text("Failure"), "Failed")
        self.assertIsNone(precond_text(None))

    def test_failure_reasons_in_words(self):
        self.assertEqual(precond_failure_text("DoorOpened"), "Door open")
        self.assertEqual(precond_failure_text("LowBattery"), "Battery too low")
        self.assertEqual(precond_failure_text("SomethingNew"), "SomethingNew")


class TestCarState(unittest.TestCase):

    def test_parked(self):
        self.assertEqual(car_state_text("Stop", False, 0.0), "Parked")

    def test_switched_on(self):
        self.assertEqual(car_state_text("Start", False, 0.0), "On")

    def test_driving_with_speed(self):
        self.assertEqual(car_state_text("Start", True, 48.28), "Driving · 30 mph")
        self.assertEqual(car_state_text("Start", True, None), "Driving")

    def test_nothing_known(self):
        self.assertIsNone(car_state_text(None, None, None))


class TestUpdatedAndPosition(unittest.TestCase):

    def test_today_shows_the_time_only(self):
        now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        updated = now - timedelta(minutes=5)
        self.assertEqual(format_updated(updated, now), f"{updated.astimezone():%H:%M}")

    def test_earlier_day_shows_the_date(self):
        now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        updated = now - timedelta(days=1)
        local = updated.astimezone()
        self.assertEqual(format_updated(updated, now), f"{local.day} {local:%b %H:%M}")

    def test_unknown_time(self):
        self.assertIsNone(format_updated(None))

    def test_position_from_geojson(self):
        # GeoJSON puts longitude first
        self.assertEqual(position([-0.1, 51.5, 20]), (51.5, -0.1))
        self.assertIsNone(position(None))
        self.assertIsNone(position([None, None]))

    def test_map_urls(self):
        self.assertEqual(map_embed_url(51.5, -0.1),
                         "https://maps.google.com/maps?q=51.500000,-0.100000&z=16&output=embed")
        self.assertEqual(map_link_url(51.5, -0.1),
                         "https://www.google.com/maps/search/?api=1&query=51.500000,-0.100000")


class TestSignal(unittest.TestCase):

    def test_signal_with_the_time_the_car_reported_it(self):
        seen_at = datetime.now(timezone.utc)
        self.assertEqual(signal_text(4, seen_at), f"Signal 4/5 at {seen_at.astimezone():%H:%M}")

    def test_signal_without_a_time(self):
        self.assertEqual(signal_text(3), "Signal 3/5")

    def test_no_signal_reported(self):
        self.assertIsNone(signal_text(None))
        self.assertIsNone(signal_text("—"))


if __name__ == '__main__':
    unittest.main()
