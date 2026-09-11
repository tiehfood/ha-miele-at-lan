"""Miele@LAN buttons — only entries that don't have a more natural
on/off representation as a switch / light.

* `wake`          — devices with deep-standby. Sends DeviceAction:2 to
  pull the device back to a remote-controllable state.
* `stop_program`  — oven-family. Sends DOP2 STOP (no heat risk; cleanly
  ends a running program) via GLOBAL_USER_REQ.
* `start_process`, `pause_process`, `resume_process` — cycle devices
  (oven/laundry/dishwasher). Send `ProcessAction` via `PUT /State` instead
  of DOP2 — the only control surface on appliances whose firmware blocks
  DOP2 writes outright (HTTP 404 on every leaf). Ovens have no existing
  start/pause/resume equivalent, so there's no duplication there.
  `resume_process` has no dedicated opcode — it resends Start (1), same as
  the official app (see `MieleLanClient.resume_process`).
* `stop_process`  — laundry + dishwasher only (STOP_PROCESS_FAMILY), same
  `PUT /State` mechanism. Deliberately excludes ovens: they already have a
  working `stop_program`, we have no evidence the two stop mechanisms
  behave identically, and a second unproven "Stop" would just leave an
  oven owner guessing which button to press for no gain — see
  `STOP_PROCESS_FAMILY` in const.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CYCLE_FAMILY,
    DOMAIN,
    OPCODE_STOP,
    OVEN_FAMILY,
    PROCESS_ACTION_PAUSE,
    PROCESS_ACTION_START,
    PROCESS_ACTION_STOP,
    STOP_PROCESS_FAMILY,
    WAKEABLE_FAMILY,
    MieleAppliance,
)
from .coordinator import MieleLanCoordinator
from .entity import MieleLanEntity


@dataclass(frozen=True, kw_only=True)
class MieleLanButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[MieleLanCoordinator], Awaitable[Any]]


@dataclass(frozen=True, kw_only=True)
class MieleLanButtonDef:
    types: tuple[MieleAppliance, ...]
    description: MieleLanButtonDescription


def _process_action_press(action: int) -> Callable[[MieleLanCoordinator], Awaitable[Any]]:
    async def press(coordinator: MieleLanCoordinator) -> None:
        state = coordinator.data.state if coordinator.data else None
        await coordinator.client.send_process_action(action, precondition_state=state)

    return press


BUTTONS: tuple[MieleLanButtonDef, ...] = (
    MieleLanButtonDef(
        types=WAKEABLE_FAMILY,
        description=MieleLanButtonDescription(
            key="wake",
            translation_key="wake",
            press_fn=lambda coord: coord.client.wake(),
        ),
    ),
    MieleLanButtonDef(
        types=OVEN_FAMILY,
        description=MieleLanButtonDescription(
            key="stop_program",
            translation_key="stop_program",
            press_fn=lambda coord: coord.client.write_user_request(OPCODE_STOP),
        ),
    ),
    MieleLanButtonDef(
        types=CYCLE_FAMILY,
        description=MieleLanButtonDescription(
            key="start_process",
            translation_key="start_process",
            press_fn=_process_action_press(PROCESS_ACTION_START),
        ),
    ),
    MieleLanButtonDef(
        types=STOP_PROCESS_FAMILY,
        description=MieleLanButtonDescription(
            key="stop_process",
            translation_key="stop_process",
            press_fn=_process_action_press(PROCESS_ACTION_STOP),
        ),
    ),
    MieleLanButtonDef(
        types=CYCLE_FAMILY,
        description=MieleLanButtonDescription(
            key="pause_process",
            translation_key="pause_process",
            press_fn=_process_action_press(PROCESS_ACTION_PAUSE),
        ),
    ),
    MieleLanButtonDef(
        types=CYCLE_FAMILY,
        description=MieleLanButtonDescription(
            key="resume_process",
            translation_key="resume_process",
            press_fn=_process_action_press(PROCESS_ACTION_START),
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
        await self.entity_description.press_fn(self.coordinator)
        await self.coordinator.async_request_refresh()
