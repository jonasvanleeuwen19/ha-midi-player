from __future__ import annotations

from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode as HARepeatMode,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ATTR_BUZZER, ATTR_FILENAME, DOMAIN, MAX_BUZZERS, SERVICE_PLAY_FILE
from .engine import PlaybackState
from .manager import MidiPlayerConfigEntry, MidiPlayerManager

PARALLEL_UPDATES = 0

STATE_MAP = {
    PlaybackState.IDLE: MediaPlayerState.IDLE,
    PlaybackState.PLAYING: MediaPlayerState.PLAYING,
    PlaybackState.PAUSED: MediaPlayerState.PAUSED,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MidiPlayerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([MidiPlayerMediaPlayer(entry.runtime_data, entry)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_PLAY_FILE,
        {
            vol.Required(ATTR_FILENAME): cv.string,
            vol.Optional(ATTR_BUZZER): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=MAX_BUZZERS)
            ),
        },
        "async_play_file",
    )


class MidiPlayerMediaPlayer(MediaPlayerEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = None
    _attr_media_content_type = MediaType.MUSIC
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.SEEK
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.SELECT_SOUND_MODE
        | MediaPlayerEntityFeature.REPEAT_SET
    )

    def __init__(
        self,
        manager: MidiPlayerManager,
        entry: MidiPlayerConfigEntry,
    ) -> None:
        self._manager = manager
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            model="ESPHome buzzer player",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._manager.async_add_listener(self.async_write_ha_state)
        )

    @property
    def state(self) -> MediaPlayerState:
        return STATE_MAP[self._manager.state]

    @property
    def repeat(self) -> HARepeatMode:
        return HARepeatMode(self._manager.repeat.value)

    async def async_set_repeat(self, repeat: HARepeatMode) -> None:
        await self._manager.async_set_repeat(repeat.value)

    @property
    def source_list(self) -> list[str]:
        return sorted(self._manager.files)

    @property
    def source(self) -> str | None:
        return self._manager.selected_file

    @property
    def sound_mode_list(self) -> list[str]:
        return [
            f"Buzzer {number}"
            for number in range(1, self._manager.buzzer_count + 1)
        ]

    @property
    def sound_mode(self) -> str:
        return f"Buzzer {self._manager.buzzer}"

    @property
    def media_title(self) -> str | None:
        return self._manager.selected_file

    @property
    def media_duration(self) -> int | None:
        if self._manager.selected_file is None:
            return None
        return round(self._manager.duration_ms / 1000)

    @property
    def media_position(self) -> float | None:
        if self._manager.state is PlaybackState.IDLE:
            return None
        return self._manager.position_ms / 1000

    @property
    def media_position_updated_at(self) -> datetime | None:
        if self._manager.state is PlaybackState.IDLE:
            return None
        return self._manager.position_updated_at

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "buzzer": self._manager.buzzer,
            "buzzer_count": self._manager.buzzer_count,
            "file_count": len(self._manager.files),
            "last_error": self._manager.last_error,
        }

    async def async_media_play(self) -> None:
        await self._manager.async_play()

    async def async_media_pause(self) -> None:
        await self._manager.async_pause()

    async def async_media_stop(self) -> None:
        await self._manager.async_stop()

    async def async_media_seek(self, position: float) -> None:
        await self._manager.async_seek(int(position * 1000))

    async def async_select_source(self, source: str) -> None:
        await self._manager.async_select_file(source)

    async def async_select_sound_mode(self, sound_mode: str) -> None:
        try:
            number = int(sound_mode.rsplit(" ", 1)[-1])
        except ValueError as err:
            raise ServiceValidationError(
                f"Unknown buzzer: {sound_mode}"
            ) from err
        await self._manager.async_set_buzzer(number)

    async def async_play_file(
        self,
        filename: str,
        buzzer: int | None = None,
    ) -> None:
        await self._manager.async_play_file(filename, buzzer)