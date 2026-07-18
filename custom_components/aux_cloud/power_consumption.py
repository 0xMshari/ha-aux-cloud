"""Cumulative year-to-date energy consumption coordinator and sensor."""

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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .api.aux_cloud import (
    aggregate_device_stats_by_day,
    parse_device_stats_latest,
    parse_device_stats_total,
    parse_device_stats_values,
    resolve_stats_report_type,
)
from .api.const import AuxProducts
from .const import (
    DOMAIN,
    MANUFACTURER,
    POWER_CONSUMPTION_KEY,
    POWER_UPDATE_INTERVAL,
    _LOGGER,
)

POWER_SENSOR_DESCRIPTION = SensorEntityDescription(
    key=POWER_CONSUMPTION_KEY,
    name="Energy Consumption",
    icon="mdi:lightning-bolt",
    translation_key="power_consumption",
    device_class=SensorDeviceClass.ENERGY,
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    # Cumulative YTD meter: value only grows within the year so HA Energy /
    # Vulpo can take day-over-day deltas without inventing negatives at
    # midnight (the failure mode of a resetting "today only" period total).
    state_class=SensorStateClass.TOTAL_INCREASING,
)


def get_meter_period(today: date | None = None) -> tuple[date, date]:
    """Return Jan 1 of the current year through today (inclusive)."""
    today = today or date.today()
    return date(today.year, 1, 1), today


class AuxCloudPowerCoordinator(DataUpdateCoordinator):
    """Fetch cumulative year-to-date energy for supported devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        api,
        device_coordinator,
        entry: ConfigEntry,
    ):
        """Initialize the energy consumption coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="AUX Cloud Energy Consumption",
            update_interval=POWER_UPDATE_INTERVAL,
        )
        self.api = api
        self.device_coordinator = device_coordinator
        self.entry = entry
        # endpoint_id -> (start_iso, end_iso, total_kwh). Ignore transient
        # cloud under-counts that would drop total_increasing mid-window.
        self._last_period_totals: dict[str, tuple[str, str, float]] = {}

    def _stabilize_total(
        self, endpoint_id: str, start_iso: str, end_iso: str, total_kwh: float | None
    ) -> float | None:
        """Keep period totals from decreasing while the date range is unchanged."""
        if total_kwh is None:
            return None
        previous = self._last_period_totals.get(endpoint_id)
        if (
            previous is not None
            and previous[0] == start_iso
            and previous[1] == end_iso
            and total_kwh < previous[2]
        ):
            _LOGGER.debug(
                "Ignoring transient energy drop for %s (%s → %s) within %s..%s",
                endpoint_id,
                previous[2],
                total_kwh,
                start_iso,
                end_iso,
            )
            total_kwh = previous[2]
        self._last_period_totals[endpoint_id] = (start_iso, end_iso, total_kwh)
        return total_kwh

    async def _async_update_data(self):
        """Fetch YTD consumption totals for all supported devices."""
        start_date, end_date = get_meter_period()
        report_type = resolve_stats_report_type(start_date, end_date)
        start_iso = start_date.isoformat()
        end_iso = end_date.isoformat()
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
                daily = aggregate_device_stats_by_day(values)
                latest_when, latest_hour_kwh = parse_device_stats_latest(values)
                total_kwh = parse_device_stats_total(raw)
                if total_kwh is None and daily:
                    total_kwh = sum(daily.values())
                total_kwh = self._stabilize_total(
                    endpoint_id, start_iso, end_iso, total_kwh
                )
                consumption[endpoint_id] = {
                    "total_kwh": 0.0 if total_kwh is None else total_kwh,
                    "values": values,
                    "daily": daily,
                    "latest_hour": latest_when,
                    "latest_hour_kwh": latest_hour_kwh,
                    "data_points": len(values),
                }
            except Exception as exc:
                _LOGGER.warning(
                    "Energy consumption query failed for %s: %s",
                    endpoint_id,
                    exc,
                )
                consumption[endpoint_id] = {
                    "total_kwh": None,
                    "values": [],
                    "daily": {},
                    "latest_hour": None,
                    "latest_hour_kwh": None,
                    "data_points": 0,
                    "error": str(exc),
                }

        return {
            "consumption": consumption,
            "period": {
                "start_date": start_iso,
                "end_date": end_iso,
                "report_type": report_type,
            },
        }


class AuxCloudPowerSensor(CoordinatorEntity, SensorEntity):
    """Cumulative year-to-date energy consumption (kWh)."""

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
        info = DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=device.get("friendlyName", "AUX"),
            manufacturer=MANUFACTURER,
            model=AuxProducts.get_device_name(device.get("productId")),
        )
        if device.get("mac"):
            info["connections"] = {(CONNECTION_NETWORK_MAC, device["mac"])}
        return info

    @property
    def available(self) -> bool:
        """Return True when the device is present and a numeric total exists."""
        if self._device is None:
            return False
        if not self.coordinator.last_update_success:
            return False
        record = self._consumption_record
        return record is not None and record.get("total_kwh") is not None

    @property
    def _consumption_record(self) -> dict | None:
        """Return cached consumption for this device."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("consumption", {}).get(self._device_id)

    @property
    def native_value(self):
        """Return cumulative YTD kWh."""
        record = self._consumption_record
        if not record:
            return None
        return record.get("total_kwh")

    @property
    def extra_state_attributes(self):
        """Return the meter window and breakdown metadata."""
        period = (self.coordinator.data or {}).get("period", {})
        record = self._consumption_record or {}
        return {
            "meter_start": period.get("start_date"),
            "meter_end": period.get("end_date"),
            "report_type": period.get("report_type"),
            "data_points": record.get("data_points", 0),
            "daily": record.get("daily") or {},
            "latest_hour": record.get("latest_hour"),
        }
