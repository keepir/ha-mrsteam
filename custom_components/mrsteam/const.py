"""Constants for the MrSteam iSteamX integration."""
from __future__ import annotations

DOMAIN = "mrsteam"

# MrSteam cloud backend (public app values, not secrets)
REGION = "us-east-2"
USER_POOL_ID = "us-east-2_mqB0IdHVw"
APP_CLIENT_ID = "3j8tuk3vdmqvn6nehrq0lg6rh0"
IDENTITY_POOL_ID = "us-east-2:c7dc2adc-d39d-4aab-abd6-23df3f362a3f"
LOGIN_PROVIDER = f"cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"
IOT_ENDPOINT = "amfewofqcj9vf-ats.iot.us-east-2.amazonaws.com"
REST_BASE = "https://hn0d2u7uek.execute-api.us-east-2.amazonaws.com/latest"
DISCOVERY_PATH = "/user/get-user-devices"

CONF_MODEL_NUMBER = "model_number"
DEFAULT_MODEL_NUMBER = "SU-70"

# Polling (seconds). HTTPS shadow reads are cheap and don't touch the
# controller's MQTT session; the MQTT-read fallback (client_id == thingName)
# can bump the wall controller, so it polls far less often.
POLL_IDLE_HTTPS = 30
POLL_RUNNING_HTTPS = 15
POLL_IDLE_MQTT = 120
POLL_RUNNING_MQTT = 30

# Delayed refreshes after a command (seconds), per the handoff.
REFRESH_DELAYS = (4, 12)

# How long a commanded ("requested") value is shown before falling back to
# what the device reports, if the device never confirms.
PENDING_SECONDS = 45

# Brightness
MS_BRIGHT_MIN = 1
MS_BRIGHT_MAX = 33

# Light types
LIGHT_OFF = 0
LIGHT_RGB = 1
LIGHT_WHITE = 4
