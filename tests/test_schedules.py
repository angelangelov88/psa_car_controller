import json
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock

from paho.mqtt.client import MQTTMessage

from psa_car_controller.common.utils import same_preconditioning_programs
from psa_car_controller.psa.RemoteClient import (MQTT_EVENT_TOPIC, MQTT_RESP_TOPIC, SCHEDULE_CONFIRMED,
                                                 SCHEDULE_FAILED, SCHEDULE_WAITING, RemoteClient)
from psa_car_controller.psa.connected_car_api import Vehicles
from psa_car_controller.psa.constants import DEFAULT_PRECONDITIONING_PROGRAM
from psa_car_controller.web.view.schedule_form import HOUR_OPTIONS, minute_options, programs_from_form

OFF = {"day": [0] * 7, "hour": 34, "minute": 0, "on": 0}
WEEKDAYS_7_15 = {"day": [1, 1, 1, 1, 1, 0, 0], "hour": 7, "minute": 15, "on": 1}
CAR_PROGRAMS = {"program1": WEEKDAYS_7_15, "program2": OFF, "program3": OFF, "program4": OFF}


def remote_client(programs_file=None):
    account_info = MagicMock()
    account_info.realm = ""
    client = RemoteClient(account_info, Vehicles, None, None)
    if programs_file:
        client.keep_programs_in(programs_file)
    client.publish = MagicMock(return_value=True)
    return client


def event_with_programs(vin, programs):
    msg = MQTTMessage(topic=MQTT_EVENT_TOPIC.encode("utf-8"))
    msg.payload = json.dumps({"vin": vin, "charging_state": {}, "precond_state": {"programs": programs}}).encode()
    return msg


def reply(vin, return_code, request="ThermalPrecond"):
    msg = MQTTMessage(topic=(MQTT_RESP_TOPIC + "cid/" + request).encode("utf-8"))
    msg.payload = json.dumps({"vin": vin, "return_code": return_code}).encode()
    return msg


class TestScheduleForm(unittest.TestCase):

    def test_hour_choices(self):
        self.assertEqual(HOUR_OPTIONS[0], {"label": "Not set", "value": ""})
        self.assertEqual([o["label"] for o in HOUR_OPTIONS[1:]], [f"{h:02d}" for h in range(24)])

    def test_minutes_every_ten(self):
        self.assertEqual([o["value"] for o in minute_options(0)], ["0", "10", "20", "30", "40", "50"])
        self.assertEqual(minute_options(30)[0], {"label": "00", "value": "0"})

    def test_the_cars_own_minute_is_kept(self):
        # GIVEN the car has a schedule at :15, set elsewhere
        # THEN :15 is offered and kept in order, so loading the page and saving doesn't move it
        self.assertEqual([o["label"] for o in minute_options(15)], ["00", "10", "15", "20", "30", "40", "50"])

    def test_dropdown_values_arrive_as_text(self):
        programs, problem = programs_from_form([True, False, False, False], ["7", "", "", ""], ["10", "0", "0", "0"],
                                               [[0, 1, 2, 3, 4], [], [], []])
        self.assertIsNone(problem)
        self.assertEqual(programs["program1"], {"day": [1, 1, 1, 1, 1, 0, 0], "hour": 7, "minute": 10, "on": 1})
        self.assertEqual(programs["program2"], OFF)

    def test_weekday_and_weekend_schedules(self):
        programs, problem = programs_from_form([True, True, False, False], [7, 9, None, None], [15, None, None, None],
                                               [[0, 1, 2, 3, 4], [5, 6], [], []])
        self.assertIsNone(problem)
        self.assertEqual(programs["program1"], WEEKDAYS_7_15)
        self.assertEqual(programs["program2"], {"day": [0, 0, 0, 0, 0, 1, 1], "hour": 9, "minute": 0, "on": 1})
        self.assertEqual(programs["program3"], OFF)

    def test_numbers_typed_as_text(self):
        programs, problem = programs_from_form([True, False, False, False], ["7", None, None, None],
                                               ["15", None, None, None], [[0, 1, 2, 3, 4], [], [], []])
        self.assertIsNone(problem)
        self.assertEqual(programs["program1"], WEEKDAYS_7_15)

    def test_disabled_schedule_can_keep_its_time_and_days(self):
        programs, problem = programs_from_form([False] * 4, [7, None, None, None], [15, None, None, None],
                                               [[0], [], [], []])
        self.assertIsNone(problem)
        self.assertEqual(programs["program1"], {"day": [1, 0, 0, 0, 0, 0, 0], "hour": 7, "minute": 15, "on": 0})

    def test_enabled_without_an_hour_is_refused(self):
        # GIVEN schedule 2 switched on with days but no hour, which the car would never run
        programs, problem = programs_from_form([False, True, False, False], [None] * 4, [None] * 4,
                                               [[], [0], [], []])
        # THEN nothing is sent and the page says what to fix
        self.assertIsNone(programs)
        self.assertEqual(problem, "Schedule 2 is enabled but has no hour.")

    def test_enabled_without_days_is_refused(self):
        programs, problem = programs_from_form([True, False, False, False], [7, None, None, None], [0] * 4,
                                               [[], [], [], []])
        self.assertIsNone(programs)
        self.assertEqual(problem, "Schedule 1 is enabled but has no days.")

    def test_out_of_range_time_is_refused(self):
        programs, problem = programs_from_form([True, False, False, False], [24, None, None, None], [0] * 4,
                                               [[0], [], [], []])
        self.assertIsNone(programs)
        self.assertEqual(problem, "Hours must be 0–23 and minutes 0–59.")


