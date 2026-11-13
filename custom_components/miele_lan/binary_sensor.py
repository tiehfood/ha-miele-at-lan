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
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    COOLING_FAMILY,
    CYCLE_FAMILY,
    DOMAIN,
    DOORED_FAMILY,
    HOB_FAMILY,
    LAUNDRY_FAMILY,
    WINE_FAMILY,
    MieleAppliance,
)
from .coordinator import MieleLanCoordinator, MieleLanData
from .entity import MieleLanEntity
from .extended_state import hob_zone_count, parse_hob_extended_state

ALL_TYPES: tuple[MieleAppliance, ...] = tuple(
    t for t in MieleAppliance if t is not MieleAppliance.UNKNOWN
)

DOOR_STATE_OPEN = 1  # GLOBAL_EnumDoorState


def _door_open(data: MieleLanData) -> bool:
    # Some dishwashers (G7197) never set SignalDoor over the LAN; their
    # DOP2 2/1585 DoorState does follow the door (issue #59).
    return bool(data.state.get("SignalDoor")) or data.door_state == DOOR_STATE_OPEN


@dataclass(frozen=True, kw_only=True)
class MieleLanBinarySensorDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[dict[str, Any]], bool | None] | None = None
    # For sensors that need more than /State; takes precedence over is_on_fn.
    data_is_on_fn: Callable[[MieleLanData], bool | None] | None = None


@dataclass(frozen=True, kw_only=True)
class MieleLanBinarySensorDef:
    types: tuple[MieleAppliance, ...]
    description: MieleLanBinarySensorDescription


@dataclass(frozen=True, kw_only=True)
class MieleLanDeviceContextBinarySensorDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[dict[str, Any]], bool | None]
    container_index: int


@dataclass(frozen=True, kw_only=True)
class MieleLanDeviceContextBinarySensorDef:
    types: tuple[MieleAppliance, ...]
    description: MieleLanDeviceContextBinarySensorDescription


DEVICE_CONTEXT_BINARY_SENSORS: tuple[MieleLanDeviceContextBinarySensorDef, ...] = (
    MieleLanDeviceContextBinarySensorDef(
        types=LAUNDRY_FAMILY,
        description=MieleLanDeviceContextBinarySensorDescription(
            key="twindos_1_empty",
            translation_key="twindos_1_empty",
            entity_category=EntityCategory.DIAGNOSTIC,
            container_index=0,
            is_on_fn=lambda ctx: (
                (ctx.get("dos_containers") or [None])[0] or {}
            ).get("bitmask_empty"),
        ),
    ),
    MieleLanDeviceContextBinarySensorDef(
        types=LAUNDRY_FAMILY,
        description=MieleLanDeviceContextBinarySensorDescription(
            key="twindos_2_empty",
            translation_key="twindos_2_empty",
            entity_category=EntityCategory.DIAGNOSTIC,
            container_index=1,
            is_on_fn=lambda ctx: (
                (ctx.get("dos_containers") or [None, None])[1] or {}
            ).get("bitmask_empty"),
        ),
    ),
)


