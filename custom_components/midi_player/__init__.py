from __future__ import annotations

from pathlib import Path

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_FILENAME,
    CARD_FILENAME,
    CARD_VERSION,
    DOMAIN,
    SERVICE_DELETE_FILE,
    SERVICE_RESCAN_LIBRARY,
    STATIC_URL_PATH,
)
from .manager import (
    LibraryHub,
    MidiPlayerConfigEntry,
    MidiPlayerManager,
    async_remove_stored_state,
    get_hub,
)
from .websocket import async_register_websocket

PLATFORMS = [Platform.MEDIA_PLAYER]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hub = LibraryHub(hass)
    await hub.async_load()
    hass.data[DOMAIN] = hub

    if hass.http is not None:
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    STATIC_URL_PATH,
                    str(Path(__file__).parent / "frontend"),
                    False,
                )
            ]
        )
        add_extra_js_url(hass, f"{STATIC_URL_PATH}/{CARD_FILENAME}?v={CARD_VERSION}")
    async_register_websocket(hass)

    async def handle_delete_file(call: ServiceCall) -> None:
        await get_hub(hass).async_delete(call.data[ATTR_FILENAME])

    async def handle_rescan_library(call: ServiceCall) -> None:
        await get_hub(hass).async_rescan()

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_DELETE_FILE,
        handle_delete_file,
        vol.Schema({vol.Required(ATTR_FILENAME): cv.string}),
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_RESCAN_LIBRARY,
        handle_rescan_library,
        vol.Schema({}),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MidiPlayerConfigEntry) -> bool:
    manager = MidiPlayerManager(hass, entry, get_hub(hass))
    await manager.async_setup()
    entry.runtime_data = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, manager.async_handle_stop)
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MidiPlayerConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: MidiPlayerConfigEntry) -> None:
    await async_remove_stored_state(hass, entry.entry_id)
