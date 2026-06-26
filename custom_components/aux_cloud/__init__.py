"""Aux Cloud integration for Home Assistant."""

import asyncio
from datetime import timedelta

import voluptuous as vol
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_REGION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.aux_cloud import AuxCloudAPI, ReportType, parse_device_stats_total
from .api.const import AuxProducts
from .const import (
    _LOGGER,
    DOMAIN,
    DATA_AUX_CLOUD_CONFIG,
    PLATFORMS,
    CONF_SELECTED_DEVICES,
)

MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=60)
STATS_UPDATE_INTERVAL = timedelta(minutes=15)
ENERGY_STATS_REPORT_TYPES: tuple[ReportType, ...] = ("day", "month", "year")

# Schema to include email and password (device selection is handled in config flow)
CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_EMAIL): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """
    AUX Cloud setup for configuration.yaml import.
    This is mainly kept for backward compatibility.
    UI configuration is recommended for better security.
    """
    if DOMAIN not in config:
        return True

    hass.data[DATA_AUX_CLOUD_CONFIG] = config.get(DOMAIN, {})

    if (
        not hass.config_entries.async_entries(DOMAIN)
        and hass.data[DATA_AUX_CLOUD_CONFIG]
    ):
        # Import from configuration.yaml if no config entry exists
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data=config[DOMAIN]
            )
        )

        # Log a message about UI configuration being preferred
        _LOGGER.info(
            "AUX Cloud configured via configuration.yaml. For better security, "
            "it is recommended to configure this integration through the UI where "
            "credentials are stored encrypted."
        )

    return True


