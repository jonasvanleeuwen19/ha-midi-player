import unittest

import support
from support import end_of_track, midi_file, note_off, note_on, tempo, track

from custom_components.midi_player.melody import (
    MAX_FREQUENCY,
    MIN_FREQUENCY,
    build_melody,
    fit_frequency,
    note_to_frequency,
)
from custom_components.midi_player.midi import MidiError, parse_midi

C4 = 261.63
E4 = 329.63
C5 = 523.25
A4 = 440.0


def melody_from(tracks, **kwargs):
    division = kwargs.pop("division", 480)
    midi_format = kwargs.pop("midi_format", 1)
    return build_melody(parse_midi(midi_file(tracks, division, midi_format)), **kwargs)


def pairs(melody):
    return [(segment.frequency, segment.duration_ms) for segment in melody.segments]


class FrequencyTests(unittest.TestCase):
    def test_reference_pitches(self):
        self.assertAlmostEqual(note_to_frequency(69), 440.0, places=6)
        self.assertAlmostEqual(note_to_frequency(60), 261.6256, places=3)
        self.assertAlmostEqual(note_to_frequency(81), 880.0, places=6)
        self.assertAlmostEqual(note_to_frequency(57), 220.0, places=6)
        self.assertAlmostEqual(note_to_frequency(21), 27.5, places=6)
        self.assertAlmostEqual(note_to_frequency(127), 12543.854, places=2)

    def test_octave_doubles_frequency(self):
        for note in range(0, 115):
            self.assertAlmostEqual(
                note_to_frequency(note + 12) / note_to_frequency(note), 2.0, places=9
            )

    def test_fit_frequency_folds_into_range(self):
        for note in range(128):
            fitted = fit_frequency(note_to_frequency(note), MIN_FREQUENCY, MAX_FREQUENCY)
            self.assertGreaterEqual(fitted, MIN_FREQUENCY)
            self.assertLessEqual(fitted, MAX_FREQUENCY)

    def test_fit_frequency_keeps_in_range_values(self):
        self.assertEqual(fit_frequency(440.0, MIN_FREQUENCY, MAX_FREQUENCY), 440.0)

    def test_extreme_notes_are_shifted_by_whole_octaves(self):
        melody = melody_from(
            [track(note_on(0, 0), note_off(480, 0), note_on(0, 127), note_off(480, 127), end_of_track())]
        )
        low, high = melody.segments
        self.assertAlmostEqual(low.frequency, note_to_frequency(0) * 16, places=1)
        self.assertAlmostEqual(high.frequency, note_to_frequency(127) / 2, places=1)


