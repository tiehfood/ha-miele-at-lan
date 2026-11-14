"""Miele@LAN select entities.

- Hood fan run-on time (Nachlaufzeit): how long the extraction fan keeps
  running after it's switched off. Written over Dop1 (see const.py); the
  appliance offers exactly the discrete values in `RUN_ON_TIME_MINUTES`.
- Dishwasher programme: the programmes the appliance lists in DOP2 2/1584,
  selected through 2/1577 without starting anything (#61).
"""

from __future__ import annotations

from typing import Any, Callable

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DISHWASHER_FAMILY,
    DOMAIN,
    RUN_ON_TIME_MINUTES,
    STATUS_ON,
    STATUS_PROGRAMMED,
)
from .coordinator import MieleLanCoordinator
from .entity import MieleLanEntity
from .enums import DishWasherProgramId

# "off" plus one option per nonzero run-on time, e.g. "5_min", "15_min".
RUN_ON_OPTIONS: dict[str, int] = {
    ("off" if m == 0 else f"{m}_min"): m for m in RUN_ON_TIME_MINUTES
}

# A programme can be picked while the dishwasher is on or already has one
# programmed, never while it waits for a delayed start, runs or is paused.
_PROGRAM_SELECTABLE_STATUSES = frozenset({STATUS_ON, STATUS_PROGRAMMED})


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
        if coord.device_type not in DISHWASHER_FAMILY:
            continue
        if coord.data.program_list:
            entities.append(MieleLanProgramSelect(coord))
        else:
            entry.async_on_unload(
                coord.async_add_listener(_program_select_adder(coord, async_add_entities))
            )
    async_add_entities(entities)


def _program_select_adder(
    coord: MieleLanCoordinator, async_add_entities: AddEntitiesCallback
) -> Callable[[], None]:
    """Add the programme select once 2/1584 has been read after setup."""
    added = False

    @callback
    def _add() -> None:
        nonlocal added
        if not added and coord.data.program_list:
            added = True
            async_add_entities([MieleLanProgramSelect(coord)])

    return _add


def program_option(program_id: int) -> str:
    """The option for a programme id: its name, or the bare id when unnamed."""
    return DishWasherProgramId.get(program_id, str(program_id))


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


class MieleLanProgramSelect(MieleLanEntity, SelectEntity):
    """Dishwasher programme, from the appliance's own programme list.

    Options are the ids in DOP2 2/1584, read once while the appliance is
    awake. The current option follows /State.ProgramID, matched by name,
    since /State and 2/1584 may number the same programme differently.
    Selecting never starts the programme.
    """

    entity_description = SelectEntityDescription(
        key="program",
        translation_key="program",
    )

    def __init__(self, coordinator: MieleLanCoordinator) -> None:
        super().__init__(coordinator, "program")
        program_list = coordinator.data.program_list or []
        self._ids_by_option = {program_option(i): i for i in program_list}
        self._attr_options = list(self._ids_by_option)

    @property
    def current_option(self) -> str | None:
        program_id = self.coordinator.data.state.get("ProgramID")
        if not isinstance(program_id, int) or isinstance(program_id, bool):
            return None
        option = program_option(program_id)
        return option if option in self._ids_by_option else None

    async def async_select_option(self, option: str) -> None:
        status = self.coordinator.data.state.get("Status")
        if status not in _PROGRAM_SELECTABLE_STATUSES:
            raise HomeAssistantError(
                "A programme can only be selected while the dishwasher is on "
                f"and not running (Status={status})."
            )
        await self.coordinator.client.select_program(self._ids_by_option[option])
        await self.coordinator.async_request_refresh()
