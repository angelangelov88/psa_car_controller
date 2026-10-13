# psacc (fork)

PSA Car Controller from the [angelangelov88 fork](https://github.com/angelangelov88/psa_car_controller),
built from source as a local Home Assistant app.

- Open it from the sidebar (**psacc (fork)**). It has no open port; other apps and HA reach it at
  `http://local-psacc-fork:5000`.
- Its config, tokens and OTP state are in `addon_configs/local_psacc_fork` on the Samba share.
- Only run one psacc per Vauxhall/Stellantis account. Stop any other copy before starting this one.

## Updating

From the fork on the PC, run `ha-app/sync.ps1`, then in HA open the app and click **Rebuild**.
