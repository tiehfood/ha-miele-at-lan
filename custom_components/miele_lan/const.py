"""Miele@LAN — constants."""

from __future__ import annotations

from enum import IntEnum
from typing import Any

DOMAIN = "miele_lan"
DEFAULT_NAME = "Miele@LAN"
DEFAULT_TIMEOUT = 10.0

# Status values where program/phase/time fields are stale and should be hidden
# from the user. Mirrors the official Miele app's gating (RE'd from
# Oven.UI.1.dll:45992 BuildStateFor(OvenState) and
# LaundryCareMappi.UI.dll:38047 GetActionBarLeftText): the app's action bar
# only shows ProgramPhase when state == Running (5). We extend this to also
# hide for Off / On / Service / Default / Locked / NotConnected — the firmware
# keeps the last ProgramID/ProgramPhase cached until the next cycle starts.
IDLE_STATUSES = {1, 2, 8, 12, 144, 145, 255}


def is_idle_state(state: dict[str, Any]) -> bool:
    """Whether `/State.Status` reflects an idle/no-programme appliance.

    Shared by sensor.py (gates stale program/phase/time fields) and
    coordinator.py (adaptive polling cadence) — both need the same
    "nothing interesting is currently happening" gate.
    """
    status = state.get("Status")
    return not isinstance(status, int) or status in IDLE_STATUSES


CONF_GROUP_ID = "group_id"
CONF_GROUP_KEY = "group_key"
CONF_ROUTE = "route"

# Cloud-pair config-entry keys.
CONF_COUNTRY = "country"
CONF_REGION = "region"              # EU / AS / EU2 (sales-company region)
CONF_REFRESH_TOKEN = "refresh_token"
CONF_DEVICES = "devices"            # list[{fabNr, deviceType, deviceName}] from cloud
CONF_HA_FAB = "ha_fab"              # our synthetic fab number
CONF_HA_PORT = "ha_port"            # which unprivileged port our listener uses
CONF_STATIC_IPS = "static_ips"      # optional dict fab→ip for users whose mDNS is flaky
STATIC_IPS_TEXT_FIELD = "static_ips_text"  # options-flow free-form fab=ip textarea field

CONF_FLOW_KIND = "flow_kind"        # "cloud" vs "manual" (single-device legacy path)
DEFAULT_HA_PUSH_PORT = 18082

# /State action keys.
DEVICE_ACTION_WAKE = 2
PROCESS_ACTION_START = 1
PROCESS_ACTION_STOP = 2
PROCESS_ACTION_PAUSE = 3
PROCESS_ACTION_RESUME = 6

# Preconditions for a `ProcessAction` write (see MieleLanClient.send_process_action).
# Sourced from the reference implementation's readiness gate (MieleRESTServer:
# DeviceReadyToStart = Status==0x04, DeviceRemoteStartCapable = 15 in
# RemoteEnable) and cross-checked against our own StateStatus[4] ==
# "waiting_to_start" (enums.py) and REMOTE_LABELS[15] == "full" (sensor.py).
STATUS_WAITING_TO_START = 4
REMOTE_ENABLE_FULL_CONTROL_INDEX = 0
REMOTE_ENABLE_FULL_CONTROL_VALUE = 15

# DOP2 GLOBAL_USER_REQ leaf — universal across oven, laundry, dishwasher.
USER_REQUEST_UNIT = 2
USER_REQUEST_LEAF = 1583

# Opcode → label.  Sourced from MieleDop2Structures.UserRequestOven (akappner/MieleRESTServer)
# and confirmed alive on H7560BP via DOP2 writes (light, switch on/off).
OPCODE_NOP = 0x00
OPCODE_START = 0x01
OPCODE_STOP = 0x02
OPCODE_PAUSE = 0x03
OPCODE_START_DELAY = 0x08
OPCODE_DOOR_OPEN = 0x0B
OPCODE_DOOR_CLOSE = 0x0C
OPCODE_LIGHT_ON = 0x0D
OPCODE_LIGHT_OFF = 0x0E
OPCODE_FACTORY_RESET = 0x0F
OPCODE_SWITCH_ON = 0x10
OPCODE_NEXT = 0x11
OPCODE_BACK = 0x12
OPCODE_SWITCH_OFF = 0x13
OPCODE_KEEPALIVE = 0x15
OPCODE_PROGRAM_STOP = 0x36
OPCODE_PROGRAM_ABORT = 0x37
OPCODE_PROGRAM_FINALIZE = 0x38

