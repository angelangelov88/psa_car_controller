import unittest

from psa_car_controller.web.view.status_text import battery_too_low_to_precondition, format_mileage


class TestStatusText(unittest.TestCase):

    def test_mileage_is_shown_in_whole_miles(self):
        self.assertEqual(format_mileage(16093.44), "10,000 mi")
        self.assertEqual(format_mileage(0), "0 mi")
        self.assertEqual(format_mileage(2.5), "2 mi")

    def test_preconditioning_needs_at_least_51_percent(self):
        self.assertTrue(battery_too_low_to_precondition(50))
        self.assertTrue(battery_too_low_to_precondition(50.9))
        self.assertFalse(battery_too_low_to_precondition(51))
        self.assertFalse(battery_too_low_to_precondition(100))

    def test_unknown_battery_does_not_block_preconditioning(self):
        self.assertFalse(battery_too_low_to_precondition(None))
        self.assertFalse(battery_too_low_to_precondition("—"))
