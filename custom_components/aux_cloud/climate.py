"""Climate platform for AUX Cloud integration."""

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    ClimateEntityDescription,
    HVACMode,
    HVACAction,
)
from homeassistant.components.climate.const import (
    FAN_AUTO,
    FAN_LOW,
    FAN_MEDIUM,
    FAN_HIGH,
    SWING_OFF,
    SWING_ON,
    SWING_HORIZONTAL_OFF,
    SWING_HORIZONTAL_ON,
    PRESET_ECO,
    PRESET_NONE,
    PRESET_SLEEP,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api.const import (
    AC_FAN_SPEED,
    AUX_MODE,
    AC_SWING_HORIZONTAL,
    AC_SWING_HORIZONTAL_OFF,
    AC_SWING_HORIZONTAL_ON,
    AC_SWING_VERTICAL,
    AC_SWING_VERTICAL_OFF,
    AC_SWING_VERTICAL_ON,
    AC_TEMPERATURE_AMBIENT,
    AC_TEMPERATURE_TARGET,
    HP_MODE_COOLING,
    HP_MODE_HEATING,
    AuxProducts,
    AUX_ECOMODE,
    AUX_ECOMODE_OFF,
    AUX_ECOMODE_ON,
    HP_HEATER_TEMPERATURE_TARGET,
    HP_HEATER_POWER,
    HP_HEATER_POWER_OFF,
    HP_HEATER_POWER_ON,
    AC_POWER,
    AC_POWER_OFF,
    AC_POWER_ON,
    AC_SLEEP,
    AC_SLEEP_OFF,
    AC_SLEEP_ON,
    ACFanSpeed,
)
from .const import (
    DOMAIN,
    FAN_MODE_HA_TO_AUX,
    FAN_MODE_AUX_TO_HA,
    MODE_MAP_AUX_AC_TO_HA,
    MODE_MAP_HA_TO_AUX,
    _LOGGER,
)
from .util import BaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the AUX climate platform."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities = []

    # Create climate entities for each device
    for device in coordinator.data["devices"]:
        if device.get("productId") in AuxProducts.DeviceType.AC_GENERIC:
            entities.append(
                AuxACClimateEntity(
                    coordinator,
                    device["endpointId"],
                    ClimateEntityDescription(
                        key="ac",
                        name="Air Conditioner",
                        translation_key="aux_ac",
                        icon="mdi:air-conditioner",
                    ),
                )
            )
        elif device.get("productId") in AuxProducts.DeviceType.HEAT_PUMP:
            entities.append(
                AuxHeatPumpClimateEntity(
                    coordinator,
                    device["endpointId"],
                    ClimateEntityDescription(
                        key="heat_pump_central_heating",
                        name="Central Heating",
                        translation_key="aux_heater",
                        icon="mdi:hvac",
                    ),
                )
            )

    if entities:
        async_add_entities(entities, True)
    else:
        _LOGGER.info("No AUX climate devices added")


# pylint: disable=abstract-method
class AuxHeatPumpClimateEntity(BaseEntity, CoordinatorEntity, ClimateEntity):
    """AUX Cloud heat pump climate entity."""

    def __init__(
        self, coordinator, device_id, entity_description: ClimateEntityDescription
    ):
        """Initialize the heat pump climate entity."""
        super().__init__(coordinator, device_id, entity_description)
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.PRESET_MODE
        )
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]
        self._attr_min_temp = 0  # Minimum temperature in Celsius
        self._attr_max_temp = 64  # Maximum temperature in Celsius
        self._attr_target_temperature_step = 1
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_preset_modes = [PRESET_NONE, PRESET_ECO]
        self.entity_id = f"climate.{self._attr_unique_id}"

    @property
    def preset_mode(self):
        """Return the current preset mode."""
        if self._get_device_params().get("ecomode", False):
            return PRESET_ECO
        return PRESET_NONE

    @property
    def target_temperature(self):
        """Return the target water temperature."""
        return (
            self._get_device_params().get(HP_HEATER_TEMPERATURE_TARGET, None) / 10
            if HP_HEATER_TEMPERATURE_TARGET in self._get_device_params()
            else None
        )

    @property
    def hvac_mode(self):
        """Return the current operation mode."""
        if not self._get_device_params().get(HP_HEATER_POWER, False):
            return HVACMode.OFF
        return HVACMode.HEAT

    async def async_set_hvac_mode(self, hvac_mode):
        """Set new operation mode."""
        if hvac_mode == HVACMode.OFF:
            params = HP_HEATER_POWER_OFF
        elif hvac_mode == HVACMode.HEAT:
            params = {**HP_MODE_HEATING, **HP_HEATER_POWER_ON}
        elif hvac_mode == HVACMode.COOL:
            params = {**HP_MODE_COOLING, **HP_HEATER_POWER_ON}
        else:
            return

        await self._set_device_params(params)

    @property
    def hvac_action(self):
        """Return the current HVAC action."""
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if self.hvac_mode == HVACMode.HEAT:
            return HVACAction.HEATING
        if self.hvac_mode == HVACMode.COOL:
            return HVACAction.COOLING
        if self.hvac_mode == HVACMode.DRY:
            return HVACAction.DRYING
        if self.hvac_mode == HVACMode.FAN_ONLY:
            return HVACAction.FAN

        return HVACAction.IDLE

    async def async_turn_on(self):
        """Turn the heat pump on."""
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self):
        """Turn the heat pump off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_preset_mode(self, preset_mode: str):
        """Set the preset mode."""
        if preset_mode == PRESET_ECO:
            await self._set_device_params(AUX_ECOMODE_ON)
        else:
            await self._set_device_params(AUX_ECOMODE_OFF)

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        if ATTR_TEMPERATURE not in kwargs:
            return

        temperature = kwargs[ATTR_TEMPERATURE]
        if temperature < self._attr_min_temp:
            temperature = self._attr_min_temp
        elif temperature > self._attr_max_temp:
            temperature = self._attr_max_temp

        await self._set_device_params(
            {HP_HEATER_TEMPERATURE_TARGET: int(temperature * 10)}
        )

    async def async_set_fan_mode(self, fan_mode):
        """Set new fan mode."""
        _LOGGER.warning("Fan mode setting is not supported for heat pump devices")
        return


class AuxACClimateEntity(BaseEntity, CoordinatorEntity, ClimateEntity):
    """AUX Cloud climate entity."""

    def __init__(
        self, coordinator, device_id, entity_description: ClimateEntityDescription
    ):
        """Initialize the climate entity."""
        super().__init__(coordinator, device_id, entity_description)
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.SWING_MODE
            | ClimateEntityFeature.SWING_HORIZONTAL_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        self._attr_hvac_modes = [HVACMode.OFF, *MODE_MAP_AUX_AC_TO_HA.values()]
        # Standard modes first — HomeKit maps auto/low/medium/high reliably.
        self._attr_fan_modes = [
            FAN_AUTO,
            FAN_LOW,
            FAN_MEDIUM,
            FAN_HIGH,
            "turbo",
            "silent",
        ]
        self._attr_preset_modes = [PRESET_NONE, PRESET_ECO, PRESET_SLEEP]
        self._attr_swing_modes = [SWING_OFF, SWING_ON]
        self._attr_swing_horizontal_modes = [
            SWING_HORIZONTAL_OFF,
            SWING_HORIZONTAL_ON,
        ]
        self._attr_min_temp = 16
        self._attr_max_temp = 32
        self._attr_target_temperature_step = 0.5
        self.entity_id = f"climate.{self._attr_unique_id}"

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return (
            self._get_device_params().get(AC_TEMPERATURE_AMBIENT, None) / 10
            if AC_TEMPERATURE_AMBIENT in self._get_device_params()
            else None
        )

    @property
    def target_temperature(self):
        """Return the target temperature."""
        return (
            self._get_device_params().get(AC_TEMPERATURE_TARGET, None) / 10
            if AC_TEMPERATURE_TARGET in self._get_device_params()
            else None
        )

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        if ATTR_TEMPERATURE not in kwargs:
            return

        temperature = kwargs[ATTR_TEMPERATURE]
        if temperature < self._attr_min_temp:
            temperature = self._attr_min_temp
        elif temperature > self._attr_max_temp:
            temperature = self._attr_max_temp

        await self._set_device_params({AC_TEMPERATURE_TARGET: int(temperature * 10)})

    @property
    def hvac_mode(self):
        """Return the current operation mode."""
        mode = self._get_device_params().get(AUX_MODE, None)
        if mode is None or not self._get_device_params().get(AC_POWER, False):
            return HVACMode.OFF
        return MODE_MAP_AUX_AC_TO_HA.get(mode, HVACMode.OFF)

    async def async_set_hvac_mode(self, hvac_mode):
        """Set a new operation mode."""
        if hvac_mode == HVACMode.OFF:
            params = AC_POWER_OFF
        else:
            aux_mode = MODE_MAP_HA_TO_AUX.get(hvac_mode)
            if aux_mode is None:
                return
            params = {**AC_POWER_ON, AUX_MODE: aux_mode}

        await self._set_device_params(params)

    @property
    def hvac_action(self):
        """Return the current HVAC action."""
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if self.hvac_mode == HVACMode.HEAT:
            return HVACAction.HEATING
        if self.hvac_mode == HVACMode.COOL:
            return HVACAction.COOLING
        if self.hvac_mode == HVACMode.DRY:
            return HVACAction.DRYING
        if self.hvac_mode == HVACMode.FAN_ONLY:
            return HVACAction.FAN

        return HVACAction.IDLE

    @property
    def fan_mode(self):
        """Return the fan mode."""
        return FAN_MODE_AUX_TO_HA.get(
            self._get_device_params().get(ACFanSpeed.PARAM_NAME), FAN_AUTO
        )

    async def async_set_fan_mode(self, fan_mode):
        """Async set new fan mode."""
        if fan_mode is None:
            return

        aux_speed = FAN_MODE_HA_TO_AUX.get(fan_mode)
        if aux_speed is None:
            _LOGGER.warning("Unsupported fan mode %s for %s", fan_mode, self._device_id)
            return

        await self._set_device_params({AC_FAN_SPEED: aux_speed})

    @property
    def preset_mode(self):
        """Return the current preset mode."""
        params = self._get_device_params()
        if params.get(AC_SLEEP):
            return PRESET_SLEEP
        if params.get(AUX_ECOMODE):
            return PRESET_ECO
        return PRESET_NONE

    async def async_set_preset_mode(self, preset_mode: str):
        """Set the preset mode."""
        if preset_mode == PRESET_SLEEP:
            await self._set_device_params({**AC_SLEEP_ON, **AUX_ECOMODE_OFF})
        elif preset_mode == PRESET_ECO:
            await self._set_device_params({**AUX_ECOMODE_ON, **AC_SLEEP_OFF})
        else:
            await self._set_device_params({**AUX_ECOMODE_OFF, **AC_SLEEP_OFF})

    @property
    def swing_mode(self):
        """Return vertical swing state."""
        if self._get_device_params().get(AC_SWING_VERTICAL, 0):
            return SWING_ON
        return SWING_OFF

    @property
    def swing_horizontal_mode(self):
        """Return horizontal swing state."""
        if self._get_device_params().get(AC_SWING_HORIZONTAL, 0):
            return SWING_HORIZONTAL_ON
        return SWING_HORIZONTAL_OFF

    async def async_set_swing_mode(self, swing_mode):
        """Set vertical swing."""
        if swing_mode == SWING_ON:
            await self._set_device_params(AC_SWING_VERTICAL_ON)
        else:
            await self._set_device_params(AC_SWING_VERTICAL_OFF)

    async def async_set_swing_horizontal_mode(self, swing_mode):
        """Set horizontal swing."""
        if swing_mode == SWING_HORIZONTAL_ON:
            await self._set_device_params(AC_SWING_HORIZONTAL_ON)
        else:
            await self._set_device_params(AC_SWING_HORIZONTAL_OFF)

    async def async_turn_on(self):
        """Turn the AC on, restoring the last HVAC mode when possible."""
        mode = self._get_device_params().get(AUX_MODE)
        ha_mode = MODE_MAP_AUX_AC_TO_HA.get(mode)
        if ha_mode and ha_mode != HVACMode.OFF:
            await self.async_set_hvac_mode(ha_mode)
        else:
            await self.async_set_hvac_mode(HVACMode.COOL)

    async def async_turn_off(self):
        """Async turn the entity off."""
        await self._set_device_params(AC_POWER_OFF)
