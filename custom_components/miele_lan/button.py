"""Miele@LAN buttons — only entries that don't have a more natural
on/off representation as a switch / light.

* `wake`         — devices with deep-standby. Sends DeviceAction:2 to
  pull the device back to a remote-controllable state.
* `stop_program` — oven-family. Sends DOP2 STOP (no heat risk; cleanly
  ends a running program).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import MieleLanClient
from .const import (
    DOMAIN,
    OPCODE_STOP,
    OVEN_FAMILY,
    WAKEABLE_FAMILY,
    MieleAppliance,
)
from .coordinator import MieleLanCoordinator
from .entity import MieleLanEntity


@dataclass(frozen=True, kw_only=True)
class MieleLanButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[MieleLanClient], Awaitable[Any]]
    requires_dop2: bool = False


@dataclass(frozen=True, kw_only=True)
class MieleLanButtonDef:
    types: tuple[MieleAppliance, ...]
    description: MieleLanButtonDescription


BUTTONS: tuple[MieleLanButtonDef, ...] = (
    MieleLanButtonDef(
        types=WAKEABLE_FAMILY,
        description=MieleLanButtonDescription(
            key="wake",
            translation_key="wake",
            requires_dop2=False,
            press_fn=lambda c: c.wake(),
        ),
    ),
    MieleLanButtonDef(
        types=OVEN_FAMILY,
        description=MieleLanButtonDescription(
            key="stop_program",
            translation_key="stop_program",
            requires_dop2=True,
            press_fn=lambda c: c.write_user_request(OPCODE_STOP),
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    bundle = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[str, MieleLanCoordinator] = bundle["coordinators"]
    entities = []
    for coord in coordinators.values():
        dt = coord.device_type
        for d in BUTTONS:
            if dt not in d.types:
                continue
            if d.description.requires_dop2 and not coord.dop2_supported:
                continue
            entities.append(MieleLanButton(coord, d.description))
    async_add_entities(entities)


class MieleLanButton(MieleLanEntity, ButtonEntity):
    entity_description: MieleLanButtonDescription

    def __init__(
        self,
        coordinator: MieleLanCoordinator,
        description: MieleLanButtonDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        await self.entity_description.press_fn(self.coordinator.client)
        await self.coordinator.async_request_refresh()
