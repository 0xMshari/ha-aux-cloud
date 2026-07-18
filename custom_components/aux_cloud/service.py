"""AUX Cloud service handlers."""

from __future__ import annotations

from datetime import date, datetime

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
from .power_consumption import async_update_power_period

SERVICE_GET_POWER_CONSUMPTION = "get_power_consumption"
SERVICE_SET_POWER_CONSUMPTION_PERIOD = "set_power_consumption_period"


def _coerce_service_date(value) -> date:
    """Accept date, datetime, or ISO date/datetime strings from service calls."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError as err:
            raise vol.Invalid(f"Invalid date: {value}") from err
    raise vol.Invalid(f"Expected a date, got {type(value).__name__}")


DATE_FIELD = _coerce_service_date

GET_POWER_CONSUMPTION_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("start_date"): DATE_FIELD,
        vol.Required("end_date"): DATE_FIELD,
        vol.Optional("report_type"): vol.In(["day", "month", "year"]),
    }
)

SET_POWER_CONSUMPTION_PERIOD_SCHEMA = vol.Schema(
    {
        vol.Required("start_date"): DATE_FIELD,
        vol.Required("end_date"): DATE_FIELD,
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
            return device, api, data.get("config_entry")
    return None, None, None


def _get_config_entry(hass: HomeAssistant):
    """Return the active config entry when only one is loaded."""
    entries = hass.data.get(DOMAIN, {})
    if len(entries) != 1:
        return None
    return next(iter(entries.values())).get("config_entry")


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
            "values": values,
        }

    async def handle_set_power_consumption_period(call: ServiceCall):
        start_date = call.data["start_date"]
        end_date = call.data["end_date"]
        entry = _get_config_entry(hass)
        if entry is None:
            raise ServiceValidationError(
                "Could not determine config entry for power consumption period"
            )

        await async_update_power_period(hass, entry, start_date, end_date)
        if end_date < start_date:
            start_date, end_date = end_date, start_date

        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "report_type": resolve_stats_report_type(start_date, end_date),
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_POWER_CONSUMPTION,
        handle_get_power_consumption,
        schema=GET_POWER_CONSUMPTION_SCHEMA,
        supports_response=True,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_POWER_CONSUMPTION_PERIOD,
        handle_set_power_consumption_period,
        schema=SET_POWER_CONSUMPTION_PERIOD_SCHEMA,
        supports_response=True,
    )
