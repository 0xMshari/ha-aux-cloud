"""Support for AUX Cloud sensors."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api.const import (
    AC_TEMPERATURE_AMBIENT,
    AC_TEMPERATURE_TARGET,
    AC_TENELEC,
    AUX_ERROR_FLAG,
    AuxProducts,
    HP_HOT_WATER_TANK_TEMPERATURE,
    HP_HOT_WATER_TEMPERATURE_TARGET,
    HP_HEATER_TEMPERATURE_TARGET,
)
from .const import DOMAIN, _LOGGER
from .power_consumption import AuxCloudPowerSensor
from .util import BaseEntity

SENSORS: dict[str, dict[str, any]] = {
    AC_TEMPERATURE_AMBIENT: {
        "type": "temperature",
        "param": AC_TEMPERATURE_AMBIENT,
        "description": SensorEntityDescription(
            key=AC_TEMPERATURE_AMBIENT,
            name="Ambient Temperature",
            icon="mdi:thermometer",
            translation_key="ambient_temperature",
            device_class="temperature",
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        ),
        "get_fn": lambda d: d.get("params", {}).get(AC_TEMPERATURE_AMBIENT, 0) / 10,
    },
    HP_HOT_WATER_TANK_TEMPERATURE: {
        "type": "temperature",
        "param": HP_HOT_WATER_TANK_TEMPERATURE,
        "description": SensorEntityDescription(
            key=HP_HOT_WATER_TANK_TEMPERATURE,
            name="Water Tank Temperature",
            icon="mdi:thermometer-water",
            translation_key="water_tank_temperature",
            device_class="temperature",
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        ),
        "get_fn": lambda d: (
            d.get("params", {}).get(HP_HOT_WATER_TANK_TEMPERATURE, 0) / 10
            if AuxProducts.is_v3_heat_pump(d)
            else d.get("params", {}).get(HP_HOT_WATER_TANK_TEMPERATURE, 0)
        ),
    },
    HP_HOT_WATER_TEMPERATURE_TARGET: {
        "type": "temperature",
        "param": HP_HOT_WATER_TEMPERATURE_TARGET,
        "description": SensorEntityDescription(
            key=HP_HOT_WATER_TEMPERATURE_TARGET,
            name="Hot Water Temperature",
            icon="mdi:thermometer-water",
            translation_key="hot_water_temperature",
            device_class="temperature",
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        ),
        "get_fn": lambda d: d.get("params", {}).get(HP_HOT_WATER_TEMPERATURE_TARGET, 0)
        / 10,
    },
    AC_TEMPERATURE_TARGET: {
        "type": "temperature",
        "param": AC_TEMPERATURE_TARGET,
        "description": SensorEntityDescription(
            key=AC_TEMPERATURE_TARGET,
            name="AC Target Temperature",
            icon="mdi:home-thermometer",
            translation_key="ac_temperature",
            device_class="temperature",
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        ),
        "get_fn": lambda d: d.get("params", {}).get(AC_TEMPERATURE_TARGET, 0) / 10,
    },
    HP_HEATER_TEMPERATURE_TARGET: {
        "type": "temperature",
        "param": HP_HEATER_TEMPERATURE_TARGET,
        "description": SensorEntityDescription(
            key=HP_HEATER_TEMPERATURE_TARGET,
            name="HP Target Temperature",
            icon="mdi:home-thermometer",
            translation_key="ac_temperature",
            device_class="temperature",
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        ),
        "get_fn": lambda d: d.get("params", {}).get(HP_HEATER_TEMPERATURE_TARGET, 0)
        / 10,
    },
    AUX_ERROR_FLAG: {
        "type": "diagnostic",
        "param": AUX_ERROR_FLAG,
        "description": SensorEntityDescription(
            key=AUX_ERROR_FLAG,
            name="Error Flag",
            icon="mdi:alert-circle",
            translation_key="err_flag",
            device_class="diagnostic",
        ),
        "get_fn": lambda d: d.get("params", {}).get(AUX_ERROR_FLAG, None),
    },
    # Live power (W). Required by apps like Vulpo that filter device_class=power.
    # Kept separate from the kWh Energy Consumption sensor.
    AC_TENELEC: {
        "type": "power",
        "param": AC_TENELEC,
        "description": SensorEntityDescription(
            key="power",
            name="Power",
            icon="mdi:flash",
            translation_key="power",
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.WATT,
            state_class=SensorStateClass.MEASUREMENT,
        ),
        "get_fn": lambda d: _parse_live_power_watts(d),
    },
}


def _parse_live_power_watts(device: dict) -> float | None:
    """Return instantaneous power in W from the live tenelec device param."""
    raw = device.get("params", {}).get(AC_TENELEC)
    if raw is None or raw == "":
        return None
    try:
        return float(raw) / 10
    except (TypeError, ValueError):
        return None


def _estimate_power_watts_from_hour_kwh(hour_kwh: float | None) -> float | None:
    """Estimate average Watts from a one-hour energy bucket (kWh)."""
    if hour_kwh is None:
        return None
    try:
        return round(float(hour_kwh) * 1000.0, 1)
    except (TypeError, ValueError):
        return None


def _device_exposes_power(device: dict) -> bool:
    """True when live tenelec and/or energy stats can feed the Power sensor."""
    product_id = device.get("productId")
    if not product_id:
        return False
    if AuxProducts.supports_energy_stats(product_id):
        return True
    supported_params = AuxProducts.get_params_list(product_id) or []
    supported_special = AuxProducts.get_special_params_list(product_id) or []
    return AC_TENELEC in supported_params or AC_TENELEC in supported_special


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AUX Cloud sensors."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    power_coordinator = data["power_coordinator"]

    entities = []

    _LOGGER.debug("Setting up AUX Cloud sensors %s", coordinator.data["devices"])
    for device in coordinator.data["devices"]:
        supported_params = AuxProducts.get_params_list(device["productId"])
        supported_special_params = AuxProducts.get_special_params_list(
            device["productId"]
        )

        if AuxProducts.supports_energy_stats(device.get("productId")):
            entities.append(
                AuxCloudPowerSensor(
                    power_coordinator,
                    coordinator,
                    device["endpointId"],
                )
            )

        if _device_exposes_power(device):
            entities.append(
                AuxCloudPowerWattsSensor(
                    coordinator,
                    power_coordinator,
                    device["endpointId"],
                )
            )

        for entity in SENSORS.values():
            param_key = entity.get("param") or entity["description"].key
            # Power is handled by AuxCloudPowerWattsSensor (live + stats fallback).
            if param_key == AC_TENELEC:
                continue
            if "productId" in device and (
                (supported_params and param_key in supported_params)
                or (
                    supported_special_params
                    and param_key in supported_special_params
                )
            ):
                sensor = AuxCloudSensor(
                    coordinator,
                    device["endpointId"],
                    entity["description"],
                    entity["get_fn"],
                )
                entities.append(sensor)
                _LOGGER.debug(
                    "Adding sensor entity for %s with unique_id %s",
                    device["friendlyName"],
                    sensor.unique_id,
                )

    async_add_entities(entities, True)


class AuxCloudSensor(BaseEntity, CoordinatorEntity, SensorEntity):
    """Representation of an AUX Cloud temperature sensor."""

    def __init__(self, coordinator, device_id, entity_description, get_value_fn):
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, entity_description)
        self._get_value_fn = get_value_fn
        self.entity_id = f"sensor.{self._attr_unique_id}"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        if self._device is None:
            return None

        return self._get_value_fn(self._device)


class AuxCloudPowerWattsSensor(BaseEntity, SensorEntity):
    """Instantaneous power in Watts for Vulpo / power cards.

    Prefers live ``tenelec`` (device_class=power). When the unit does not expose
    live tenelec, falls back to average Watts from the latest hourly energy
    bucket so the card is not stuck on unknown.
    """

    def __init__(self, device_coordinator, power_coordinator, device_id: str):
        """Initialize the power sensor."""
        super().__init__(
            device_coordinator, device_id, SENSORS[AC_TENELEC]["description"]
        )
        self._power_coordinator = power_coordinator
        self.entity_id = f"sensor.{self._attr_unique_id}"

    async def async_added_to_hass(self) -> None:
        """Also refresh when energy stats update (fallback source)."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._power_coordinator.async_add_listener(self._handle_power_stats_update)
        )

    @callback
    def _handle_power_stats_update(self) -> None:
        self.async_write_ha_state()

    def _power_record(self) -> dict:
        data = self._power_coordinator.data or {}
        return data.get("consumption", {}).get(self._device_id) or {}

    @property
    def available(self) -> bool:
        """Available when live or estimated watts can be resolved."""
        if not self._device or not self._device.get("endpointId"):
            return False
        return self.native_value is not None

    @property
    def native_value(self):
        """Return Watts from live tenelec, else latest hourly estimate."""
        live = _parse_live_power_watts(self._device or {})
        if live is not None:
            return live
        return _estimate_power_watts_from_hour_kwh(
            self._power_record().get("latest_hour_kwh")
        )

    @property
    def extra_state_attributes(self):
        """Expose which source produced the Watts reading."""
        live = _parse_live_power_watts(self._device or {})
        record = self._power_record()
        if live is not None:
            source = "live"
        elif record.get("latest_hour_kwh") is not None:
            source = "estimated_from_hour"
        else:
            source = None
        return {
            "power_source": source,
            "latest_hour": record.get("latest_hour"),
            "latest_hour_kwh": record.get("latest_hour_kwh"),
        }
