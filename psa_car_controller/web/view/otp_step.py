"""Checks and messages for the code step (text me a code, then code + PIN), kept free of Dash so they can be
unit tested."""
import math
import re

SMS_COOLDOWN_IN_S = 60
# Vauxhall texts 9 digits; leave room for other brands and lengths.
SMS_CODE_PATTERN = re.compile(r"^\d{4,12}$")
PIN_PATTERN = re.compile(r"^\d{4}$")
SEND_LABEL = "Send me a code by SMS"
RESEND_LABEL = "Send a new code"
SMS_HINT = "Only a few codes can be sent each day, so wait for the text before asking for another."
NOT_SIGNED_IN = "Sign in first (step 1), then ask for a code."
CODE_REJECTED = ("That code or PIN wasn't accepted. Check both and try again. "
                 "If the code is more than a few minutes old, ask for a new one.")
FINISH_FAILED = "We couldn't finish setup just now. Try again in a moment. The log has the details."


def clean_code(sms_code):
    """The code without spaces, in case it is typed in groups as the text shows it."""
    return "".join((sms_code or "").split())


def is_code_ready(sms_code, pin):
    return bool(SMS_CODE_PATTERN.match(clean_code(sms_code)) and pin and PIN_PATTERN.match(pin.strip()))


def sms_button_state(sent_at, now):
    """(disabled, label, still counting down) for the send button, given when the last code was sent."""
    if sent_at is None:
        return False, SEND_LABEL, False
    remaining = math.ceil(SMS_COOLDOWN_IN_S - (now - sent_at))
    if remaining > 0:
        return True, f"Send a new code in {remaining} s", True
    return False, RESEND_LABEL, False


def sms_failed_message(status_code):
    """A plain explanation of why the code wasn't sent, with what to do next."""
    if status_code == 429:
        return "Too many codes have been asked for today. Try again tomorrow."
    if status_code in (401, 403):
        return "Your sign-in has expired. Sign in again (step 1), then ask for a new code."
    return "We couldn't send a code just now. Try again in a minute. The log has the details."
