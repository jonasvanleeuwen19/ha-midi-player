from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector

from .const import (
    ACTION_PLAY_CHUNK,
    ACTION_STOP,
    CONF_BUZZER_COUNT,
    CONF_ESPHOME_ENTRY_ID,
    DEFAULT_BUZZER_COUNT,
    DOMAIN,
    ESPHOME_DOMAIN,
    MAX_BUZZERS,
)
from .esphome_actions import device_name_from_entry, esphome_service_name

BUZZER_COUNT_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=1, max=MAX_BUZZERS, step=1, mode=selector.NumberSelectorMode.BOX
    )
)


class MidiPlayerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def _user_schema(self) -> vol.Schema:
        options = [
            selector.SelectOptionDict(value=entry.entry_id, label=entry.title)
            for entry in self.hass.config_entries.async_entries(ESPHOME_DOMAIN)
        ]
        return vol.Schema(
            {
                vol.Required(CONF_ESPHOME_ENTRY_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_BUZZER_COUNT, default=DEFAULT_BUZZER_COUNT
                ): BUZZER_COUNT_SELECTOR,
            }
        )

    def _validate(self, esphome_entry_id: str) -> str | None:
        entry = self.hass.config_entries.async_get_entry(esphome_entry_id)
        if entry is None or entry.domain != ESPHOME_DOMAIN:
            return "invalid_device"
        device_name = device_name_from_entry(entry.data, entry.title)
        for action in (ACTION_PLAY_CHUNK, ACTION_STOP):
            service = esphome_service_name(device_name, action)
            if not self.hass.services.has_service(ESPHOME_DOMAIN, service):
                return "actions_missing"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self.hass.config_entries.async_entries(ESPHOME_DOMAIN):
            return self.async_abort(reason="no_esphome_devices")
        errors: dict[str, str] = {}
        if user_input is not None:
            esphome_entry_id = user_input[CONF_ESPHOME_ENTRY_ID]
            error = self._validate(esphome_entry_id)
            if error is None:
                await self.async_set_unique_id(esphome_entry_id)
                self._abort_if_unique_id_configured()
                esphome_entry = self.hass.config_entries.async_get_entry(
                    esphome_entry_id
                )
                return self.async_create_entry(
                    title=f"MIDI Player ({esphome_entry.title})",
                    data={
                        CONF_ESPHOME_ENTRY_ID: esphome_entry_id,
                        CONF_BUZZER_COUNT: int(user_input[CONF_BUZZER_COUNT]),
                    },
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="user", data_schema=self._user_schema(), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            return self.async_update_reload_and_abort(
                entry,
                data_updates={CONF_BUZZER_COUNT: int(user_input[CONF_BUZZER_COUNT])},
            )
        schema = vol.Schema({vol.Required(CONF_BUZZER_COUNT): BUZZER_COUNT_SELECTOR})
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(schema, entry.data),
        )
