"""AUX Cloud service handlers."""

from __future__ import annotations

from datetime import date

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .api.aux_cloud import (
    ReportType,
    parse_device_stats_total,
    parse_device_stats_values,
    resolve_stats_report_type,
)
from .const import DOMAIN

SERVICE_GET_POWER_CONSUMPTION = "get_power_consumption"

GET_POWER_CONSUMPTION_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("start_date"): cv.string,
        vol.Required("end_date"): cv.string,
        vol.Optional("report_type"): vol.In(["day", "month", "year"]),
    }
)


def _find_device_and_api(hass: HomeAssistant, device_id: str):
    """Look up a device record and API client from loaded config entries."""
    for data in hass.data.get(DOMAIN, {}).values():
        coordinator = data.get("coordinator")
        api = data.get("api")
        if not coordinator or not api:
            continue
        device = coordinator.get_device_by_endpoint_id(device_id)
        if device:
            return device, api
    return None, None


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register AUX Cloud services."""
    if hass.services.has_service(DOMAIN, SERVICE_GET_POWER_CONSUMPTION):
        return

    async def handle_get_power_consumption(call: ServiceCall):
        device_id = call.data["device_id"]
        start_date = date.fromisoformat(call.data["start_date"])
        end_date = date.fromisoformat(call.data["end_date"])
        report_type: ReportType | None = call.data.get("report_type")

        device, api = _find_device_and_api(hass, device_id)
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
            "values": values,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_POWER_CONSUMPTION,
        handle_get_power_consumption,
        schema=GET_POWER_CONSUMPTION_SCHEMA,
        supports_response=True,
    )
