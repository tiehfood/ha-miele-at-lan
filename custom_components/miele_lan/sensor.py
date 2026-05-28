"""Miele@LAN sensors — per-device-type filtered.

Each entity in SENSOR_TYPES declares which MieleAppliance values it applies
to. At setup we iterate coordinators × sensor types and emit only the
intersection — so an oven gets cavity/core temperatures, a fridge gets
multi-zone temperatures, a dryer gets program/phase/remaining-time, etc.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import enums
from .extended_state import parse_hob_extended_state
from .const import (
    COOLING_FAMILY,
    CYCLE_FAMILY,
    DISHWASHER_FAMILY,
    DOMAIN,
    HOB_FAMILY,
    IDLE_STATUSES,
    LAUNDRY_FAMILY,
    OVEN_FAMILY,
    WINE_FAMILY,
    MieleAppliance,
)
from .coordinator import MieleLanCoordinator
from .entity import MieleLanEntity


# Per-device-type ProgramID and ProgramPhase tables.  Keyed by MieleAppliance.
_PROGRAM_ID_BY_TYPE: dict[MieleAppliance, dict[int, str]] = {
    MieleAppliance.WASHING_MACHINE: enums.WashingMachineProgramId,
    MieleAppliance.WASHING_MACHINE_SEMI_PROFESSIONAL: enums.WashingMachineProgramId,
    MieleAppliance.WASHING_MACHINE_PROFESSIONAL: enums.WashingMachineProgramId,
    MieleAppliance.TUMBLE_DRYER: enums.TumbleDryerProgramId,
    MieleAppliance.TUMBLE_DRYER_SEMI_PROFESSIONAL: enums.TumbleDryerProgramId,
    MieleAppliance.DRYER_PROFESSIONAL: enums.TumbleDryerProgramId,
    MieleAppliance.WASHER_DRYER: enums.WashingMachineProgramId,
    MieleAppliance.DISHWASHER: enums.DishWasherProgramId,
    MieleAppliance.DISHWASHER_SEMI_PROFESSIONAL: enums.DishWasherProgramId,
    MieleAppliance.DISHWASHER_PROFESSIONAL: enums.DishWasherProgramId,
    MieleAppliance.OVEN: enums.OvenProgramId,
    MieleAppliance.OVEN_MICROWAVE: enums.OvenProgramId,
    MieleAppliance.STEAM_OVEN: enums.OvenProgramId,
    MieleAppliance.STEAM_OVEN_COMBI: enums.OvenProgramId,
    MieleAppliance.STEAM_OVEN_MICRO: enums.SteamOvenMicroProgramId,
    MieleAppliance.STEAM_OVEN_MK2: enums.OvenProgramId,
    MieleAppliance.DIALOG_OVEN: enums.OvenProgramId,
    MieleAppliance.MICROWAVE: enums.OvenProgramId,
    MieleAppliance.DISH_WARMER: enums.DishWarmerProgramId,
    MieleAppliance.COFFEE_SYSTEM: enums.CoffeeSystemProgramId,
    MieleAppliance.ROBOT_VACUUM_CLEANER: enums.RobotVacuumCleanerProgramId,
}

_PROGRAM_PHASE_BY_TYPE: dict[MieleAppliance, dict[int, str]] = {
    MieleAppliance.WASHING_MACHINE: enums.ProgramPhaseWashingMachine,
    MieleAppliance.WASHING_MACHINE_SEMI_PROFESSIONAL: enums.ProgramPhaseWashingMachine,
    MieleAppliance.WASHING_MACHINE_PROFESSIONAL: enums.ProgramPhaseWashingMachine,
    MieleAppliance.TUMBLE_DRYER: enums.ProgramPhaseTumbleDryer,
    MieleAppliance.TUMBLE_DRYER_SEMI_PROFESSIONAL: enums.ProgramPhaseTumbleDryer,
    MieleAppliance.DRYER_PROFESSIONAL: enums.ProgramPhaseTumbleDryer,
    MieleAppliance.WASHER_DRYER: enums.ProgramPhaseWasherDryer,
    MieleAppliance.DISHWASHER: enums.ProgramPhaseDishwasher,
    MieleAppliance.DISHWASHER_SEMI_PROFESSIONAL: enums.ProgramPhaseDishwasher,
    MieleAppliance.DISHWASHER_PROFESSIONAL: enums.ProgramPhaseDishwasher,
    MieleAppliance.OVEN: enums.ProgramPhaseOven,
    MieleAppliance.OVEN_MICROWAVE: enums.ProgramPhaseMicrowaveOvenCombo,
    MieleAppliance.STEAM_OVEN: enums.ProgramPhaseSteamOven,
    MieleAppliance.STEAM_OVEN_COMBI: enums.ProgramPhaseSteamOvenCombi,
    MieleAppliance.STEAM_OVEN_MICRO: enums.ProgramPhaseSteamOvenMicro,
    MieleAppliance.STEAM_OVEN_MK2: enums.ProgramPhaseSteamOven,
    MieleAppliance.DIALOG_OVEN: enums.ProgramPhaseOven,
    MieleAppliance.MICROWAVE: enums.ProgramPhaseMicrowave,
    MieleAppliance.DISH_WARMER: enums.ProgramPhaseWarmingDrawer,
    MieleAppliance.COFFEE_SYSTEM: enums.ProgramPhaseCoffeeSystem,
    MieleAppliance.ROBOT_VACUUM_CLEANER: enums.ProgramPhaseRobotVacuumCleaner,
}


def _lookup_for_type(table_map: dict[MieleAppliance, dict[int, str]],
                     state: dict[str, Any],
                     field: str,
                     device_type: MieleAppliance) -> str | None:
    table = table_map.get(device_type)
    if not table:
        return None
    v = state.get(field)
    if not isinstance(v, int):
        return None
    return table.get(v) or str(v)

ALL_TYPES: tuple[MieleAppliance, ...] = tuple(
    t for t in MieleAppliance if t is not MieleAppliance.UNKNOWN
)

# --- enum lookups (translation-key values) -----------------------------------
STATUS_LABELS = enums.StateStatus
PROGRAM_TYPE_LABELS = enums.StateProgramType
PROCESS_ACTION_LABELS = {0: "no_action", 1: "start", 2: "stop", 3: "pause", 6: "resume"}
DEVICE_ACTION_LABELS = {0: "no_action", 1: "start_remote", 2: "wake_up", 3: "go_to_standby"}
STANDBY_STATE_LABELS = {0: "not_in_standby", 1: "network_idle", 2: "deep_standby", 3: "going_to_standby"}
SYNC_STATE_LABELS = {0: "unknown", 1: "synced", 2: "out_of_sync"}
REMOTE_LABELS = {0: "disabled", 7: "enabled_but_not_possible", 15: "full"}


def _enum(state: dict[str, Any], key: str, labels: dict[int, str]) -> str | None:
    v = state.get(key)
    if not isinstance(v, int):
        return None
    return labels.get(v) or str(v)


def _enum_option(state: dict[str, Any], key: str, labels: dict[int, str]) -> str | None:
    """Like _enum but for ENUM-class sensors: unmapped firmware codes return "unknown"
    instead of str(v), keeping the returned value always within the declared options list.
    Firmware variants emitting undocumented codes are a genuine I/O-boundary case."""
    v = state.get(key)
    if not isinstance(v, int):
        return None
    return labels.get(v, "unknown")


def _is_idle(state: dict[str, Any]) -> bool:
    """The local /State endpoint keeps the last ProgramID/Phase/Time cached
    until the next cycle starts. The official Miele app gates display on the
    device's Status code — same gate we apply here. Idle = no meaningful
    program info to show.
    """
    s = state.get("Status")
    return not isinstance(s, int) or s in IDLE_STATUSES


def _gated_enum(key: str, labels: dict[int, str]) -> Callable[[dict[str, Any]], Any]:
    def fn(state: dict[str, Any]) -> Any:
        if _is_idle(state):
            return None
        return _enum(state, key, labels)
    return fn


def _gated_enum_option(key: str, labels: dict[int, str]) -> Callable[[dict[str, Any]], Any]:
    def fn(state: dict[str, Any]) -> Any:
        if _is_idle(state):
            return None
        return _enum_option(state, key, labels)
    return fn


def _gated(getter: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
    def fn(state: dict[str, Any]) -> Any:
        if _is_idle(state):
            return None
        return getter(state)
    return fn


def _hob_plate_step(state: dict[str, Any], zone: int) -> str | None:
    """Resolve a hob zone's display value, preferring Booster over Level.

    Mirrors the APK's `MapPowerlevelToLevel`: if `Booster` is non-zero, the
    UI labels it `Boost I` / `Boost II` / `Boost III`; otherwise the base
    `PlateStep` is shown via `HobPlateStep`. On newer K-modules (KM7576 /
    EK039W) the booster lives in `ExtendedState` and *not* in `PlateStep`,
    so this dual lookup is the only way to surface boost over LAN.
    """
    plate = state.get("PlateStep") or []
    if zone >= len(plate):
        return None
    ext = parse_hob_extended_state(state.get("ExtendedState"))
    if ext and zone < len(ext.zones) and ext.zones[zone].booster:
        return f"boost_{ext.zones[zone].booster}"
    return enums.HobPlateStep.get(plate[zone])


def _temp_or_none(temps: Any, idx: int, *, divisor: int = 100) -> int | float | None:
    """Read the i-th element of /State.Temperature (or .TargetTemperature).
    Returns None for the `-32768` sentinel (unsupported)."""
    if not isinstance(temps, list) or idx >= len(temps):
        return None
    v = temps[idx]
    if not isinstance(v, int) or v == -32768:
        return None
    return v / divisor if divisor > 1 else v


def _minutes(field: Any) -> int | None:
    """Convert /State.RemainingTime [h, m] → total minutes."""
    if isinstance(field, list) and len(field) == 2 and all(isinstance(x, int) for x in field):
        return field[0] * 60 + field[1]
    return None


def _utc_iso_now_plus(minutes: int | None) -> str | None:
    if minutes is None:
        return None
    return (
        _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(minutes=minutes)
    ).replace(microsecond=0).isoformat()


# --- entity descriptions ------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class MieleLanSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]
    required_state_key: str | None = None


@dataclass(frozen=True, kw_only=True)
class MieleLanIdentSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]


@dataclass(frozen=True, kw_only=True)
class MieleLanSensorDef:
    types: tuple[MieleAppliance, ...]
    description: MieleLanSensorDescription


@dataclass(frozen=True, kw_only=True)
class MieleLanIdentSensorDef:
    types: tuple[MieleAppliance, ...]
    description: MieleLanIdentSensorDescription


# --- /State-derived sensors ---------------------------------------------------

SENSOR_TYPES: tuple[MieleLanSensorDef, ...] = (
    # Status — every device
    MieleLanSensorDef(
        types=ALL_TYPES,
        description=MieleLanSensorDescription(
            key="status",
            translation_key="status",
            device_class=SensorDeviceClass.ENUM,
            options=[*sorted(set(STATUS_LABELS.values())), "unknown"],
            value_fn=lambda s: _enum_option(s, "Status", STATUS_LABELS),
        ),
    ),
    # ProgramID / ProgramPhase use *device-type-aware* tables (see
    # _PROGRAM_ID_BY_TYPE and _PROGRAM_PHASE_BY_TYPE above). The value_fn
    # here is just a placeholder — the enum lookup happens in
    # MieleLanProgramSensor.native_value which is selected at setup_entry().
    MieleLanSensorDef(
        types=CYCLE_FAMILY,
        description=MieleLanSensorDescription(
            key="program_id",
            translation_key="program_id",
            value_fn=lambda s: s.get("ProgramID"),
        ),
    ),
    MieleLanSensorDef(
        types=CYCLE_FAMILY,
        description=MieleLanSensorDescription(
            key="program_phase",
            translation_key="program_phase",
            value_fn=lambda s: s.get("ProgramPhase"),
        ),
    ),
    MieleLanSensorDef(
        types=CYCLE_FAMILY,
        description=MieleLanSensorDescription(
            key="program_type",
            translation_key="program_type",
            device_class=SensorDeviceClass.ENUM,
            options=[*sorted(set(PROGRAM_TYPE_LABELS.values())), "unknown"],
            value_fn=_gated_enum_option("ProgramType", PROGRAM_TYPE_LABELS),
        ),
    ),
    # Times — only cycle devices. Gated: while the firmware reports last-cycle
    # values when idle, we hide them to mirror the official app (which shows
    # remaining/elapsed only during Running, and StartAt only when Programmed).
    MieleLanSensorDef(
        types=CYCLE_FAMILY,
        description=MieleLanSensorDescription(
            key="remaining_minutes",
            translation_key="remaining_minutes",
            device_class=SensorDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.MINUTES,
            value_fn=_gated(lambda s: _minutes(s.get("RemainingTime"))),
        ),
    ),
    MieleLanSensorDef(
        types=CYCLE_FAMILY,
        description=MieleLanSensorDescription(
            key="elapsed_minutes",
            translation_key="elapsed_minutes",
            device_class=SensorDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.MINUTES,
            value_fn=_gated(lambda s: _minutes(s.get("ElapsedTime"))),
        ),
    ),
    MieleLanSensorDef(
        types=CYCLE_FAMILY,
        description=MieleLanSensorDescription(
            key="start_in_minutes",
            translation_key="start_in_minutes",
            device_class=SensorDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.MINUTES,
            value_fn=_gated(lambda s: _minutes(s.get("StartTime"))),
        ),
    ),
    # Cavity + core temperature — only oven-family
    MieleLanSensorDef(
        types=OVEN_FAMILY,
        description=MieleLanSensorDescription(
            key="cavity_temperature",
            translation_key="cavity_temperature",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda s: _temp_or_none(s.get("Temperature"), 0),
        ),
    ),
    MieleLanSensorDef(
        types=OVEN_FAMILY,
        description=MieleLanSensorDescription(
            key="core_temperature",
            translation_key="core_temperature",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=lambda s: _temp_or_none(s.get("Temperature"), 1),
        ),
    ),
    MieleLanSensorDef(
        types=OVEN_FAMILY,
        description=MieleLanSensorDescription(
            key="target_temperature",
            translation_key="target_temperature",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            value_fn=_gated(lambda s: _temp_or_none(s.get("TargetTemperature"), 0)),
        ),
    ),
    MieleLanSensorDef(
        types=OVEN_FAMILY,
        description=MieleLanSensorDescription(
            key="core_target_temperature",
            translation_key="core_target_temperature",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            value_fn=_gated(lambda s: _temp_or_none(s.get("TargetTemperature"), 1)),
        ),
    ),
    # Cooling devices — up to 3 zones (fridge_freezer + wine_cabinet_freezer = up to 3)
    *(
        MieleLanSensorDef(
            types=(*COOLING_FAMILY, *WINE_FAMILY),
            description=MieleLanSensorDescription(
                key=f"temperature_zone_{n}",
                translation_key=f"temperature_zone_{n}",
                device_class=SensorDeviceClass.TEMPERATURE,
                native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                state_class=SensorStateClass.MEASUREMENT,
                value_fn=lambda s, _i=n - 1: _temp_or_none(s.get("Temperature"), _i),
            ),
        )
        for n in (1, 2, 3)
    ),
    *(
        MieleLanSensorDef(
            types=(*COOLING_FAMILY, *WINE_FAMILY),
            description=MieleLanSensorDescription(
                key=f"target_temperature_zone_{n}",
                translation_key=f"target_temperature_zone_{n}",
                device_class=SensorDeviceClass.TEMPERATURE,
                native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                value_fn=lambda s, _i=n - 1: _temp_or_none(s.get("TargetTemperature"), _i),
            ),
        )
        for n in (1, 2, 3)
    ),
    # Diagnostics — everywhere
    MieleLanSensorDef(
        types=ALL_TYPES,
        description=MieleLanSensorDescription(
            key="remote_control",
            translation_key="remote_control",
            entity_category=EntityCategory.DIAGNOSTIC,
            device_class=SensorDeviceClass.ENUM,
            options=[*sorted(set(REMOTE_LABELS.values())), "unknown"],
            required_state_key="RemoteEnable",
            value_fn=lambda s: REMOTE_LABELS.get(
                (s.get("RemoteEnable") or [None])[0],  # type: ignore[index]
                "unknown",
            ) if isinstance((s.get("RemoteEnable") or [None])[0], int) else None,
        ),
    ),
    MieleLanSensorDef(
        types=ALL_TYPES,
        description=MieleLanSensorDescription(
            key="standby_state",
            translation_key="standby_state",
            entity_category=EntityCategory.DIAGNOSTIC,
            device_class=SensorDeviceClass.ENUM,
            options=[*sorted(set(STANDBY_STATE_LABELS.values())), "unknown"],
            required_state_key="StandbyState",
            value_fn=lambda s: _enum_option(s, "StandbyState", STANDBY_STATE_LABELS),
        ),
    ),
    MieleLanSensorDef(
        types=ALL_TYPES,
        description=MieleLanSensorDescription(
            key="process_action",
            translation_key="process_action",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            required_state_key="ProcessAction",
            value_fn=lambda s: _enum(s, "ProcessAction", PROCESS_ACTION_LABELS),
        ),
    ),
    MieleLanSensorDef(
        types=ALL_TYPES,
        description=MieleLanSensorDescription(
            key="device_action",
            translation_key="device_action",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            required_state_key="DeviceAction",
            value_fn=lambda s: _enum(s, "DeviceAction", DEVICE_ACTION_LABELS),
        ),
    ),
    # Sync state — meaningful for oven-family and hob (MealSync)
    MieleLanSensorDef(
        types=(*OVEN_FAMILY, *HOB_FAMILY),
        description=MieleLanSensorDescription(
            key="sync_state",
            translation_key="sync_state",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            required_state_key="SyncState",
            value_fn=lambda s: _enum(s, "SyncState", SYNC_STATE_LABELS),
        ),
    ),
    # Dryer — drying step (extra_dry / normal / iron_dry / …)
    MieleLanSensorDef(
        types=(
            MieleAppliance.TUMBLE_DRYER,
            MieleAppliance.TUMBLE_DRYER_SEMI_PROFESSIONAL,
            MieleAppliance.DRYER_PROFESSIONAL,
            MieleAppliance.WASHER_DRYER,
        ),
        description=MieleLanSensorDescription(
            key="drying_step",
            translation_key="drying_step",
            required_state_key="DryingStep",
            value_fn=_gated_enum("DryingStep", enums.StateDryingStep),
        ),
    ),
    # --- Raw (diagnostic) siblings of gated cycle-family sensors -------------
    # These expose the firmware's last-cached value with no status gating —
    # off by default, available for debugging or for users who want the
    # stale value to persist after a cycle ends.
    MieleLanSensorDef(
        types=CYCLE_FAMILY,
        description=MieleLanSensorDescription(
            key="program_id_raw",
            translation_key="program_id_raw",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            value_fn=lambda s: s.get("ProgramID"),
        ),
    ),
    MieleLanSensorDef(
        types=CYCLE_FAMILY,
        description=MieleLanSensorDescription(
            key="program_phase_raw",
            translation_key="program_phase_raw",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            value_fn=lambda s: s.get("ProgramPhase"),
        ),
    ),
    MieleLanSensorDef(
        types=CYCLE_FAMILY,
        description=MieleLanSensorDescription(
            key="program_type_raw",
            translation_key="program_type_raw",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            device_class=SensorDeviceClass.ENUM,
            options=[*sorted(set(PROGRAM_TYPE_LABELS.values())), "unknown"],
            value_fn=lambda s: _enum_option(s, "ProgramType", PROGRAM_TYPE_LABELS),
        ),
    ),
    MieleLanSensorDef(
        types=(
            MieleAppliance.TUMBLE_DRYER,
            MieleAppliance.TUMBLE_DRYER_SEMI_PROFESSIONAL,
            MieleAppliance.DRYER_PROFESSIONAL,
            MieleAppliance.WASHER_DRYER,
        ),
        description=MieleLanSensorDescription(
            key="drying_step_raw",
            translation_key="drying_step_raw",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            required_state_key="DryingStep",
            value_fn=lambda s: _enum(s, "DryingStep", enums.StateDryingStep),
        ),
    ),
    # Hob — per-zone power step (PlateStep[N]) plus per-zone Booster from
    # ExtendedState. On newer K-modules (KM7576 / EK039W) PlateStep stays at
    # the base level while Booster is encoded as bits 6-7 of InfoZone<N>
    # inside ExtendedState. See _hob_plate_step + _hob_booster helpers.
    *(
        MieleLanSensorDef(
            types=HOB_FAMILY,
            description=MieleLanSensorDescription(
                key=f"plate_{n}_step",
                translation_key=f"plate_{n}_step",
                value_fn=lambda s, _i=n - 1: _hob_plate_step(s, _i),
            ),
        )
        for n in range(1, 7)
    ),
    # Per-zone residual heat — user-facing safety info, not diagnostic.
    *(
        MieleLanSensorDef(
            types=HOB_FAMILY,
            description=MieleLanSensorDescription(
                key=f"plate_{n}_remaining_heat",
                translation_key=f"plate_{n}_remaining_heat",
                value_fn=lambda s, _i=n - 1: (
                    enums.HobRemainingHeat.get((s.get("PlateRemainingHeat") or [0])[_i])
                    if _i < len(s.get("PlateRemainingHeat") or [])
                    else None
                ),
            ),
        )
        for n in range(1, 7)
    ),
    *(
        MieleLanSensorDef(
            types=HOB_FAMILY,
            description=MieleLanSensorDescription(
                key=f"plate_{n}_remaining_minutes",
                translation_key=f"plate_{n}_remaining_minutes",
                device_class=SensorDeviceClass.DURATION,
                native_unit_of_measurement=UnitOfTime.MINUTES,
                value_fn=lambda s, _i=n - 1:
                    s.get("PlateRemainingMinutes", [])[_i] if _i < len(s.get("PlateRemainingMinutes", [])) else None,
            ),
        )
        for n in range(1, 7)
    ),
    # Hob: per-zone programmed cook duration, decoded from ExtendedState
    # (DurationZone<N> u16 at offset 28+4*(N-1)). Disabled by default — only
    # meaningful when the user pre-programmed a timer on that zone.
    *(
        MieleLanSensorDef(
            types=HOB_FAMILY,
            description=MieleLanSensorDescription(
                key=f"plate_{n}_duration_minutes",
                translation_key=f"plate_{n}_duration_minutes",
                device_class=SensorDeviceClass.DURATION,
                native_unit_of_measurement=UnitOfTime.MINUTES,
                entity_category=EntityCategory.DIAGNOSTIC,
                entity_registry_enabled_default=False,
                value_fn=lambda s, _i=n - 1: (
                    (e := parse_hob_extended_state(s.get("ExtendedState")))
                    and _i < len(e.zones)
                    and e.zones[_i].duration_minutes or None
                ),
            ),
        )
        for n in range(1, 7)
    ),
    # Hob cooktop-wide cook timer (`TimerHours`+`TimerMinutes` in ExtendedState).
    MieleLanSensorDef(
        types=HOB_FAMILY,
        description=MieleLanSensorDescription(
            key="cooktop_timer_minutes",
            translation_key="cooktop_timer_minutes",
            device_class=SensorDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.MINUTES,
            value_fn=lambda s: (
                (e := parse_hob_extended_state(s.get("ExtendedState")))
                and e.cooktop_timer_minutes or None
            ),
        ),
    ),
    # Light *value* (the boolean "is it on?" question is handled by the light
    # entity itself; this sensor just surfaces the raw enum for diagnostics).
    MieleLanSensorDef(
        types=(*OVEN_FAMILY, MieleAppliance.COFFEE_SYSTEM, MieleAppliance.HOOD, *WINE_FAMILY),
        description=MieleLanSensorDescription(
            key="light_state",
            translation_key="light_state",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            required_state_key="Light",
            value_fn=lambda s: {0: "not_supported", 1: "on", 2: "off"}.get(s.get("Light")),
        ),
    ),
)


# --- /Ident-derived sensors --------------------------------------------------

IDENT_SENSORS: tuple[MieleLanIdentSensorDef, ...] = (
    MieleLanIdentSensorDef(
        types=ALL_TYPES,
        description=MieleLanIdentSensorDescription(
            key="firmware_version",
            translation_key="firmware_version",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            value_fn=lambda i: i.get("xkm_release_version"),
        ),
    ),
    MieleLanIdentSensorDef(
        types=ALL_TYPES,
        description=MieleLanIdentSensorDescription(
            key="wifi_module",
            translation_key="wifi_module",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            value_fn=lambda i: i.get("xkm_tech_type"),
        ),
    ),
    MieleLanIdentSensorDef(
        types=ALL_TYPES,
        description=MieleLanIdentSensorDescription(
            key="material_number",
            translation_key="material_number",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            value_fn=lambda i: i.get("mat_number"),
        ),
    ),
)


@dataclass(frozen=True, kw_only=True)
class MieleLanWlanSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]


@dataclass(frozen=True, kw_only=True)
class MieleLanWlanSensorDef:
    types: tuple[MieleAppliance, ...]
    description: MieleLanWlanSensorDescription


@dataclass(frozen=True, kw_only=True)
class MieleLanDop2SensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]
    required_dop2_key: str | None = None


@dataclass(frozen=True, kw_only=True)
class MieleLanDop2SensorDef:
    types: tuple[MieleAppliance, ...]
    description: MieleLanDop2SensorDescription


# DOP2-derived diagnostics. Only exposed by appliances whose firmware leaves
# the DOP2 surface reachable at the HAN tier (locally-commissioned ovens, in
# practice). Entries map a translation key to a getter against the coordinator's
# `dop2` dict, which is refreshed at DOP2_REFRESH_INTERVAL (see coordinator.py).
DOP2_SENSORS: tuple[MieleLanDop2SensorDef, ...] = (
    MieleLanDop2SensorDef(
        types=OVEN_FAMILY,
        description=MieleLanDop2SensorDescription(
            key="hours_of_operation",
            translation_key="hours_of_operation",
            device_class=SensorDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.HOURS,
            state_class=SensorStateClass.TOTAL_INCREASING,
            entity_category=EntityCategory.DIAGNOSTIC,
            required_dop2_key="hours_of_operation",
            value_fn=lambda d: (
                round(v / 60, 1)
                if (v := (d.get("hours_of_operation") or {}).get("total")) is not None
                else None
            ),
        ),
    ),
)


@dataclass(frozen=True, kw_only=True)
class MieleLanDeviceContextSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]
    container_index: int | None = None


@dataclass(frozen=True, kw_only=True)
class MieleLanDeviceContextSensorDef:
    types: tuple[MieleAppliance, ...]
    description: MieleLanDeviceContextSensorDescription


_WASH2DRY_OPTIONS = [
    "not_active", "activatable", "active",
    "ready_to_receive", "program_received", "no_program_available",
]


def _dos_container_value(
    ctx: dict[str, Any], idx: int, key: str
) -> Any:
    containers = ctx.get("dos_containers")
    if not containers or len(containers) <= idx:
        return None
    c = containers[idx]
    if not c.get("bitmask_inserted"):
        return None
    return c.get(key)


DEVICE_CONTEXT_SENSORS: tuple[MieleLanDeviceContextSensorDef, ...] = (
    MieleLanDeviceContextSensorDef(
        types=LAUNDRY_FAMILY,
        description=MieleLanDeviceContextSensorDescription(
            key="twindos_1_level",
            translation_key="twindos_1_level",
            native_unit_of_measurement="%",
            entity_category=EntityCategory.DIAGNOSTIC,
            container_index=0,
            value_fn=lambda ctx: _dos_container_value(ctx, 0, "filling_level_pct"),
        ),
    ),
    MieleLanDeviceContextSensorDef(
        types=LAUNDRY_FAMILY,
        description=MieleLanDeviceContextSensorDescription(
            key="twindos_2_level",
            translation_key="twindos_2_level",
            native_unit_of_measurement="%",
            entity_category=EntityCategory.DIAGNOSTIC,
            container_index=1,
            value_fn=lambda ctx: _dos_container_value(ctx, 1, "filling_level_pct"),
        ),
    ),
    MieleLanDeviceContextSensorDef(
        types=LAUNDRY_FAMILY,
        description=MieleLanDeviceContextSensorDescription(
            key="twindos_1_dosage",
            translation_key="twindos_1_dosage",
            native_unit_of_measurement="mL",
            entity_category=EntityCategory.DIAGNOSTIC,
            container_index=0,
            value_fn=lambda ctx: _dos_container_value(ctx, 0, "current_dosage_ml"),
        ),
    ),
    MieleLanDeviceContextSensorDef(
        types=LAUNDRY_FAMILY,
        description=MieleLanDeviceContextSensorDescription(
            key="twindos_2_dosage",
            translation_key="twindos_2_dosage",
            native_unit_of_measurement="mL",
            entity_category=EntityCategory.DIAGNOSTIC,
            container_index=1,
            value_fn=lambda ctx: _dos_container_value(ctx, 1, "current_dosage_ml"),
        ),
    ),
    MieleLanDeviceContextSensorDef(
        types=LAUNDRY_FAMILY,
        description=MieleLanDeviceContextSensorDescription(
            key="wash2dry_state",
            translation_key="wash2dry_state",
            device_class=SensorDeviceClass.ENUM,
            options=_WASH2DRY_OPTIONS,
            entity_category=EntityCategory.DIAGNOSTIC,
            container_index=None,
            value_fn=lambda ctx: ctx.get("wash2dry_state"),
        ),
    ),
)


# WLAN connection info — surfaced from the device's `/WLAN/` endpoint.
WLAN_SENSORS: tuple[MieleLanWlanSensorDef, ...] = (
    MieleLanWlanSensorDef(
        types=ALL_TYPES,
        description=MieleLanWlanSensorDescription(
            key="ip_address",
            translation_key="ip_address",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            value_fn=lambda w: w.get("IP"),
        ),
    ),
    MieleLanWlanSensorDef(
        types=ALL_TYPES,
        description=MieleLanWlanSensorDescription(
            key="ssid",
            translation_key="ssid",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            value_fn=lambda w: w.get("SSID"),
        ),
    ),
    MieleLanWlanSensorDef(
        types=ALL_TYPES,
        description=MieleLanWlanSensorDescription(
            key="rssi",
            translation_key="rssi",
            entity_category=EntityCategory.DIAGNOSTIC,
            device_class=SensorDeviceClass.SIGNAL_STRENGTH,
            native_unit_of_measurement="dBm",
            value_fn=lambda w: w.get("RSSI"),
        ),
    ),
    MieleLanWlanSensorDef(
        types=ALL_TYPES,
        description=MieleLanWlanSensorDescription(
            key="signal_percentage",
            translation_key="signal_percentage",
            entity_category=EntityCategory.DIAGNOSTIC,
            native_unit_of_measurement="%",
            value_fn=lambda w: w.get("Percentage"),
        ),
    ),
    MieleLanWlanSensorDef(
        types=ALL_TYPES,
        description=MieleLanWlanSensorDescription(
            key="gateway",
            translation_key="gateway",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            value_fn=lambda w: w.get("Gateway"),
        ),
    ),
    MieleLanWlanSensorDef(
        types=ALL_TYPES,
        description=MieleLanWlanSensorDescription(
            key="dns_server",
            translation_key="dns_server",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            value_fn=lambda w: w.get("DNS1"),
        ),
    ),
)


# --- setup -------------------------------------------------------------------

def _present_temperature_zones(coord: MieleLanCoordinator) -> set[int]:
    """Return {1..3} subset of temperature zones actually installed.

    Cooling/wine appliances expose Temperature/TargetTemperature as a
    fixed-length-3 int16 array. Zones that aren't installed report the
    int16 minimum (-32768) sentinel. Observed on a 2-compartment KF 7772 B:
    Temperature=[500, -1807, -32768]  → zones 1 and 2 present, zone 3 absent.
    """
    if not coord.data:
        return {1, 2, 3}
    state = coord.data.state
    present = set()
    for source in ("Temperature", "TargetTemperature"):
        arr = state.get(source) or []
        for i in range(min(3, len(arr))):
            v = arr[i]
            if isinstance(v, int) and v != -32768:
                present.add(i + 1)
    return present


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    bundle = hass.data[DOMAIN][entry.entry_id]
    coordinators: dict[str, MieleLanCoordinator] = bundle["coordinators"]
    entities: list[Any] = []
    for coord in coordinators.values():
        dt = coord.device_type
        temp_zones = _present_temperature_zones(coord)
        state = coord.data.state if coord.data else {}
        for d in SENSOR_TYPES:
            if dt not in d.types:
                continue
            key = d.description.key
            # Skip per-zone temperature sensors for absent zones (e.g. zone 3
            # on a 2-compartment fridge-freezer where Temperature[2] == -32768).
            if key.startswith("temperature_zone_") or key.startswith("target_temperature_zone_"):
                n = int(key.rsplit("_", 1)[1])
                if n not in temp_zones:
                    continue
            rsk = d.description.required_state_key
            if rsk is not None and rsk not in state:
                continue
            # Switch to a device-type-aware class for program_id / phase.
            # Gated variants → MieleLanProgramSensor (Status-gated).
            # Raw variants → MieleLanProgramRawSensor (always exposes
            # the firmware's cached value, marked diagnostic).
            if key in ("program_id", "program_phase"):
                entities.append(MieleLanProgramSensor(coord, d.description))
            elif key in ("program_id_raw", "program_phase_raw"):
                entities.append(MieleLanProgramRawSensor(coord, d.description))
            else:
                entities.append(MieleLanSensor(coord, d.description))
        for d in IDENT_SENSORS:
            if dt in d.types:
                entities.append(MieleLanIdentSensor(coord, d.description))
        for d in WLAN_SENSORS:
            if dt in d.types:
                entities.append(MieleLanWlanSensor(coord, d.description))
        for d in DOP2_SENSORS:
            if dt not in d.types:
                continue
            if not coord.dop2_supported:
                continue
            rdk = d.description.required_dop2_key
            if rdk is not None and rdk not in coord.data.dop2:
                continue
            entities.append(MieleLanDop2Sensor(coord, d.description))
        for d in DEVICE_CONTEXT_SENSORS:
            if dt not in d.types:
                continue
            if not coord.data.device_context:
                continue
            ci = d.description.container_index
            if ci is not None:
                containers = coord.data.device_context.get("dos_containers")
                if not containers or len(containers) <= ci:
                    continue
            entities.append(MieleLanDeviceContextSensor(coord, d.description))
        # Diagnostic push-mode sensor — applies to every appliance.
        entities.append(MieleLanPushStateSensor(coord))
    async_add_entities(entities)


# --- entity classes ----------------------------------------------------------

class MieleLanSensor(MieleLanEntity, SensorEntity):
    entity_description: MieleLanSensorDescription

    def __init__(
        self, coordinator: MieleLanCoordinator, description: MieleLanSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        try:
            return self.entity_description.value_fn(self.coordinator.data.state)
        except Exception:  # noqa: BLE001
            return None


class MieleLanProgramSensor(MieleLanEntity, SensorEntity):
    """ProgramID / ProgramPhase sensor that maps the raw int through the
    per-device-type enum table at read-time, and gates the result on Status
    so stale firmware-cached values don't leak when the appliance is off.

    For ProgramPhase, the secondary `phase==0` ("not_running" sentinel) gate
    mirrors Oven.UI.1.dll:46043 — even within Running, phase 0 hides the text.
    """

    entity_description: MieleLanSensorDescription

    def __init__(
        self, coordinator: MieleLanCoordinator, description: MieleLanSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        state = self.coordinator.data.state
        if _is_idle(state):
            return None
        dt = self.coordinator.device_type
        key = self.entity_description.key
        if key == "program_id":
            return _lookup_for_type(_PROGRAM_ID_BY_TYPE, state, "ProgramID", dt)
        if key == "program_phase":
            phase = state.get("ProgramPhase")
            if phase == 0:
                return None
            return _lookup_for_type(_PROGRAM_PHASE_BY_TYPE, state, "ProgramPhase", dt)
        return None


class MieleLanProgramRawSensor(MieleLanEntity, SensorEntity):
    """Diagnostic sibling of MieleLanProgramSensor — exposes the firmware's
    raw cached enum value without any status gating. Useful for debugging
    "why is my phase stale" questions and for users who explicitly want the
    last-cycle value to persist after a program ends.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    entity_description: MieleLanSensorDescription

    def __init__(
        self, coordinator: MieleLanCoordinator, description: MieleLanSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        state = self.coordinator.data.state
        dt = self.coordinator.device_type
        key = self.entity_description.key
        if key == "program_id_raw":
            return _lookup_for_type(_PROGRAM_ID_BY_TYPE, state, "ProgramID", dt)
        if key == "program_phase_raw":
            return _lookup_for_type(_PROGRAM_PHASE_BY_TYPE, state, "ProgramPhase", dt)
        return None


class MieleLanIdentSensor(MieleLanEntity, SensorEntity):
    entity_description: MieleLanIdentSensorDescription

    def __init__(
        self,
        coordinator: MieleLanCoordinator,
        description: MieleLanIdentSensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        try:
            return self.entity_description.value_fn(self.coordinator.data.ident)
        except Exception:  # noqa: BLE001
            return None


class MieleLanWlanSensor(MieleLanEntity, SensorEntity):
    entity_description: MieleLanWlanSensorDescription

    def __init__(
        self,
        coordinator: MieleLanCoordinator,
        description: MieleLanWlanSensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        try:
            return self.entity_description.value_fn(self.coordinator.data.wlan)
        except Exception:  # noqa: BLE001
            return None


class MieleLanDop2Sensor(MieleLanEntity, SensorEntity):
    entity_description: MieleLanDop2SensorDescription

    def __init__(
        self,
        coordinator: MieleLanCoordinator,
        description: MieleLanDop2SensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        try:
            return self.entity_description.value_fn(self.coordinator.data.dop2)
        except Exception:  # noqa: BLE001
            return None


class MieleLanDeviceContextSensor(MieleLanEntity, SensorEntity):
    entity_description: MieleLanDeviceContextSensorDescription

    def __init__(
        self,
        coordinator: MieleLanCoordinator,
        description: MieleLanDeviceContextSensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        try:
            return self.entity_description.value_fn(self.coordinator.data.device_context)
        except Exception:  # noqa: BLE001
            return None


class MieleLanPushStateSensor(MieleLanEntity, SensorEntity):
    """Diagnostic: shows whether this device is on push or polling."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "push_mode"
    _attr_has_entity_name = True

    def __init__(self, coordinator: MieleLanCoordinator) -> None:
        super().__init__(coordinator, "push_mode")

    @property
    def native_value(self) -> str:
        return self.coordinator.push_mode

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        e = self.coordinator.enrollment
        last = self.coordinator.last_push_at
        return {
            "supervision_ok": bool(e and e.supervision_ok),
            "subscription_routes": list(e.subscriptions_ok) if e else [],
            "push_count": self.coordinator.push_count,
            "last_push_at": (
                _dt.datetime.fromtimestamp(last, tz=_dt.timezone.utc).isoformat()
                if last else None
            ),
            "host_ip": e.host_ip if e else None,
            "fab": self.coordinator.fab,
        }
