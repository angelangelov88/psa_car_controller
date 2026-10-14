# psacc (fork)

PSA Car Controller from the [angelangelov88 fork](https://github.com/angelangelov88/psa_car_controller),
built from source as a local Home Assistant app. It has one page, **Home**: the car's status and
preconditioning (switch it on now, and the weekly schedules).

## How it works

```
Browser or HA phone app ──(HA login)──► HA ingress ──┐
HA automations and sensors ─────────────────────────►├─► this app (psacc, port 5000) ──► Stellantis API + MQTT ──► car
Optional direct port (no login, off by default) ─────┘
```

- HA builds the app from this folder (`Dockerfile`, `run.sh`) plus the fork's source, which
  `sync.ps1` copies over the Samba share. Nothing is pulled from GitHub.
- `run.sh` starts `psa-car-controller` with the web UI on port 5000 and its config in `/config`.
- `/config` is `addon_configs/local_psacc_fork` on the Samba share:
  - `config.json`: account and tokens
  - `otp.bin`: the remote-control device created by the SMS code and PIN step
  - `cars.json`
  - `info.db`
  - `activity.log`: the log

  These files are private. Never commit them or share them.
- Other HA parts call the app at `http://local-psacc-fork:5000`, for example `/preconditioning/<VIN>/1`
  and `/get_vehicleinfo/<VIN>`. The API is described in [docs/psacc_api.md](../docs/psacc_api.md).
- **Only one psacc may use an account at a time.** Two copies, such as the stock psacc app or a local copy
  signed in to the same account, fight over its tokens and both stop working.

## Opening it

- From HA's sidebar (**psacc (fork)**), or bookmark `http://<HA address>/local_psacc_fork`. Both are
  behind the HA login and work away from home over a VPN into the home network.
- Optional direct port: set a host port for 5000/tcp in the app's **Network** tab to open the web UI at
  `http://<HA address>:<port>` without HA. It has **no login**, so anyone who can reach that port can control
  the car. It's off by default.
- The gear icon opens the log. Until setup is finished, every page opens the setup step you're on.

## First install

1. On the PC, from the fork: `& .\ha-app\sync.ps1`. It copies to `\\<HA address>\addons\psacc_fork` by
   default; pass `-Target` for another share.
2. In HA: **Settings → Apps → App store → ⋮ → Check for updates**, then open **psacc (fork)** under Local
   apps and click **Install**. The build takes about 40 minutes on a small VM.
3. Stop any other psacc using the same account, then **Start** this one and open it.
4. **Sign in:** choose your brand, then enter your email, password and country. If automatic sign-in fails,
   follow the **Finish sign-in manually** steps it shows, and paste the code from the browser's DevTools.
5. **Get a code:** click **Send me a code by SMS** once and wait for the text; only a few codes can be sent
   each day. Enter all the digits and your 4-digit app PIN, then **Finish setup**.

## Updating

1. Run `sync.ps1` again.
2. In HA, open the app:
   - **Rebuild** if `version` in `config.yaml` is unchanged.
   - Otherwise **⋮ → Check for updates** in the app store, then **Update**. Changes to `config.yaml` itself,
     such as ports or boot, only apply this way.
3. Avoid restarting the app within seconds of it starting. On start, psacc swaps its sign-in token for a
   new one and saves it straight away; if it's stopped in that brief gap, it's signed out and you have to
   sign in again.

## When something goes wrong

Read the log first. It's in three places:
- the gear icon in the app
- the app's **Log** tab in HA
- `addon_configs/local_psacc_fork/activity.log` on the Samba share

The log contains your VIN, email and account id, so remove those before sharing it. For build failures, see
**Settings → System → Logs → Supervisor**.

| Log says | Meaning | What to do |
|---|---|---|
| `Can't refresh token: 400 - invalid_grant` / `Please reconnect` | Signed out: the token was lost or revoked, for example after a password change. | Open the app; it shows the sign-in step. Sign in again. A new SMS is usually not needed. |
| `Rate limit exceeded for <action>` | psacc caps some actions: 6 wake-ups per 20 minutes, 6 token refreshes per 30 minutes, 6 OTP codes per day. | Wait, then try again. |
| MQTT `result code 16`, or reconnect messages | The connection to the car service dropped and reconnected. A command sent during the drop can be lost. | Send the command again. |
| `No config file` | Not set up yet. | Sign in. |
| `Automatic login failed` | Headless sign-in timed out or was blocked. | Use the manual steps on the page. |
| App keeps stopping, or HA shows it as not running | Usually a crash at start. | Read the end of the log, then restart. |

## Trying changes locally

Never point a local copy at your real account while the HA app runs. Use one of these instead:

- **Offline, on test data:** shows the Home page and settings; nothing reaches a car.
  ```powershell
  New-Item -ItemType Directory -Force C:\psacc-local | Out-Null
  Copy-Item .\tests\data\config.json C:\psacc-local\
  New-Item -ItemType File -Force C:\psacc-local\otp.bin | Out-Null   # delete it to see "setup unfinished"
  cd C:\psacc-local
  <fork>\.venv\Scripts\python.exe -m psa_car_controller --web-conf --offline -l 127.0.0.1 -p 5003 -f config.json
  ```
  Then open `http://127.0.0.1:5003`.
- **Fake setup:** set `PSACC_FAKE_SETUP=1` and start with `--web-conf` in an empty folder to walk through the
  sign-in and code steps without contacting Stellantis or using an SMS. Special emails show the error cases;
  see `psa_car_controller/web/view/setup_flow.py`.
- **Checks before syncing:**
  ```powershell
  .venv\Scripts\python.exe -m unittest
  .venv\Scripts\python.exe -m autopep8 --ignore-local-config --max-line-length 120 --diff <changed files>
  .venv\Scripts\python.exe -m prospector --without-tool dodgy <changed files>
  ```