class AuxCloudCoordinator(DataUpdateCoordinator):
    """DataUpdateCoordinator for AUX Cloud."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: AuxCloudAPI,
        email: str,
        password: str,
        selected_device_ids: list,
    ):
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="AUX Cloud Coordinator",
            update_interval=MIN_TIME_BETWEEN_UPDATES,
        )
        self.api = api
        self.email = email
        self.password = password
        self.selected_device_ids = selected_device_ids
        self.devices = []

    def get_device_by_endpoint_id(self, endpoint_id: str):
        """Get a device by its endpoint ID."""
        return next(
            (
                device
                for device in self.data.get("devices", [])
                if device.get("endpointId") == endpoint_id
            ),
            None,
        )

    async def _async_update_data(self):
        """Fetch data from AUX Cloud."""
        _LOGGER.debug("Updating AUX Cloud data...")

        try:
            if not self.api.is_logged_in():
                # Attempt to log in
                _LOGGER.debug("Logging into AUX Cloud API...")
                login_success = await self.api.login(self.email, self.password)
                if not login_success:
                    raise UpdateFailed("Login to AUX Cloud API failed")

            if self.api.families is None:
                _LOGGER.debug("Fetching families from AUX Cloud API...")
                await self.api.get_families()

            # Create a single list of tasks for fetching devices (shared and non-shared)
            device_tasks = []

            for family_id in self.api.families:
                device_tasks.append(
                    self.api.get_devices(
                        family_id,
                        shared=False,
                        selected_devices=self.selected_device_ids,
                    )
                )
                device_tasks.append(
                    self.api.get_devices(
                        family_id,
                        shared=True,
                        selected_devices=self.selected_device_ids,
                    )
                )

            # Run all tasks concurrently
            devices_results = await asyncio.gather(
                *device_tasks, return_exceptions=True
            )

            # Process results and handle exceptions
            all_devices = []

            for result in devices_results:
                for device in result:
                    if isinstance(device, Exception):
                        continue
                    if (
                        device["endpointId"] in self.selected_device_ids
                        or not self.selected_device_ids
                    ):
                        all_devices.append(device)

            self.devices = all_devices
            _LOGGER.debug("Fetched AUX Cloud data: %s devices", len(self.devices))

            self.async_set_updated_data({"devices": self.devices})

            return {"devices": self.devices}

        except Exception as e:
            raise UpdateFailed(f"Error updating AUX Cloud data: {e}") from e


class AuxCloudStatsCoordinator(DataUpdateCoordinator):
    """Fetch historical energy statistics from AUX Cloud."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: AuxCloudAPI,
        device_coordinator: AuxCloudCoordinator,
    ):
        """Initialize the stats coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="AUX Cloud Stats Coordinator",
            update_interval=STATS_UPDATE_INTERVAL,
        )
        self.api = api
        self.device_coordinator = device_coordinator

    async def _async_update_data(self):
        """Fetch energy statistics for supported devices."""
        devices = self.device_coordinator.data.get("devices", []) if self.device_coordinator.data else []
        stats: dict[str, dict[str, dict | None]] = {}

        for device in devices:
            product_id = device.get("productId")
            endpoint_id = device.get("endpointId")
            if not product_id or not endpoint_id:
                continue
            if not AuxProducts.supports_energy_stats(product_id):
                continue
            if not device.get("familyid"):
                _LOGGER.debug(
                    "Skipping energy stats for %s: missing familyid",
                    endpoint_id,
                )
                continue

            stats[endpoint_id] = {}
            for report_type in ENERGY_STATS_REPORT_TYPES:
                try:
                    raw = await self.api.get_device_stats(device, report_type)
                    total_kwh = parse_device_stats_total(raw)
                    data_points = _count_stats_data_points(raw)
                    if total_kwh is None:
                        _LOGGER.warning(
                            "Energy stats for %s (%s) returned no usable data. "
                            "Check region setting and API response: %s",
                            endpoint_id,
                            report_type,
                            raw,
                        )
                    stats[endpoint_id][report_type] = {
                        "total_kwh": total_kwh,
                        "data_points": data_points,
                    }
                except Exception as exc:
                    _LOGGER.warning(
                        "Energy stats request failed for %s (%s): %s",
                        endpoint_id,
                        report_type,
                        exc,
                    )
                    stats[endpoint_id][report_type] = None

        return {"stats": stats}


def _count_stats_data_points(response: dict) -> int:
    """Count rows returned in a stats response."""
    if not isinstance(response, dict):
        return 0

    table = response.get("table")
    if isinstance(table, list) and table and isinstance(table[0], dict):
        values = table[0].get("values")
        if isinstance(values, list):
            return len(values)
        cnt = table[0].get("cnt")
        if isinstance(cnt, int):
            return cnt

    devices = response.get("device")
    if not isinstance(devices, list) or not devices:
        return 0

    device_data = devices[0]
    if not isinstance(device_data, dict):
        return 0

    values = device_data.get("values")
    if isinstance(values, list):
        return len(values)

    data = device_data.get("data")
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list):
                return len(value)
    return 0


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AUX Cloud from a config entry."""
    region = entry.data.get(CONF_REGION, "eu")
    api = AuxCloudAPI(region=region)
    email = entry.data.get(CONF_EMAIL)
    password = entry.data.get(CONF_PASSWORD)
    selected_device_ids = entry.data.get(CONF_SELECTED_DEVICES, [])

    if not email or not password:
        _LOGGER.error("Missing required credentials for AUX Cloud")
        return False

    coordinator = AuxCloudCoordinator(hass, api, email, password, selected_device_ids)

    # Attempt to log in
    try:
        login_success = await api.login(email, password)
        if not login_success:
            _LOGGER.error("Login to AUX Cloud API failed")
            return False
    except Exception as e:
        _LOGGER.error("Exception during login: %s", e)
        return False

    # Perform an initial update
    await coordinator.async_config_entry_first_refresh()

    stats_coordinator = AuxCloudStatsCoordinator(hass, api, coordinator)
    await stats_coordinator.async_config_entry_first_refresh()

    # Store the coordinator for platform use
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "stats_coordinator": stats_coordinator,
        "api": api,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the config entry and platforms."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.pop(DOMAIN)
    return unload_ok
