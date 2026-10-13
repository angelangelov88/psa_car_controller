# psacc (fork)

PSA Car Controller from the [angelangelov88 fork](https://github.com/angelangelov88/psa_car_controller),
built from source as a local Home Assistant app.

- Open it from the sidebar (**psacc (fork)**), or bookmark `http://<HA address>/hassio/ingress/local_psacc_fork`.
  Both are behind the HA login. Other apps and HA reach it at `http://local-psacc-fork:5000`.
- Optional direct port: set a host port for 5000/tcp in the app's **Network** tab to open the web UI at
  `http://<HA address>:<port>` without HA. It has **no login**, so anyone who can reach that port can control
  the car. It's off by default.
- Its config, tokens and OTP state are in `addon_configs/local_psacc_fork` on the Samba share.
- Only run one psacc per Vauxhall/Stellantis account. Stop any other copy before starting this one.

## Updating

From the fork on the PC, run `ha-app/sync.ps1`. Then in HA open the app and click **Rebuild**, or, if
`version` in `config.yaml` changed, reload the app store (**Check for updates**) and click **Update**.