class TimingTests(unittest.TestCase):
    def test_single_note_uses_default_tempo(self):
        melody = melody_from([track(note_on(0, 69), note_off(480, 69), end_of_track())])
        self.assertEqual(pairs(melody), [(A4, 500)])
        self.assertEqual(melody.duration_ms, 500)

    def test_ticks_per_beat_scales_duration(self):
        melody = melody_from(
            [track(note_on(0, 69), note_off(96, 69), end_of_track())], division=96
        )
        self.assertEqual(pairs(melody), [(A4, 500)])

    def test_tempo_set_at_tick_zero(self):
        melody = melody_from(
            [track(tempo(0, 1_000_000), note_on(0, 69), note_off(480, 69), end_of_track())]
        )
        self.assertEqual(pairs(melody), [(A4, 1000)])

    def test_tempo_change_between_notes(self):
        melody = melody_from(
            [
                track(
                    note_on(0, 60),
                    note_off(480, 60),
                    tempo(0, 1_000_000),
                    note_on(0, 64),
                    note_off(480, 64),
                    end_of_track(),
                )
            ]
        )
        self.assertEqual(pairs(melody), [(C4, 500), (E4, 1000)])

    def test_tempo_change_inside_a_note(self):
        melody = melody_from(
            [
                track(
                    note_on(0, 60),
                    tempo(480, 1_000_000),
                    note_off(480, 60),
                    end_of_track(),
                )
            ]
        )
        self.assertEqual(pairs(melody), [(C4, 1500)])

    def test_multiple_tempo_changes(self):
        melody = melody_from(
            [
                track(
                    note_on(0, 60),
                    note_off(480, 60),
                    tempo(0, 250_000),
                    note_on(0, 62),
                    note_off(480, 62),
                    tempo(0, 2_000_000),
                    note_on(0, 64),
                    note_off(480, 64),
                    end_of_track(),
                )
            ]
        )
        self.assertEqual([duration for _, duration in pairs(melody)], [500, 250, 2000])

    def test_tempo_event_on_another_track(self):
        melody = melody_from(
            [
                track(tempo(0, 250_000), end_of_track()),
                track(note_on(0, 69), note_off(480, 69), end_of_track()),
            ]
        )
        self.assertEqual(pairs(melody), [(A4, 250)])

    def test_smpte_timing(self):
        division = ((256 - 25) << 8) | 40
        melody = melody_from(
            [track(note_on(0, 69), note_off(500, 69), end_of_track())],
            division=division,
        )
        self.assertEqual(pairs(melody), [(A4, 500)])

    def test_leading_and_inner_rests(self):
        melody = melody_from(
            [
                track(
                    note_on(480, 69),
                    note_off(480, 69),
                    note_on(480, 72),
                    note_off(480, 72),
                    end_of_track(),
                )
            ]
        )
        self.assertEqual(
            [(round(f, 2) if f else 0.0, d) for f, d in pairs(melody)],
            [(0.0, 500), (A4, 500), (0.0, 500), (523.25 * 0 + round(note_to_frequency(72), 2), 500)],
        )

    def test_trailing_silence_is_removed(self):
        melody = melody_from(
            [track(note_on(0, 69), note_off(480, 69), end_of_track(4800))]
        )
        self.assertEqual(pairs(melody), [(A4, 500)])
        self.assertEqual(melody.duration_ms, 500)

    def test_duration_equals_sum_of_segments(self):
        melody = melody_from(
            [
                track(
                    note_on(0, 60),
                    note_on(100, 64),
                    note_off(100, 60),
                    note_off(173, 64),
                    note_on(55, 67),
                    note_off(333, 67),
                    end_of_track(),
                )
            ]
        )
        self.assertEqual(melody.duration_ms, sum(s.duration_ms for s in melody.segments))

    def test_hanging_note_ends_at_end_of_track(self):
        melody = melody_from([track(note_on(0, 69), end_of_track(480))])
        self.assertEqual(pairs(melody), [(A4, 500)])


