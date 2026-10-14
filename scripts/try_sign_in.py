"""Try the automatic (headless) sign-in on this PC, optionally with the CPU slowed down to mimic a small VM.

It runs the same code the app uses to get a sign-in code from Stellantis, then throws the code away. It never
exchanges the code for tokens, so it doesn't sign anything in and can't disturb a psacc that's already running on
the same account.

Run it from the fork's folder, in your own terminal (it asks for your password):

    .venv\\Scripts\\python.exe scripts\\try_sign_in.py --cpu-slowdown 6

Options:
    --cpu-slowdown N   Make the browser's CPU N times slower (1 = normal speed). 6 is roughly a 2-CPU HA VM.
    --latency MS       Delay every request by MS milliseconds, like a busy HA VM (8000 made the sign-in pages'
                       script loader give up before the app worked around it).
    --show             Show the browser window instead of running it headless.
    --show-password    Show the password as you type it. It stays in the terminal, so clear it afterwards (cls).
"""
import argparse
import getpass
import logging
import os
import tempfile
import time

from playwright.sync_api import sync_playwright

from psa_car_controller.psa.constants import BRAND
from psa_car_controller.psa.setup.app_decoder import get_content_from_apk
from psa_car_controller.psa.setup.headless_oauth import (BROWSER_ARGS, REQUIREJS_NO_TIMEOUT, TIMEOUT_MS,
                                                         FormException, check_for_error, get_code)
from psa_car_controller.psacc.application.psa_client import PSAClient

DEFAULT_BRAND = "com.psa.mym.myvauxhall"


def ask_brand():
    brands = list(BRAND)
    for number, package in enumerate(brands, 1):
        print(f"  {number}. {BRAND[package]['app_name']}")
    choice = input(f"Brand number [{brands.index(DEFAULT_BRAND) + 1}]: ").strip()
    return brands[int(choice) - 1] if choice else DEFAULT_BRAND


def sign_in_address(brand, country):
    """The sign-in page address and the app's redirect scheme, built as the app builds them.

    They need only the app's client id, from the brand data, so no account service is called here.
    """
    apk = get_content_from_apk(brand.split(".")[-1] + ".apk", country)
    client = PSAClient(None, apk.client_id, apk.client_secret, None, None, BRAND[brand]["realm"], country,
                       BRAND[brand]["brand_code"])
    return client.manager.generate_redirect_url(), client.manager.redirect_uri.split("://")[0]


def slow_down(page, cpu_slowdown, latency):
    cdp = page.context.new_cdp_session(page)
    if cpu_slowdown > 1:
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": cpu_slowdown})
    if latency > 0:
        cdp.send("Network.enable")
        cdp.send("Network.emulateNetworkConditions",
                 {"offline": False, "latency": latency, "downloadThroughput": -1, "uploadThroughput": -1})


def try_sign_in(auth_url, scheme, email, password, args):
    started = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.show, args=BROWSER_ARGS)
        context = browser.new_context()
        context.add_init_script(REQUIREJS_NO_TIMEOUT)
        page = context.new_page()
        page_errors = []
        page.on("pageerror", lambda error: page_errors.append(str(error).splitlines()[0]))
        slow_down(page, args.cpu_slowdown, args.latency)
        try:
            page.goto(auth_url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            get_code(page, scheme, email, password)
        except Exception as error:  # pylint: disable=broad-except
            print(f"\nFAILED after {time.time() - started:.0f} s: {str(error).splitlines()[0]}")
            print(f"Last page: {page.url.split('?')[0]}")
            for page_error in page_errors:
                print(f"Page error: {page_error}")
            try:
                check_for_error(page)
            except FormException as form_error:
                print(f"The form says: {form_error}")
            return
        finally:
            browser.close()
    print(f"\nOK: got a sign-in code after {time.time() - started:.0f} s. It was thrown away, not used.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cpu-slowdown", type=float, default=1)
    parser.add_argument("--latency", type=int, default=0)
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--show-password", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    brand = ask_brand()
    email = input("Email: ").strip()
    password = input("Password: ") if args.show_password else getpass.getpass("Password (not shown): ")
    country = input("Country code [GB]: ").strip().upper() or "GB"
    if not email or not password:
        print("The email or password came through empty. Run this in PowerShell or Windows Terminal.")
        return

    # Keep the downloaded brand data and any car list out of the fork's folder.
    os.chdir(tempfile.mkdtemp(prefix="psacc-try-sign-in-"))
    auth_url, scheme = sign_in_address(brand, country)

    try_sign_in(auth_url, scheme, email, password, args)


if __name__ == "__main__":
    main()
