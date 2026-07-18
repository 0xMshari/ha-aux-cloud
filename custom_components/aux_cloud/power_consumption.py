"""Period energy consumption with TOTAL + last_reset for HA Energy."""

from __future__ import annotations

from datetime import date, datetime

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
from homeassistant.util import dt as dt_util

from .api.aux_cloud import (
    ReportType,
    aggregate_device_stats_by_day,
    parse_device_stats_latest,
    parse_device_stats_total,
    parse_device_stats_values,
)
from .api.const import AuxProducts
from .const import (
    CONF_ENERGY_PERIOD,
    DOMAIN,
    ENERGY_PERIOD_DAY,
    ENERGY_PERIOD_MONTH,
    ENERGY_PERIOD_YEAR,
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
    # TOTAL + last_reset: the period total intentionally resets (daily /
    # monthly / yearly). HA Energy uses last_reset as the meter cycle
    # boundary so the drop is not charted as negative consumption.
    state_class=SensorStateClass.TOTAL,
)


def get_energy_period(entry: ConfigEntry | None = None) -> ReportType:
    """Return configured energy period (day / month / year)."""
    if entry is None:
        return ENERGY_PERIOD_DAY
    value = entry.options.get(CONF_ENERGY_PERIOD) or entry.data.get(
        CONF_ENERGY_PERIOD, ENERGY_PERIOD_DAY
    )
    if value in (ENERGY_PERIOD_DAY, ENERGY_PERIOD_MONTH, ENERGY_PERIOD_YEAR):
        return value  # type: ignore[return-value]
    return ENERGY_PERIOD_DAY


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


def last_reset_for_period(start: date) -> datetime:
    """Return timezone-aware local midnight at the cycle start."""
    return dt_util.as_local(datetime.combine(start, datetime.min.time()))


class AuxCloudPowerCoordinator(DataUpdateCoordinator):
    """Fetch energy for the configured day/month/year cycle."""

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
        # endpoint_id -> (cycle_start_iso, total_kwh). Refuse mid-cycle drops
        # from sparse cloud under-counts; allow a drop when the cycle rolls.
        self._last_period_totals: dict[str, tuple[str, float]] = {}

    def _stabilize_total(
        self, endpoint_id: str, start_iso: str, total_kwh: float | None
    ) -> float | None:
        """Keep totals from decreasing within the same reset cycle."""
        if total_kwh is None:
            return None
        previous = self._last_period_totals.get(endpoint_id)
        if previous is not None and previous[0] == start_iso and total_kwh < previous[1]:
            _LOGGER.debug(
                "Ignoring transient energy drop for %s (%s → %s) within cycle %s",
                endpoint_id,
                previous[1],
                total_kwh,
                start_iso,
            )
            total_kwh = previous[1]
        self._last_period_totals[endpoint_id] = (start_iso, total_kwh)
        return total_kwh

    async def _async_update_data(self):
        """Fetch consumption for the active day/month/year cycle."""
        report_type = get_energy_period(self.entry)
        start_date, end_date = get_meter_period(period=report_type)
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
                # Force report_type to match the cycle so AUX buckets align
                # with last_reset (day / month / year).
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
                total_kwh = self._stabilize_total(endpoint_id, start_iso, total_kwh)
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
    """Energy for the active day / month / year cycle (kWh).

    Exposes device_class=energy with state_class=total and last_reset at the
    cycle boundary so HA Energy treats rollovers as meter resets.
    """

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
        """Return kWh for the active cycle (resets with last_reset)."""
        record = self._consumption_record
        if not record:
            return None
        return record.get("total_kwh")

    @property
    def last_reset(self) -> datetime | None:
        """Return the start of the active day/month/year cycle."""
        period = (self.coordinator.data or {}).get("period", {})
        start = period.get("start_date")
        if not start:
            return None
        try:
            return last_reset_for_period(date.fromisoformat(str(start)[:10]))
        except ValueError:
            return None

    @property
    def extra_state_attributes(self):
        """Return the cycle window and breakdown metadata."""
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
