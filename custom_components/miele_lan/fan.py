"""Miele@LAN fan — hood ventilation control over the legacy Dop1 protocol.

Only created for hoods that take the Dop1 writes (see
`MieleLanCoordinator.hood_dop1_supported` for the gate and const.py for the
wire format). GLOBAL_USER_REQ, the mechanism every other control entity in
this integration uses, answers HTTP 404 on these appliances, which is why
ventilation needs its own path.

Exposed as named presets rather than a percentage: the physical panel has
discrete labeled steps, and "Boost" isn't the top of a linear scale, so
mapping it onto 100% would misrepresent it. Turning the fan on takes a few
seconds to show up in `/State.VentilationStep` while the motor spins up; the
push channel delivers that update when it lands.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import (
    FanEntity,
    FanEntityDescription,
    FanEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MieleLanCoordinator
from .entity import MieleLanEntity

# Luefterstufe -> preset label. Step 4 is labeled "Boost" on the appliance,
# not "level 4". Step 5 (interval ventilation) is a real panel mode but isn't
# offered here — it's untested over this write path, so it's reported as "no
# recognized preset" rather than given a label we can't actually set.
LEVEL_TO_PRESET: dict[int, str] = {1: "1", 2: "2", 3: "3", 4: "boost"}
PRESET_TO_LEVEL: dict[str, int] = {v: k for k, v in LEVEL_TO_PRESET.items()}
PRESET_MODES: list[str] = list(LEVEL_TO_PRESET.values())


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    bundle = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[str, MieleLanCoordinator] = bundle["coordinators"]
    entities: list[Any] = []
    for coord in coordinators.values():
        state = coord.data.state if coord.data else {}
        if coord.hood_dop1_supported and "VentilationStep" in state:
            entities.append(MieleLanFan(coord))
    async_add_entities(entities)


class MieleLanFan(MieleLanEntity, FanEntity):
    """Hood ventilation fan."""

    _attr_supported_features = (
        FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )
    _attr_preset_modes = PRESET_MODES
    entity_description = FanEntityDescription(
        key="ventilation",
        translation_key="ventilation",
    )

    def __init__(self, coordinator: MieleLanCoordinator) -> None:
        super().__init__(coordinator, "ventilation")

    @property
    def _step(self) -> int | None:
        if not self.coordinator.data:
            return None
        step = self.coordinator.data.state.get("VentilationStep")
        return step if isinstance(step, int) else None

    @property
    def is_on(self) -> bool | None:
        step = self._step
        return None if step is None else step > 0

    @property
    def preset_mode(self) -> str | None:
        return LEVEL_TO_PRESET.get(self._step or 0)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self.coordinator.client.set_fan_level(PRESET_TO_LEVEL[preset_mode])
        await self.coordinator.async_request_refresh()

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        await self.async_set_preset_mode(preset_mode or PRESET_MODES[0])

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.client.set_fan_level(0)
        await self.coordinator.async_request_refresh()
