import unittest

from psa_car_controller.web.view.status_text import format_mileage


class TestStatusText(unittest.TestCase):

    def test_mileage_is_shown_in_whole_miles(self):
        self.assertEqual(format_mileage(16093.44), "10,000 mi")
        self.assertEqual(format_mileage(0), "0 mi")
        self.assertEqual(format_mileage(2.5), "2 mi")
