"""Miele@LAN light — cavity / cabinet / range-hood lamp.

Only emitted for appliances with a controllable lamp:
oven-family, coffee system, hood, wine cabinets, steam-microwave combos.

State source: `/State.Light` (1 = on, 2 = off, 0 = unsupported).
Writes: signed `PUT /State {"Light": 1|2}` — wrapped by
`MieleLanClient.light_on()` / `light_off()`.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ColorMode, LightEntity, LightEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, LIGHTABLE_FAMILY, MieleAppliance
from .coordinator import MieleLanCoordinator
from .entity import MieleLanEntity


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

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.client.light_on()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.client.light_off()
        await self.coordinator.async_request_refresh()
