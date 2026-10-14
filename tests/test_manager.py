import asyncio
import datetime
from pathlib import Path
import sys
import tempfile
import types
import unittest

import support

if not support.HOMEASSISTANT_AVAILABLE:

    def _module(name, **attributes):
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        sys.modules[name] = module
        return module

    class _Subscriptable:
        def __class_getitem__(cls, item):
            return cls

    class _HomeAssistantError(Exception):
        pass

    class _ServiceValidationError(_HomeAssistantError):
        pass

    _module("homeassistant")
    _module("homeassistant.helpers")
    _module("homeassistant.config_entries", ConfigEntry=_Subscriptable)
    _module("homeassistant.core", Event=object, HomeAssistant=object)
    _module(
        "homeassistant.exceptions",
        HomeAssistantError=_HomeAssistantError,
        ServiceValidationError=_ServiceValidationError,
    )
    _module("homeassistant.helpers.storage", Store=_Subscriptable)
    _module(
        "homeassistant.util.dt",
        utcnow=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
    _module("homeassistant.util", dt=sys.modules["homeassistant.util.dt"])

from homeassistant.exceptions import ServiceValidationError

from custom_components.midi_player import manager as manager_module
from custom_components.midi_player.engine import PlaybackState
from custom_components.midi_player.manager import LibraryHub, MidiPlayerManager


class FakeStore:
    saved = {}

    def __init__(self, hass, version, key):
        self._key = key

    async def async_load(self):
        return FakeStore.saved.get(self._key)

    def async_delay_save(self, data_function, delay):
        FakeStore.saved[self._key] = data_function()

    async def async_save(self, data):
        FakeStore.saved[self._key] = data

    async def async_remove(self):
        FakeStore.saved.pop(self._key, None)


class FakeServices:
    def __init__(self):
        self.calls = []
        self.available = True

    def has_service(self, domain, service):
        return self.available and service.startswith("my_device_midi_")

    async def async_call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, data))


class FakeEsphomeEntry:
    domain = "esphome"
    title = "my-device"
    data = {"device_name": "my-device"}


class FakeConfigEntries:
    def async_get_entry(self, entry_id):
        return FakeEsphomeEntry()


class FakeHass:
    def __init__(self, directory):
        self.services = FakeServices()
        self.config_entries = FakeConfigEntries()
        self.config = types.SimpleNamespace(path=lambda name: str(Path(directory) / name))

    async def async_add_executor_job(self, function, *args):
        return function(*args)


class FakeEntry:
    def __init__(self, entry_id="one", buzzer_count=2):
        self.entry_id = entry_id
        self.data = {"esphome_entry_id": "esphome-id", "buzzer_count": buzzer_count}

    def async_create_background_task(self, hass, coroutine, name):
        return asyncio.get_running_loop().create_task(coroutine)


class ManagerTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeStore.saved = {}
        self._original_store = manager_module.Store
        manager_module.Store = FakeStore
        self.directory = tempfile.TemporaryDirectory()
        self.hass = FakeHass(self.directory.name)
        self.hub = LibraryHub(self.hass)
        await self.hub.async_load()
        self.managers = []

    async def asyncTearDown(self):
        for manager in self.managers:
            await manager.async_shutdown()
        manager_module.Store = self._original_store
        self.directory.cleanup()

    async def make_manager(self, entry_id="one", buzzer_count=2):
        manager = MidiPlayerManager(self.hass, FakeEntry(entry_id, buzzer_count), self.hub)
        await manager.async_setup()
        self.managers.append(manager)
        return manager