# ---------------------------------------------------------------------------
# Legacy "Dop1" objects (hood ventilation, hood light, hood settings)
# ---------------------------------------------------------------------------
# Dop1 is a separate, older write mechanism from DOP2/GLOBAL_USER_REQ: a signed
# `POST /Devices/{fab}/DOP/` carrying {"Request": "<hex>"}, where the hex is
# "{ObjectId}{RequestType}00" followed by the object's own packed struct.
#
# It is the mechanism the official Miele app itself picks for hoods: its action
# providers try [Dop1, OpCode, Dop2, Default] in order and the Dop1 variant's
# `IsApplianceSpecificallySupported` is `ProtocolVersion == 2`, so on a
# ProtocolVersion==2 hood the app never reaches the DOP2 path. That matters
# here because GLOBAL_USER_REQ (leaf 2/1583) answers HTTP 404 outright on
# these hoods — see `MieleLanClient.write_user_request`. Everything below was
# recovered from the app's own action classes and then confirmed against real
# hardware; `coordinator.hood_dop1_supported` mirrors the app's gate.
DOP1_REQUEST_TYPE_READ = "01"
DOP1_REQUEST_TYPE_WRITE = "02"

# "ServiceDataExt_Lueftersteuerung" (ventilation control), from the app's
# SetFanPowerLevelBaseAction. 4-byte write section:
#   [0] Struktur_Version  = 0
#   [1] Luefterauswahl    = 0xFF (all/default fan)
#   [2] Luefterstufe      = 0..5 (FanPowerLevel: Off, 1, 2, 3, Booster, Interval)
#   [3] Luefterleistung   = 0
# Confirmed live: turning ON takes ~4s before /State.VentilationStep reflects
# it (motor spin-up); turning OFF is near-instant (<1s). No keep-alive needed.
DOP1_LUEFTERSTEUERUNG_OBJECT_ID = "8E01"

# "ServiceDataExt_Nachlaufzeit" — fan run-on time, from SetFanRunOnTimeBaseAction.
# 2-byte write section: [0] Struktur_Version = 0, [1] Nachlaufzeit in minutes.
# The app itself only offers the discrete RunOnTimeLevel values below.
DOP1_NACHLAUFZEIT_OBJECT_ID = "8E06"
RUN_ON_TIME_MINUTES: tuple[int, ...] = (0, 5, 15)

# "SwitchLight_W" — the hood's main cavity light, from the app's
# LightingDop1Actions / ToggleLightSourceAsync(MainLightsource, ...).
# 13-byte write section, all U16s big-endian:
#   [0]     Struktur_Version = 2 (VersionEnum.Version_2_BE)
#   [1]     Lichtquelle      = 1 (TypeLightSource.Licht_Kochfeld_ — main light)
#   [2]     Betriebszustand  = LightingMode: 4 (CookingMode) on, 0 (Off) off
#   [3:9]   Rot/Gruen/Blau_Dimmwert = 0
#   [9:11]  WW_Dimmwert      = 0xFFFF when on, 0 when off
#   [11:13] KW_Dimmwert      = 0
# Worth having alongside the plain `PUT /State {"Light":1|2}` path because it
# applies far faster on this firmware — measured ~0.8s vs ~4.9s to turn on.
DOP1_SWITCHLIGHT_OBJECT_ID = "1400"
DOP1_SWITCHLIGHT_STRUKTUR_VERSION = 2
DOP1_SWITCHLIGHT_LICHTQUELLE_MAIN = 1
DOP1_LIGHTINGMODE_OFF = 0
DOP1_LIGHTINGMODE_COOKING = 4

# Generic "Programmierfunktion" settings object (Setting_PF_Lesen/Schreiben_BE).
# One object id for both directions, distinguished by the request-type byte:
#   write section: [0] Version = 0, [1:3] PF_ID (U16 BE), [3:7] Wert (U32 BE)
#   read  request: [0] Version = 0, [1:3] PF_ID (U16 BE)
#   read  section: [0] Version, [1:3] PF_ID, [3:7] Wert, [7:11] Min, [11:15] Max
DOP1_SETTING_PF_OBJECT_ID = "1201"

# Hood Programmierfunktion ids (ProgrammierfunktionEnum; DA_* = Dunstabzug).
PF_DA_FETTFILTER_GRENZE_AKTUELL = 40010   # current grease-filter saturation grade
PF_DA_KOHLEFILTER_GRENZE_AKTUELL = 40011  # current charcoal-filter saturation grade

