"""Support for AUX Cloud sensors."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api.aux_cloud import ReportType
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
from .const import DOMAIN, MANUFACTURER, _LOGGER
from .util import BaseEntity

ENERGY_SENSORS: dict[ReportType, SensorEntityDescription] = {
    "day": SensorEntityDescription(
        key="energy_consumption_day",
        name="Power Consumption Today",
        icon="mdi:lightning-bolt",
        translation_key="energy_consumption_day",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
    ),
    "month": SensorEntityDescription(
        key="energy_consumption_month",
        name="Power Consumption This Month",
        icon="mdi:calendar-month",
        translation_key="energy_consumption_month",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
    ),
    "year": SensorEntityDescription(
        key="energy_consumption_year",
        name="Power Consumption This Year",
        icon="mdi:calendar",
        translation_key="energy_consumption_year",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
    ),
}

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
    AC_TENELEC: {
        "type": "power",
        "param": AC_TENELEC,
        "description": SensorEntityDescription(
            key=AC_TENELEC,
            name="Power Consumption",
            icon="mdi:flash",
            translation_key="power_consumption",
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.WATT,
            state_class=SensorStateClass.MEASUREMENT,
        ),
        "get_fn": lambda d: (
            d.get("params", {}).get(AC_TENELEC) / 10
            if d.get("params", {}).get(AC_TENELEC) is not None
            else None
        ),
    },
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up AUX Cloud sensors."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    stats_coordinator = data["stats_coordinator"]

    entities = []

    _LOGGER.debug("Setting up AUX Cloud sensors %s", coordinator.data["devices"])
    for device in coordinator.data["devices"]:
        supported_params = AuxProducts.get_params_list(device["productId"])
        supported_special_params = AuxProducts.get_special_params_list(
            device["productId"]
        )

        if AuxProducts.supports_energy_stats(device.get("productId")):
            for report_type, description in ENERGY_SENSORS.items():
                entities.append(
                    AuxCloudEnergySensor(
                        stats_coordinator,
                        coordinator,
                        device["endpointId"],
                        report_type,
                        description,
                    )
                )

        for entity in SENSORS.values():
            if "productId" in device and (
                (supported_params and entity["description"].key in supported_params)
                or (
                    supported_special_params
                    and entity["description"].key in supported_special_params
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


class AuxCloudEnergySensor(CoordinatorEntity, SensorEntity):
    """Energy consumption sensor backed by AUX Cloud statistics."""

    def __init__(
        self,
        stats_coordinator,
        device_coordinator,
        device_id: str,
        report_type: ReportType,
        entity_description: SensorEntityDescription,
    ):
        """Initialize the energy sensor."""
        super().__init__(stats_coordinator)
        self._device_coordinator = device_coordinator
        self._device_id = device_id
        self._report_type = report_type
        self.entity_description = entity_description
        self._attr_has_entity_name = True
        self._attr_unique_id = (
            f"{DOMAIN}_{self._device_id.lstrip('0')}_{entity_description.key}"
        )
        self.entity_id = f"sensor.{self._attr_unique_id}"

    @property
    def _device(self):
        """Return the live device record."""
        return self._device_coordinator.get_device_by_endpoint_id(self._device_id)

    @property
    def unique_id(self):
        """Return a unique ID for the sensor."""
        return self._attr_unique_id

    @property
    def device_info(self):
        """Return the device info."""
        device = self._device or {}
        return DeviceInfo(
            connections=(
                {(CONNECTION_NETWORK_MAC, device["mac"])}
                if "mac" in device
                else None
            ),
            identifiers={(DOMAIN, self._device_id)},
            name=device.get("friendlyName", "AUX"),
            manufacturer=MANUFACTURER,
            model=AuxProducts.get_device_name(device.get("productId")),
        )

    @property
    def available(self) -> bool:
        """Return True if stats data is available."""
        if self._device is None:
            return False
        stats = self.coordinator.data.get("stats", {}).get(self._device_id, {})
        report = stats.get(self._report_type)
        return report is not None and report.get("total_kwh") is not None

    @property
    def native_value(self):
        """Return total energy consumption for the period."""
        stats = self.coordinator.data.get("stats", {}).get(self._device_id, {})
        report = stats.get(self._report_type)
        if not report:
            return None
        return report.get("total_kwh")
