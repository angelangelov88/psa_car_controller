import json
import logging
import os
import threading
from datetime import datetime
from os import environ
from typing import Optional
import time

import paho.mqtt.client as mqtt
from requests import RequestException

from psa_car_controller.psacc.model.car import Cars
from psa_car_controller.psa.AccountInformation import AccountInformation
from psa_car_controller.psa.connected_car_api.models.doors_state import DoorsState
from psa_car_controller.psa.RemoteCredentials import RemoteCredentials
from psa_car_controller.psa.constants import DEFAULT_PRECONDITIONING_PROGRAM, IMMEDIATE_CHARGE, DELAYED_CHARGE, \
    REMOTE_URL
from psa_car_controller.psa.mqtt_request import MQTTRequest
from psa_car_controller.psa.oauth import OpenIdCredentialManager
from psa_car_controller.common.utils import RateLimitException, rate_limit, parse_hour, TIMEOUT_IN_S, \
    same_preconditioning_programs, validate_preconditioning_programs
from psa_car_controller.psa.otp.otp import ConfigException, save_otp, load_otp

logger = logging.getLogger(__name__)

# mqtt reports the lock state as an int, unlike the rest api's DoorsState.lockedState enum.
# Only these two values have been observed on a real car (an Opel Mokka Electric, confirmed
# against the /Doors command responses).
MQTT_DOORS_LOCKING_STATE = {1: "Locked", 3: "Unlocked"}
MQTT_SERVER = "mwa.mpsa.com"
MQTT_RESP_TOPIC = "psa/RemoteServices/to/cid/"
# The car only sends its weekly preconditioning schedules when it wakes, so the last ones seen are kept here
# across restarts. Without them a preconditioning command would send empty schedules and wipe the car's.
PRECOND_PROGRAMS_FILE = "precond_programs.json"
# Where a schedule sent to the car stands, see schedule_delivery.
SCHEDULE_CONFIRMED = "confirmed"
SCHEDULE_FAILED = "failed"
SCHEDULE_WAITING = "waiting"
SCHEDULE_WAKING = "waking"
SCHEDULE_NOT_WOKEN = "not_woken"
SCHEDULE_CAR_ON = "car_on"
# preconditioning_result before any preconditioning command, which otherwise uses the SCHEDULE_ states
PRECOND_NONE = "none"
# The car sleeps soon after it last spoke: on 2026-10-15 the server called it asleep 104 s after an answer. From
# midday that day every schedule sent to the sleeping car timed out, while a /VehCharge/state request still woke it,
# so a schedule for a car not heard from within AWAKE_WINDOW_S goes once a wakeup gets an answer, if that comes
# within WAKEUP_WAIT_S. Waiting any longer could send it hours later, when the user no longer expects it. A car that
# doesn't answer the wakeup isn't sent it at all: at 17:39 that day schedules sent after a timed out wakeup timed out
# too, so sending them only kept the user waiting another 2.5 minutes for the same answer.
# They wait for the wakeup's answer, not the car's first event: at 19:34 and 20:38 that day schedules sent on the
# event, a second before the answer, were told the car was asleep and timed out although it was awake.
AWAKE_WINDOW_S = 60
WAKEUP_WAIT_S = 180
# The server answers a wakeup the car doesn't answer with a 300 about 2.5 minutes later, as on 2026-10-25 and at 13:01
# on 2026-10-30, while a tap at 13:07 woke it straight away. So one more wakeup goes before giving up, and once the car
# hasn't answered WAKEUP_TRIES of them a preconditioning goes anyway: it may still get through, and
# preconditioning_result checks the car's status for it. Schedules still don't, as above.
WAKEUP_TRIES = 2
# On 2026-10-30 at 12:44 a preconditioning sent once the car woke got nothing back from the server, not even the
# ProcessManagement message it acknowledges a request with within seconds. One that isn't acknowledged within
# PRECOND_ACK_WAIT_S is sent once more.
PRECOND_ACK_WAIT_S = 30
# The car's status from the rest api confirms a preconditioning too, when it shows it on (or off) as of at most
# PRECOND_STATUS_SLACK_S before the command, which allows for the car's clock being a little behind.
PRECOND_STATUS_SLACK_S = 30
PRECOND_ON_STATUSES = ("Enabled",)
PRECOND_OFF_STATUSES = ("Disabled", "Finished")
# A switched-on car is awake, so requests go without a wakeup. Its events say whether it's on: on 2026-10-16
# sev_state was 1 or 2 in every event from start to stop of three drives, and 0 while it was parked. Each time it
# was switched off it sent a sev_state 0 event within seconds, so CAR_ON_TRUST_S only matters when that one is lost.
# It's shorter than the 12 to 29 minutes between events while on, as trusting a lost one is the costlier mistake:
# requests sent without a wakeup to a car that has gone to sleep time out, while a wakeup sent to a switched-on car
# is refused within seconds and the requests go anyway.
CAR_ON_TRUST_S = 15 * 60
# A switched-on car refuses a /VehCharge/state request within seconds with return_code 500 and resp_data
# {"error": 4}, as it did on all 7 tries on 2026-10-16. It is awake, so what waits for the wakeup goes anyway.
CAR_ON_RETURN_CODE = "500"
CAR_ON_ERROR = 4
MQTT_EVENT_TOPIC = "psa/RemoteServices/events/MPHRTServices/"
MQTT_TOKEN_TTL = 890
# minimum delay between two remote token refresh attempts: the broker closes the socket without
# CONNACK when the access token is expired, and paho reconnects at least every
# MAX_REFRESH_DELAY seconds, so refreshing on every disconnect would burn the
# refresh_token_now() rate limit (6 per 30 min) during a mere network outage.
MAX_REFRESH_DELAY = 120
# The remote refresh token's server-side lifetime is about 24h: a grant attempted 24h
# (plus a few seconds) after the previous one fails with 'invalid_grant', and each grant
# rotates the token. The __keep_mqtt timer is the ONLY periodic refresh on an idle car,
# so it must run well within that lifetime: 8h keeps the chain rotating forever and
# survives one missed cycle.
KEEPALIVE_PERIOD = 3600 * 8


