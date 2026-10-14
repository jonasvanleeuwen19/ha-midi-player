from fractions import Fraction
import unittest

import support
from support import (
    end_of_track,
    event,
    midi_file,
    note_off,
    note_on,
    tempo,
    track,
    variable_length,
)

from custom_components.midi_player.midi import MidiError, parse_midi


class ParseMidiTests(unittest.TestCase):
    def test_parses_notes_and_header(self):
        data = midi_file(
            [track(note_on(0, 60), note_off(480, 60), end_of_track())],
            division=96,
            midi_format=0,
        )
        midi = parse_midi(data)
        self.assertEqual(midi.format, 0)
        self.assertEqual(midi.ticks_per_beat, 96)
        self.assertIsNone(midi.ticks_per_second)
        self.assertEqual(len(midi.notes), 2)
        self.assertTrue(midi.notes[0].is_on)
        self.assertFalse(midi.notes[1].is_on)
        self.assertEqual(midi.notes[1].tick, 480)
        self.assertEqual(midi.end_tick, 480)

    def test_note_on_with_zero_velocity_is_note_off(self):
        data = midi_file(
            [track(note_on(0, 60), note_on(100, 60, velocity=0), end_of_track())]
        )
        notes = parse_midi(data).notes
        self.assertTrue(notes[0].is_on)
        self.assertFalse(notes[1].is_on)

    def test_running_status(self):
        body = (
            variable_length(0)
            + bytes([0x90, 60, 100])
            + variable_length(10)
            + bytes([62, 100])
            + variable_length(10)
            + bytes([64, 100])
        )
        data = midi_file([b"MTrk" + len(body).to_bytes(4, "big") + body])
        notes = parse_midi(data).notes
        self.assertEqual([note.note for note in notes], [60, 62, 64])
        self.assertEqual([note.tick for note in notes], [0, 10, 20])

    def test_tempo_events_are_collected(self):
        data = midi_file(
            [
                track(
                    tempo(0, 600000),
                    note_on(0, 60),
                    tempo(480, 300000),
                    note_off(480, 60),
                    end_of_track(),
                )
            ]
        )
        tempos = parse_midi(data).tempos
        self.assertEqual([(t.tick, t.microseconds_per_quarter) for t in tempos], [(0, 600000), (480, 300000)])

    def test_multiple_tracks_are_merged_and_sorted(self):
        data = midi_file(
            [
                track(note_on(100, 60), note_off(100, 60), end_of_track()),
                track(note_on(0, 72), note_off(300, 72), end_of_track()),
            ]
        )
        midi = parse_midi(data)
        self.assertEqual([note.tick for note in midi.notes], [0, 100, 200, 300])
        self.assertEqual(midi.end_tick, 300)

    def test_smpte_division(self):
        division = ((256 - 25) << 8) | 40
        data = midi_file(
            [track(note_on(0, 60), note_off(1000, 60), end_of_track())],
            division=division,
        )
        midi = parse_midi(data)
        self.assertIsNone(midi.ticks_per_beat)
        self.assertEqual(midi.ticks_per_second, Fraction(1000))

    def test_unknown_chunks_are_skipped(self):
        data = midi_file([track(note_on(0, 60), note_off(10, 60), end_of_track())])
        data += b"JUNK" + (3).to_bytes(4, "big") + b"abc"
        self.assertEqual(len(parse_midi(data).notes), 2)

    def test_meta_and_sysex_events_are_skipped(self):
        data = midi_file(
            [
                track(
                    event(0, b"\xff\x03\x04name"),
                    event(0, b"\xf0\x03\x01\x02\xf7"),
                    note_on(0, 60),
                    note_off(10, 60),
                    end_of_track(),
                )
            ]
        )
        self.assertEqual(len(parse_midi(data).notes), 2)

    def test_track_length_beyond_file_is_tolerated(self):
        body = note_on(0, 60) + note_off(10, 60)
        data = midi_file([b"MTrk" + (1000).to_bytes(4, "big") + body])
        self.assertEqual(len(parse_midi(data).notes), 2)

    def test_delta_time_without_event_at_end_is_tolerated(self):
        data = midi_file([track(note_on(0, 60), note_off(10, 60), variable_length(5))])
        self.assertEqual(len(parse_midi(data).notes), 2)