BINARY_SENSOR_TYPES: tuple[MieleLanBinarySensorDef, ...] = (
    # Door — every appliance that physically has one
    MieleLanBinarySensorDef(
        types=DOORED_FAMILY,
        description=MieleLanBinarySensorDescription(
            key="door_open",
            translation_key="door_open",
            device_class=BinarySensorDeviceClass.DOOR,
            data_is_on_fn=_door_open,
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
    # /State RemoteEnable is [fullRemoteControl, smartGrid, mobileStart] — the
    # same triple, in the same order, that the cloud API exposes as
    # remoteEnable.{fullRemoteControl,smartGrid,mobileStart}. Verified against a
    # live WER875 reporting [15, 1, 0] while the cloud reported
    # fullRemoteControl=on, smartGrid=on, mobileStart=off.
    # Smart Grid is the second RemoteEnable slot. Laundry only: on a WMV960
    # the slot followed the Smart Grid setting in the machine's menu (1 when
    # on, 0 when off). A dishwasher and an oven always report 0 there, and the
    # dishwasher's own SmartStart setting does not move it.
    MieleLanBinarySensorDef(
        types=LAUNDRY_FAMILY,
        description=MieleLanBinarySensorDescription(
            key="smart_grid",
            translation_key="smart_grid",
            is_on_fn=lambda s: bool((s.get("RemoteEnable") or [0, 0, 0])[1]),  # type: ignore[index]
        ),
    ),
    MieleLanBinarySensorDef(
        types=CYCLE_FAMILY,
        description=MieleLanBinarySensorDescription(
            key="mobile_start",
            translation_key="mobile_start",
            is_on_fn=lambda s: bool((s.get("RemoteEnable") or [0, 0, 0])[2]),  # type: ignore[index]
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
    # Hob — per-zone "pot detected" decoded from ExtendedState (bit 3 of
    # InfoZone<n>). Useful for "burner has a pan on it" automations.
    *(
        MieleLanBinarySensorDef(
            types=HOB_FAMILY,
            description=MieleLanBinarySensorDescription(
                key=f"plate_{n}_pot_detected",
                translation_key=f"plate_{n}_pot_detected",
                is_on_fn=lambda s, _i=n - 1: (
                    (e := parse_hob_extended_state(s.get("ExtendedState")))
                    and _i < len(e.zones)
                    and e.zones[_i].pot_detected
                ),
            ),
        )
        for n in range(1, 7)
    ),
    # Hob — per-zone "Ankochautomatik" (auto pre-boil / boost-to-target).
    # Diagnostic, off by default; mostly informational for power users.
    *(
        MieleLanBinarySensorDef(
            types=HOB_FAMILY,
            description=MieleLanBinarySensorDescription(
                key=f"plate_{n}_ankochautomatik",
                translation_key=f"plate_{n}_ankochautomatik",
                entity_category=EntityCategory.DIAGNOSTIC,
                entity_registry_enabled_default=False,
                is_on_fn=lambda s, _i=n - 1: (
                    (e := parse_hob_extended_state(s.get("ExtendedState")))
                    and _i < len(e.zones)
                    and e.zones[_i].ankochautomatik
                ),
            ),
        )
        for n in range(1, 7)
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
        zone_count = hob_zone_count(coord.data.state if coord.data else {})
        for d in BINARY_SENSOR_TYPES:
            if dt not in d.types:
                continue
            key = d.description.key
            if key.startswith("plate_") and int(key.split("_")[1]) > zone_count:
                continue
            # Skip per-zone door sensors for absent zones (e.g. zone 3 on a
            # 2-compartment fridge-freezer).
            if key.startswith("door_zone_"):
                if int(key.rsplit("_", 1)[1]) not in door_zones:
                    continue
            entities.append(MieleLanBinarySensor(coord, d.description))
        created: set[str] = set()
        entities.extend(_new_device_context_entities(coord, created))
        entry.async_on_unload(
            coord.async_add_listener(_late_entity_adder(coord, created, async_add_entities))
        )
    async_add_entities(entities)


def _late_entity_adder(
    coord: MieleLanCoordinator, created: set[str], async_add_entities: AddEntitiesCallback
) -> Callable[[], None]:
    @callback
    def _add() -> None:
        if new := _new_device_context_entities(coord, created):
            async_add_entities(new)

    return _add


def _new_device_context_entities(
    coord: MieleLanCoordinator, created: set[str]
) -> list[BinarySensorEntity]:
    """2/1585 entities whose data is there now and that don't exist yet.

    Washers and dryers are not woken for 2/1585 (#65), so one asleep at
    platform setup only answers it later.
    """
    entities: list[BinarySensorEntity] = []
    containers = coord.data.device_context.get("dos_containers") if coord.data else None
    for d in DEVICE_CONTEXT_BINARY_SENSORS:
        if coord.device_type not in d.types or d.description.key in created:
            continue
        if not containers or len(containers) <= d.description.container_index:
            continue
        created.add(d.description.key)
        entities.append(MieleLanDeviceContextBinarySensor(coord, d.description))
    return entities


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
        description = self.entity_description
        try:
            if description.data_is_on_fn is not None:
                return description.data_is_on_fn(self.coordinator.data)
            return description.is_on_fn(self.coordinator.data.state)
        except Exception:  # noqa: BLE001
            return None


class MieleLanDeviceContextBinarySensor(MieleLanEntity, BinarySensorEntity):
    entity_description: MieleLanDeviceContextBinarySensorDescription

    def __init__(
        self,
        coordinator: MieleLanCoordinator,
        description: MieleLanDeviceContextBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        try:
            return self.entity_description.is_on_fn(self.coordinator.data.device_context)
        except Exception:  # noqa: BLE001
            return None
