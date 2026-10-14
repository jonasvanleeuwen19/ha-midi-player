from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import logging
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ACTION_PLAY_CHUNK,
    ACTION_STOP,
    CONF_BUZZER_COUNT,
    CONF_ESPHOME_ENTRY_ID,
    DEFAULT_BUZZER_COUNT,
    DOMAIN,
    ESPHOME_DOMAIN,
    LIBRARY_DIRECTORY,
    STORAGE_VERSION,
)
from .engine import PlaybackEngine, PlaybackError, PlaybackState, RepeatMode
from .esphome_actions import device_name_from_entry, esphome_service_name
from .library import LibraryEntry, LibraryError, MidiLibrary
from .melody import Melody

_LOGGER = logging.getLogger(__name__)


class EsphomeSender:
    def __init__(self, hass: HomeAssistant, esphome_entry_id: str) -> None:
        self._hass = hass
        self._esphome_entry_id = esphome_entry_id

    def _resolve(self, action: str) -> str:
        entry = self._hass.config_entries.async_get_entry(self._esphome_entry_id)
        if entry is None or entry.domain != ESPHOME_DOMAIN:
            raise HomeAssistantError("The configured ESPHome device no longer exists")
        service = esphome_service_name(
            device_name_from_entry(entry.data, entry.title), action
        )
        if not self._hass.services.has_service(ESPHOME_DOMAIN, service):
            raise HomeAssistantError(
                f"The action {ESPHOME_DOMAIN}.{service} is not available. "
                "Check that the device is online and includes the MIDI Player package"
            )
        return service

    async def send_chunk(
        self,
        buzzer: int,
        clear_queue: bool,
        frequencies: list[float],
        durations: list[int],
    ) -> None:
        service = self._resolve(ACTION_PLAY_CHUNK)
        await self._hass.services.async_call(
            ESPHOME_DOMAIN,
            service,
            {
                "buzzer": buzzer,
                "clear_queue": clear_queue,
                "frequencies": frequencies,
                "durations": durations,
            },
            blocking=True,
        )

    async def send_stop(self) -> None:
        service = self._resolve(ACTION_STOP)
        await self._hass.services.async_call(
            ESPHOME_DOMAIN, service, {}, blocking=True
        )


class LibraryHub:
    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._library = MidiLibrary(Path(hass.config.path(LIBRARY_DIRECTORY)))
        self._files: dict[str, LibraryEntry] = {}
        self._managers: list[MidiPlayerManager] = []
        self._lock = asyncio.Lock()

    @property
    def files(self) -> dict[str, LibraryEntry]:
        return dict(self._files)

    async def async_load(self) -> None:
        await self._hass.async_add_executor_job(self._library.ensure_directory)
        self._files = await self._hass.async_add_executor_job(
            self._library.list_entries
        )

    def add_manager(self, manager: MidiPlayerManager) -> None:
        self._managers.append(manager)

    def remove_manager(self, manager: MidiPlayerManager) -> None:
        if manager in self._managers:
            self._managers.remove(manager)

    async def async_load_melody(self, filename: str) -> Melody:
        if filename not in self._files:
            raise ServiceValidationError(f"Unknown MIDI file: {filename}")
        try:
            return await self._hass.async_add_executor_job(
                self._library.load, filename
            )
        except LibraryError as err:
            raise ServiceValidationError(str(err)) from err

    async def async_upload(self, filename: str, data: bytes) -> LibraryEntry:
        async with self._lock:
            try:
                entry = await self._hass.async_add_executor_job(
                    self._library.save, filename, data
                )
            except LibraryError as err:
                raise ServiceValidationError(str(err)) from err
            self._files[entry.filename] = entry
        await self._notify(entry.filename)
        return entry

    async def async_delete(self, filename: str) -> None:
        async with self._lock:
            try:
                await self._hass.async_add_executor_job(
                    self._library.delete, filename
                )
            except LibraryError as err:
                raise ServiceValidationError(str(err)) from err
            self._files.pop(filename, None)
        await self._notify(filename)

    async def async_rescan(self) -> None:
        async with self._lock:
            self._files = await self._hass.async_add_executor_job(
                self._library.list_entries
            )
        await self._notify(None)

    async def _notify(self, changed: str | None) -> None:
        for manager in list(self._managers):
            await manager.async_library_changed(changed)


