"""Miele@LAN binary sensors — per-device-type filtered."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    COOLING_FAMILY,
    CYCLE_FAMILY,
    DOMAIN,
    DOORED_FAMILY,
    WINE_FAMILY,
    MieleAppliance,
)
from .coordinator import MieleLanCoordinator
from .entity import MieleLanEntity

ALL_TYPES: tuple[MieleAppliance, ...] = tuple(
    t for t in MieleAppliance if t is not MieleAppliance.UNKNOWN
)


@dataclass(frozen=True, kw_only=True)
class MieleLanBinarySensorDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[dict[str, Any]], bool | None]


@dataclass(frozen=True, kw_only=True)
class MieleLanBinarySensorDef:
    types: tuple[MieleAppliance, ...]
    description: MieleLanBinarySensorDescription


BINARY_SENSOR_TYPES: tuple[MieleLanBinarySensorDef, ...] = (
    # Door — every appliance that physically has one
    MieleLanBinarySensorDef(
        types=DOORED_FAMILY,
        description=MieleLanBinarySensorDescription(
            key="door_open",
            translation_key="door_open",
            device_class=BinarySensorDeviceClass.DOOR,
            is_on_fn=lambda s: bool(s.get("SignalDoor")),
        ),
    ),
    # Failure — every device
    MieleLanBinarySensorDef(
        types=ALL_TYPES,
        description=MieleLanBinarySensorDescription(
            key="failure",
            translation_key="failure",
            device_class=BinarySensorDeviceClass.PROBLEM,
            is_on_fn=lambda s: bool(s.get("SignalFailure")),
        ),
    ),
    # Notification — cycle devices (cooking & laundry & dish)
    MieleLanBinarySensorDef(
        types=CYCLE_FAMILY,
        description=MieleLanBinarySensorDescription(
            key="info",
            translation_key="info",
            is_on_fn=lambda s: bool(s.get("SignalInfo")),
        ),
    ),
    # Running (Status==5 = in_use) — cycle devices
    MieleLanBinarySensorDef(
        types=CYCLE_FAMILY,
        description=MieleLanBinarySensorDescription(
            key="running",
            translation_key="running",
            device_class=BinarySensorDeviceClass.RUNNING,
            is_on_fn=lambda s: s.get("Status") == 5,
        ),
    ),
    # Mobile-start enabled (RemoteEnable[0] == 15) — devices that can be remote-started
    MieleLanBinarySensorDef(
        types=CYCLE_FAMILY,
        description=MieleLanBinarySensorDescription(
            key="mobile_start",
            translation_key="mobile_start",
            is_on_fn=lambda s: (s.get("RemoteEnable") or [0])[0] == 15,  # type: ignore[index]
        ),
    ),
    # SuperCool — fridges. Read-only on LAN by firmware policy on K7000
    # EasyControl (KF 7772 B verified); converted from a switch to a sensor
    # since we can't actually toggle it.
    MieleLanBinarySensorDef(
        types=(MieleAppliance.FRIDGE, MieleAppliance.FRIDGE_FREEZER),
        description=MieleLanBinarySensorDescription(
            key="super_cool",
            translation_key="super_cool",
            is_on_fn=lambda s: s.get("Status") == 14,
        ),
    ),
    # SuperFreeze — freezer / fridge-freezer / wine_cabinet_freezer. Same
    # read-only reasoning as SuperCool.
    MieleLanBinarySensorDef(
        types=(
            MieleAppliance.FREEZER,
            MieleAppliance.FRIDGE_FREEZER,
            MieleAppliance.WINE_CABINET_FREEZER,
        ),
        description=MieleLanBinarySensorDescription(
            key="super_freeze",
            translation_key="super_freeze",
            is_on_fn=lambda s: s.get("Status") == 13,
        ),
    ),
    # Cooling-family: per-compartment door. RE'd from live KF 7772 B 2026-05-22:
    # `DoorStates[N]` is a 2-tuple `[state, ?]` where state code is
    #   0 = zone not present (sentinel — skip the entity entirely)
    #   1 = door open
    #   2 = door closed
    # Confirmed against the device-level `SignalDoor` boolean: with all
    # DoorStates[*][0] == 2 the appliance reports SignalDoor=false (closed).
    # The previous comment that "2 = open" was incorrect and inverted the sensor.
    *(
        MieleLanBinarySensorDef(
            types=(*COOLING_FAMILY, *WINE_FAMILY),
            description=MieleLanBinarySensorDescription(
                key=f"door_zone_{n}",
                translation_key=f"door_zone_{n}",
                device_class=BinarySensorDeviceClass.DOOR,
                is_on_fn=lambda s, _i=n - 1: (
                    (s.get("DoorStates", []) or [])[_i][0] == 1
                    if _i < len(s.get("DoorStates", []) or [])
                       and isinstance((s.get("DoorStates") or [])[_i], list)
                       and len((s.get("DoorStates") or [])[_i]) >= 1
                       and (s.get("DoorStates") or [])[_i][0] != 0
                    else None
                ),
            ),
        )
        for n in (1, 2, 3)
    ),
)


def _present_door_zones(coord: MieleLanCoordinator) -> set[int]:
    """Return {1..3} subset of door zones actually installed.

    A door zone is "installed" iff DoorStates[N-1][0] is non-zero
    (0 is the firmware's "zone absent" sentinel — observed on a 2-compartment
    KF 7772 B which exposes DoorStates=[[2,0],[2,0],[0,0]]).
    """
    if not coord.data:
        return {1, 2, 3}
    states = coord.data.state.get("DoorStates") or []
    present = set()
    for i in range(3):
        if i < len(states) and isinstance(states[i], list) and len(states[i]) >= 1 \
           and states[i][0] != 0:
            present.add(i + 1)
    return present


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
        door_zones = _present_door_zones(coord)
        for d in BINARY_SENSOR_TYPES:
            if dt not in d.types:
                continue
            key = d.description.key
            # Skip per-zone door sensors for absent zones (e.g. zone 3 on a
            # 2-compartment fridge-freezer).
            if key.startswith("door_zone_"):
                if int(key.rsplit("_", 1)[1]) not in door_zones:
                    continue
            entities.append(MieleLanBinarySensor(coord, d.description))
    async_add_entities(entities)


class MieleLanBinarySensor(MieleLanEntity, BinarySensorEntity):
    entity_description: MieleLanBinarySensorDescription

    def __init__(
        self,
        coordinator: MieleLanCoordinator,
        description: MieleLanBinarySensorDescription,
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
