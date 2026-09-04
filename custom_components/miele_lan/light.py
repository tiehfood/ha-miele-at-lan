"""Miele@LAN light — cavity / cabinet / range-hood lamp.

Only emitted for appliances with a controllable lamp:
oven-family, coffee system, hood, wine cabinets, steam-microwave combos.

State source: `/State.Light` (1 = on, 2 = off, 0 = unsupported).
Writes: signed `PUT /State {"Light": 1|2}` — wrapped by
`MieleLanClient.light_on()` / `light_off()`. Hoods that take the Dop1 writes
use `set_main_light_dop1()` instead, which the appliance applies much faster
(~0.8s vs ~4.9s to switch on, measured on an EK039W hood), falling back to
/State if that write fails.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import ColorMode, LightEntity, LightEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, LIGHTABLE_FAMILY, MieleAppliance
from .coordinator import MieleLanCoordinator
from .entity import MieleLanEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    bundle = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[str, MieleLanCoordinator] = bundle["coordinators"]
    entities: list[Any] = []
    for coord in coordinators.values():
        if coord.device_type in LIGHTABLE_FAMILY and "Light" in coord.data.state:
            entities.append(MieleLanLight(coord))
    async_add_entities(entities)


class MieleLanLight(MieleLanEntity, LightEntity):
    """Cavity / cabinet light. On/Off only — no brightness or color."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}
    entity_description = LightEntityDescription(
        key="cavity_light",
        translation_key="cavity_light",
    )

    def __init__(self, coordinator: MieleLanCoordinator) -> None:
        super().__init__(coordinator, "cavity_light")

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        v = self.coordinator.data.state.get("Light")
        if v == 1:
            return True
        if v == 2:
            return False
        return None  # 0 = unsupported / not yet known

    async def _set_light(self, on: bool) -> None:
        client = self.coordinator.client
        if self.coordinator.hood_dop1_supported:
            try:
                await client.set_main_light_dop1(on)
            except Exception as err:  # noqa: BLE001
                # A light that can't be switched is worse than a slow one, so
                # any Dop1 failure falls through to the /State path that every
                # other lightable appliance uses.
                _LOGGER.warning(
                    "Dop1 main-light write failed (%s) — falling back to /State", err
                )
            else:
                await self.coordinator.async_request_refresh()
                return
        await (client.light_on() if on else client.light_off())
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_light(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_light(False)