class MalformedFileTests(unittest.TestCase):
    def assert_rejected(self, data: bytes, fragment: str | None = None):
        with self.assertRaises(MidiError) as context:
            parse_midi(data)
        if fragment:
            self.assertIn(fragment, str(context.exception))

    def test_empty_and_tiny_files(self):
        self.assert_rejected(b"", "too small")
        self.assert_rejected(b"MThd\x00\x00", "too small")

    def test_wrong_signature(self):
        data = bytearray(support.single_note_file())
        data[0:4] = b"RIFF"
        self.assert_rejected(bytes(data), "header")

    def test_invalid_header_length(self):
        data = bytearray(support.single_note_file())
        data[4:8] = (2).to_bytes(4, "big")
        self.assert_rejected(bytes(data), "header")
        data[4:8] = (500).to_bytes(4, "big")
        self.assert_rejected(bytes(data), "header")

    def test_format_two_is_rejected(self):
        data = midi_file([track(end_of_track())], midi_format=2)
        self.assert_rejected(data, "format 2")

    def test_zero_division_is_rejected(self):
        data = midi_file([track(end_of_track())], division=0)
        self.assert_rejected(data, "zero")

    def test_unsupported_smpte_rate(self):
        data = midi_file([track(end_of_track())], division=((256 - 12) << 8) | 40)
        self.assert_rejected(data, "SMPTE")

    def test_file_without_tracks(self):
        self.assert_rejected(midi_file([]), "tracks")

    def test_data_byte_without_running_status(self):
        body = variable_length(0) + bytes([60, 100])
        self.assert_rejected(midi_file([b"MTrk" + len(body).to_bytes(4, "big") + body]), "running status")

    def test_meta_event_clears_running_status(self):
        body = (
            variable_length(0)
            + bytes([0x90, 60, 100])
            + variable_length(0)
            + b"\xff\x01\x00"
            + variable_length(0)
            + bytes([60, 0])
        )
        self.assert_rejected(midi_file([b"MTrk" + len(body).to_bytes(4, "big") + body]), "running status")

    def test_invalid_data_byte(self):
        body = variable_length(0) + bytes([0x90, 200 & 0x7F | 0x80, 100])
        self.assert_rejected(midi_file([b"MTrk" + len(body).to_bytes(4, "big") + body]))
        body = variable_length(0) + bytes([0x90, 60, 0x80])
        self.assert_rejected(midi_file([b"MTrk" + len(body).to_bytes(4, "big") + body]), "data byte")

    def test_truncated_channel_event(self):
        body = variable_length(0) + bytes([0x90, 60])
        self.assert_rejected(midi_file([b"MTrk" + len(body).to_bytes(4, "big") + body]), "truncated")

    def test_variable_length_too_long(self):
        body = bytes([0x81, 0x81, 0x81, 0x81, 0x01]) + bytes([0x90, 60, 100])
        self.assert_rejected(midi_file([b"MTrk" + len(body).to_bytes(4, "big") + body]), "four bytes")

    def test_truncated_meta_event(self):
        body = variable_length(0) + b"\xff\x51\x03\x07"
        self.assert_rejected(midi_file([b"MTrk" + len(body).to_bytes(4, "big") + body]), "past the end")

    def test_tempo_event_with_wrong_size(self):
        body = variable_length(0) + b"\xff\x51\x02\x07\xa1"
        self.assert_rejected(midi_file([b"MTrk" + len(body).to_bytes(4, "big") + body]), "three bytes")

    def test_zero_tempo(self):
        self.assert_rejected(midi_file([track(tempo(0, 0), end_of_track())]), "zero tempo")

    def test_system_exclusive_past_end(self):
        body = variable_length(0) + b"\xf0\x20\x01"
        self.assert_rejected(midi_file([b"MTrk" + len(body).to_bytes(4, "big") + body]), "system exclusive")

    def test_unsupported_status_byte(self):
        body = variable_length(0) + bytes([0xF8])
        self.assert_rejected(midi_file([b"MTrk" + len(body).to_bytes(4, "big") + body]), "status byte")

    def test_random_bytes_never_raise_other_exceptions(self):
        import random

        generator = random.Random(1234)
        base = support.single_note_file()
        for _ in range(500):
            data = bytearray(base)
            for _ in range(generator.randint(1, 6)):
                data[generator.randrange(len(data))] = generator.randrange(256)
            try:
                parse_midi(bytes(data))
            except MidiError:
                pass


if __name__ == "__main__":
    unittest.main()