class TestSameSchedules(unittest.TestCase):
    """Decides whether Save is needed: the page's schedules against the ones the car reported."""

    def test_the_same_schedules_match(self):
        self.assertTrue(same_preconditioning_programs(CAR_PROGRAMS, json.loads(json.dumps(CAR_PROGRAMS))))

    def test_the_minute_of_an_unset_schedule_is_ignored(self):
        # GIVEN the car keeps minute 7 on a schedule with no hour, while the page shows and sends 00
        car = dict(CAR_PROGRAMS, program4=DEFAULT_PRECONDITIONING_PROGRAM["program4"])
        # THEN they still match, so Save isn't offered for a change that isn't one
        self.assertEqual(car["program4"]["minute"], 7)
        self.assertTrue(same_preconditioning_programs(CAR_PROGRAMS, car))

    def test_a_different_time_day_or_switch_differs(self):
        for change in ({"hour": 8}, {"minute": 20}, {"day": [1, 1, 1, 1, 1, 1, 0]}, {"on": 0}):
            with self.subTest(change=change):
                changed = dict(CAR_PROGRAMS, program1=dict(WEEKDAYS_7_15, **change))
                self.assertFalse(same_preconditioning_programs(changed, CAR_PROGRAMS))

    def test_nothing_reported_never_matches(self):
        # Before the car has reported, Save stays available
        self.assertFalse(same_preconditioning_programs(CAR_PROGRAMS, None))


