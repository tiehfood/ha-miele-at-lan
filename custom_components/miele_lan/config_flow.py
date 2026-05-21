"""Config flow.

Two setup paths:

1. Cloud-pair (recommended): user authorises with Miele cloud once, we extract
   the household GroupKey and enrol HA as a SuperVision peer for every cloud-
   paired device. No factory reset, Miele app keeps working.

2. Manual key entry (legacy): user pastes a GroupID + GroupKey they obtained by
   locally provisioning a device. One config entry per device. Kept for users
   who reverse-engineered their own setup before we shipped the cloud path.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import zeroconf
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResult

from .api import MieleLanClient
from .cloud import (
    CONSUMER_CLIENT_IDS,
    PKCEChallenge,
    build_authorize_url,
    exchange_code,
    fetch_groupkey,
    parse_redirect_url,
)
from .enrollment import mdns_discover_household
from .const import (
    CONF_COUNTRY,
    CONF_DEVICES,
    CONF_GROUP_ID,
    CONF_GROUP_KEY,
    CONF_HA_FAB,
    CONF_HA_PORT,
    CONF_REFRESH_TOKEN,
    CONF_REGION,
    CONF_ROUTE,
    DEFAULT_HA_PUSH_PORT,
    DEFAULT_NAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _validate_keys(group_id: str, group_key: str) -> tuple[str, str] | None:
    g_id = group_id.strip().lower()
    g_key = group_key.strip().lower()
    try:
        if len(bytes.fromhex(g_id)) != 8:
            return None
        if len(bytes.fromhex(g_key)) != 64:
            return None
    except ValueError:
        return None
    return g_id, g_key


def _new_ha_fab() -> str:
    """Synthesise a stable 12-digit fab number for HA's peer identity.

    Format: leading `000999` so it's clearly synthetic (real Miele fabs start
    with the registration year etc.), plus 6 random digits.
    """
    return "000999" + f"{secrets.randbelow(1_000_000):06d}"


class MieleLanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """User-driven setup with two entry-paths."""

    VERSION = 2

    def __init__(self) -> None:
        # Manual / zeroconf path
        self._discovered_host: str | None = None
        self._discovered_route: str | None = None
        self._discovered_name: str | None = None
        # Cloud path
        self._challenge: PKCEChallenge | None = None
        self._authorize_url: str | None = None

    # --------------------------------------------------------------- entrypoint
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Top-level: ask which kind of setup."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["cloud_country", "cloud_tokens", "direct_keys"],
        )

    # =================================================================== Cloud
    async def async_step_cloud_country(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pick the country whose Miele cloud tenant we'll auth against."""
        if user_input is not None:
            cc = user_input[CONF_COUNTRY]
            self._authorize_url, self._challenge = build_authorize_url(cc)
            return await self.async_step_cloud_authorize()
        return self.async_show_form(
            step_id="cloud_country",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_COUNTRY, default="de"): vol.In(
                        sorted(CONSUMER_CLIENT_IDS)
                    ),
                }
            ),
        )

    async def async_step_cloud_authorize(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show the authorize URL; user logs in, captures `miele://oauth2-code/`,
        pastes the URL back here."""
        errors: dict[str, str] = {}
        if user_input is not None:
            redirect = user_input["redirect_url"].strip()
            assert self._challenge is not None
            try:
                code = parse_redirect_url(redirect, expected_state=self._challenge.state)
            except ValueError as err:
                errors["redirect_url"] = "invalid_redirect"
                _LOGGER.warning("redirect URL invalid: %s", err)
            if not errors:
                return await self._finalise_cloud(code)
        return self.async_show_form(
            step_id="cloud_authorize",
            data_schema=vol.Schema({vol.Required("redirect_url"): str}),
            description_placeholders={"authorize_url": self._authorize_url or "?"},
            errors=errors,
        )

    async def _finalise_cloud(self, code: str) -> FlowResult:
        """Exchange the code, fetch the household, create the config entry."""
        assert self._challenge is not None
        async with aiohttp.ClientSession() as session:
            try:
                tokens = await exchange_code(session, self._challenge, code)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("token exchange failed: %s", err)
                return self.async_abort(reason="oauth_failed")
            access = tokens.get("access_token")
            refresh = tokens.get("refresh_token")
            if not access or not refresh:
                return self.async_abort(reason="oauth_failed")
            # Region from country code (very simple mapping — extend later).
            region = "EU"
            if self._challenge.cc in ("us",):
                region = "EU2"  # placeholder; real US region TBD
            try:
                groupkey = await fetch_groupkey(session, access, region=region)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("GroupKey fetch failed: %s", err)
                return self.async_abort(reason="groupkey_failed")
        # One config entry per household. unique_id = household group_id.
        await self.async_set_unique_id(groupkey.group_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"{DEFAULT_NAME} (household {groupkey.group_id[:6]}…)",
            data={
                "flow_kind": "cloud",
                CONF_COUNTRY: self._challenge.cc,
                CONF_REGION: region,
                CONF_REFRESH_TOKEN: refresh,
                CONF_GROUP_ID: groupkey.group_id,
                CONF_GROUP_KEY: groupkey.group_key,
                CONF_DEVICES: groupkey.devices,
                CONF_HA_FAB: _new_ha_fab(),
                CONF_HA_PORT: DEFAULT_HA_PUSH_PORT,
            },
        )

    # ============================================================== Cloud-tokens
    async def async_step_cloud_tokens(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Power-user path: paste access_token + refresh_token already obtained
        via the bundled `tools/miele_oauth_*.py` scripts. We skip code-exchange
        entirely and go straight to fetch_groupkey()."""
        errors: dict[str, str] = {}
        if user_input is not None:
            cc = user_input[CONF_COUNTRY]
            access = (user_input.get("access_token") or "").strip()
            refresh = (user_input.get(CONF_REFRESH_TOKEN) or "").strip()
            if not access or not refresh:
                errors["base"] = "invalid_token"
            else:
                region = "EU"
                if cc == "us":
                    region = "EU2"
                async with aiohttp.ClientSession() as session:
                    try:
                        groupkey = await fetch_groupkey(session, access, region=region)
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.warning("GroupKey fetch failed: %s", err)
                        return self.async_abort(reason="groupkey_failed")
                await self.async_set_unique_id(groupkey.group_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{DEFAULT_NAME} (household {groupkey.group_id[:6]}…)",
                    data={
                        "flow_kind": "cloud",
                        CONF_COUNTRY: cc,
                        CONF_REGION: region,
                        CONF_REFRESH_TOKEN: refresh,
                        CONF_GROUP_ID: groupkey.group_id,
                        CONF_GROUP_KEY: groupkey.group_key,
                        CONF_DEVICES: groupkey.devices,
                        CONF_HA_FAB: _new_ha_fab(),
                        CONF_HA_PORT: DEFAULT_HA_PUSH_PORT,
                    },
                )
        return self.async_show_form(
            step_id="cloud_tokens",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_COUNTRY, default="de"): vol.In(
                        sorted(CONSUMER_CLIENT_IDS)
                    ),
                    vol.Required(CONF_REFRESH_TOKEN): str,
                    vol.Required("access_token"): str,
                }
            ),
            errors=errors,
        )

    # ============================================================ Direct keys
    async def async_step_direct_keys(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Setup without OAuth: user pastes group_id + group_key (e.g. they
        already extracted them or did local provisioning). HA mDNS-browses for
        devices in the same group and treats them like cloud-discovered ones —
        no host field needed."""
        errors: dict[str, str] = {}
        if user_input is not None:
            keys = _validate_keys(user_input[CONF_GROUP_ID], user_input[CONF_GROUP_KEY])
            if not keys:
                errors["base"] = "invalid_credentials"
            else:
                g_id, g_key = keys
                try:
                    from homeassistant.components import zeroconf as ha_zc
                    shared_zc = await ha_zc.async_get_async_instance(self.hass)
                    devices = await mdns_discover_household(
                        g_id, g_key, timeout=4.0, zeroconf=shared_zc,
                    )
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("mDNS discovery raised: %s", err)
                    devices = []
                if not devices:
                    errors["base"] = "no_devices"
                else:
                    await self.async_set_unique_id(g_id.upper())
                    self._abort_if_unique_id_configured()
                    # Normalise to the same shape the cloud path uses.
                    cloud_devices = [
                        {
                            "fabNr": d.get("fabNr") or "",
                            "deviceType": d.get("deviceType", 0),
                            "deviceName": d.get("deviceName") or "",
                        }
                        for d in devices
                    ]
                    static_ips = {
                        d["fabNr"]: d["host"]
                        for d in devices
                        if d.get("fabNr") and d.get("host")
                    }
                    return self.async_create_entry(
                        title=f"{DEFAULT_NAME} (household {g_id[:6].upper()}…)",
                        data={
                            "flow_kind": "cloud",   # runtime identical
                            CONF_COUNTRY: "",       # no cloud auth
                            CONF_REGION: "",
                            CONF_REFRESH_TOKEN: "",
                            CONF_GROUP_ID: g_id.upper(),
                            CONF_GROUP_KEY: g_key.upper(),
                            CONF_DEVICES: cloud_devices,
                            CONF_HA_FAB: _new_ha_fab(),
                            CONF_HA_PORT: DEFAULT_HA_PUSH_PORT,
                            "static_ips": static_ips,
                        },
                    )
        return self.async_show_form(
            step_id="direct_keys",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_GROUP_ID): str,
                    vol.Required(CONF_GROUP_KEY): str,
                }
            ),
            errors=errors,
        )

    # ================================================================ Zeroconf
    async def async_step_zeroconf(
        self, discovery_info: zeroconf.ZeroconfServiceInfo
    ) -> FlowResult:
        """Zeroconf is gated on "integration already configured".

        Before any Miele@LAN entry exists, mDNS discoveries are dropped
        silently. The user installs and configures the integration via the
        menu first (no auto-prompts, no race on first install).

        After at least one entry exists, mDNS discoveries are processed:

          * Same household, known device → silent (runtime mDNS handles it).
          * Same household, NEW device   → discovery card so the user can
            reload the entry and pick the new appliance up.
          * Different household          → silent for now (rare case; user
            adds another household manually if needed).
        """
        entries = self._async_current_entries(include_ignore=False)
        if not entries:
            return self.async_abort(reason="no_integration")

        props = discovery_info.properties or {}
        group = (props.get("group") or props.get("Group") or "").upper()
        if not group:
            return self.async_abort(reason="not_miele")

        matching_entry = next(
            (e for e in entries if (e.data.get(CONF_GROUP_ID) or "").upper() == group),
            None,
        )
        if matching_entry is None:
            return self.async_abort(reason="different_household")

        # Try to extract fab from service-instance name. Our own listener uses
        # "HomeAssistant <fab>" so it works there. Real Miele appliances use
        # "Miele <model>._mieleathome._tcp.local." with NO fab in the name —
        # we'd need a signed GET /Devices to learn it.
        fab = ""
        bare = discovery_info.name.removesuffix("._mieleathome._tcp.local.")
        for tok in reversed(bare.split()):
            digits = "".join(c for c in tok if c.isdigit())
            if len(digits) >= 10:
                fab = digits[-12:]
                break

        # Suppress self-discovery — our own listener has a known synthetic fab.
        ha_fabs = {(e.data.get(CONF_HA_FAB) or "") for e in entries}
        if fab and fab in ha_fabs:
            return self.async_abort(reason="already_configured")

        known_fabs = {
            (d.get("fabNr") or "")
            for d in (matching_entry.data.get(CONF_DEVICES) or [])
        }
        if fab and fab in known_fabs:
            return self.async_abort(reason="already_configured")

        # If we can't parse a fab from the service name, suppress the card.
        # The `group=` already matched a configured household, so by definition
        # the device is one of the appliances we set up — there's no new
        # device to add. (Real Miele service names don't embed the fab, so
        # this is the common case for any real appliance re-advertising.)
        if not fab:
            return self.async_abort(reason="already_configured")

        # New device in an already-configured household. Per-device unique_id
        # so multiple newcomers don't block each other.
        await self.async_set_unique_id(f"{group}:{fab or 'unknown'}")
        self._abort_if_unique_id_configured()
        self._discovered_group_id = group
        self._discovered_fab = fab
        self._discovered_entry_id = matching_entry.entry_id
        self.context["title_placeholders"] = {
            "name": f"Miele device {fab or '(unknown fab)'}",
            "host": str(discovery_info.host or ""),
        }
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm card for a new device in an existing household. On confirm,
        reload the matching config entry so its mDNS browser picks the device
        up and runs enrollment for it."""
        if user_input is not None:
            entry_id = getattr(self, "_discovered_entry_id", None)
            if entry_id:
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(entry_id)
                )
            return self.async_abort(reason="reloaded")
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={
                "group_id": getattr(self, "_discovered_group_id", "") or "?",
                "fab": getattr(self, "_discovered_fab", "") or "(unknown)",
            },
        )
