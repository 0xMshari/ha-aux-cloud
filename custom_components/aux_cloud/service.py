"""AUX Cloud service handlers."""

from __future__ import annotations

from datetime import date, datetime

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .api.aux_cloud import (
    ReportType,
    aggregate_device_stats_by_day,
    parse_device_stats_total,
    parse_device_stats_values,
    resolve_stats_report_type,
)
from .const import DOMAIN

SERVICE_GET_POWER_CONSUMPTION = "get_power_consumption"


def _coerce_service_date(value) -> date:
    """Accept date, datetime, or ISO date/datetime strings from service calls."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise vol.Invalid(f"Invalid date: {value!r}")


GET_POWER_CONSUMPTION_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("start_date"): _coerce_service_date,
        vol.Required("end_date"): _coerce_service_date,
        vol.Optional("report_type"): vol.In(["day", "month", "year"]),
    }
)


def _find_device_and_api(hass: HomeAssistant, device_id: str):
    """Locate a device record and its API from coordinator data."""
    for entry_data in hass.data.get(DOMAIN, {}).values():
        coordinator = entry_data.get("coordinator")
        api = entry_data.get("api")
        if not coordinator or not api:
            continue
        device = coordinator.get_device_by_endpoint_id(device_id)
        if device is not None:
            return device, api, None
        # Also match unique_id style without leading zeros.
        devices = (coordinator.data or {}).get("devices", [])
        for candidate in devices:
            endpoint = candidate.get("endpointId", "")
            if endpoint == device_id or endpoint.lstrip("0") == device_id.lstrip("0"):
                return candidate, api, None
    return None, None, None


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register AUX Cloud services."""
    if hass.services.has_service(DOMAIN, SERVICE_GET_POWER_CONSUMPTION):
        return

    async def handle_get_power_consumption(call: ServiceCall):
        device_id = call.data["device_id"]
        start_date = call.data["start_date"]
        end_date = call.data["end_date"]
        report_type: ReportType | None = call.data.get("report_type")

        if end_date < start_date:
            start_date, end_date = end_date, start_date

        device, api, _entry = _find_device_and_api(hass, device_id)
        if not device or not api:
            raise ServiceValidationError(f"Device {device_id} not found")

        raw = await api.get_device_stats_for_period(
            device,
            start_date,
            end_date,
            report_type=report_type,
        )

        values = parse_device_stats_values(raw)
        used_report_type = report_type or resolve_stats_report_type(
            start_date, end_date
        )

        return {
            "total_kwh": parse_device_stats_total(raw),
            "report_type": used_report_type,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "data_points": len(values),
            "daily": aggregate_device_stats_by_day(values),
            "values": values,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_POWER_CONSUMPTION,
        handle_get_power_consumption,
        schema=GET_POWER_CONSUMPTION_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
