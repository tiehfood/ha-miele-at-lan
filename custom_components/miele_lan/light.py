"""Miele@LAN light — cavity / cabinet / range-hood lamp.

Only emitted for appliances with a controllable lamp:
oven-family, coffee system, hood, wine cabinets, steam-microwave combos.

State source: `/State.Light` (1 = on, 2 = off, 0 = unsupported).
Writes: signed `PUT /State {"Light": 1|2}` — wrapped by
`MieleLanClient.light_on()` / `light_off()`. Hoods that take the Dop1 writes
use `set_main_light_dop1()` instead, which the appliance applies much faster
(~0.8s vs ~4.9s to switch on, measured on an EK039W hood), falling back to
/State if that write fails. Only that write carries a brightness, so a hood
light that reports itself as dimmable gets a brightness as well.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
    LightEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, LIGHTABLE_FAMILY, MieleAppliance
from .coordinator import MieleLanCoordinator, model_name
from .entity import MieleLanEntity
from .extended_state import (
    DOP1_HOOD_MAX_DIM_VALUE,
    dop1_hood_light_dimmable,
    parse_dop1_hood_light_dim_value,
)

_LOGGER = logging.getLogger(__name__)


# Models that report /State.Light = 0 in every state and answer a Light write
# with HTTP 403. A H6800BMX kept 0 with the lamp on, the door open and the oven
# running, its cloud state has no `light` field and the cloud `light` action
# list is empty. A light entity for these would never show a state.
_NO_LIGHT_STATE_MODELS = frozenset({"H6800BMX"})


def _light_is_dead(data: Any) -> bool:
    model = model_name(data.ident)
    return model in _NO_LIGHT_STATE_MODELS and data.state.get("Light") == 0


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
            if not _light_is_dead(coord.data):
                entities.append(MieleLanLight(coord))
    async_add_entities(entities)


class MieleLanLight(MieleLanEntity, LightEntity):
    """Cavity / cabinet / hood light. On/off, plus brightness on a dimmable hood light."""

    entity_description = LightEntityDescription(
        key="cavity_light",
        translation_key="cavity_light",
    )

    def __init__(self, coordinator: MieleLanCoordinator) -> None:
        super().__init__(coordinator, "cavity_light")
        # The write always carries a dim value, so switching on without a
        # brightness needs the last one to come back at the same level.
        self._last_dim_value = DOP1_HOOD_MAX_DIM_VALUE

    @property
    def _dim_value(self) -> int | None:
        """The hood light's dim value, None for a light that cannot be dimmed."""
        coord = self.coordinator
        if not coord.hood_dop1_supported:
            return None
        extended = coord.data.state.get("ExtendedState")
        if not dop1_hood_light_dimmable(extended):
            return None
        return parse_dop1_hood_light_dim_value(extended)

    @property
    def color_mode(self) -> ColorMode:
        return ColorMode.ONOFF if self._dim_value is None else ColorMode.BRIGHTNESS

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        return {self.color_mode}

    @property
    def brightness(self) -> int | None:
        # The hood keeps eight bits and reports them twice (0x5555 = 85).
        dim = self._dim_value
        return None if dim is None else dim >> 8

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.is_on and self._dim_value:
            self._last_dim_value = self._dim_value
        super()._handle_coordinator_update()

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

    async def _set_light(self, on: bool, dim_value: int = DOP1_HOOD_MAX_DIM_VALUE) -> None:
        client = self.coordinator.client
        if self.coordinator.hood_dop1_supported:
            try:
                await client.set_main_light_dop1(on, dim_value)
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
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is None:
            await self._set_light(True, self._last_dim_value)
        else:
            await self._set_light(True, brightness * 257)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_light(False)
