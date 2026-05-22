"""Base entity for Miele@LAN — shared device_info + availability."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN, MieleAppliance
from .coordinator import MieleLanCoordinator


# Human-readable model labels per device type. Used as DeviceInfo.model so
# the user sees "Oven" rather than the raw "DGC7860HCXL" tech-type.
_MODEL_LABELS: dict[MieleAppliance, str] = {
    MieleAppliance.WASHING_MACHINE: "Washing Machine",
    MieleAppliance.WASHING_MACHINE_SEMI_PROFESSIONAL: "Washing Machine",
    MieleAppliance.WASHING_MACHINE_PROFESSIONAL: "Washing Machine",
    MieleAppliance.TUMBLE_DRYER: "Tumble Dryer",
    MieleAppliance.TUMBLE_DRYER_SEMI_PROFESSIONAL: "Tumble Dryer",
    MieleAppliance.DRYER_PROFESSIONAL: "Tumble Dryer",
    MieleAppliance.WASHER_DRYER: "Washer-Dryer",
    MieleAppliance.DISHWASHER: "Dishwasher",
    MieleAppliance.DISHWASHER_SEMI_PROFESSIONAL: "Dishwasher",
    MieleAppliance.DISHWASHER_PROFESSIONAL: "Dishwasher",
    MieleAppliance.OVEN: "Oven",
    MieleAppliance.OVEN_MICROWAVE: "Oven-Microwave",
    MieleAppliance.STEAM_OVEN: "Steam Oven",
    MieleAppliance.STEAM_OVEN_COMBI: "Combi Steam Oven",
    MieleAppliance.STEAM_OVEN_MICRO: "Steam-Microwave",
    MieleAppliance.STEAM_OVEN_MK2: "Steam Oven",
    MieleAppliance.DIALOG_OVEN: "Dialog Oven",
    MieleAppliance.MICROWAVE: "Microwave",
    MieleAppliance.HOB_HIGHLIGHT: "Hob",
    MieleAppliance.HOB_INDUCTION: "Hob",
    MieleAppliance.HOB_INDUCT_EXTR: "Hob with Extractor",
    MieleAppliance.HOOD: "Hood",
    MieleAppliance.FRIDGE: "Fridge",
    MieleAppliance.FREEZER: "Freezer",
    MieleAppliance.FRIDGE_FREEZER: "Fridge-Freezer",
    MieleAppliance.WINE_CABINET: "Wine Cabinet",
    MieleAppliance.WINE_CONDITIONING_UNIT: "Wine Cabinet",
    MieleAppliance.WINE_STORAGE_CONDITIONING_UNIT: "Wine Cabinet",
    MieleAppliance.WINE_CABINET_FREEZER: "Wine Cabinet/Freezer",
    MieleAppliance.COFFEE_SYSTEM: "Coffee System",
    MieleAppliance.DISH_WARMER: "Dish Warmer",
    MieleAppliance.ROBOT_VACUUM_CLEANER: "Robot Vacuum",
}


class MieleLanEntity(CoordinatorEntity[MieleLanCoordinator]):
    """Base for all Miele@LAN entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MieleLanCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.client.route}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        coord = self.coordinator
        ident = coord.data.ident if coord.data else {}
        device_type = coord.device_type
        model_label = _MODEL_LABELS.get(device_type)

        # Combine the XKM WiFi-module hardware tech-type, the appliance's
        # Material Number, and the LAN IP so all three show on the "Hardware"
        # line in HA's device card. HA's DeviceInfo schema has no dedicated
        # `ip_address` field — appending here is the standard workaround.
        xkm_tech = ident.get("xkm_tech_type") or ""
        mat = ident.get("mat_number") or ""
        host = getattr(coord.client, "host", None)
        parts: list[str] = []
        if xkm_tech:
            parts.append(xkm_tech)
        if mat:
            parts.append(f"(Mat. {mat})")
        if host:
            parts.append(f"@ {host}")
        hw_version = " ".join(parts) or None

        # Also expose as configuration_url so HA renders the "Visit device"
        # link at the bottom of the device card.
        configuration_url = f"http://{host}/" if host else None

        return DeviceInfo(
            identifiers={(DOMAIN, coord.client.route)},
            manufacturer="Miele",
            model=model_label or ident.get("tech_type") or "Unknown",
            model_id=ident.get("tech_type") or None,
            name=ident.get("device_name") or model_label or DEFAULT_NAME,
            serial_number=ident.get("fab_number") or coord.client.route,
            hw_version=hw_version,
            sw_version=ident.get("xkm_release_version") or None,
            configuration_url=configuration_url,
        )
