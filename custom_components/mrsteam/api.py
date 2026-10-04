"""MrSteam iSteamX cloud API (AWS Cognito + AWS IoT device shadow).

Read path:   HTTPS GetThingShadow (SigV4, Cognito identity creds) only.
             Reads with client_id == thingName are NEVER used: they kick the
             wall controller off AWS IoT. If HTTPS is denied, entities run in
             assumed state.
Command path: MQTT publish to $aws/things/<thing>/shadow/update, QoS 1,
             unique client id app-ha<random>-dev, clientToken app-<thing>.
             Only state.desired is ever written.

All blocking work (boto3 / pycognito / paho) runs in executor threads.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging
import secrets
import ssl
import threading
from typing import Any
from urllib.parse import quote

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    APP_CLIENT_ID,
    DISCOVERY_PATH,
    IDENTITY_POOL_ID,
    IOT_ENDPOINT,
    LOGIN_PROVIDER,
    REGION,
    REST_BASE,
    USER_POOL_ID,
)

_LOGGER = logging.getLogger(__name__)

MQTT_TIMEOUT = 10


class MrSteamError(Exception):
    """Generic MrSteam failure."""


class MrSteamAuthError(MrSteamError):
    """Login failed (bad credentials or account problem)."""


class ReadDenied(MrSteamError):
    """HTTPS shadow read not permitted for this identity."""


class ReadsUnavailable(MrSteamError):
    """No safe read path; run in assumed state."""


# ── SigV4 presigned websocket URL for AWS IoT ───────────────────────────────


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret: str, datestamp: str, region: str, service: str) -> bytes:
    """Derive the SigV4 signing key."""
    k_date = _hmac(("AWS4" + secret).encode("utf-8"), datestamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    return _hmac(k_service, "aws4_request")


def presign_iot_ws_path(
    host: str,
    region: str,
    access_key: str,
    secret_key: str,
    session_token: str | None,
    now: dt.datetime | None = None,
) -> str:
    """Return the /mqtt?... path for an AWS IoT websocket connection.

    AWS IoT quirk: the session token is appended AFTER signing and is not part
    of the canonical query string.
    """
    service = "iotdevicegateway"
    now = now or dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-SignedHeaders": "host",
    }
    canonical_qs = "&".join(
        f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted(params.items())
    )
    canonical_request = "\n".join(
        [
            "GET",
            "/mqtt",
            canonical_qs,
            f"host:{host}\n",
            "host",
            hashlib.sha256(b"").hexdigest(),
        ]
    )
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signature = hmac.new(
        signing_key(secret_key, datestamp, region, service),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    path = f"/mqtt?{canonical_qs}&X-Amz-Signature={signature}"
    if session_token:
        path += f"&X-Amz-Security-Token={quote(session_token, safe='')}"
    return path


def find_things(obj: Any) -> list[dict[str, Any]]:
    """Walk a discovery response and return every dict carrying a thingName."""
    found: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        if "thingName" in obj:
            found.append(obj)
        for value in obj.values():
            found.extend(find_things(value))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(find_things(item))
    return found


# ── Client ──────────────────────────────────────────────────────────────────


class MrSteamApi:
    """One MrSteam account."""

    def __init__(
        self, hass: HomeAssistant, email: str, password: str, model_number: str
    ) -> None:
        self.hass = hass
        self.email = email
        self._password = password
        self.model_number = model_number
        self._cognito: Any = None
        self._identity_id: str | None = None
        self._creds: dict[str, Any] | None = None
        self._lock = threading.Lock()
        # None = untested, True = HTTPS works, False = use MQTT fallback
        self.https_read_ok: bool | None = None

    # ── auth (executor) ─────────────────────────────────────────────────────

    def _login_sync(self) -> None:
        from botocore import UNSIGNED  # noqa: PLC0415
        from botocore.config import Config  # noqa: PLC0415
        from botocore.exceptions import ClientError  # noqa: PLC0415
        from pycognito import Cognito  # noqa: PLC0415

        cog = Cognito(
            USER_POOL_ID,
            APP_CLIENT_ID,
            user_pool_region=REGION,
            username=self.email,
            botocore_config=Config(signature_version=UNSIGNED),
        )
        try:
            cog.authenticate(password=self._password)  # SRP
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code", "")
            if code in ("NotAuthorizedException", "UserNotFoundException"):
                raise MrSteamAuthError(code) from err
            raise MrSteamError(str(err)) from err
        self._cognito = cog
        self._identity_id = None
        self._creds = None

    def _id_token_sync(self) -> str:
        with self._lock:
            if self._cognito is None:
                self._login_sync()
            else:
                try:
                    self._cognito.check_token(renew=True)
                except Exception:  # noqa: BLE001 - refresh token expired etc.
                    _LOGGER.debug("Token refresh failed, logging in again")
                    self._login_sync()
            return self._cognito.id_token

    def _aws_creds_sync(self) -> dict[str, Any]:
        id_token = self._id_token_sync()
        with self._lock:
            if self._creds:
                exp = self._creds["Expiration"]
                if exp - dt.datetime.now(dt.timezone.utc) > dt.timedelta(minutes=5):
                    return self._creds
            import boto3  # noqa: PLC0415
            from botocore import UNSIGNED  # noqa: PLC0415
            from botocore.config import Config  # noqa: PLC0415

            client = boto3.client(
                "cognito-identity",
                region_name=REGION,
                config=Config(signature_version=UNSIGNED),
            )
            logins = {LOGIN_PROVIDER: id_token}
            if not self._identity_id:
                self._identity_id = client.get_id(
                    IdentityPoolId=IDENTITY_POOL_ID, Logins=logins
                )["IdentityId"]
            creds = client.get_credentials_for_identity(
                IdentityId=self._identity_id, Logins=logins
            )["Credentials"]
            self._creds = creds
            return creds

    # ── discovery (async) ───────────────────────────────────────────────────

    async def async_login(self) -> None:
        await self.hass.async_add_executor_job(self._id_token_sync)

    async def async_discover(self) -> list[dict[str, Any]]:
        """Return [{thingName, device_id, device_custom_name, connected}, ...]."""
        id_token = await self.hass.async_add_executor_job(self._id_token_sync)
        session = async_get_clientsession(self.hass)
        try:
            async with session.post(
                REST_BASE + DISCOVERY_PATH,
                headers={"Authorization": id_token, "Content-Type": "application/json"},
                json={"model_number": self.model_number, "user_id": self.email},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status in (401, 403):
                    raise MrSteamAuthError(f"discovery HTTP {resp.status}")
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise MrSteamError(f"discovery failed: {err}") from err
        things = find_things(data)
        unique: dict[str, dict[str, Any]] = {}
        for thing in things:
            unique.setdefault(thing["thingName"], thing)
        return list(unique.values())

    # ── shadow read ─────────────────────────────────────────────────────────

    def _get_shadow_https_sync(self, thing: str) -> dict[str, Any]:
        import boto3  # noqa: PLC0415
        from botocore.exceptions import ClientError  # noqa: PLC0415

        creds = self._aws_creds_sync()
        client = boto3.client(
            "iot-data",
            region_name=REGION,
            endpoint_url=f"https://{IOT_ENDPOINT}",
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretKey"],
            aws_session_token=creds["SessionToken"],
        )
        try:
            resp = client.get_thing_shadow(thingName=thing)
        except ClientError as err:
            status = err.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = err.response.get("Error", {}).get("Code", "")
            if status == 403 or code in (
                "ForbiddenException",
                "UnauthorizedException",
                "AccessDeniedException",
            ):
                raise ReadDenied(code or "403") from err
            raise MrSteamError(str(err)) from err
        return json.loads(resp["payload"].read())

    def _mqtt_client(self, client_id: str):  # -> paho Client
        import paho.mqtt.client as mqtt  # noqa: PLC0415

        creds = self._aws_creds_sync()
        try:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=client_id,
                transport="websockets",
                protocol=mqtt.MQTTv311,
            )
        except AttributeError:  # paho-mqtt < 2.0
            client = mqtt.Client(
                client_id=client_id, transport="websockets", protocol=mqtt.MQTTv311
            )
        client.ws_set_options(
            path=presign_iot_ws_path(
                IOT_ENDPOINT,
                REGION,
                creds["AccessKeyId"],
                creds["SecretKey"],
                creds["SessionToken"],
            )
        )
        client.tls_set_context(ssl.create_default_context())
        return client

    @staticmethod
    def _connect(client) -> None:
        connected = threading.Event()
        result: dict[str, Any] = {}

        def _on_connect(_c, _u, _f, rc, *_args):
            result["rc"] = rc
            connected.set()

        client.on_connect = _on_connect
        client.connect(IOT_ENDPOINT, 443, keepalive=30)
        client.loop_start()
        if not connected.wait(MQTT_TIMEOUT):
            client.loop_stop()
            raise MrSteamError("MQTT connect timed out")
        if result.get("rc") != 0:
            client.loop_stop()
            raise MrSteamError(f"MQTT connect refused: {result.get('rc')}")

    @staticmethod
    def _close(client) -> None:
        try:
            client.disconnect()
        finally:
            client.loop_stop()

    def _get_shadow_sync(self, thing: str) -> dict[str, Any]:
        """HTTPS only. Never connect with client_id == thingName: that
        kicks the wall controller off AWS IoT (confirmed Oct 4 2026)."""
        if self.https_read_ok is False:
            raise ReadsUnavailable("HTTPS shadow reads denied for this identity")
        try:
            shadow = self._get_shadow_https_sync(thing)
        except ReadDenied as err:
            _LOGGER.warning(
                "HTTPS shadow read denied (%s). Reads are disabled; entities run "
                "in assumed state (last commanded value) until a safe read path exists",
                err,
            )
            self.https_read_ok = False
            raise ReadsUnavailable(str(err)) from err
        if self.https_read_ok is None:
            _LOGGER.info("HTTPS shadow reads are allowed; using them")
        self.https_read_ok = True
        return shadow

    async def async_get_shadow(self, thing: str) -> dict[str, Any]:
        """Return the shadow 'state' block: {'desired':..., 'reported':...}."""
        shadow = await self.hass.async_add_executor_job(self._get_shadow_sync, thing)
        return shadow.get("state", shadow)

    # ── command ─────────────────────────────────────────────────────────────

    def _update_desired_sync(self, thing: str, fragment: dict[str, Any]) -> None:
        payload = json.dumps(
            {"state": {"desired": fragment}, "clientToken": f"app-{thing}"},
            separators=(",", ":"),
        )
        client = self._mqtt_client(f"app-ha{secrets.token_hex(4)}-dev")
        self._connect(client)
        try:
            info = client.publish(f"$aws/things/{thing}/shadow/update", payload, qos=1)
            info.wait_for_publish(timeout=MQTT_TIMEOUT)
            if not info.is_published():
                raise MrSteamError("shadow update was not acknowledged")
        finally:
            self._close(client)
        _LOGGER.debug("Published desired fragment to %s: %s", thing, payload)

    async def async_update_desired(self, thing: str, fragment: dict[str, Any]) -> None:
        """Publish a partial state.desired update."""
        await self.hass.async_add_executor_job(
            self._update_desired_sync, thing, fragment
        )