class MidiPlayerManager:
    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, hub: LibraryHub
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._hub = hub
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        sender = EsphomeSender(hass, entry.data[CONF_ESPHOME_ENTRY_ID])
        self._engine = PlaybackEngine(
            sender.send_chunk,
            sender.send_stop,
            on_change=self._handle_change,
            create_task=self._create_task,
            buzzer_count=entry.data.get(CONF_BUZZER_COUNT, DEFAULT_BUZZER_COUNT),
        )
        self._selected: str | None = None
        self._listeners: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()
        self.position_updated_at = dt_util.utcnow()

    def _create_task(self, coroutine: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        return self._entry.async_create_background_task(
            self.hass, coroutine, f"{DOMAIN}_playback"
        )

    @property
    def files(self) -> dict[str, LibraryEntry]:
        return self._hub.files

    @property
    def selected_file(self) -> str | None:
        return self._selected

    @property
    def buzzer(self) -> int:
        return self._engine.buzzer

    @property
    def buzzer_count(self) -> int:
        return self._engine.buzzer_count

    @property
    def repeat(self) -> RepeatMode:
        return self._engine.repeat

    @property
    def state(self) -> PlaybackState:
        return self._engine.state

    @property
    def position_ms(self) -> int:
        return self._engine.position_ms

    @property
    def duration_ms(self) -> int:
        return self._engine.duration_ms

    @property
    def last_error(self) -> str | None:
        return self._engine.last_error

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def _handle_change(self) -> None:
        self.position_updated_at = dt_util.utcnow()
        for listener in list(self._listeners):
            listener()

    def _save_state(self) -> None:
        self._store.async_delay_save(self._stored_data, 1)

    def _stored_data(self) -> dict[str, Any]:
        return {
            "selected_file": self._selected,
            "buzzer": self._engine.buzzer,
            "repeat": self._engine.repeat.value,
        }

    async def _run(self, operation: Coroutine[Any, Any, None]) -> None:
        try:
            await operation
        except PlaybackError as err:
            raise ServiceValidationError(str(err)) from err

    async def async_setup(self) -> None:
        stored = await self._store.async_load() or {}

        buzzer = stored.get("buzzer")
        if isinstance(buzzer, int) and 1 <= buzzer <= self.buzzer_count:
            await self._engine.set_buzzer(buzzer)

        stored_repeat = stored.get("repeat")
        if isinstance(stored_repeat, str):
            try:
                repeat = RepeatMode(stored_repeat)
            except ValueError:
                repeat = RepeatMode.OFF
            await self._engine.set_repeat(repeat)

        selected = stored.get("selected_file")
        if selected in self._hub.files:
            try:
                melody = await self._hub.async_load_melody(selected)
            except HomeAssistantError as err:
                _LOGGER.warning("Could not restore %s: %s", selected, err)
            else:
                await self._engine.load(melody)
                self._selected = selected

        self._hub.add_manager(self)

    async def async_shutdown(self) -> None:
        self._hub.remove_manager(self)
        try:
            await self._engine.stop()
        except Exception as err:
            _LOGGER.warning("Could not stop playback during shutdown: %s", err)
        await self._store.async_save(self._stored_data())
        self._listeners.clear()

    async def async_handle_stop(self, event: Event) -> None:
        await self.async_shutdown()

    async def async_library_changed(self, changed: str | None) -> None:
        async with self._lock:
            selected = self._selected
            if selected is not None:
                if selected not in self._hub.files:
                    await self._engine.load(None)
                    self._selected = None
                    self._save_state()
                elif changed == selected:
                    try:
                        melody = await self._hub.async_load_melody(selected)
                    except HomeAssistantError as err:
                        _LOGGER.warning("Could not reload %s: %s", selected, err)
                    else:
                        await self._engine.load(melody)
        self._handle_change()

    async def async_select_file(self, filename: str) -> None:
        async with self._lock:
            melody = await self._hub.async_load_melody(filename)
            await self._engine.load(melody)
            self._selected = filename
            self._save_state()
        self._handle_change()

    async def async_set_buzzer(self, buzzer: int) -> None:
        await self._run(self._engine.set_buzzer(buzzer))
        self._save_state()
        self._handle_change()

    async def async_set_repeat(self, repeat: str | RepeatMode) -> None:
        try:
            mode = repeat if isinstance(repeat, RepeatMode) else RepeatMode(repeat)
        except ValueError as err:
            raise ServiceValidationError(f"Unknown repeat mode: {repeat}") from err

        await self._run(self._engine.set_repeat(mode))
        self._save_state()
        self._handle_change()

    async def async_play(self) -> None:
        if self._engine.state is PlaybackState.PAUSED:
            await self._run(self._engine.resume())
            return
        if self._selected is None:
            raise ServiceValidationError("Select a MIDI file first")
        await self._run(self._engine.play(0))

    async def async_play_file(self, filename: str, buzzer: int | None = None) -> None:
        if buzzer is not None:
            await self.async_set_buzzer(buzzer)
        await self.async_select_file(filename)
        await self._run(self._engine.play(0))

    async def async_pause(self) -> None:
        await self._run(self._engine.pause())

    async def async_stop(self) -> None:
        await self._run(self._engine.stop())

    async def async_seek(self, position_ms: int) -> None:
        await self._run(self._engine.seek(position_ms))


async def async_remove_stored_state(hass: HomeAssistant, entry_id: str) -> None:
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}").async_remove()


type MidiPlayerConfigEntry = ConfigEntry[MidiPlayerManager]


def get_hub(hass: HomeAssistant) -> LibraryHub:
    hub = hass.data.get(DOMAIN)
    if hub is None:
        raise ServiceValidationError("MIDI Player is not set up")
    return hub