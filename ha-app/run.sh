#!/bin/sh
# Start psacc with its config in the app's config folder and the web UI behind HA ingress.
cd "$PSACC_CONFIG_DIR" || exit 1
# PSACC_OPTIONS holds several flags, so it is left unquoted.
exec psa-car-controller -p "$PSACC_PORT" -l 0.0.0.0 -b "$PSACC_BASE_PATH" $PSACC_OPTIONS
