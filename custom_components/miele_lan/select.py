"""Miele@LAN select — hood fan run-on time (Nachlaufzeit).

How long the extraction fan keeps running after it's switched off. Written
over Dop1 (see const.py); the appliance offers exactly the discrete values in
`RUN_ON_TIME_MINUTES`.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, RUN_ON_TIME_MINUTES
from .coordinator import MieleLanCoordinator
from .entity import MieleLanEntity

# "off" plus one option per nonzero run-on time, e.g. "5_min", "15_min".
RUN_ON_OPTIONS: dict[str, int] = {
    ("off" if m == 0 else f"{m}_min"): m for m in RUN_ON_TIME_MINUTES
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    bundle = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[str, MieleLanCoordinator] = bundle["coordinators"]
    entities: list[Any] = []
    for coord in coordinators.values():
        if coord.hood_dop1_supported:
            entities.append(MieleLanFanRunOnSelect(coord))
    async_add_entities(entities)


class MieleLanFanRunOnSelect(MieleLanEntity, SelectEntity):
    """Hood fan run-on time.

    The appliance doesn't report this setting back in /State and the Dop1
    read path only covers Programmierfunktionen, so the entity is optimistic:
    it shows the last value written from Home Assistant, and stays unknown
    until something sets it.
    """

    entity_description = SelectEntityDescription(
        key="fan_run_on_time",
        translation_key="fan_run_on_time",
    )
    _attr_options = list(RUN_ON_OPTIONS)

    def __init__(self, coordinator: MieleLanCoordinator) -> None:
        super().__init__(coordinator, "fan_run_on_time")
        self._attr_current_option: str | None = None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.client.set_fan_run_on_time(RUN_ON_OPTIONS[option])
        self._attr_current_option = option
        self.async_write_ha_state()
