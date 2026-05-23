"""Parser for the hob's `/State.ExtendedState` binary blob.

Layout RE'd from `MACI_DS_HobExtendedData` in the InfoControl APK
(`Miele.Modules.Hobs.ApplianceCommunication.ApplianceData.Dop1.dll`, the same
struct also exposed as DOP2 object id 31). The blob is a hex-encoded byte
array; on a 6-zone induction hob (KM-series) it is 62 bytes long.

Field offsets (verified live 2026-05-22 on KM7576 with Boost I active on
zone 3: byte 14 = 0x11 (=level 9), byte 15 = 0x48 → bit 3 = pot detected,
bits 6-7 = 01 = Boost I):

    Offset  Type  Field
       0    u8    MainState (0x05 active / 0x0D idle on KM7576)
       1    u8    TimerSeconds (cooktop-wide cook timer countdown)
       2    u8    TimerMinutes
       3    u8    TimerHours
       4    u8    Type
       5    u8    CommonError
       6    u8    CommonInformation
       7    u8    Information
     8+3n   u8    PowerLevelZone<n+1>           (n = 0..5)
     9+3n   u8    InfoZone<n+1>                 — bitfield (see below)
    10+3n   u8    ErrorHintZone<n+1>
    26+4n   u16   StartTimeZone<n+1>            (minutes from midnight)
    28+4n   u16   DurationZone<n+1>             (minutes programmed)
    50+n    u8    KeyZone<n+1>                  — last sensor pressed

The InfoZone byte is a `Info_Kochstelle_Kochstelle_<n>` bitfield (LSB-numbered):

    bit 0   Ankochautomatik    — auto pre-boil ("boost to set point")
    bit 1   Variozone_1        — bridged zone half 1 active
    bit 2   Variozone_2        — bridged zone half 2 active
    bit 3   Topf_erkannt       — pot detected on the zone
    bit 4   Topfgroesse_VZ1    — pot fits VarioZone 1
    bit 5   Topfgroesse_VZ2    — pot fits VarioZone 2
    bit 6-7 Boosterfunktion    — 0 off, 1 Boost I, 2 Boost II, 3 Boost III

The trailing 6 bytes (offsets 56–61) hold reserved / vendor-specific data
not covered by the public APK struct; we ignore them.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# InfoZone bitfield helpers
# ---------------------------------------------------------------------------

def _bit(byte: int, n: int) -> bool:
    return bool((byte >> n) & 1)


@dataclass(frozen=True)
class HobZone:
    """One cooking zone's decoded fields. All 0 / False / None when the
    zone is off or not installed on the model."""

    power_level: int             # raw 0..23 (or 100..107 residual, etc.)
    booster: int                 # 0/1/2/3 (off, I, II, III)
    pot_detected: bool
    ankochautomatik: bool
    variozone_1: bool
    variozone_2: bool
    pot_fits_variozone_1: bool
    pot_fits_variozone_2: bool
    error_hint: int              # raw u8
    start_time_minutes: int      # 0..1439 minutes from midnight (programmed start)
    duration_minutes: int        # 0..1439 minutes
    key: int                     # raw u8 of last touched key


@dataclass(frozen=True)
class HobExtended:
    """Decoded view of `MACI_DS_HobExtendedData`."""

    main_state: int
    timer_seconds: int
    timer_minutes: int
    timer_hours: int
    type_code: int
    common_error: int
    common_information: int
    information: int
    zones: tuple[HobZone, ...]   # 6 zones, index 0 = zone 1

    @property
    def cooktop_timer_minutes(self) -> int:
        """Cooktop-wide cook timer countdown, normalised to minutes."""
        return self.timer_hours * 60 + self.timer_minutes


def parse_hob_extended_state(hex_str: str | None) -> HobExtended | None:
    """Decode the hex-encoded `/State.ExtendedState` blob into a `HobExtended`.

    Returns None when the blob is missing, malformed, or too short.
    """
    if not isinstance(hex_str, str) or not hex_str:
        return None
    try:
        b = bytes.fromhex(hex_str)
    except ValueError:
        return None
    if len(b) < 56:
        return None

    zones: list[HobZone] = []
    for n in range(6):
        power = b[8 + 3 * n]
        info = b[9 + 3 * n]
        err = b[10 + 3 * n]
        start = int.from_bytes(b[26 + 4 * n:28 + 4 * n], "little") if 28 + 4 * n <= len(b) else 0
        dur = int.from_bytes(b[28 + 4 * n:30 + 4 * n], "little") if 30 + 4 * n <= len(b) else 0
        key = b[50 + n] if 50 + n < len(b) else 0
        zones.append(HobZone(
            power_level=power,
            booster=(info >> 6) & 0x3,
            ankochautomatik=_bit(info, 0),
            variozone_1=_bit(info, 1),
            variozone_2=_bit(info, 2),
            pot_detected=_bit(info, 3),
            pot_fits_variozone_1=_bit(info, 4),
            pot_fits_variozone_2=_bit(info, 5),
            error_hint=err,
            start_time_minutes=start,
            duration_minutes=dur,
            key=key,
        ))
    return HobExtended(
        main_state=b[0],
        timer_seconds=b[1],
        timer_minutes=b[2],
        timer_hours=b[3],
        type_code=b[4],
        common_error=b[5],
        common_information=b[6],
        information=b[7],
        zones=tuple(zones),
    )
