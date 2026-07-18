"""Tests for energy consumption period helpers and sensor metadata."""

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy

from custom_components.aux_cloud.power_consumption import (
    POWER_SENSOR_DESCRIPTION,
    AuxCloudPowerSensor,
    _parse_config_date,
    get_power_period,
)
from custom_components.aux_cloud.service import _coerce_service_date


def test_power_sensor_matches_energy_dashboard_standard():
    """Energy sensors must expose HA Energy-compatible attributes."""
    assert POWER_SENSOR_DESCRIPTION.device_class == SensorDeviceClass.ENERGY
    assert (
        POWER_SENSOR_DESCRIPTION.native_unit_of_measurement
        == UnitOfEnergy.KILO_WATT_HOUR
    )
    assert POWER_SENSOR_DESCRIPTION.state_class == SensorStateClass.TOTAL
    assert POWER_SENSOR_DESCRIPTION.name == "Energy Consumption"


def test_parse_config_date_accepts_common_shapes():
    """Config dates may arrive as ISO strings, dates, or datetimes."""
    assert _parse_config_date("2026-06-01") == date(2026, 6, 1)
    assert _parse_config_date("2026-06-01T12:30:00") == date(2026, 6, 1)
    assert _parse_config_date(date(2026, 6, 1)) == date(2026, 6, 1)
    assert _parse_config_date(datetime(2026, 6, 1, 8, 0)) == date(2026, 6, 1)
    assert _parse_config_date(None) is None
    assert _parse_config_date("not-a-date") is None


def test_coerce_service_date_accepts_any_common_input():
    """Service calls should accept date objects and ISO-like strings."""
    assert _coerce_service_date("2026-06-01") == date(2026, 6, 1)
    assert _coerce_service_date("2026-06-01T23:59:59") == date(2026, 6, 1)
    assert _coerce_service_date(date(2026, 6, 2)) == date(2026, 6, 2)
    assert _coerce_service_date(datetime(2026, 6, 3, 1, 2)) == date(2026, 6, 3)


def test_get_power_period_uses_configured_dates_and_swaps_order():
    """Configured periods should accept any inclusive date range."""
    entry = SimpleNamespace(
        options={"power_start_date": "2026-06-26", "power_end_date": "2026-06-01"},
        data={},
    )
    assert get_power_period(entry) == (date(2026, 6, 1), date(2026, 6, 26))


def test_get_power_period_defaults_to_today():
    """Unset periods should fall back to today."""
    entry = SimpleNamespace(options={}, data={})
    today = date.today()
    assert get_power_period(entry) == (today, today)


def test_sensor_last_reset_follows_period_start():
    """last_reset must track the configured period for Energy statistics."""
    coordinator = MagicMock()
    coordinator.data = {
        "consumption": {"device1": {"total_kwh": 1.5, "data_points": 1}},
        "period": {
            "start_date": "2026-06-01",
            "end_date": "2026-06-26",
            "report_type": "day",
        },
    }
    coordinator.last_update_success = True
    device_coordinator = MagicMock()
    device_coordinator.get_device_by_endpoint_id.return_value = {
        "endpointId": "device1",
        "friendlyName": "Heat Pump",
        "productId": "000000000000000000000000c3aa0000",
    }

    sensor = AuxCloudPowerSensor(coordinator, device_coordinator, "device1")
    assert sensor.native_value == 1.5
    assert sensor.available is True
    assert sensor.last_reset.date() == date(2026, 6, 1)

    coordinator.data["period"]["start_date"] = "2026-07-01"
    sensor._sync_last_reset()
    assert sensor.last_reset.date() == date(2026, 7, 1)
