"""Miele@LAN switches — per-device-type filtered.

* **Power** (oven-family / dishwasher / coffee-system / etc.) maps
  `Status != 1 (Off)` ↔ DOP2 SWITCH_ON / SWITCH_OFF opcodes.

Cooling-family SuperCool/SuperFreeze are NOT switches — see binary_sensor.py.
Confirmed 2026-05-22: the K7000 / EK057* fridge firmware blocks LAN writes
unconditionally (RE'd against KF 7772 B; cross-checked against Miele's own
docs which state MobileStart for cooling appliances is permanently on and
non-toggleable, and against the astrandb/miele maintainer who confirms even
the cloud 3rd-party API treats SuperCool/SuperFreeze as read-only).

Light is **not** a switch here — it's a proper HA `light` entity (see
`light.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import MieleLanClient
from .const import (
    DOMAIN,
    POWERABLE_FAMILY,
    MieleAppliance,
)
from .coordinator import MieleLanCoordinator
from .entity import MieleLanEntity


@dataclass(frozen=True, kw_only=True)
class MieleLanSwitchDescription(SwitchEntityDescription):
    is_on_fn: Callable[[dict[str, Any]], bool | None]
    turn_on_fn: Callable[[MieleLanClient], Awaitable[Any]]
    turn_off_fn: Callable[[MieleLanClient], Awaitable[Any]]


@dataclass(frozen=True, kw_only=True)
class MieleLanSwitchDef:
    types: tuple[MieleAppliance, ...]
    description: MieleLanSwitchDescription


SWITCH_TYPES: tuple[MieleLanSwitchDef, ...] = (
    # Power (panel on/off) — oven family + dishwasher + coffee + hood + dish-warmer
    MieleLanSwitchDef(
        types=POWERABLE_FAMILY,
        description=MieleLanSwitchDescription(
            key="power",
            translation_key="power_switch",
            device_class=SwitchDeviceClass.SWITCH,
            is_on_fn=lambda s: (s.get("Status") is not None and s["Status"] != 1),
            turn_on_fn=lambda c: c.switch_on(),
            turn_off_fn=lambda c: c.switch_off(),
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
        if not coord.dop2_supported:
            continue
        for d in SWITCH_TYPES:
            if dt in d.types:
                entities.append(MieleLanSwitch(coord, d.description))
    async_add_entities(entities)


class MieleLanSwitch(MieleLanEntity, SwitchEntity):
    entity_description: MieleLanSwitchDescription

    def __init__(
        self,
        coordinator: MieleLanCoordinator,
        description: MieleLanSwitchDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        try:
            return self.entity_description.is_on_fn(self.coordinator.data.state)
        except Exception:  # noqa: BLE001
            return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.entity_description.turn_on_fn(self.coordinator.client)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.entity_description.turn_off_fn(self.coordinator.client)
        await self.coordinator.async_request_refresh()
