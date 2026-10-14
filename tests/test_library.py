from pathlib import Path
import tempfile
import unittest

import support

from custom_components.midi_player.const import MAX_FILE_SIZE
from custom_components.midi_player.esphome_actions import esphome_service_name
from custom_components.midi_player.library import (
    LibraryError,
    MidiLibrary,
    sanitize_filename,
)


class SanitizeFilenameTests(unittest.TestCase):
    def test_accepts_regular_names(self):
        self.assertEqual(sanitize_filename("song.mid"), "song.mid")
        self.assertEqual(sanitize_filename("My Song (v2).MIDI"), "My Song (v2).MIDI")

    def test_strips_directories(self):
        self.assertEqual(sanitize_filename("../../etc/passwd.mid"), "passwd.mid")
        self.assertEqual(sanitize_filename("C:\\music\\tune.mid"), "tune.mid")

    def test_replaces_unsafe_characters(self):
        self.assertEqual(sanitize_filename('a<b>"c.mid'), "a_b__c.mid")

    def test_rejects_other_extensions(self):
        for name in ("song.mp3", "song", "song.mid.exe", "midi"):
            with self.assertRaises(LibraryError):
                sanitize_filename(name)

    def test_rejects_empty_stem(self):
        for name in (".mid", " .mid", "...mid"):
            with self.assertRaises(LibraryError):
                sanitize_filename(name)

    def test_rejects_long_names(self):
        with self.assertRaises(LibraryError):
            sanitize_filename("a" * 200 + ".mid")

    def test_is_idempotent(self):
        for name in ("a .mid", "weird name (1).mid", "é.midi"):
            once = sanitize_filename(name)
            self.assertEqual(sanitize_filename(once), once)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.library = MidiLibrary(Path(self.directory.name) / "midi_player")

    def test_save_validates_and_stores(self):
        entry = self.library.save("tune.mid", support.single_note_file())
        self.assertEqual(entry.filename, "tune.mid")
        self.assertEqual(entry.duration_ms, 500)
        self.assertTrue((self.library.directory / "tune.mid").is_file())
        self.assertEqual(list(self.library.directory.glob("*.tmp")), [])

    def test_list_returns_saved_files(self):
        self.library.save("b.mid", support.single_note_file(60))
        self.library.save("a.midi", support.single_note_file(72))
        self.assertEqual(list(self.library.list_entries()), ["a.midi", "b.mid"])

    def test_list_on_missing_directory(self):
        self.assertEqual(self.library.list_entries(), {})

    def test_list_skips_invalid_and_foreign_files(self):
        self.library.ensure_directory()
        (self.library.directory / "broken.mid").write_bytes(b"not midi at all")
        (self.library.directory / "notes.txt").write_text("hello")
        (self.library.directory / "weird;name.mid").write_bytes(support.single_note_file())
        self.library.save("good.mid", support.single_note_file())
        self.assertEqual(list(self.library.list_entries()), ["good.mid"])

    def test_save_overwrites_existing_file(self):
        self.library.save("tune.mid", support.single_note_file(69, 480))
        entry = self.library.save("tune.mid", support.single_note_file(69, 960))
        self.assertEqual(entry.duration_ms, 1000)
        self.assertEqual(self.library.load("tune.mid").duration_ms, 1000)

    def test_save_rejects_malformed_file(self):
        with self.assertRaises(LibraryError):
            self.library.save("bad.mid", b"MThd" + b"\x00" * 20)
        self.assertEqual(self.library.list_entries(), {})

    def test_save_rejects_empty_file(self):
        with self.assertRaises(LibraryError):
            self.library.save("empty.mid", b"")

    def test_save_rejects_oversized_file(self):
        data = support.single_note_file() + b"\x00" * MAX_FILE_SIZE
        with self.assertRaises(LibraryError) as context:
            self.library.save("big.mid", data)
        self.assertIn("larger", str(context.exception))

    def test_save_rejects_wrong_extension(self):
        with self.assertRaises(LibraryError):
            self.library.save("tune.wav", support.single_note_file())

    def test_path_traversal_cannot_escape_the_library(self):
        entry = self.library.save("../../escape.mid", support.single_note_file())
        self.assertEqual(entry.filename, "escape.mid")
        self.assertTrue((self.library.directory / "escape.mid").is_file())
        self.assertFalse((self.library.directory.parent / "escape.mid").exists())

    def test_load_unknown_file(self):
        with self.assertRaises(LibraryError):
            self.library.load("missing.mid")

    def test_load_rejects_unsanitized_name(self):
        with self.assertRaises(LibraryError):
            self.library.load("../tune.mid")

    def test_delete(self):
        self.library.save("tune.mid", support.single_note_file())
        self.library.delete("tune.mid")
        self.assertEqual(self.library.list_entries(), {})
        with self.assertRaises(LibraryError):
            self.library.delete("tune.mid")

    def test_delete_rejects_unsanitized_name(self):
        with self.assertRaises(LibraryError):
            self.library.delete("../tune.mid")


class EsphomeServiceNameTests(unittest.TestCase):
    def test_dashes_become_underscores(self):
        self.assertEqual(
            esphome_service_name("tux-universe-esp32-wroom-1", "midi_play_chunk"),
            "tux_universe_esp32_wroom_1_midi_play_chunk",
        )

    def test_plain_names_are_kept(self):
        self.assertEqual(esphome_service_name("livingroom", "midi_stop"), "livingroom_midi_stop")


if __name__ == "__main__":
    unittest.main()