def _event_time(data):
    """time.time() value of the car's own date on an event, or None without a valid one."""
    try:
        return datetime.fromisoformat(data["date"].replace("Z", "+00:00")).timestamp()
    except (KeyError, AttributeError, ValueError):
        return None


def refused_car_on(data) -> bool:
    """True for a car's answer refusing a request because it's switched on."""
    return data.get("return_code") == CAR_ON_RETURN_CODE and (data.get("resp_data") or {}).get("error") == CAR_ON_ERROR


class RemoteException(Exception):
    pass


class SmsRequestFailed(RemoteException):
    """PSA refused to text an OTP code. The status code says why, e.g. 429 after too many requests."""

    def __init__(self, status_code, text):
        super().__init__(f"SMS request failed: HTTP {status_code} {text}")
        self.status_code = status_code


class RemoteClient:

    def __init__(self, account_info: AccountInformation, vehicles_list: Cars, manager: OpenIdCredentialManager,
                 remoteCredentials: RemoteCredentials):
        self.vehicles_list = vehicles_list
        self.remoteCredentials: RemoteCredentials = remoteCredentials
        self.manager = manager
        self.programs_file: Optional[str] = None
        self.precond_programs = {}
        # (time, programs) the car itself last reported, and (time, return_code) of its last answer to a
        # /ThermalPrecond request, both by vin. They tell when a sent schedule has really reached the car.
        self.car_programs = {}
        self.precond_replies = {}
        # time.time() the car last answered or sent an event, and (time, request) to send once it wakes, by vin
        self.car_heard = {}
        self.after_wakeup = {}
        # (wakeups sent, wakeups the car didn't answer) for the requests in after_wakeup, by vin
        self.queue_wakeups = {}
        # the last wakeup request sent, by vin: its correlation_id tells its answer from another wakeup's
        self.last_wakeup = {}
        # (time, answered) of the last answer to a wakeup by vin: answered is False when the car didn't answer it
        self.wake_replies = {}
        # (time of the car's event, switched on) from its latest event, and time.time() it last refused a
        # /ThermalPrecond request for being switched on, by vin
        self.car_on = {}
        self.precond_refused_car_on = {}
        # (time of the car's event, signal_quality 0 to 5) from its latest event that had one, by vin
        self.signal = {}
        # (time, activate, request or None if it couldn't be sent) of the last preconditioning(), by vin
        self.precond_sent = {}
        # the correlation_id of the server's last acknowledgement of that preconditioning, and the request if it was
        # sent again for want of one, by vin
        self.precond_acked = {}
        self.precond_resent = {}
        # (time.time() it arrived, preconditioning on) from the car's latest event that said, by vin. On 2026-10-21
        # precond_state.asap went to 1 in an event 7 s after a start reached the car, and back to 0 when it stopped,
        # while the server answered that start with a 300 timeout 2 minutes later. So the event confirms a command.
        self.car_precond_on = {}
        self.lock_state = {}
        self.account_info = account_info
        self.headers = {
            "x-introspect-realm": self.account_info.realm,
            "accept": "application/hal+json",
            "User-Agent": "okhttp/4.8.0",
        }
        self.last_request = None
        self.mqtt_client = None
        self.otp = None
        self._lock = threading.Lock()
        self.update_thread: threading.Timer = None
        self.remote_token_last_update: Optional[datetime] = None

    def __on_mqtt_connect(self, client, userdata, result_code, _):  # pylint: disable=unused-argument
        logger.info("Connected with result code %s", result_code)
        topics = [MQTT_RESP_TOPIC + self.account_info.get_mqtt_customer_id() + "/#"]
        for car in self.vehicles_list:
            topics.append(MQTT_EVENT_TOPIC + car.vin)
        for topic in topics:
            client.subscribe(topic)
            logger.info("subscribe to %s", topic)

    def _on_mqtt_disconnect(self, client, userdata, result_code):  # pylint: disable=unused-argument
        logger.warning("Disconnected with result code %d", result_code)
        if result_code == 1:
            self._refresh_remote_token(force=True)
        elif result_code == 7:
            # the broker closes the socket without CONNACK when the access token is expired:
            # paho then retries forever with the same stale token unless it is refreshed here.
            # Throttling is handled inside _refresh_remote_token.
            self._refresh_remote_token(force=True)
        else:
            logger.warning(mqtt.error_string(result_code))

    def _on_mqtt_message(self, client, userdata, msg):  # pylint: disable=unused-argument
        try:
            logger.info("mqtt msg received: %s %s", msg.topic, msg.payload)
            data = json.loads(msg.payload)
            if msg.topic.startswith(MQTT_RESP_TOPIC):
                self._note_precond_acknowledgement(data)
                if "return_code" not in data:
                    logger.debug("mqtt msg hasn't return code")
                elif data["return_code"] == "400":
                    self._refresh_remote_token(force=True)
                    if self.last_request:
                        logger.warning("last request is send again, token was expired")
                        last_request = self.last_request
                        self.last_request = None
                        self.publish(last_request, store=False)
                    else:
                        logger.error("Last request might have been send twice without success")
                elif data["return_code"] == "0" or refused_car_on(data):
                    self.car_heard[data["vin"]] = time.time()
                if data.get("return_code") not in (None, "0", "400"):
                    logger.error('mqtt error %s : %s', data["return_code"], data.get("reason", data.get("resp_data")))
                if msg.topic.endswith("/ThermalPrecond") and data.get("return_code") not in (None, "400"):
                    self.precond_replies[data["vin"]] = (time.time(), data["return_code"])
                    if refused_car_on(data):
                        self.precond_refused_car_on[data["vin"]] = time.time()
                if msg.topic.endswith("/VehCharge/state") and data.get("return_code") not in (None, "400"):
                    self._on_wakeup_reply(data)
            elif msg.topic.startswith(MQTT_EVENT_TOPIC):
                programs = data["precond_state"].get("programs", None)
                if programs:
                    self.car_programs[data["vin"]] = (time.time(), programs)
                    self._remember_precond_programs(data["vin"], programs)
                if "asap" in data["precond_state"]:
                    self.car_precond_on[data["vin"]] = (time.time(), data["precond_state"]["asap"] == 1)
                self._store_lock_state(data)
                self._store_car_on(data)
                self._store_signal(data)
                self.car_heard[data["vin"]] = time.time()
        except KeyError:
            logger.exception("on_mqtt_message:")

    def _on_wakeup_reply(self, data):
        answered = data["return_code"] == "0" or refused_car_on(data)
        # any answer means the car is awake, but only the last wakeup's error means it didn't wake
        if not answered and not self._answers_last_wakeup(data):
            return
        self.wake_replies[data["vin"]] = (time.time(), answered)
        if answered:
            self._send_after_wakeup(data["vin"])
        else:
            self._wakeup_not_answered(data["vin"])

    def _store_car_on(self, data):
        # Events can arrive out of order, so only one dated after the latest seen counts.
        event_time = _event_time(data)
        if "sev_state" in data and event_time is not None and event_time >= self.car_on.get(data["vin"], (0,))[0]:
            self.car_on[data["vin"]] = (event_time, data["sev_state"] != 0)

    def _store_signal(self, data):
        # Stellantis doesn't document signal_quality, but it's 0 to 5 like a phone's bars and not part of the
        # position. It shows how well the car could reach the network when it last spoke, as wakeups need that.
        event_time = _event_time(data)
        if "signal_quality" in data and event_time is not None and event_time >= self.signal.get(data["vin"], (0,))[0]:
            self.signal[data["vin"]] = (event_time, data["signal_quality"])

    def last_signal(self, vin):
        """(time of the car's event, signal quality 0 to 5) from its latest event that had one, or None."""
        return self.signal.get(vin)

    def car_switched_on(self, vin):
        """True while the car's latest event says it's switched on, unless that event is too old to trust."""
        event_time, switched_on = self.car_on.get(vin, (0, False))
        return switched_on and time.time() - event_time < CAR_ON_TRUST_S

    def _store_lock_state(self, data):
        # doors_state can be absent or explicitly null depending on the event
        locking_state = (data.get("doors_state") or {}).get("doors_locking_state")
        if locking_state is None:
            return
        lock_state = self._decode_lock_state(locking_state)
        if lock_state is None:
            return
        vin = data["vin"]
        self.lock_state[vin] = lock_state
        self.apply_lock_state(self.vehicles_list.get_car_by_vin(vin))

    @staticmethod
    def _decode_lock_state(locking_state):
        lock_state = MQTT_DOORS_LOCKING_STATE.get(locking_state)
        if lock_state is None:
            logger.warning("unknown doors_locking_state %s, please report it on github", locking_state)
        return lock_state

    def apply_lock_state(self, car):
        """Overwrite car.status's doors lock state with the last one seen on mqtt.

        mqtt pushes changes live; the rest api is polled every ~2 min and on some cars never
        reports lockedState at all, so mqtt takes precedence whenever we have a value for it.
        """
        if car is None or car.status is None:
            return
        lock_state = self.lock_state.get(car.vin)
        if lock_state is None:
            return
        if car.status.doors_state is None:
            car.status.doors_state = DoorsState()
        car.status.doors_state.locked_state = [lock_state]
        logger.debug("lock state of %s set from mqtt: %s", car.vin, lock_state)

    def start(self):
        if self.load_otp():
            self.mqtt_client = mqtt.Client(clean_session=True, protocol=mqtt.MQTTv311)
            if environ.get("MQTT_LOG", "0") == "1":
                self.mqtt_client.enable_logger(logger=logger)
            if self._refresh_remote_token():
                # The refresh skips a token under MQTT_TOKEN_TTL old and then doesn't sign the new client in,
                # so a restart soon after one (as at the end of setup) was refused with rc 5 until the TTL ran out.
                self.mqtt_client.username_pw_set("IMA_OAUTH_ACCESS_TOKEN", self.remoteCredentials.access_token)
                self.mqtt_client.tls_set_context()
                self.mqtt_client.on_connect = self.__on_mqtt_connect
                self.mqtt_client.on_message = self._on_mqtt_message
                self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
                self.mqtt_client.connect(MQTT_SERVER, 8885, 60)
                self.mqtt_client.loop_start()
                self.__keep_mqtt()
                return self.mqtt_client.is_connected()
        logger.error("Can't configure MQTT Client")
        return False

    def stop(self):
        if self.mqtt_client:
            logger.info("stop mqtt...")
            self.mqtt_client.on_disconnect = None
            self.mqtt_client.disconnect()
        if self.update_thread:
            self.update_thread.cancel()
            self.update_thread.join(timeout=TIMEOUT_IN_S)

    def __keep_mqtt(self):  # avoid token expiration
        if len(self.vehicles_list) > 0:
            try:
                self.wakeup(self.vehicles_list[0].vin)
            except Exception:
                logger.exception("__keep_mqtt")
        self.update_thread = threading.Timer(KEEPALIVE_PERIOD, self.__keep_mqtt)
        self.update_thread.daemon = True
        self.update_thread.start()

    def veh_charge_request(self, vin, hour, minute, charge_type):
        msg = self.mqtt_request(vin, {"program": {"hour": hour, "minute": minute}, "type": charge_type}, "/VehCharge")
        logger.info("veh_charge_request: %s", msg)
        self.publish(msg)
        return msg

    def publish(self, mqtt_request: MQTTRequest, store=True) -> bool:
        if not self._refresh_remote_token():
            logger.error("Can't publish %s: remote token refresh failed", mqtt_request.topic)
            return False
        message = mqtt_request.get_message_to_json(self.remoteCredentials.access_token)
        logger.debug("mqtt publish: %s %s", mqtt_request.topic, message)
        self.mqtt_client.publish(mqtt_request.topic, message)
        if store:
            self.last_request = mqtt_request
        return True

    def mqtt_request(self, vin, req_parameters, topic):
        return MQTTRequest(topic, vin, req_parameters, self.account_info.get_mqtt_customer_id())

    def _refresh_remote_token(self, force=False) -> bool:
        with self._lock:
            bad_remote_token = self.remoteCredentials.refresh_token is None
            if not force and not bad_remote_token and self.remoteCredentials.last_update:
                last_update: datetime = self.remoteCredentials.last_update
                if (datetime.now() - last_update).total_seconds() < MQTT_TOKEN_TTL:
                    return True
            if force and self.remote_token_last_update is not None:
                # throttle forced refreshes (e.g. one per paho reconnect attempt on rc 7):
                # a stale token makes the broker drop the socket, and paho retries at most every
                # MAX_REFRESH_DELAY seconds, so refreshing more often than that is either
                # useless (network outage: the token isn't the problem) or harmful
                # (it burns the refresh_token_now() rate limit: 6 per 30 min).
                since_last_refresh = (datetime.now() - self.remote_token_last_update).total_seconds()
                if since_last_refresh < MAX_REFRESH_DELAY:
                    logger.debug("remote token refreshed %.0fs ago, skipping forced refresh",
                                 since_last_refresh)
                    return True
            try:
                if not self.manager.refresh_token_now():
                    logger.error("Can't refresh remote token: access token refresh failed")
                    return False
                if bad_remote_token:
                    logger.warning("remote_refresh_token isn't defined")
                else:
                    res = self.manager.post(REMOTE_URL + self.account_info.client_id,
                                            json={"grant_type": "refresh_token",
                                                  "refresh_token": self.remoteCredentials.refresh_token},
                                            headers=self.headers, timeout=TIMEOUT_IN_S)
                    data = res.json()
                    logger.debug("refresh_remote_token: %s", data)
                    if "access_token" in data:
                        self.remoteCredentials.access_token = data["access_token"]
                        # The reply usually has no new refresh token, and only setting one used to restart the
                        # TTL, so every publish refreshed again: about 3 s each, and 6 of them used up
                        # refresh_token_now()'s limit for half an hour.
                        self.remoteCredentials.last_update = datetime.now()
                        bad_remote_token = False
                        if "refresh_token" in data:
                            self.remoteCredentials.refresh_token = data["refresh_token"]
                    else:
                        logger.error("can't refresh_remote_token: %s\n Create a new one", data)
                        bad_remote_token = True
                if bad_remote_token:
                    otp_code = self.get_otp_code()
                    self._get_remote_access_token(otp_code)
                self.remote_token_last_update = datetime.now()
                self.mqtt_client.username_pw_set("IMA_OAUTH_ACCESS_TOKEN", self.remoteCredentials.access_token)
                return True
            except RateLimitException as e:
                logger.error("Can't refresh remote token please wait... %s", e)
                return False
            except (RequestException, KeyError, AttributeError, RemoteException, ConfigException):
                logger.exception("Can't refresh remote token, please redo otp procedure")
                return False

    def get_sms_otp_code(self):
        res = self.manager.post(
            "https://api.groupe-psa.com/applications/cvs/v4/mobile/smsCode?client_id=" + self.account_info.client_id,
            headers=self.headers, timeout=TIMEOUT_IN_S)
        logger.info("SMS code request: HTTP %s", res.status_code)
        if not res.ok:
            logger.error("SMS code request failed: HTTP %s %s", res.status_code, res.text)
            raise SmsRequestFailed(res.status_code, res.text)
        return res

    # 6 otp by day
    @rate_limit(6, 3600 * 24)
    def get_otp_code(self):
        try:
            otp_code = self.otp.get_otp_code()
        except ConfigException:
            logger.exception("get_otp_code:")
            # load_otp() re-reads otp.bin from disk: it is the ONLY recovery when the pickled
            # session is desynchronized with the server. force_new would NOT reload it (it
            # always takes the "Please redo otp config" branch and returns False), so the old
            # code retried the very session that just failed.
            if not self.load_otp():
                raise
            otp_code = self.otp.get_otp_code()
        save_otp(self.otp)
        return otp_code

    def _get_remote_access_token(self, password):
        res = self.manager.post(REMOTE_URL + self.account_info.client_id,
                                json={"grant_type": "password", "password": password},
                                headers=self.headers, timeout=TIMEOUT_IN_S)
        data = res.json()
        try:
            self.remoteCredentials.access_token = data["access_token"]
            self.remoteCredentials.refresh_token = data["refresh_token"]
        except KeyError as e:
            raise RemoteException("get_remote_access_token: bad response" + str(data)) from e
        return res

    def horn(self, vin, count):
        msg = self.mqtt_request(vin, {"nb_horn": count, "action": "activate"}, "/Horn")
        logger.info(msg)
        self.mqtt_client.publish(msg)

    def lights(self, vin, duration: int):
        msg = self.mqtt_request(vin, {"action": "activate", "duration": duration}, "/Lights")
        logger.info(msg)
        self.publish(msg)

    @rate_limit(6, 60 * 20)
    def wakeup(self, vin):
        logger.info("ask wakeup to %s", vin)
        msg = self.mqtt_request(vin, {"action": "state"}, "/VehCharge/state")
        logger.info(msg)
        self.last_wakeup[vin] = msg
        return self.publish(msg)

    def _answers_last_wakeup(self, data):
        """False for an answer to another wakeup than the last one sent: the server answers one sent while another is
        on its way with an error straight away. Without both correlation_ids to compare it counts."""
        sent = self.last_wakeup.get(data["vin"])
        sent_id = sent.data.get("correlation_id") if sent is not None else None
        return not sent_id or not data.get("correlation_id") or data["correlation_id"] == sent_id

    def heard_since(self, vin, since):
        """True once the car has answered or sent an event after since (a time.time() value)."""
        return self.car_heard.get(vin, 0) >= since

    def _publish_when_awake(self, vin, request) -> bool:
        if time.time() - self.car_heard.get(vin, 0) < AWAKE_WINDOW_S or self.car_switched_on(vin):
            return self._publish_checked(request)
        if self._waiting_for_wakeup(vin):
            # a wakeup is already on its way, so this one goes with the others when it gets an answer
            self.after_wakeup[vin][1].append(request)
            return True
        self.after_wakeup[vin] = (time.time(), [request])
        self.queue_wakeups[vin] = (1, 0)
        try:
            woken = self.wakeup(vin)
        except RateLimitException:
            logger.warning("wakeup limit reached, sending %s to the car without one", request.topic)
            self.after_wakeup.pop(vin, None)
            return self._publish_checked(request)
        if not woken:
            self.after_wakeup.pop(vin, None)
        return woken

    def _wakeup_not_answered(self, vin):
        if not self._waiting_for_wakeup(vin):
            return
        sent, unanswered = self.queue_wakeups.get(vin, (1, 0))
        if sent < WAKEUP_TRIES:
            # the wait for the car starts again with the new wakeup
            _, requests = self.after_wakeup[vin]
            self.after_wakeup[vin] = (time.time(), requests)
            self.queue_wakeups[vin] = (sent + 1, unanswered + 1)
            logger.warning("the car didn't answer the wakeup, trying once more")
            # Not on the mqtt thread: publish can refresh the token over http, which would hold up the connection.
            threading.Thread(target=self._wake_again, args=(vin,), daemon=True).start()
            return
        self.queue_wakeups[vin] = (sent, unanswered + 1)
        threading.Thread(target=self._send_precond_unwoken, args=(vin,), daemon=True).start()

    def _wake_again(self, vin):
        try:
            woken = self.wakeup(vin)
        except RateLimitException:
            woken = False
        if not woken:
            sent, _ = self.queue_wakeups.get(vin, (1, 0))
            self.queue_wakeups[vin] = (sent, sent)
            self._send_precond_unwoken(vin)

    def _send_precond_unwoken(self, vin):
        """Sends the preconditioning waiting for a car that didn't wake, see WAKEUP_TRIES. Schedules stay queued, so
        schedule_delivery can tell they were never sent."""
        _, requests = self.after_wakeup.get(vin, (0, []))
        request = self._latest_precond(vin)
        if request is None or request not in requests:
            return
        requests.remove(request)
        logger.warning("the car didn't wake, sending %s anyway", request.topic)
        if not self._publish_checked(request):
            sent_at, activate, _ = self.precond_sent[vin]
            self.precond_sent[vin] = (sent_at, activate, None)

    def _latest_precond(self, vin):
        """The last preconditioning() request for this car, or None if there's none or it couldn't be sent."""
        return self.precond_sent.get(vin, (0, False, None))[2]

    def _publish_checked(self, request) -> bool:
        """Publishes request, and if it's the latest preconditioning, sends it once more unless the server
        acknowledges it within PRECOND_ACK_WAIT_S."""
        published = self.publish(request)
        if published and request is self._latest_precond(request.vin):
            timer = threading.Timer(PRECOND_ACK_WAIT_S, self._resend_unacknowledged, args=(request,))
            timer.daemon = True
            timer.start()
        return published

    def _note_precond_acknowledgement(self, data):
        # Any message about the request counts: the server's ProcessManagement messages come within seconds.
        request = self._latest_precond(data.get("vin"))
        correlation_id = data.get("correlation_id")
        if request is not None and correlation_id and correlation_id == request.data.get("correlation_id"):
            self.precond_acked[data["vin"]] = correlation_id

    def _resend_unacknowledged(self, request):
        vin = request.vin
        if request is not self._latest_precond(vin) or self.precond_resent.get(vin) is request:
            return
        correlation_id = request.data.get("correlation_id")
        if correlation_id and self.precond_acked.get(vin) == correlation_id:
            return
        if self.preconditioning_result(vin)["state"] != SCHEDULE_WAITING:
            return
        logger.warning("no acknowledgement of %s within %d s, sending it again", request.topic, PRECOND_ACK_WAIT_S)
        self.precond_resent[vin] = request
        self.publish(request)

    def wakeup_failed_since(self, vin, since):
        """True once a wakeup got an error back after since (a time.time() value): the car didn't answer it."""
        replied_at, answered = self.wake_replies.get(vin, (0, True))
        return replied_at >= since and not answered

    def _waiting_for_wakeup(self, vin):
        queued_at, _ = self.after_wakeup.get(vin, (0, []))
        sent, unanswered = self.queue_wakeups.get(vin, (1, 0))
        return time.time() - queued_at < WAKEUP_WAIT_S and unanswered < sent

    def _not_woken(self, vin, since):
        """True when requests queued after since were never sent, because the car didn't wake for them."""
        queued_at, requests = self.after_wakeup.get(vin, (0, []))
        return bool(requests) and queued_at >= since and not self._waiting_for_wakeup(vin)

    def _queued_for_wakeup(self, request):
        """True while request waits for the car to wake, and after the car didn't wake for it."""
        return any(request in requests for _, requests in self.after_wakeup.values())

    def _send_after_wakeup(self, vin):
        # Requests the car didn't wake for stay queued, so schedule_delivery and preconditioning_result can tell they
        # were never sent.
        if not self._waiting_for_wakeup(vin):
            return
        _, requests = self.after_wakeup.pop(vin)
        self.queue_wakeups.pop(vin, None)
        for request in requests:
            logger.info("car is awake, sending %s", request.topic)
            # Not on the mqtt thread: publish can refresh the token over http, which would hold up the connection.
            threading.Thread(target=self._publish_checked, args=(request,), daemon=True).start()

    def lock_door(self, vin, lock: bool):
        if lock:
            value = "lock"
        else:
            value = "unlock"

        msg = self.mqtt_request(vin, {"action": value}, "/Doors")
        logger.info(msg)
        self.publish(msg)
        return True

    def preconditioning(self, vin, activate: bool):
        if activate:
            value = "activate"
        else:
            value = "deactivate"
        if vin in self.precond_programs:
            programs = self.precond_programs[vin]
        else:
            programs = DEFAULT_PRECONDITIONING_PROGRAM
        msg = self.mqtt_request(vin, {"asap": value, "programs": programs}, "/ThermalPrecond")
        logger.info("Preconditioning: %s", msg)
        # recorded before it goes, so the acknowledgement check knows it's the latest
        sent_at = time.time()
        self.precond_sent[vin] = (sent_at, activate, msg)
        published = self._publish_when_awake(vin, msg)
        if not published:
            self.precond_sent[vin] = (sent_at, activate, None)
        return published

    def preconditioning_result(self, vin):
        """Where the last preconditioning() for this car stands, for Home Assistant to poll: "state" is PRECOND_NONE
        before any, SCHEDULE_WAKING while it waits for the car to wake, SCHEDULE_WAITING once sent until the car
        answers, then SCHEDULE_CONFIRMED or SCHEDULE_FAILED. It's confirmed by the server's answer, or by an event
        from the car or its status from the rest api showing preconditioning on (or off), whatever the server answers,
        so asking again later can turn a failure into a confirmation. A failure has the server's "return_code", or none
        if the command never left: it couldn't be sent, or no wakeup got an answer in time. A refusal also says whether
        it was because the car is switched on ("car_on")."""
        if vin not in self.precond_sent:
            return {"state": PRECOND_NONE}
        sent_at, activate, request = self.precond_sent[vin]
        result = {"activate": bool(activate), "seconds_ago": round(time.time() - sent_at)}
        replied_at, return_code = self.precond_replies.get(vin, (0, None))
        if request is None:
            return dict(result, state=SCHEDULE_FAILED, return_code=None)
        if self._queued_for_wakeup(request):
            if self._waiting_for_wakeup(vin):
                return dict(result, state=SCHEDULE_WAKING)
            return dict(result, state=SCHEDULE_FAILED, return_code=None)
        reported_at, precond_on = self.car_precond_on.get(vin, (0, None))
        reported = reported_at >= sent_at and precond_on == bool(activate)
        if reported or self._status_shows_precond(vin, activate, sent_at):
            return dict(result, state=SCHEDULE_CONFIRMED)
        if replied_at < sent_at:
            return dict(result, state=SCHEDULE_WAITING)
        if return_code == "0":
            return dict(result, state=SCHEDULE_CONFIRMED, return_code=return_code)
        return dict(result, state=SCHEDULE_FAILED, return_code=return_code,
                    car_on=self._refused_car_on_since(vin, sent_at))

    def _status_shows_precond(self, vin, activate, since):
        """True when the car's status from the rest api, refreshed every couple of minutes, shows preconditioning on
        (or off) as of since, see PRECOND_STATUS_SLACK_S."""
        try:
            air_conditioning = self.vehicles_list.get_car_by_vin(vin).status.preconditionning.air_conditioning
            status, updated_at = air_conditioning.status, air_conditioning.updated_at.timestamp()
        except (AttributeError, TypeError):
            return False
        wanted = PRECOND_ON_STATUSES if activate else PRECOND_OFF_STATUSES
        return status in wanted and updated_at >= since - PRECOND_STATUS_SLACK_S

    def get_preconditioning_program(self, vin):
        return self.precond_programs.get(vin, DEFAULT_PRECONDITIONING_PROGRAM)

    def schedule_delivery(self, vin, programs, sent_at):
        """SCHEDULE_CONFIRMED once the car reports these programs after sent_at (a time.time() value),
        SCHEDULE_FAILED if it answered the request with an error, or SCHEDULE_CAR_ON if it refused because it's
        switched on, SCHEDULE_WAKING while they wait for the car to wake,
        SCHEDULE_NOT_WOKEN if it didn't, so they weren't sent, and SCHEDULE_WAITING until it answers."""
        reported_at, reported = self.car_programs.get(vin, (0, None))
        if reported_at >= sent_at and same_preconditioning_programs(reported, programs):
            return SCHEDULE_CONFIRMED
        if self._waiting_for_wakeup(vin):
            return SCHEDULE_WAKING
        if self._not_woken(vin, sent_at):
            return SCHEDULE_NOT_WOKEN
        replied_at, return_code = self.precond_replies.get(vin, (0, None))
        if replied_at >= sent_at and return_code != "0":
            return SCHEDULE_CAR_ON if self._refused_car_on_since(vin, sent_at) else SCHEDULE_FAILED
        return SCHEDULE_WAITING

    def _refused_car_on_since(self, vin, since):
        return self.precond_refused_car_on.get(vin, 0) >= since

    def has_preconditioning_program(self, vin):
        """True once the car's schedules are known: seen from the car, sent to it, or kept from a previous run."""
        return vin in self.precond_programs

    def reported_preconditioning_program(self, vin):
        """The programs the car last reported since the app started, or None before it has."""
        return self.car_programs.get(vin, (0, None))[1]

    def keep_programs_in(self, programs_file):
        """Keep the schedules in this file from now on, starting with the ones saved there by a previous run."""
        self.programs_file = programs_file
        self.precond_programs = self._load_precond_programs()

    def _load_precond_programs(self):
        if not os.path.isfile(self.programs_file):
            return {}
        try:
            with open(self.programs_file, encoding="utf-8") as file:
                saved = json.load(file)
            return {vin: validate_preconditioning_programs(programs) for vin, programs in saved.items()}
        except (OSError, ValueError, TypeError, AttributeError):
            logger.warning("Ignoring unreadable %s", self.programs_file)
            return {}

    def _remember_precond_programs(self, vin, programs):
        # The car repeats its schedules in every event while it's awake; only a change needs saving.
        if self.precond_programs.get(vin) == programs:
            return
        self.precond_programs[vin] = programs
        if not self.programs_file:
            return
        temp_file = self.programs_file + ".tmp"
        try:
            with open(temp_file, "w", encoding="utf-8") as file:
                json.dump(self.precond_programs, file)
            os.replace(temp_file, self.programs_file)
        except OSError:
            logger.exception("Couldn't save %s", self.programs_file)

    def set_preconditioning_program(self, vin, programs):
        # Push the schedule to the car. "asap": "deactivate" updates the stored
        # programs without starting an immediate preconditioning session. Record
        # the programs locally only once the publish succeeds, so a command that
        # never reaches the car (e.g. the remote token can't refresh) doesn't
        # leave get_preconditioning_program reporting a schedule the car never
        # received.
        msg = self.mqtt_request(vin, {"asap": "deactivate", "programs": programs}, "/ThermalPrecond")
        logger.info("set_preconditioning_program: %s", msg)
        published = self._publish_when_awake(vin, msg)
        if published:
            self._remember_precond_programs(vin, programs)
        return published

    def load_otp(self, force_new=False):
        otp_session = load_otp()
        if otp_session is None or force_new:
            logger.error("Please redo otp config")
            return False
        self.otp = otp_session
        return True

    def change_charge_hour(self, vin, hour, miinute):
        self.veh_charge_request(vin, hour, miinute, DELAYED_CHARGE)
        return True

    def charge_now(self, vin, now):
        if now:
            charge_type = IMMEDIATE_CHARGE
        else:
            charge_type = DELAYED_CHARGE
        hour, minute = self.get_charge_hour(vin)
        res = self.veh_charge_request(vin, hour, minute, charge_type)
        logger.info("charge_now: %s", res)
        return True

    def get_charge_hour(self, vin):
        hour_str = self.vehicles_list.get_car_by_vin(vin).status.get_energy('Electric').charging.next_delayed_time
        try:
            return parse_hour(hour_str)[:2]
        except IndexError:
            logger.exception("Can't get charge hour: %s", hour_str)
            return None