class TestKeptSchedules(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.file = os.path.join(self.folder.name, "precond_programs.json")

    def tearDown(self):
        self.folder.cleanup()

    def test_schedules_from_the_car_survive_a_restart(self):
        # GIVEN the car sent its schedules before the app restarted
        remote_client(self.file)._on_mqtt_message(None, None, event_with_programs("VIN", CAR_PROGRAMS))

        # WHEN the app starts again and preconditioning is turned on before the car sends anything
        client = remote_client(self.file)
        client.preconditioning("VIN", True)

        # THEN the car's schedules are sent back with it, not empty ones that would wipe them
        self.assertTrue(client.has_preconditioning_program("VIN"))
        self.assertEqual(client.get_preconditioning_program("VIN"), CAR_PROGRAMS)
        sent = client.publish.call_args[0][0]
        self.assertEqual(sent.req_parameters["programs"], CAR_PROGRAMS)

    def test_saved_schedules_survive_a_restart(self):
        remote_client(self.file).set_preconditioning_program("VIN", CAR_PROGRAMS)
        self.assertEqual(remote_client(self.file).get_preconditioning_program("VIN"), CAR_PROGRAMS)

    def test_schedules_that_never_reached_the_car_are_not_kept(self):
        client = remote_client(self.file)
        client.publish.return_value = False
        self.assertFalse(client.set_preconditioning_program("VIN", CAR_PROGRAMS))
        self.assertFalse(remote_client(self.file).has_preconditioning_program("VIN"))

    def test_unknown_schedules_fall_back_to_the_default(self):
        client = remote_client(self.file)
        self.assertFalse(client.has_preconditioning_program("VIN"))
        self.assertEqual(client.get_preconditioning_program("VIN"), DEFAULT_PRECONDITIONING_PROGRAM)

    def test_unreadable_file_is_ignored(self):
        with open(self.file, "w", encoding="utf-8") as file:
            file.write('{"VIN": {"program1": "broken"}}')
        self.assertFalse(remote_client(self.file).has_preconditioning_program("VIN"))

    def test_no_file_without_a_file_name(self):
        remote_client()._on_mqtt_message(None, None, event_with_programs("VIN", CAR_PROGRAMS))
        self.assertFalse(os.path.exists(self.file))


class TestScheduleDelivery(unittest.TestCase):

    def setUp(self):
        self.client = remote_client()
        self.client._refresh_remote_token = MagicMock(return_value=True)
        self.sent_at = time.time()
        self.client.set_preconditioning_program("VIN", CAR_PROGRAMS)

    def test_waiting_until_the_car_answers(self):
        self.assertEqual(self.client.schedule_delivery("VIN", CAR_PROGRAMS, self.sent_at), SCHEDULE_WAITING)

    def test_confirmed_when_the_car_reports_the_new_schedules(self):
        # GIVEN the car accepted the request and then reported its schedules, as at 11:58 on 2026-10-15
        self.client._on_mqtt_message(None, None, reply("VIN", "0"))
        self.client._on_mqtt_message(None, None, event_with_programs("VIN", CAR_PROGRAMS))
        # THEN the save is confirmed
        self.assertEqual(self.client.schedule_delivery("VIN", CAR_PROGRAMS, self.sent_at), SCHEDULE_CONFIRMED)

    def test_confirmed_when_the_car_keeps_its_own_minute_on_an_unset_schedule(self):
        car = dict(CAR_PROGRAMS, program2=dict(OFF, minute=7))
        self.client._on_mqtt_message(None, None, event_with_programs("VIN", car))
        self.assertEqual(self.client.schedule_delivery("VIN", CAR_PROGRAMS, self.sent_at), SCHEDULE_CONFIRMED)

    def test_reported_schedules_are_only_what_the_car_sent(self):
        # Saving isn't a report: the page compares against the car's own word
        self.assertIsNone(self.client.reported_preconditioning_program("VIN"))
        self.client._on_mqtt_message(None, None, event_with_programs("VIN", CAR_PROGRAMS))
        self.assertEqual(self.client.reported_preconditioning_program("VIN"), CAR_PROGRAMS)

    def test_accepted_but_not_reported_yet_is_still_waiting(self):
        self.client._on_mqtt_message(None, None, reply("VIN", "0"))
        self.assertEqual(self.client.schedule_delivery("VIN", CAR_PROGRAMS, self.sent_at), SCHEDULE_WAITING)

    def test_old_schedules_from_the_car_are_not_a_confirmation(self):
        old = dict(CAR_PROGRAMS, program1=OFF)
        self.client._on_mqtt_message(None, None, event_with_programs("VIN", old))
        self.assertEqual(self.client.schedule_delivery("VIN", CAR_PROGRAMS, self.sent_at), SCHEDULE_WAITING)

    def test_a_report_from_before_the_save_is_not_a_confirmation(self):
        self.client._on_mqtt_message(None, None, event_with_programs("VIN", CAR_PROGRAMS))
        later = time.time() + 1
        self.assertEqual(self.client.schedule_delivery("VIN", CAR_PROGRAMS, later), SCHEDULE_WAITING)

    def test_an_error_from_the_car_fails(self):
        self.client._on_mqtt_message(None, None, reply("VIN", "300"))
        self.assertEqual(self.client.schedule_delivery("VIN", CAR_PROGRAMS, self.sent_at), SCHEDULE_FAILED)

    def test_an_expired_token_answer_is_retried_not_failed(self):
        # "400" means the token had expired: the request is sent again, so keep waiting
        self.client._on_mqtt_message(None, None, reply("VIN", "400"))
        self.assertEqual(self.client.schedule_delivery("VIN", CAR_PROGRAMS, self.sent_at), SCHEDULE_WAITING)

    def test_errors_for_other_requests_are_ignored(self):
        self.client._on_mqtt_message(None, None, reply("VIN", "502", request="VehCharge/state"))
        self.assertEqual(self.client.schedule_delivery("VIN", CAR_PROGRAMS, self.sent_at), SCHEDULE_WAITING)


if __name__ == '__main__':
    unittest.main()
