"""Tests for period energy / power sensors used by HA Energy and Vulpo."""

from datetime import date
from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy, UnitOfPower

from custom_components.aux_cloud.power_consumption import (
    POWER_SENSOR_DESCRIPTION,
    AuxCloudPowerCoordinator,
    AuxCloudPowerSensor,
    get_meter_period,
    last_reset_for_period,
)
from custom_components.aux_cloud.sensor import (
    SENSORS,
    AuxCloudPowerWattsSensor,
    _estimate_power_watts_from_hour_kwh,
    _parse_live_power_watts,
)
from custom_components.aux_cloud.api.const import AC_TENELEC


def test_energy_sensor_is_total_with_last_reset():
    """Period meter uses TOTAL so intentional day/month/year resets are valid."""
    assert POWER_SENSOR_DESCRIPTION.device_class == SensorDeviceClass.ENERGY
    assert (
        POWER_SENSOR_DESCRIPTION.native_unit_of_measurement
        == UnitOfEnergy.KILO_WATT_HOUR
    )
    assert POWER_SENSOR_DESCRIPTION.state_class == SensorStateClass.TOTAL
    assert POWER_SENSOR_DESCRIPTION.name == "Energy Consumption"


def test_live_power_sensor_matches_vulpo_power_card_standard():
    """Vulpo Power Consumption cards filter device_class=power in Watts."""
    power = SENSORS[AC_TENELEC]["description"]
    assert power.device_class == SensorDeviceClass.POWER
    assert power.native_unit_of_measurement == UnitOfPower.WATT
    assert power.state_class == SensorStateClass.MEASUREMENT
    assert power.key == "power"
    assert power.name == "Power"


def test_tenelec_is_fetched_as_special_param():
    """Live power requires an explicit special-params fetch for tenelec."""
    from custom_components.aux_cloud.api.const import AuxProducts

    assert AC_TENELEC in AuxProducts.AC_SPECIAL_PARAMS
    assert AC_TENELEC in AuxProducts.AC_PARAMS


def test_parse_live_power_watts():
    """Live tenelec values are scaled like other AUX x10 params."""
    assert _parse_live_power_watts({"params": {"tenelec": 1250}}) == 125.0
    assert _parse_live_power_watts({"params": {"tenelec": "80"}}) == 8.0
    assert _parse_live_power_watts({"params": {"tenelec": 0}}) == 0.0
    assert _parse_live_power_watts({"params": {}}) is None
    assert _parse_live_power_watts({"params": {"tenelec": None}}) is None


def test_estimate_power_watts_from_hour_kwh():
    """One hourly kWh bucket equals average watts over that hour."""
    assert _estimate_power_watts_from_hour_kwh(1.8125) == 1812.5
    assert _estimate_power_watts_from_hour_kwh(0) == 0.0
    assert _estimate_power_watts_from_hour_kwh(None) is None


def test_get_meter_period_day_month_year():
    """Day / month / year cycles match last_reset boundaries."""
    today = date(2026, 7, 18)
    assert get_meter_period(today, period="day") == (today, today)
    assert get_meter_period(today, period="month") == (date(2026, 7, 1), today)
    assert get_meter_period(today, period="year") == (date(2026, 1, 1), today)
    # Default period is daily (entity always resets at midnight).
    assert get_meter_period(today) == (today, today)


def test_last_reset_for_period_is_local_midnight():
    """last_reset must be timezone-aware local midnight of the cycle start."""
    reset = last_reset_for_period(date(2026, 7, 18))
    assert reset.date() == date(2026, 7, 18)
    assert reset.hour == 0
    assert reset.minute == 0
    assert reset.tzinfo is not None


def test_energy_sensor_exposes_period_value_and_last_reset():
    """Energy sensor reports the cycle total and last_reset at meter_start."""
    coordinator = MagicMock()
    coordinator.data = {
        "consumption": {
            "device1": {
                "total_kwh": 4.2,
                "data_points": 10,
                "daily": {"2026-07-18": 4.2},
                "latest_hour": "2026-07-18_10:00:00",
            }
        },
        "period": {
            "start_date": "2026-07-18",
            "end_date": "2026-07-18",
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
    assert sensor.native_value == 4.2
    assert sensor.available is True
    assert sensor.extra_state_attributes["meter_start"] == "2026-07-18"
    assert sensor.extra_state_attributes["report_type"] == "day"
    assert sensor.extra_state_attributes["daily"] == {"2026-07-18": 4.2}
    assert sensor.last_reset is not None
    assert sensor.last_reset.date() == date(2026, 7, 18)


def test_stabilize_total_ignores_transient_drops_within_cycle():
    """Totals must not decrease within the same cycle; new cycle may reset."""
    coordinator = AuxCloudPowerCoordinator.__new__(AuxCloudPowerCoordinator)
    coordinator._last_period_totals = {}

    assert coordinator._stabilize_total("dev1", "2026-07-18", 10.0) == 10.0
    assert coordinator._stabilize_total("dev1", "2026-07-18", 8.0) == 10.0
    assert coordinator._stabilize_total("dev1", "2026-07-18", 12.0) == 12.0
    # Next day starts a new cycle — drop is allowed (last_reset moves).
    assert coordinator._stabilize_total("dev1", "2026-07-19", 3.0) == 3.0
    # Month cycle example: stay high within July, reset in August.
    assert coordinator._stabilize_total("dev2", "2026-07-01", 40.0) == 40.0
    assert coordinator._stabilize_total("dev2", "2026-07-01", 35.0) == 40.0
    assert coordinator._stabilize_total("dev2", "2026-08-01", 2.0) == 2.0


def test_power_watts_sensor_prefers_live_tenelec():
    """Live tenelec wins over the hourly estimate."""
    device_coordinator = MagicMock()
    device_coordinator.get_device_by_endpoint_id.return_value = {
        "endpointId": "device1",
        "friendlyName": "AC",
        "productId": "000000000000000000000000c0620000",
        "mac": "aa:bb:cc:dd:ee:ff",
        "params": {"tenelec": 1250},
    }
    power_coordinator = MagicMock()
    power_coordinator.data = {
        "consumption": {
            "device1": {
                "latest_hour_kwh": 1.5,
                "latest_hour": "2026-07-18_23:00:00",
            }
        }
    }

    sensor = AuxCloudPowerWattsSensor(
        device_coordinator, power_coordinator, "device1"
    )
    sensor._device = device_coordinator.get_device_by_endpoint_id("device1")
    assert sensor.native_value == 125.0
    assert sensor.extra_state_attributes["power_source"] == "live"


def test_power_watts_sensor_falls_back_to_latest_hour():
    """When live tenelec is missing, estimate Watts from the latest hour."""
    device_coordinator = MagicMock()
    device_coordinator.get_device_by_endpoint_id.return_value = {
        "endpointId": "device1",
        "friendlyName": "AC",
        "productId": "000000000000000000000000c0620000",
        "params": {"ac_temp": 250},
    }
    power_coordinator = MagicMock()
    power_coordinator.data = {
        "consumption": {
            "device1": {
                "latest_hour_kwh": 1.8125,
                "latest_hour": "2026-07-18_23:00:00",
            }
        }
    }

    sensor = AuxCloudPowerWattsSensor(
        device_coordinator, power_coordinator, "device1"
    )
    sensor._device = device_coordinator.get_device_by_endpoint_id("device1")
    assert sensor.native_value == 1812.5
    assert sensor.available is True
    assert sensor.extra_state_attributes["power_source"] == "estimated_from_hour"