class MonophonicReductionTests(unittest.TestCase):
    def test_highest_active_note_wins(self):
        melody = melody_from(
            [
                track(
                    note_on(0, 60),
                    note_on(480, 72),
                    note_off(240, 72),
                    note_off(240, 60),
                    end_of_track(),
                )
            ]
        )
        self.assertEqual(pairs(melody), [(C4, 500), (C5, 250), (C4, 250)])

    def test_overlapping_notes_across_tracks(self):
        melody = melody_from(
            [
                track(note_on(0, 60), note_off(960, 60), end_of_track()),
                track(note_on(480, 64), note_off(480, 64), end_of_track()),
            ]
        )
        self.assertEqual(pairs(melody), [(C4, 500), (E4, 500)])

    def test_lower_note_started_later_does_not_interrupt(self):
        melody = melody_from(
            [track(note_on(0, 72), note_on(240, 60), note_off(240, 72), note_off(240, 60), end_of_track())]
        )
        self.assertEqual(pairs(melody), [(C5, 500), (C4, 250)])

    def test_same_pitch_on_two_channels_is_one_tone(self):
        melody = melody_from(
            [
                track(
                    note_on(0, 60, channel=0),
                    note_on(240, 60, channel=1),
                    note_off(240, 60, channel=0),
                    note_off(240, 60, channel=1),
                    end_of_track(),
                )
            ]
        )
        self.assertEqual(pairs(melody), [(C4, 750)])

    def test_repeated_note_gets_articulation_gap(self):
        melody = melody_from(
            [
                track(
                    note_on(0, 60),
                    note_off(480, 60),
                    note_on(0, 60),
                    note_off(480, 60),
                    end_of_track(),
                )
            ]
        )
        self.assertEqual(pairs(melody), [(C4, 480), (0.0, 20), (C4, 500)])

    def test_note_hand_off_between_pitches_has_no_gap(self):
        melody = melody_from(
            [
                track(
                    note_on(0, 60),
                    note_off(480, 60),
                    note_on(0, 64),
                    note_off(480, 64),
                    end_of_track(),
                )
            ]
        )
        self.assertEqual(pairs(melody), [(C4, 500), (E4, 500)])

    def test_percussion_channel_is_ignored(self):
        melody = melody_from(
            [
                track(
                    note_on(0, 36, channel=9),
                    note_on(0, 69, channel=0),
                    note_off(480, 36, channel=9),
                    note_off(0, 69, channel=0),
                    end_of_track(),
                )
            ]
        )
        self.assertEqual(pairs(melody), [(A4, 500)])

    def test_percussion_only_file_is_rejected(self):
        with self.assertRaises(MidiError):
            melody_from([track(note_on(0, 36, channel=9), note_off(480, 36, channel=9), end_of_track())])

    def test_percussion_can_be_included(self):
        melody = melody_from(
            [track(note_on(0, 69, channel=9), note_off(480, 69, channel=9), end_of_track())],
            include_percussion=True,
        )
        self.assertEqual(pairs(melody), [(A4, 500)])

    def test_zero_length_notes_produce_nothing(self):
        melody = melody_from(
            [
                track(
                    note_on(0, 72),
                    note_off(0, 72),
                    note_on(0, 69),
                    note_off(480, 69),
                    end_of_track(),
                )
            ]
        )
        self.assertEqual(pairs(melody), [(A4, 500)])

    def test_short_blip_between_different_notes_is_merged_into_previous(self):
        melody = melody_from(
            [
                track(
                    note_on(0, 60),
                    note_off(480, 60),
                    note_on(0, 72),
                    note_off(4, 72),
                    note_on(0, 64),
                    note_off(476, 64),
                    end_of_track(),
                )
            ]
        )
        self.assertEqual(pairs(melody), [(C4, 504), (E4, 496)])
        self.assertEqual(melody.duration_ms, 1000)

    def test_short_blip_inside_a_sustained_note_disappears(self):
        melody = melody_from(
            [
                track(
                    note_on(0, 60),
                    note_on(480, 72),
                    note_off(4, 72),
                    note_off(476, 60),
                    end_of_track(),
                )
            ]
        )
        self.assertEqual(pairs(melody), [(C4, 1000)])

    def test_file_without_notes_is_rejected(self):
        with self.assertRaises(MidiError):
            melody_from([track(tempo(0, 500000), end_of_track())])

    def test_only_note_off_events_are_rejected(self):
        with self.assertRaises(MidiError):
            melody_from([track(note_off(0, 60), end_of_track())])

    def test_note_off_for_unknown_note_is_harmless(self):
        melody = melody_from(
            [track(note_off(0, 50), note_on(0, 69), note_off(480, 69), end_of_track())]
        )
        self.assertEqual(pairs(melody), [(A4, 500)])

    def test_segments_are_contiguous_in_time(self):
        data = support.midi_file(
            [
                track(
                    note_on(0, 60),
                    note_on(97, 67),
                    note_off(131, 60),
                    note_off(59, 67),
                    note_on(23, 72),
                    note_off(401, 72),
                    end_of_track(),
                )
            ]
        )
        melody = build_melody(parse_midi(data))
        self.assertTrue(all(segment.duration_ms >= 10 for segment in melody.segments))
        for first, second in zip(melody.segments, melody.segments[1:]):
            self.assertNotEqual(first.frequency, second.frequency)


if __name__ == "__main__":
    unittest.main()
