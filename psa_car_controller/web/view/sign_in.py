"""Choices, checks and messages for the sign-in form, kept free of Dash so they can be unit tested."""
import re

# Markets where the Stellantis apps are sold. The code must match a key in the
# brand app's cultures.json, so it's picked from a list rather than typed.
COUNTRIES = [
    ("AT", "Austria"), ("BE", "Belgium"), ("HR", "Croatia"), ("CZ", "Czechia"),
    ("DK", "Denmark"), ("FI", "Finland"), ("FR", "France"), ("DE", "Germany"),
    ("GR", "Greece"), ("HU", "Hungary"), ("IE", "Ireland"), ("IT", "Italy"),
    ("LU", "Luxembourg"), ("NL", "Netherlands"), ("NO", "Norway"), ("PL", "Poland"),
    ("PT", "Portugal"), ("RO", "Romania"), ("SK", "Slovakia"), ("SI", "Slovenia"),
    ("ES", "Spain"), ("SE", "Sweden"), ("CH", "Switzerland"), ("TR", "Turkey"),
    ("GB", "United Kingdom"),
]
BRANDS = [
    ("com.psa.mym.mypeugeot", "Peugeot"), ("com.psa.mym.myopel", "Opel"),
    ("com.psa.mym.mycitroen", "Citroën"), ("com.psa.mym.myds", "DS"),
    ("com.psa.mym.myvauxhall", "Vauxhall"),
]
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RETURN_CODE_PATTERN = re.compile(r"^[A-Z0-9_]{1,40}$")
SIGN_IN_HINT = "This can take a moment while we sign in for you."
SIGN_IN_FAILED = ("We couldn't sign you in just now. Check your connection and try again. "
                  "The log has the details.")


def is_sign_in_ready(app_name, email, password, countrycode):
    return bool(app_name and countrycode and password and email and EMAIL_PATTERN.match(email.strip()))


def sign_in_rejected_message(return_code, app_name, countrycode):
    """A plain explanation of the account service's return code, with what to check next."""
    brand = dict(BRANDS).get(app_name, "car")
    country = dict(COUNTRIES).get(countrycode, countrycode)
    if return_code == "NEED_CREATION":
        return (f"No {brand} account found for this email (country: {country}). "
                "Check the email, car brand and country.")
    # Only show the code if it looks like one, never the service's raw reply.
    code = f" (code {return_code})" if RETURN_CODE_PATTERN.match(str(return_code)) else ""
    return f"{brand} didn't accept this sign-in{code}. Check your email and password, then try again."
