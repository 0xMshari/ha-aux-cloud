"""Day / month / year energy sensors with total_increasing for HA Energy.

Period meters (today / this month / this year) intentionally reset at cycle
boundaries. ``state_class=total_increasing`` tells HA Energy that a drop is a
counter reset (same pattern as Nous A1T “ENERGY Today”), not negative usage.
"""

from __future__ import annotations

import asyncio
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
    ReportType,
    aggregate_device_stats_by_day,
    parse_device_stats_latest,
    parse_device_stats_total,
    parse_device_stats_values,
)
from .api.const import AuxProducts
from .const import (
    DOMAIN,
    ENERGY_PERIOD_DAY,
    ENERGY_PERIOD_MONTH,
    ENERGY_PERIOD_YEAR,
    ENERGY_PERIODS,
    MANUFACTURER,
    POWER_UPDATE_INTERVAL,
    _LOGGER,
)

ENERGY_SENSOR_DESCRIPTIONS: dict[ReportType, SensorEntityDescription] = {
    ENERGY_PERIOD_DAY: SensorEntityDescription(
        key="energy_consumption_day",
        name="Energy Consumption Day",
        icon="mdi:lightning-bolt",
        translation_key="energy_consumption_day",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    ENERGY_PERIOD_MONTH: SensorEntityDescription(
        key="energy_consumption_month",
        name="Energy Consumption Month",
        icon="mdi:lightning-bolt",
        translation_key="energy_consumption_month",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    ENERGY_PERIOD_YEAR: SensorEntityDescription(
        key="energy_consumption_year",
        name="Energy Consumption Year",
        icon="mdi:lightning-bolt",
        translation_key="energy_consumption_year",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
}


def get_meter_period(
    today: date | None = None,
    *,
    period: ReportType = ENERGY_PERIOD_DAY,
) -> tuple[date, date]:
    """Return inclusive start/end dates for the active energy cycle."""
    today = today or date.today()
    if period == ENERGY_PERIOD_YEAR:
        return date(today.year, 1, 1), today
    if period == ENERGY_PERIOD_MONTH:
        return date(today.year, today.month, 1), today
    return today, today


class AuxCloudPowerCoordinator(DataUpdateCoordinator):
    """Fetch day, month, and year energy totals for each device."""

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
        # (endpoint_id, period) -> (cycle_start_iso, total_kwh)
        self._last_period_totals: dict[tuple[str, str], tuple[str, float]] = {}

    def _stabilize_total(
        self,
        endpoint_id: str,
        period: ReportType,
        start_iso: str,
        total_kwh: float | None,
    ) -> float | None:
        """Keep totals from decreasing within the same reset cycle."""
        if total_kwh is None:
            return None
        key = (endpoint_id, period)
        previous = self._last_period_totals.get(key)
        if previous is not None and previous[0] == start_iso and total_kwh < previous[1]:
            _LOGGER.debug(
                "Ignoring transient energy drop for %s/%s (%s → %s) within cycle %s",
                endpoint_id,
                period,
                previous[1],
                total_kwh,
                start_iso,
            )
            total_kwh = previous[1]
        self._last_period_totals[key] = (start_iso, total_kwh)
        return total_kwh

    async def _fetch_period_total(
        self, device: dict, period: ReportType
    ) -> dict:
        """Fetch one period total for a device."""
        start_date, end_date = get_meter_period(period=period)
        start_iso = start_date.isoformat()
        end_iso = end_date.isoformat()
        raw = await self.api.get_device_stats_for_period(
            device,
            start_date,
            end_date,
            report_type=period,
        )
        values = parse_device_stats_values(raw)
        daily = aggregate_device_stats_by_day(values)
        latest_when, latest_hour_kwh = parse_device_stats_latest(values)
        total_kwh = parse_device_stats_total(raw)
        if total_kwh is None and daily:
            total_kwh = sum(daily.values())
        total_kwh = self._stabilize_total(
            device["endpointId"], period, start_iso, total_kwh
        )
        return {
            "total_kwh": 0.0 if total_kwh is None else total_kwh,
            "values": values,
            "daily": daily,
            "latest_hour": latest_when,
            "latest_hour_kwh": latest_hour_kwh,
            "data_points": len(values),
            "start_date": start_iso,
            "end_date": end_iso,
            "report_type": period,
        }

    async def _async_update_data(self):
        """Fetch day / month / year consumption for every energy-capable device."""
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

            periods: dict[str, dict] = {}
            try:
                results = await asyncio.gather(
                    *(self._fetch_period_total(device, period) for period in ENERGY_PERIODS),
                    return_exceptions=True,
                )
                for period, result in zip(ENERGY_PERIODS, results):
                    if isinstance(result, Exception):
                        _LOGGER.warning(
                            "Energy %s query failed for %s: %s",
                            period,
                            endpoint_id,
                            result,
                        )
                        start_date, end_date = get_meter_period(period=period)
                        periods[period] = {
                            "total_kwh": None,
                            "values": [],
                            "daily": {},
                            "latest_hour": None,
                            "latest_hour_kwh": None,
                            "data_points": 0,
                            "start_date": start_date.isoformat(),
                            "end_date": end_date.isoformat(),
                            "report_type": period,
                            "error": str(result),
                        }
                    else:
                        periods[period] = result
            except Exception as exc:
                _LOGGER.warning(
                    "Energy consumption query failed for %s: %s",
                    endpoint_id,
                    exc,
                )
                for period in ENERGY_PERIODS:
                    start_date, end_date = get_meter_period(period=period)
                    periods[period] = {
                        "total_kwh": None,
                        "values": [],
                        "daily": {},
                        "latest_hour": None,
                        "latest_hour_kwh": None,
                        "data_points": 0,
                        "start_date": start_date.isoformat(),
                        "end_date": end_date.isoformat(),
                        "report_type": period,
                        "error": str(exc),
                    }

            day = periods.get(ENERGY_PERIOD_DAY) or {}
            consumption[endpoint_id] = {
                "periods": periods,
                # Shared fallback for the Watts sensor (latest hour of today).
                "latest_hour": day.get("latest_hour"),
                "latest_hour_kwh": day.get("latest_hour_kwh"),
            }

        return {"consumption": consumption}


class AuxCloudPowerSensor(CoordinatorEntity, SensorEntity):
    """One period energy meter (day / month / year) in kWh."""

    def __init__(
        self,
        coordinator: AuxCloudPowerCoordinator,
        device_coordinator,
        device_id: str,
        period: ReportType,
    ):
        """Initialize the sensor for a single period."""
        super().__init__(coordinator)
        self._device_coordinator = device_coordinator
        self._device_id = device_id
        self._period = period
        self.entity_description = ENERGY_SENSOR_DESCRIPTIONS[period]
        self._attr_has_entity_name = True
        self._attr_unique_id = (
            f"{DOMAIN}_{self._device_id.lstrip('0')}_{self.entity_description.key}"
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
        record = self._period_record
        return record is not None and record.get("total_kwh") is not None

    @property
    def _period_record(self) -> dict | None:
        """Return cached consumption for this device + period."""
        if not self.coordinator.data:
            return None
        device_data = self.coordinator.data.get("consumption", {}).get(self._device_id)
        if not device_data:
            return None
        return device_data.get("periods", {}).get(self._period)

    @property
    def native_value(self):
        """Return kWh for this period (resets; total_increasing handles it)."""
        record = self._period_record
        if not record:
            return None
        return record.get("total_kwh")

    @property
    def extra_state_attributes(self):
        """Return the cycle window and breakdown metadata."""
        record = self._period_record or {}
        return {
            "meter_start": record.get("start_date"),
            "meter_end": record.get("end_date"),
            "report_type": record.get("report_type") or self._period,
            "data_points": record.get("data_points", 0),
            "daily": record.get("daily") or {},
            "latest_hour": record.get("latest_hour"),
        }


def create_energy_sensors(
    coordinator: AuxCloudPowerCoordinator,
    device_coordinator,
    device_id: str,
) -> list[AuxCloudPowerSensor]:
    """Create day / month / year energy sensors for one device."""
    return [
        AuxCloudPowerSensor(coordinator, device_coordinator, device_id, period)
        for period in ENERGY_PERIODS
    ]