# ---------------------------------------------------------------------------
# Appliance taxonomy
# ---------------------------------------------------------------------------
# Mirror of HA core `miele.const.MieleAppliance`, which itself mirrors the
# Miele API enum. We use this to filter per-entity availability — not every
# entity makes sense for every device type.


class MieleAppliance(IntEnum):
    """Miele device-type enum (matches the cloud's `deviceType` field)."""

    WASHING_MACHINE = 1
    TUMBLE_DRYER = 2
    WASHING_MACHINE_SEMI_PROFESSIONAL = 3
    TUMBLE_DRYER_SEMI_PROFESSIONAL = 4
    WASHING_MACHINE_PROFESSIONAL = 5
    DRYER_PROFESSIONAL = 6
    DISHWASHER = 7
    DISHWASHER_SEMI_PROFESSIONAL = 8
    DISHWASHER_PROFESSIONAL = 9
    OVEN = 12
    OVEN_MICROWAVE = 13
    HOB_HIGHLIGHT = 14
    STEAM_OVEN = 15
    MICROWAVE = 16
    COFFEE_SYSTEM = 17
    HOOD = 18
    FRIDGE = 19
    FREEZER = 20
    FRIDGE_FREEZER = 21
    ROBOT_VACUUM_CLEANER = 23
    WASHER_DRYER = 24
    DISH_WARMER = 25
    HOB_INDUCTION = 27
    STEAM_OVEN_COMBI = 31
    WINE_CABINET = 32
    WINE_CONDITIONING_UNIT = 33
    WINE_STORAGE_CONDITIONING_UNIT = 34
    STEAM_OVEN_MICRO = 45
    DIALOG_OVEN = 67
    WINE_CABINET_FREEZER = 68
    STEAM_OVEN_MK2 = 73
    HOB_INDUCT_EXTR = 74
    UNKNOWN = 2147483647  # int.MaxValue sentinel — our own HA peer uses this


# Pre-computed family tuples so per-platform entity tables stay readable.
OVEN_FAMILY: tuple[MieleAppliance, ...] = (
    MieleAppliance.OVEN,
    MieleAppliance.OVEN_MICROWAVE,
    MieleAppliance.STEAM_OVEN,
    MieleAppliance.MICROWAVE,
    MieleAppliance.STEAM_OVEN_COMBI,
    MieleAppliance.STEAM_OVEN_MICRO,
    MieleAppliance.DIALOG_OVEN,
    MieleAppliance.STEAM_OVEN_MK2,
)

LAUNDRY_FAMILY: tuple[MieleAppliance, ...] = (
    MieleAppliance.WASHING_MACHINE,
    MieleAppliance.WASHING_MACHINE_SEMI_PROFESSIONAL,
    MieleAppliance.WASHING_MACHINE_PROFESSIONAL,
    MieleAppliance.TUMBLE_DRYER,
    MieleAppliance.TUMBLE_DRYER_SEMI_PROFESSIONAL,
    MieleAppliance.DRYER_PROFESSIONAL,
    MieleAppliance.WASHER_DRYER,
)

DISHWASHER_FAMILY: tuple[MieleAppliance, ...] = (
    MieleAppliance.DISHWASHER,
    MieleAppliance.DISHWASHER_SEMI_PROFESSIONAL,
    MieleAppliance.DISHWASHER_PROFESSIONAL,
)

# Cycle devices = devices that run a "program" with phases, remaining time, etc.
CYCLE_FAMILY: tuple[MieleAppliance, ...] = (
    *OVEN_FAMILY, *LAUNDRY_FAMILY, *DISHWASHER_FAMILY,
)

# Devices that get the /State ProcessAction "stop" button (button.py:
# stop_process). Deliberately excludes ovens: they already have a working
# stop via the DOP2 GLOBAL_USER_REQ button (stop_program), and we have no
# evidence the two mechanisms behave identically, so shipping both would
# just leave an oven owner guessing which one to press. Laundry and
# dishwasher are exactly the families this PUT /State path exists for —
# appliances that have no DOP2 stop at all on firmware that blocks DOP2
# writes outright.
STOP_PROCESS_FAMILY: tuple[MieleAppliance, ...] = (
    *LAUNDRY_FAMILY, *DISHWASHER_FAMILY,
)

HOB_FAMILY: tuple[MieleAppliance, ...] = (
    MieleAppliance.HOB_HIGHLIGHT,
    MieleAppliance.HOB_INDUCTION,
    MieleAppliance.HOB_INDUCT_EXTR,
)


