# Built by Home Assistant from the folder ha-app/sync.ps1 lays out:
# this file, run.sh, constraints.txt and src/ (pyproject.toml, LICENSE, psa_car_controller/).
# Python 3.12 to match the dev venv the UI is tested in.
FROM python:3.12.10-slim-bookworm

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PSACC_BASE_PATH=/ PSACC_PORT=5000 PSACC_OPTIONS="-c -r --web-conf" PSACC_CONFIG_DIR=/config

# constraints.txt pins the same package versions as the dev venv.
COPY constraints.txt /tmp/constraints.txt
COPY src /tmp/src
RUN pip install -c /tmp/constraints.txt /tmp/src && rm -rf /tmp/src /tmp/constraints.txt

# Headless Chromium for the automatic sign-in.
RUN playwright install --with-deps --only-shell chromium && rm -rf /var/lib/apt/lists/*

COPY run.sh /run.sh
# The file may arrive with Windows line endings.
RUN sed -i 's/\r$//' /run.sh && chmod +x /run.sh
WORKDIR /config
CMD ["/run.sh"]
