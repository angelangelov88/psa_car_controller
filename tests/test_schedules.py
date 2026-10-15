import json
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from paho.mqtt.client import MQTTMessage

from psa_car_controller.common.utils import RateLimitException, same_preconditioning_programs
from psa_car_controller.psa.RemoteClient import (MQTT_EVENT_TOPIC, MQTT_RESP_TOPIC, SCHEDULE_CONFIRMED,
                                                 SCHEDULE_FAILED, SCHEDULE_WAITING, SCHEDULE_WAKING, WAKEUP_WAIT_S,
                                                 RemoteClient)
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
    # wakeup's rate limit is shared by every client, so tests use a stand-in that publishes like it
    client.wakeup = MagicMock(side_effect=lambda vin: client.publish("wakeup"))
    return client


def sent_schedules(client):
    return [call.args[0].req_parameters["programs"] for call in client.publish.call_args_list
            if call.args[0] != "wakeup"]


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
        self.client.car_heard["VIN"] = time.time()  # awake, so the schedules go straight away
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


class InlineThread:
    """Runs a thread's work straight away, so a test sees what it sent."""

    def __init__(self, target, args, daemon):  # pylint: disable=unused-argument
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@patch("psa_car_controller.psa.RemoteClient.threading.Thread", InlineThread)
class TestWakeBeforeSending(unittest.TestCase):
    """From midday on 2026-10-15 every schedule sent to the sleeping car timed out, while a wakeup still woke it."""

    def setUp(self):
        self.client = remote_client()
        self.sent_at = time.time()

    def test_a_sleeping_car_is_woken_first(self):
        # WHEN schedules are saved for a car that hasn't been heard from
        self.assertTrue(self.client.set_preconditioning_program("VIN", CAR_PROGRAMS))
        # THEN it's woken, and the schedules wait for it
        self.client.wakeup.assert_called_once_with("VIN")
        self.assertEqual(sent_schedules(self.client), [])
        self.assertEqual(self.client.schedule_delivery("VIN", CAR_PROGRAMS, self.sent_at), SCHEDULE_WAKING)

    def test_the_schedules_go_once_the_car_answers_the_wakeup(self):
        self.client.set_preconditioning_program("VIN", CAR_PROGRAMS)
        self.client._on_mqtt_message(None, None, reply("VIN", "0", request="VehCharge/state"))
        self.assertEqual(sent_schedules(self.client), [CAR_PROGRAMS])
        self.assertEqual(self.client.schedule_delivery("VIN", CAR_PROGRAMS, self.sent_at), SCHEDULE_WAITING)

    def test_the_schedules_go_when_the_waking_car_sends_an_event(self):
        # On 2026-10-15 the car's events came a few seconds before its answer to the wakeup
        self.client.set_preconditioning_program("VIN", CAR_PROGRAMS)
        self.client._on_mqtt_message(None, None, event_with_programs("VIN", dict(CAR_PROGRAMS, program1=OFF)))
        self.assertEqual(sent_schedules(self.client), [CAR_PROGRAMS])

    def test_they_are_sent_once(self):
        self.client.set_preconditioning_program("VIN", CAR_PROGRAMS)
        self.client._on_mqtt_message(None, None, event_with_programs("VIN", CAR_PROGRAMS))
        self.client._on_mqtt_message(None, None, reply("VIN", "0", request="VehCharge/state"))
        self.assertEqual(sent_schedules(self.client), [CAR_PROGRAMS])

    def test_they_still_go_when_the_wakeup_fails(self):
        # the server then tries to wake the car itself, as before
        self.client.set_preconditioning_program("VIN", CAR_PROGRAMS)
        self.client._on_mqtt_message(None, None, reply("VIN", "300", request="VehCharge/state"))
        self.assertEqual(sent_schedules(self.client), [CAR_PROGRAMS])

    def test_an_expired_token_answer_to_the_wakeup_keeps_them_waiting(self):
        # "400" means the token had expired: the wakeup is sent again, so its answer is still to come
        self.client._refresh_remote_token = MagicMock(return_value=True)
        self.client.set_preconditioning_program("VIN", CAR_PROGRAMS)
        self.client._on_mqtt_message(None, None, reply("VIN", "400", request="VehCharge/state"))
        self.assertEqual(sent_schedules(self.client), [])

    def test_a_car_heard_from_just_now_gets_them_straight_away(self):
        self.client._on_mqtt_message(None, None, reply("VIN", "0", request="VehCharge/state"))
        self.client.set_preconditioning_program("VIN", CAR_PROGRAMS)
        self.client.wakeup.assert_not_called()
        self.assertEqual(sent_schedules(self.client), [CAR_PROGRAMS])

    def test_schedules_are_not_sent_long_after_the_save(self):
        # GIVEN a save whose wakeup never got an answer
        self.client.set_preconditioning_program("VIN", CAR_PROGRAMS)
        queued_at, request = self.client.after_wakeup["VIN"]
        self.client.after_wakeup["VIN"] = (queued_at - WAKEUP_WAIT_S - 1, request)
        # WHEN the car wakes hours later
        self.client._on_mqtt_message(None, None, event_with_programs("VIN", CAR_PROGRAMS))
        # THEN the old schedules aren't sent, and the page stops showing "Waking the car"
        self.assertEqual(sent_schedules(self.client), [])
        self.assertNotEqual(self.client.schedule_delivery("VIN", CAR_PROGRAMS, self.sent_at), SCHEDULE_WAKING)

    def test_without_wakeups_left_they_go_straight_away(self):
        self.client.wakeup.side_effect = RateLimitException("wakeup")
        self.assertTrue(self.client.set_preconditioning_program("VIN", CAR_PROGRAMS))
        self.assertEqual(sent_schedules(self.client), [CAR_PROGRAMS])
        self.assertEqual(self.client.schedule_delivery("VIN", CAR_PROGRAMS, self.sent_at), SCHEDULE_WAITING)

    def test_a_wakeup_that_cant_be_sent_is_a_failed_save(self):
        self.client.publish.return_value = False
        self.assertFalse(self.client.set_preconditioning_program("VIN", CAR_PROGRAMS))
        self.assertNotEqual(self.client.schedule_delivery("VIN", CAR_PROGRAMS, self.sent_at), SCHEDULE_WAKING)

    def test_heard_since(self):
        self.assertFalse(self.client.heard_since("VIN", self.sent_at))
        self.client._on_mqtt_message(None, None, event_with_programs("VIN", CAR_PROGRAMS))
        self.assertTrue(self.client.heard_since("VIN", self.sent_at))
        self.assertFalse(self.client.heard_since("VIN", time.time() + 1))


if __name__ == '__main__':
    unittest.main()