class PlaybackFlowTests(ManagerTestCase):
    async def test_upload_select_play_pause_resume_seek_stop(self):
        manager = await self.make_manager()
        entry = await self.hub.async_upload("a.mid", support.single_note_file(69, 960))
        self.assertEqual(entry.duration_ms, 1000)
        self.assertIn("a.mid", manager.files)
        await manager.async_select_file("a.mid")
        await manager.async_set_buzzer(2)
        await manager.async_play()
        self.assertEqual(manager.state, PlaybackState.PLAYING)
        domain, service, data = self.hass.services.calls[-1]
        self.assertEqual((domain, service), ("esphome", "my_device_midi_play_chunk"))
        self.assertEqual(data["buzzer"], 2)
        self.assertTrue(data["clear_queue"])
        self.assertEqual(data["frequencies"], [440.0])
        self.assertEqual(data["durations"], [1000])
        await asyncio.sleep(0.15)
        await manager.async_pause()
        self.assertEqual(manager.state, PlaybackState.PAUSED)
        self.assertGreater(manager.position_ms, 100)
        self.assertEqual(self.hass.services.calls[-1][1], "my_device_midi_stop")
        await manager.async_play()
        self.assertEqual(manager.state, PlaybackState.PLAYING)
        await manager.async_seek(500)
        self.assertGreaterEqual(manager.position_ms, 500)
        await manager.async_stop()
        self.assertEqual(manager.state, PlaybackState.IDLE)
        self.assertEqual(self.hass.services.calls[-1][1], "my_device_midi_stop")

    async def test_play_without_selection_is_rejected(self):
        manager = await self.make_manager()
        with self.assertRaises(ServiceValidationError):
            await manager.async_play()

    async def test_unknown_file_is_rejected(self):
        manager = await self.make_manager()
        with self.assertRaises(ServiceValidationError):
            await manager.async_select_file("missing.mid")

    async def test_invalid_buzzer_is_rejected(self):
        manager = await self.make_manager(buzzer_count=2)
        with self.assertRaises(ServiceValidationError):
            await manager.async_set_buzzer(3)

    async def test_play_file_sets_buzzer_and_starts(self):
        manager = await self.make_manager(buzzer_count=3)
        await self.hub.async_upload("a.mid", support.single_note_file())
        await manager.async_play_file("a.mid", 3)
        self.assertEqual(manager.state, PlaybackState.PLAYING)
        self.assertEqual(manager.buzzer, 3)
        self.assertEqual(self.hass.services.calls[-1][2]["buzzer"], 3)

    async def test_missing_device_actions_raise_a_clear_error(self):
        manager = await self.make_manager()
        await self.hub.async_upload("a.mid", support.single_note_file())
        await manager.async_select_file("a.mid")
        self.hass.services.available = False
        with self.assertRaises(Exception) as context:
            await manager.async_play()
        self.assertIn("MIDI Player package", str(context.exception))
        self.assertEqual(manager.state, PlaybackState.IDLE)

    async def test_selection_and_buzzer_survive_a_restart(self):
        manager = await self.make_manager(buzzer_count=2)
        await self.hub.async_upload("a.mid", support.single_note_file())
        await manager.async_select_file("a.mid")
        await manager.async_set_buzzer(2)
        await manager.async_shutdown()
        self.managers.remove(manager)
        restored = await self.make_manager(buzzer_count=2)
        self.assertEqual(restored.selected_file, "a.mid")
        self.assertEqual(restored.buzzer, 2)
        self.assertEqual(restored.duration_ms, 500)


class LibraryHubTests(ManagerTestCase):
    async def test_malformed_upload_is_rejected_and_not_listed(self):
        with self.assertRaises(ServiceValidationError):
            await self.hub.async_upload("bad.mid", b"junk")
        self.assertEqual(self.hub.files, {})

    async def test_delete_clears_selection_and_stops_playback(self):
        manager = await self.make_manager()
        await self.hub.async_upload("a.mid", support.single_note_file())
        await manager.async_play_file("a.mid")
        await self.hub.async_delete("a.mid")
        self.assertIsNone(manager.selected_file)
        self.assertEqual(manager.state, PlaybackState.IDLE)
        self.assertEqual(self.hass.services.calls[-1][1], "my_device_midi_stop")

    async def test_overwriting_the_selected_file_reloads_it(self):
        manager = await self.make_manager()
        await self.hub.async_upload("a.mid", support.single_note_file(69, 480))
        await manager.async_select_file("a.mid")
        self.assertEqual(manager.duration_ms, 500)
        await self.hub.async_upload("a.mid", support.single_note_file(69, 960))
        self.assertEqual(manager.duration_ms, 1000)

    async def test_files_are_shared_between_devices(self):
        first = await self.make_manager("one")
        second = await self.make_manager("two")
        await self.hub.async_upload("shared.mid", support.single_note_file())
        self.assertIn("shared.mid", first.files)
        await second.async_select_file("shared.mid")
        await first.async_select_file("shared.mid")
        await self.hub.async_delete("shared.mid")
        self.assertIsNone(first.selected_file)
        self.assertIsNone(second.selected_file)

    async def test_rescan_picks_up_files_copied_manually(self):
        await self.make_manager()
        library_directory = Path(self.directory.name) / "midi_player"
        (library_directory / "manual.mid").write_bytes(support.single_note_file())
        await self.hub.async_rescan()
        self.assertIn("manual.mid", self.hub.files)

    async def test_stored_state_is_per_entry(self):
        first = await self.make_manager("one")
        second = await self.make_manager("two")
        await self.hub.async_upload("a.mid", support.single_note_file())
        await first.async_select_file("a.mid")
        await asyncio.sleep(0)
        self.assertIn("midi_player.one", FakeStore.saved)
        self.assertNotIn("midi_player.two", FakeStore.saved)
        self.assertIsNone(second.selected_file)


if __name__ == "__main__":
    unittest.main()
