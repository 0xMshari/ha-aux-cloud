"""Power consumption coordinator, sensor, and entity cleanup."""

from __future__ import annotations

from datetime import date

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity_registry import (
    EntityRegistry,
    async_get as async_get_entity_registry,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .api.aux_cloud import (
    parse_device_stats_total,
    parse_device_stats_values,
    resolve_stats_report_type,
)
from .api.const import AuxProducts
from .const import (
    CONF_POWER_END_DATE,
    CONF_POWER_START_DATE,
    DOMAIN,
    MANUFACTURER,
    POWER_CONSUMPTION_KEY,
    POWER_UPDATE_INTERVAL,
    STALE_POWER_UNIQUE_ID_SUFFIXES,
    _LOGGER,
)

POWER_SENSOR_DESCRIPTION = SensorEntityDescription(
    key=POWER_CONSUMPTION_KEY,
    name="Power Consumption",
    icon="mdi:lightning-bolt",
    translation_key="power_consumption",
    device_class=SensorDeviceClass.ENERGY,
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    state_class=SensorStateClass.TOTAL,
)


def get_power_period(entry: ConfigEntry) -> tuple[date, date]:
    """Return the configured consumption period, defaulting to today."""
    today = date.today()
    start_raw = entry.options.get(CONF_POWER_START_DATE) or entry.data.get(
        CONF_POWER_START_DATE
    )
    end_raw = entry.options.get(CONF_POWER_END_DATE) or entry.data.get(
        CONF_POWER_END_DATE
    )

    if start_raw and end_raw:
        return date.fromisoformat(start_raw), date.fromisoformat(end_raw)

    return today, today


class AuxCloudPowerCoordinator(DataUpdateCoordinator):
    """Fetch power consumption for the configured date range."""

    def __init__(
        self,
        hass: HomeAssistant,
        api,
        device_coordinator,
        entry: ConfigEntry,
    ):
        """Initialize the power consumption coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="AUX Cloud Power Consumption",
            update_interval=POWER_UPDATE_INTERVAL,
        )
        self.api = api
        self.device_coordinator = device_coordinator
        self.entry = entry

    async def _async_update_data(self):
        """Fetch consumption totals for all supported devices."""
        start_date, end_date = get_power_period(self.entry)
        report_type = resolve_stats_report_type(start_date, end_date)
        devices = (
            self.device_coordinator.data.get("devices", [])
            if self.device_coordinator.data
            else []
        )

        consumption: dict[str, dict] = {}
        for device in devices:
            product_id = device.get("productId")
            endpoint_id = device.get("endpointId")
            if not product_id or not endpoint_id:
                continue
            if not AuxProducts.supports_energy_stats(product_id):
                continue
            if not device.get("familyid"):
                continue

            try:
                raw = await self.api.get_device_stats_for_period(
                    device,
                    start_date,
                    end_date,
                    report_type=report_type,
                )
                values = parse_device_stats_values(raw)
                total_kwh = parse_device_stats_total(raw)
                consumption[endpoint_id] = {
                    "total_kwh": total_kwh,
                    "values": values,
                    "data_points": len(values),
                }
            except Exception as exc:
                _LOGGER.warning(
                    "Power consumption query failed for %s: %s",
                    endpoint_id,
                    exc,
                )
                consumption[endpoint_id] = {
                    "total_kwh": None,
                    "values": [],
                    "data_points": 0,
                    "error": str(exc),
                }

        return {
            "consumption": consumption,
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "report_type": report_type,
            },
        }


class AuxCloudPowerSensor(CoordinatorEntity, SensorEntity):
    """Power consumption for a configurable date range."""

    def __init__(
        self,
        coordinator: AuxCloudPowerCoordinator,
        device_coordinator,
        device_id: str,
    ):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device_coordinator = device_coordinator
        self._device_id = device_id
        self.entity_description = POWER_SENSOR_DESCRIPTION
        self._attr_has_entity_name = True
        self._attr_unique_id = (
            f"{DOMAIN}_{self._device_id.lstrip('0')}_{POWER_CONSUMPTION_KEY}"
        )
        self.entity_id = f"sensor.{self._attr_unique_id}"

    @property
    def _device(self):
        """Return the live device record."""
        return self._device_coordinator.get_device_by_endpoint_id(self._device_id)

    @property
    def device_info(self):
        """Return device information."""
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
        """Return True when consumption data exists for the period."""
        if self._device is None:
            return False
        record = self._consumption_record
        return record is not None and record.get("total_kwh") is not None

    @property
    def _consumption_record(self) -> dict | None:
        """Return cached consumption for this device."""
        return self.coordinator.data.get("consumption", {}).get(self._device_id)

    @property
    def native_value(self):
        """Return total kWh for the configured period."""
        record = self._consumption_record
        if not record:
            return None
        return record.get("total_kwh")

    @property
    def extra_state_attributes(self):
        """Return the active period and breakdown metadata."""
        period = self.coordinator.data.get("period", {})
        record = self._consumption_record or {}
        return {
            "start_date": period.get("start_date"),
            "end_date": period.get("end_date"),
            "report_type": period.get("report_type"),
            "data_points": record.get("data_points", 0),
        }


@callback
def async_remove_stale_power_entities(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Remove legacy power consumption entities from older versions."""
    entity_registry = async_get_entity_registry(hass)
    removed = _remove_matching_entities(
        entity_registry,
        entry.entry_id,
        lambda unique_id: any(
            unique_id.endswith(suffix) for suffix in STALE_POWER_UNIQUE_ID_SUFFIXES
        ),
    )
    if removed:
        _LOGGER.info(
            "Removed %s stale AUX Cloud power consumption entit%s",
            removed,
            "y" if removed == 1 else "ies",
        )


def _remove_matching_entities(
    entity_registry: EntityRegistry,
    entry_id: str,
    matcher,
) -> int:
    """Remove entities belonging to an entry that match a predicate."""
    removed = 0
    for entity in list(entity_registry.entities.values()):
        if entity.config_entry_id != entry_id:
            continue
        if entity.platform != DOMAIN:
            continue
        unique_id = entity.unique_id or ""
        if not matcher(unique_id):
            continue
        entity_registry.async_remove(entity.entity_id)
        removed += 1
    return removed


async def async_update_power_period(
    hass: HomeAssistant,
    entry: ConfigEntry,
    start_date: date,
    end_date: date,
) -> None:
    """Persist a new consumption period and refresh sensors."""
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    new_options = {
        **entry.options,
        CONF_POWER_START_DATE: start_date.isoformat(),
        CONF_POWER_END_DATE: end_date.isoformat(),
    }
    hass.config_entries.async_update_entry(entry, options=new_options)

    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    power_coordinator = data.get("power_coordinator")
    if power_coordinator:
        await power_coordinator.async_request_refresh()