COOLING_FAMILY: tuple[MieleAppliance, ...] = (
    MieleAppliance.FRIDGE,
    MieleAppliance.FREEZER,
    MieleAppliance.FRIDGE_FREEZER,
)

WINE_FAMILY: tuple[MieleAppliance, ...] = (
    MieleAppliance.WINE_CABINET,
    MieleAppliance.WINE_CONDITIONING_UNIT,
    MieleAppliance.WINE_STORAGE_CONDITIONING_UNIT,
    MieleAppliance.WINE_CABINET_FREEZER,
)

# Devices that have a cavity / cabinet light controllable via /State.Light.
LIGHTABLE_FAMILY: tuple[MieleAppliance, ...] = (
    *OVEN_FAMILY,
    MieleAppliance.COFFEE_SYSTEM,
    MieleAppliance.HOOD,
    *WINE_FAMILY,
)

# Devices with deep-standby (need a Wake action before remote control wakes them).
#
# RE notes (from live /State probes 2026-05-21):
#  - oven: has DeviceAction + StandbyState   → Wake supported ✓
#  - dryer: has DeviceAction + StandbyState  → Wake supported ✓
#  - hob: no DeviceAction in /State          → Wake DOES NOT apply ✗
#  - fridge: no DeviceAction (always running)→ Wake DOES NOT apply ✗
#
# Wake is meaningful only for appliances that genuinely go to deep standby
# (cycle devices + coffee + dish-warmer).
WAKEABLE_FAMILY: tuple[MieleAppliance, ...] = (
    *CYCLE_FAMILY,
    MieleAppliance.COFFEE_SYSTEM,
    MieleAppliance.DISH_WARMER,
)

# Devices that physically have a door we can sense.
DOORED_FAMILY: tuple[MieleAppliance, ...] = (
    *OVEN_FAMILY, *LAUNDRY_FAMILY, *DISHWASHER_FAMILY,
    *COOLING_FAMILY, *WINE_FAMILY,
    MieleAppliance.DISH_WARMER,
)

# Devices that report Light as a meaningful value (1=on, 2=off) instead of 0=unsupported.
HAS_LIGHT_STATE: tuple[MieleAppliance, ...] = LIGHTABLE_FAMILY

# Power switch — devices that can be switched on/off remotely (panel power),
# not the same as Start/Stop of a program (which is heat-gated).
POWERABLE_FAMILY: tuple[MieleAppliance, ...] = (
    *OVEN_FAMILY, *DISHWASHER_FAMILY,
    MieleAppliance.DISH_WARMER,
    MieleAppliance.COFFEE_SYSTEM,
    MieleAppliance.HOOD,
)


def wants_power_switch(device_type: MieleAppliance, *, hood_dop1_supported: bool) -> bool:
    """Whether `device_type` should get the DOP2 power switch entity.

    Every other POWERABLE_FAMILY member accepts the switch's DOP2 write
    (leaf 2/1583). Dop1-capable hoods (ProtocolVersion 2) are the one
    exception: GLOBAL_USER_REQ answers 404 on them outright (see the
    DOP1_* leaf comment above), and the fan entity already covers on/off
    via `set_fan_level(0)` — a second, permanently-broken entity would
    only confuse users. Non-Dop1 hoods (ProtocolVersion 3/4) get no fan
    entity at all, and we have no evidence either way on whether they
    accept the DOP2 write, so they keep the switch rather than lose their
    only remaining power control.
    """
    if device_type not in POWERABLE_FAMILY:
        return False
    if device_type is MieleAppliance.HOOD:
        return not hood_dop1_supported
    return True


# Known-good DOP2 leaves on the H7560BP, per protocol_findings memory.
LEAF_DEVICE_COMBINED_STATE = (2, 1586)   # 24 B — modern
LEAF_DEVICE_COMBINED_LEGACY = (2, 256)   # 232 B — deprecated alias
LEAF_HOURS_OF_OPERATION = (2, 119)       # 47 B
LEAF_CYCLE_COUNTER = (2, 138)            # 49 B
LEAF_USER_REQUEST_STATUS = (2, 1583)     # readable echo of last request
LEAF_SF_VALUE = (2, 105)                 # indexed by SfValueId in idx1
LEAF_UNKNOWN_2_1577 = (2, 1577)          # 278 B — TBD
LEAF_PROGRAM_LIST = (14, 1570)           # 62 B — oven program list (legacy layout)
LEAF_OPTION_LIST = (14, 1571)            # 212 B — options for selected program
LEAF_GENERAL_1_17 = (1, 17)              # 402 B — general capabilities/info
